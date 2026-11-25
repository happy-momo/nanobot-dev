"""MQTT subscription adapter backed by a bounded in-memory sample buffer."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import ssl
from collections import deque
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import aiomqtt
from loguru import logger

from ..config.model import SourceKind, SourceSpec
from .base import DataSource, Sample

_MAX_PAYLOAD_BYTES = 1_048_576
_MAX_BUFFERED_SAMPLES = 10_000


def _value_at_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split(".") if path else ():
        if not isinstance(current, dict) or part not in current:
            raise ValueError("configured MQTT payload field is missing")
        current = current[part]
    return current


def _timestamp(value: Any) -> datetime:
    if isinstance(value, bool):
        raise ValueError("MQTT timestamp is invalid")
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError("MQTT timestamp is invalid")
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("MQTT timestamp is invalid") from exc
    if not isinstance(value, str):
        raise ValueError("MQTT timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("MQTT timestamp is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class MqttSource(DataSource):
    """Subscribe continuously and expose received samples through ``pull``."""

    kind = SourceKind.MQTT.value

    def __init__(self, spec: SourceSpec):
        super().__init__(spec)
        parsed = urlparse(spec.url)
        if parsed.scheme not in {"mqtt", "mqtts"} or not parsed.hostname:
            raise ValueError("MQTT 地址必须是有效的 mqtt:// 或 mqtts:// URL")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("MQTT 地址端口无效") from exc
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("MQTT 地址端口无效")
        if parsed.username or parsed.password or parsed.path not in {"", "/"} \
                or parsed.query or parsed.fragment:
            raise ValueError("MQTT 地址不能包含凭据、路径、查询参数或片段")
        hostname = parsed.hostname.lower()
        if hostname == "metadata.google.internal":
            raise ValueError("MQTT 地址不允许指向云元数据服务")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address and (address.is_link_local or address.is_unspecified or address.is_multicast):
            raise ValueError("MQTT 地址不允许使用链路本地、未指定或组播地址")
        if not spec.topic.strip():
            raise ValueError("MQTT topic 不能为空")
        credential_keys = set(spec.credentials)
        if credential_keys - {"username", "password"}:
            raise ValueError("MQTT 凭据必须使用用户名和密码格式")
        username = spec.credentials.get("username")
        password = spec.credentials.get("password")
        if (username is None) != (password is None) or (
            username is not None
            and (
                not username or not password
                or any(len(value) > 4096 or "\r" in value or "\n" in value
                       for value in (username, password))
            )
        ):
            raise ValueError("MQTT 用户名和密码必须同时有效填写")

        params = spec.params
        self._host = parsed.hostname
        self._port = port or (8883 if parsed.scheme == "mqtts" else 1883)
        self._tls_context = ssl.create_default_context() if parsed.scheme == "mqtts" else None
        self._timeout_s = float(params.get("timeout_s", 10))
        self._topic = spec.topic
        self._qos = int(params.get("qos", 0))
        self._timestamp_field = str(params.get("timestamp_field", "ts"))
        self._response_path = str(params.get("response_path", ""))
        self._field_map = dict(params.get("field_map", {}))
        self._samples: deque[tuple[datetime, Sample]] = deque(maxlen=_MAX_BUFFERED_SAMPLES)
        self._connected = asyncio.Event()
        self._consumer: asyncio.Task[None] | None = None
        self._closed = False
        self._payload_error = False

    def _client(self) -> aiomqtt.Client:
        credentials = self.spec.credentials
        username = credentials.get("username")
        password = credentials.get("password")
        return aiomqtt.Client(
            hostname=self._host,
            port=self._port,
            username=username,
            password=password,
            timeout=self._timeout_s,
            tls_context=self._tls_context,
            max_queued_incoming_messages=1000,
        )

    async def _consume(self) -> None:
        delay_s = 1.0
        while not self._closed:
            try:
                async with self._client() as client:
                    await client.subscribe(self._topic, qos=self._qos)
                    self._connected.set()
                    delay_s = 1.0
                    async for message in client.messages:
                        try:
                            sample = self._decode(message.payload)
                        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                            self._payload_error = True
                            continue
                        self._samples.append((datetime.fromisoformat(sample["ts"]), sample))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "MQTT connection or subscription failed for asset {asset}: {error}",
                    asset=self.asset_id,
                    error=type(exc).__name__,
                )
            finally:
                self._connected.clear()
            if not self._closed:
                await asyncio.sleep(delay_s)
                delay_s = min(delay_s * 2, 30.0)

    def _decode(self, payload: bytes) -> Sample:
        if not isinstance(payload, bytes) or len(payload) > _MAX_PAYLOAD_BYTES:
            raise ValueError("MQTT payload is invalid or too large")
        document = json.loads(payload)
        if not isinstance(document, dict):
            raise ValueError("MQTT payload must be a JSON object")
        timestamp = _timestamp(_value_at_path(document, self._timestamp_field))
        record = _value_at_path(document, self._response_path)
        if not isinstance(record, dict):
            raise ValueError("MQTT payload data must be a JSON object")
        timestamp_key = self._timestamp_field
        if self._response_path and timestamp_key.startswith(f"{self._response_path}."):
            timestamp_key = timestamp_key[len(self._response_path) + 1:]
        if "." in timestamp_key:
            timestamp_key = ""
        mapping = self._field_map or {
            key: key for key in record if key != timestamp_key
        }
        sample: Sample = {"ts": timestamp.isoformat()}
        for target, source in mapping.items():
            sample[target] = _value_at_path(record, source)
        return sample

    async def pull(self, window_start: datetime, window_end: datetime) -> list[Sample]:
        if window_start.tzinfo is None or window_end.tzinfo is None:
            raise ValueError("MQTT sample windows must include a timezone")
        if window_end <= window_start:
            raise ValueError("MQTT sample window end must be after its start")
        if self._closed:
            raise RuntimeError("MQTT source is closed")
        if self._consumer is None:
            self._consumer = asyncio.create_task(self._consume())
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=self._timeout_s)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("MQTT broker connection timed out") from exc

        if self._payload_error:
            self._payload_error = False
            raise ValueError("MQTT subscription received an invalid JSON telemetry message")

        start = window_start.astimezone(timezone.utc)
        end = window_end.astimezone(timezone.utc)
        selected: list[tuple[datetime, Sample]] = []
        retained: deque[tuple[datetime, Sample]] = deque(maxlen=_MAX_BUFFERED_SAMPLES)
        for timestamp, sample in self._samples:
            if timestamp < start:
                continue
            if timestamp < end:
                selected.append((timestamp, sample))
            else:
                retained.append((timestamp, sample))
        self._samples = retained
        selected.sort(key=lambda item: item[0])
        return [sample for _, sample in selected]

    async def aclose(self) -> None:
        self._closed = True
        self._connected.clear()
        if self._consumer is not None:
            self._consumer.cancel()
            await asyncio.gather(self._consumer, return_exceptions=True)
            self._consumer = None
