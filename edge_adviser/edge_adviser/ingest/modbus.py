"""Modbus TCP client for polling configured device registers."""

from __future__ import annotations

import ipaddress
import math
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from pymodbus.client import AsyncModbusTcpClient

from ..config.model import SourceKind, SourceSpec
from .base import DataSource, Sample

_READ_AREAS = {"coils", "discrete_inputs", "holding_registers", "input_registers"}
_REGISTER_TYPES = {"uint16", "int16", "uint32", "int32", "float32"}
_METADATA_HOSTS = {"metadata.google.internal"}


@dataclass(frozen=True)
class _Register:
    area: str
    address: int
    data_type: str
    scale: float
    offset: float
    word_order: str

    @property
    def count(self) -> int:
        return 2 if self.data_type in {"uint32", "int32", "float32"} else 1


def _finite_number(value: object, name: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Modbus {name} 必须是有限数字")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"Modbus {name} 必须是有限数字") from exc
    if not math.isfinite(number):
        raise ValueError(f"Modbus {name} 必须是有限数字")
    return number


def _register(metric: str, raw: object) -> _Register:
    if not isinstance(raw, dict):
        raise ValueError(f"Modbus 指标 {metric} 的寄存器配置必须是对象")
    if set(raw) - {"area", "address", "type", "scale", "offset", "word_order"}:
        raise ValueError(f"Modbus 指标 {metric} 包含不支持的寄存器参数")

    area = raw.get("area")
    address = raw.get("address")
    data_type = raw.get("type")
    if not isinstance(area, str) or area not in _READ_AREAS:
        raise ValueError(f"Modbus 指标 {metric} 的功能区无效")
    if isinstance(address, bool) or not isinstance(address, int) or not 0 <= address <= 65535:
        raise ValueError(f"Modbus 指标 {metric} 的地址须在 0 到 65535 之间")
    if not isinstance(data_type, str):
        raise ValueError(f"Modbus 指标 {metric} 的数据类型无效")
    if area in {"coils", "discrete_inputs"}:
        if data_type != "bool":
            raise ValueError(f"Modbus 指标 {metric} 的线圈数据类型必须是 bool")
    elif data_type not in _REGISTER_TYPES:
        raise ValueError(f"Modbus 指标 {metric} 的寄存器数据类型无效")

    scale = _finite_number(raw.get("scale"), "缩放系数", 1.0)
    offset = _finite_number(raw.get("offset"), "偏移量", 0.0)
    word_order = raw.get("word_order", "big")
    if not isinstance(word_order, str) or word_order not in {"big", "little"}:
        raise ValueError(f"Modbus 指标 {metric} 的字序必须是 big 或 little")
    count = 2 if data_type in {"uint32", "int32", "float32"} else 1
    if address + count - 1 > 65535:
        raise ValueError(f"Modbus 指标 {metric} 的地址范围超出寄存器空间")
    if count == 1 and word_order != "big":
        raise ValueError(f"Modbus 指标 {metric} 的单寄存器数据不能设置字序")
    return _Register(area, address, data_type, scale, offset, word_order)


class ModbusSource(DataSource):
    """Read one mapped Modbus TCP sample for each scheduler poll."""

    kind = SourceKind.MODBUS.value

    def __init__(self, spec: SourceSpec):
        super().__init__(spec)
        parsed = urlparse(spec.url)
        if len(spec.url) > 2048 or parsed.scheme != "modbus" or not parsed.hostname:
            raise ValueError("Modbus 地址必须是有效的 modbus:// URL")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Modbus 地址端口无效") from exc
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Modbus 地址端口无效")
        if parsed.username or parsed.password or parsed.path not in {"", "/"} \
                or parsed.query or parsed.fragment:
            raise ValueError("Modbus 地址不能包含凭据、路径、查询参数或片段")
        hostname = parsed.hostname.lower()
        if hostname in _METADATA_HOSTS:
            raise ValueError("Modbus 地址不允许指向云元数据服务")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address and (
            address.is_link_local or address.is_unspecified or address.is_multicast
        ):
            raise ValueError("Modbus 地址不允许使用链路本地、未指定或组播地址")
        if spec.credentials or spec.credential_id:
            raise ValueError("Modbus TCP 不支持连接凭据")

        params = spec.params
        if set(params) - {"timeout_s", "unit_id", "register_map"}:
            raise ValueError("Modbus 配置包含不支持的参数")
        timeout_s = _finite_number(params.get("timeout_s"), "连接超时", 10)
        if not 1 <= timeout_s <= 30:
            raise ValueError("连接超时须在 1 到 30 秒之间")
        unit_id = params.get("unit_id", 1)
        if isinstance(unit_id, bool) or not isinstance(unit_id, int) or not 0 <= unit_id <= 255:
            raise ValueError("Modbus Unit ID 须在 0 到 255 之间")

        raw_map = params.get("register_map")
        if not isinstance(raw_map, dict) or not raw_map:
            raise ValueError("Modbus 寄存器映射不能为空")
        if any(not isinstance(metric, str) or not metric.strip() for metric in raw_map):
            raise ValueError("Modbus 寄存器映射的指标键无效")
        self._registers = {
            metric: _register(metric, raw)
            for metric, raw in raw_map.items()
        }
        self._host = parsed.hostname
        self._port = port or 502
        self._timeout_s = timeout_s
        self._unit_id = unit_id

    async def _read_value(
        self, client: AsyncModbusTcpClient, metric: str, register: _Register,
    ) -> float:
        if register.area == "coils":
            response = await client.read_coils(
                register.address, count=1, device_id=self._unit_id,
            )
        elif register.area == "discrete_inputs":
            response = await client.read_discrete_inputs(
                register.address, count=1, device_id=self._unit_id,
            )
        elif register.area == "holding_registers":
            response = await client.read_holding_registers(
                register.address, count=register.count, device_id=self._unit_id,
            )
        else:
            response = await client.read_input_registers(
                register.address, count=register.count, device_id=self._unit_id,
            )

        if response.isError():
            raise RuntimeError(f"Modbus read failed for metric: {metric}")
        if register.data_type == "bool":
            bits = getattr(response, "bits", None)
            if not isinstance(bits, list) or not bits:
                raise RuntimeError(f"Modbus response is incomplete for metric: {metric}")
            value = 1.0 if bits[0] else 0.0
        else:
            words = getattr(response, "registers", None)
            if not isinstance(words, list) or len(words) != register.count:
                raise RuntimeError(f"Modbus response is incomplete for metric: {metric}")
            if register.word_order == "little":
                words = list(reversed(words))
            raw_value = 0
            for word in words:
                if isinstance(word, bool) or not isinstance(word, int) or not 0 <= word <= 65535:
                    raise RuntimeError(f"Modbus response contains an invalid register: {metric}")
                raw_value = (raw_value << 16) | word
            if register.data_type == "uint16" or register.data_type == "uint32":
                value = float(raw_value)
            elif register.data_type == "int16":
                value = float(raw_value - 65536 if raw_value & 0x8000 else raw_value)
            elif register.data_type == "int32":
                value = float(raw_value - (1 << 32) if raw_value & 0x80000000 else raw_value)
            else:
                value = struct.unpack(">f", raw_value.to_bytes(4, "big"))[0]
        scaled = value * register.scale + register.offset
        if not math.isfinite(scaled):
            raise ValueError(f"Modbus value is not finite for metric: {metric}")
        return scaled

    async def pull(self, window_start: datetime, window_end: datetime) -> list[Sample]:
        if window_start.tzinfo is None or window_end.tzinfo is None:
            raise ValueError("Modbus sample windows must include a timezone")
        if window_end <= window_start:
            raise ValueError("Modbus sample window end must be after its start")

        client = AsyncModbusTcpClient(
            self._host, port=self._port, timeout=self._timeout_s,
        )
        try:
            if not await client.connect():
                raise ConnectionError("Modbus TCP connection failed")
            values = {
                metric: await self._read_value(client, metric, register)
                for metric, register in self._registers.items()
            }
        finally:
            client.close()

        start = window_start.astimezone(timezone.utc)
        end = window_end.astimezone(timezone.utc)
        # Scheduler windows close before the live register read begins.
        timestamp = min(datetime.now(timezone.utc), end - timedelta(microseconds=1))
        if timestamp < start:
            return []
        return [{"ts": timestamp.isoformat(), **values}]
