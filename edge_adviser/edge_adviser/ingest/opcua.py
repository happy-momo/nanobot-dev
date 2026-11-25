"""OPC UA client with periodic reads and a bounded time-window buffer."""

from __future__ import annotations

import asyncio
import ipaddress
import math
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from asyncua import Client, ua
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
from asyncua.ua.uaerrors import UaStringParsingError
from loguru import logger

from ..config.model import SourceKind, SourceSpec
from .base import DataSource, Sample

_MAX_BUFFERED_SAMPLES = 10_000
_SECURITY_MODES = {
    "sign": ua.MessageSecurityMode.Sign,
    "sign_and_encrypt": ua.MessageSecurityMode.SignAndEncrypt,
}


class OpcUaSource(DataSource):
    """Poll configured OPC UA nodes at the asset sampling interval."""

    kind = SourceKind.OPCUA.value

    def __init__(self, spec: SourceSpec):
        super().__init__(spec)
        parsed = urlparse(spec.url)
        if parsed.scheme != "opc.tcp" or not parsed.hostname:
            raise ValueError("OPC UA 地址必须是有效的 opc.tcp:// URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("OPC UA 地址不能包含凭据、查询参数或片段")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("OPC UA 地址端口无效")
        if parsed.hostname.lower() == "metadata.google.internal":
            raise ValueError("OPC UA 地址不允许指向云元数据服务")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address and (
            address.is_link_local or address.is_unspecified or address.is_multicast
        ):
            raise ValueError("OPC UA 地址不允许使用链路本地、未指定或组播地址")

        params = spec.params
        node_map = params.get("node_map", {})
        if not isinstance(node_map, dict) or not node_map:
            raise ValueError("OPC UA 节点映射不能为空")
        if any(
            not isinstance(metric, str)
            or not isinstance(node_id, str)
            or not node_id.strip()
            for metric, node_id in node_map.items()
        ):
            raise ValueError("OPC UA 节点映射必须是指标键到 NodeId 的字符串映射")
        self._nodes = {metric: node_id.strip() for metric, node_id in node_map.items()}
        try:
            for node_id in self._nodes.values():
                ua.NodeId.from_string(node_id)
        except (ValueError, UaStringParsingError) as exc:
            raise ValueError("OPC UA NodeId 格式无效") from exc

        self._poll_interval_s = spec.poll_interval_s
        if not math.isfinite(self._poll_interval_s) or not 0.1 <= self._poll_interval_s <= 3600:
            raise ValueError("OPC UA 采集间隔须在 0.1 到 3600 秒之间")
        self._timeout_s = float(params.get("timeout_s", 10))
        if not math.isfinite(self._timeout_s) or not 1 <= self._timeout_s <= 30:
            raise ValueError("OPC UA 连接超时须在 1 到 30 秒之间")

        self._security_mode = str(params.get("security_mode", "none"))
        if self._security_mode not in {"none", *_SECURITY_MODES}:
            raise ValueError("OPC UA 安全模式无效")
        self._certificate = str(params.get("client_certificate", ""))
        self._private_key = str(params.get("client_private_key", ""))
        self._server_certificate = str(params.get("server_certificate", ""))
        if self._security_mode == "none":
            if any((self._certificate, self._private_key, self._server_certificate)) \
                    or spec.credentials.get("private_key_password"):
                raise ValueError("OPC UA 无安全模式不能配置证书路径")
        else:
            if not all((self._certificate, self._private_key, self._server_certificate)):
                raise ValueError("OPC UA 安全连接需要客户端证书、私钥和可信服务器证书")
            for path in (self._certificate, self._private_key, self._server_certificate):
                if not Path(path).is_file():
                    raise ValueError("OPC UA 证书路径必须指向可读取的现有文件")
            try:
                for path in (self._certificate, self._private_key, self._server_certificate):
                    with Path(path).open("rb") as file:
                        if not file.read(1):
                            raise ValueError("OPC UA 证书和私钥文件不能为空")
            except ValueError:
                raise
            except OSError as exc:
                raise ValueError("OPC UA 客户端私钥不可读取") from exc

        credentials = spec.credentials
        if set(credentials) - {"username", "password", "private_key_password"}:
            raise ValueError("OPC UA 凭据类型无效")
        username = credentials.get("username")
        password = credentials.get("password")
        if (username is None) != (password is None) or (
            username is not None
            and (
                not username
                or not password
                or any(
                    len(value) > 4096 or "\r" in value or "\n" in value
                    for value in (username, password)
                )
            )
        ):
            raise ValueError("OPC UA 用户名和密码必须同时有效填写")
        if username and self._security_mode == "none":
            raise ValueError("OPC UA 用户名密码认证必须使用签名或签名加密安全连接")

        self._endpoint = spec.url
        self._username = username
        self._password = password
        self._samples: deque[tuple[datetime, Sample]] = deque(
            maxlen=_MAX_BUFFERED_SAMPLES,
        )
        self._connected = asyncio.Event()
        self._consumer: asyncio.Task[None] | None = None
        self._closed = False

    async def _connect(self) -> Client:
        client = Client(self._endpoint, timeout=self._timeout_s)
        if self._security_mode != "none":
            await client.set_security(
                SecurityPolicyBasic256Sha256,
                self._certificate,
                self._private_key,
                private_key_password=self.spec.credentials.get("private_key_password"),
                server_certificate=self._server_certificate,
                mode=_SECURITY_MODES[self._security_mode],
            )
        if self._username and self._password:
            client.set_user(self._username)
            client.set_password(self._password)
        return client

    async def _read_sample(self, client: Client) -> Sample:
        values: dict[str, Any] = {}
        timestamps: list[datetime] = []
        for metric, node_id in self._nodes.items():
            data_value = await client.get_node(node_id).read_data_value()
            if not data_value.StatusCode.is_good():
                raise RuntimeError(f"OPC UA node read failed: {metric}")
            value = data_value.Value.Value
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"OPC UA node value is not a finite number: {metric}")
            if data_value.SourceTimestamp is None:
                raise ValueError(f"OPC UA node has no source timestamp: {metric}")
            timestamp = data_value.SourceTimestamp
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            timestamps.append(timestamp.astimezone(timezone.utc))
            values[metric] = value
        sample_timestamp = max(timestamps)
        return {"ts": sample_timestamp.isoformat(), **values}

    async def _consume(self) -> None:
        delay_s = 1.0
        while not self._closed:
            try:
                client = await self._connect()
                async with client:
                    while not self._closed:
                        sample = await self._read_sample(client)
                        self._samples.append((
                            datetime.fromisoformat(sample["ts"]),
                            sample,
                        ))
                        self._connected.set()
                        delay_s = 1.0
                        await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "OPC UA connection or node read failed for asset {asset}: {error}",
                    asset=self.asset_id,
                    error=type(exc).__name__,
                )
            finally:
                self._connected.clear()
            if not self._closed:
                await asyncio.sleep(delay_s)
                delay_s = min(delay_s * 2, 30.0)

    async def pull(self, window_start: datetime, window_end: datetime) -> list[Sample]:
        if window_start.tzinfo is None or window_end.tzinfo is None:
            raise ValueError("OPC UA sample windows must include a timezone")
        if window_end <= window_start:
            raise ValueError("OPC UA sample window end must be after its start")
        if self._closed:
            raise RuntimeError("OPC UA source is closed")
        if self._consumer is None:
            self._consumer = asyncio.create_task(self._consume())
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=self._timeout_s)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("OPC UA server connection timed out") from exc

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
        return [sample for _, sample in sorted(selected, key=lambda item: item[0])]

    async def aclose(self) -> None:
        self._closed = True
        self._connected.clear()
        if self._consumer is not None:
            self._consumer.cancel()
            await asyncio.gather(self._consumer, return_exceptions=True)
            self._consumer = None
