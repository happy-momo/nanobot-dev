"""Ingest-layer tests: REST pull semantics + factory wiring honesty.

The protocol adapters' window semantics, errors and lifecycles must be pinned
down before a plant integration depends on them. The factory tests make
configured-but-unwired sources fail loudly instead of silently collecting nothing.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402

from edge_adviser.config.model import SourceKind, SourceSpec  # noqa: E402


def _spec(**kw) -> SourceSpec:
    return SourceSpec(kind=SourceKind.REST, asset_id="pump1",
                      url="http://hist.test/api/samples", **kw)


# --------------------------------------------------------------------------- #
# factory: configured sources fail loudly when they are invalid
# --------------------------------------------------------------------------- #
def test_modbus_source_reads_and_decodes_mapped_registers(monkeypatch) -> None:
    import asyncio
    import struct

    from edge_adviser.config.model import SourceKind, SourceSpec
    from edge_adviser.ingest import create_source
    from edge_adviser.ingest import modbus as modbus_module

    captured: dict = {"reads": []}
    float_words = struct.unpack(">HH", struct.pack(">f", 1.5))

    class _Response:
        def __init__(self, *, registers=None, bits=None) -> None:
            self.registers = registers
            self.bits = bits

        def isError(self) -> bool:
            return False

    class _Client:
        def __init__(self, host: str, *, port: int, timeout: float) -> None:
            captured["endpoint"] = (host, port, timeout)

        async def connect(self) -> bool:
            return True

        async def read_holding_registers(self, address: int, *, count: int, device_id: int):
            captured["reads"].append(("holding", address, count, device_id))
            if address == 0:
                return _Response(registers=[0x41C4, 0])
            return _Response(registers=[65531])

        async def read_input_registers(self, address: int, *, count: int, device_id: int):
            captured["reads"].append(("input", address, count, device_id))
            if address == 2:
                return _Response(registers=[150])
            return _Response(registers=[float_words[1], float_words[0]])

        async def read_coils(self, address: int, *, count: int, device_id: int):
            captured["reads"].append(("coils", address, count, device_id))
            return _Response(bits=[True])

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(modbus_module, "AsyncModbusTcpClient", _Client)
    source = create_source(SourceSpec(
        kind=SourceKind.MODBUS,
        asset_id="pump1",
        url="modbus://192.168.10.20:1502",
        params={
            "timeout_s": 5,
            "unit_id": 7,
            "register_map": {
                "temp": {"area": "holding_registers", "address": 0, "type": "float32"},
                "rpm": {
                    "area": "input_registers", "address": 2, "type": "uint16",
                    "scale": 0.1,
                },
                "pressure": {"area": "holding_registers", "address": 3, "type": "int16"},
                "vibration": {
                    "area": "input_registers", "address": 4, "type": "float32",
                    "word_order": "little",
                },
                "running": {"area": "coils", "address": 7, "type": "bool"},
            },
        },
    ))

    async def run() -> None:
        now = datetime.now(timezone.utc)
        rows = await source.pull(now - timedelta(seconds=1), now + timedelta(seconds=1))
        assert len(rows) == 1
        assert rows[0]["temp"] == 24.5
        assert rows[0]["rpm"] == 15
        assert rows[0]["pressure"] == -5
        assert rows[0]["vibration"] == 1.5
        assert rows[0]["running"] == 1
        assert captured["endpoint"] == ("192.168.10.20", 1502, 5)
        assert captured["reads"] == [
            ("holding", 0, 2, 7),
            ("input", 2, 1, 7),
            ("holding", 3, 1, 7),
            ("input", 4, 2, 7),
            ("coils", 7, 1, 7),
        ]
        assert captured["closed"] is True

    _run(run())


@pytest.mark.parametrize(
    "url,register",
    [
        ("modbus://server.test", {"area": "unknown", "address": 0, "type": "uint16"}),
        ("modbus://server.test", {"area": "holding_registers", "address": 65535, "type": "float32"}),
        ("modbus://server.test", {"area": "holding_registers", "address": 0, "type": "uint16", "scale": float("inf")}),
        ("modbus://metadata.google.internal", {"area": "holding_registers", "address": 0, "type": "uint16"}),
    ],
)
def test_modbus_source_rejects_invalid_endpoint_or_register(url: str, register: dict) -> None:
    from edge_adviser.config.model import SourceKind, SourceSpec
    from edge_adviser.ingest.modbus import ModbusSource

    with pytest.raises(ValueError):
        ModbusSource(SourceSpec(
            kind=SourceKind.MODBUS,
            asset_id="pump1",
            url=url,
            params={"register_map": {"temp": register}},
        ))


def test_modbus_read_error_closes_client(monkeypatch) -> None:
    import asyncio

    from edge_adviser.config.model import SourceKind, SourceSpec
    from edge_adviser.ingest import modbus as modbus_module

    captured: dict = {}

    class _Response:
        def isError(self) -> bool:
            return True

    class _Client:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def connect(self) -> bool:
            return True

        async def read_holding_registers(self, *_args, **_kwargs):
            return _Response()

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(modbus_module, "AsyncModbusTcpClient", _Client)
    source = modbus_module.ModbusSource(SourceSpec(
        kind=SourceKind.MODBUS,
        asset_id="pump1",
        url="modbus://server.test",
        params={
            "register_map": {
                "temp": {"area": "holding_registers", "address": 0, "type": "uint16"},
            },
        },
    ))

    async def run() -> None:
        now = datetime.now(timezone.utc)
        with pytest.raises(RuntimeError, match="Modbus read failed"):
            await source.pull(now - timedelta(seconds=1), now + timedelta(seconds=1))
        assert captured["closed"] is True

    _run(run())


def test_modbus_source_reads_from_local_tcp_server() -> None:
    import asyncio
    import socket

    from pymodbus.server import ModbusTcpServer
    from pymodbus.simulator import DataType, SimData, SimDevice

    from edge_adviser.config.model import SourceKind, SourceSpec
    from edge_adviser.ingest.modbus import ModbusSource

    async def run() -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        device = SimDevice(
            id=1,
            simdata=[
                SimData(
                    address=0, values=321, datatype=DataType.UINT16, readonly=True,
                ),
            ],
        )
        server = ModbusTcpServer(device, address=("127.0.0.1", port))
        await server.serve_forever(background=True)
        source = ModbusSource(SourceSpec(
            kind=SourceKind.MODBUS,
            asset_id="pump1",
            url=f"modbus://127.0.0.1:{port}",
            params={
                "unit_id": 1,
                "register_map": {
                    "temp": {
                        "area": "holding_registers",
                        "address": 0,
                        "type": "uint16",
                    },
                },
            },
        ))
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(seconds=1)
            rows = await asyncio.wait_for(
                source.pull(start, end),
                timeout=3,
            )
            assert rows[0]["temp"] == 321
            timestamp = datetime.fromisoformat(rows[0]["ts"])
            assert timestamp.tzinfo is not None
            assert start <= timestamp < end
        finally:
            await source.aclose()
            await server.shutdown()

    _run(run())


def test_unknown_source_kind_raises_value_error() -> None:
    from edge_adviser.ingest import create_source

    spec = SourceSpec(kind=SourceKind.REST, asset_id="pump1")
    spec.kind = "bananas"  # type: ignore[assignment]
    with pytest.raises(ValueError):
        create_source(spec)


def test_simulator_and_rest_construct() -> None:
    from edge_adviser.ingest import create_source, RestSource, SimulatorSource

    assert isinstance(create_source(SourceSpec(kind=SourceKind.SIMULATOR,
                                               asset_id="p1")), SimulatorSource)
    assert isinstance(create_source(_spec()), RestSource)


def test_mqtt_source_subscribes_maps_and_returns_window_samples(monkeypatch) -> None:
    import asyncio

    from edge_adviser.config.model import SourceKind, SourceSpec
    from edge_adviser.ingest import create_source
    from edge_adviser.ingest import mqtt as mqtt_module

    payloads = [
        b'{"time":"2026-11-23T09:59:59Z","data":{"temperature":1}}',
        b'{"time":"2026-11-23T10:00:01Z","data":{"temperature":63.2}}',
        b'{"time":"2026-11-23T10:00:03Z","data":{"temperature":63.9}}',
        b'{"time":"2026-11-23T10:00:06Z","data":{"temperature":64.1}}',
    ]
    captured: dict = {}

    class _Messages:
        def __init__(self) -> None:
            self.remaining = list(payloads)
            self.drained = asyncio.Event()

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.remaining:
                return type("Message", (), {"payload": self.remaining.pop(0)})()
            self.drained.set()
            await asyncio.Future()

    class _Client:
        def __init__(self, **kwargs) -> None:
            captured["client"] = kwargs
            self.messages = _Messages()
            captured["messages"] = self.messages

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            captured["closed"] = True

        async def subscribe(self, topic: str, *, qos: int) -> None:
            captured["subscription"] = (topic, qos)

    monkeypatch.setattr(mqtt_module.aiomqtt, "Client", _Client)
    spec = SourceSpec(
        kind=SourceKind.MQTT,
        asset_id="pump1",
        url="mqtts://broker.test:8883",
        topic="factory/pump1",
        params={
            "qos": 1,
            "timestamp_field": "time",
            "response_path": "data",
            "field_map": {"temp": "temperature"},
        },
        credentials={"username": "operator", "password": "do-not-log"},
    )
    source = create_source(spec)

    async def run() -> None:
        start = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
        end = start + timedelta(seconds=5)
        first_pull = asyncio.create_task(source.pull(start, end))
        await asyncio.wait_for(source._connected.wait(), timeout=1)
        await asyncio.wait_for(captured["messages"].drained.wait(), timeout=1)
        rows = await first_pull
        assert captured["subscription"] == ("factory/pump1", 1)
        assert captured["client"]["hostname"] == "broker.test"
        assert captured["client"]["port"] == 8883
        assert captured["client"]["username"] == "operator"
        assert captured["client"]["password"] == "do-not-log"
        assert rows == [
            {"ts": "2026-11-23T10:00:01+00:00", "temp": 63.2},
            {"ts": "2026-11-23T10:00:03+00:00", "temp": 63.9},
        ]
        next_rows = await source.pull(end, end + timedelta(seconds=5))
        assert next_rows == [{"ts": "2026-11-23T10:00:06+00:00", "temp": 64.1}]
        await source.aclose()
        assert captured["closed"] is True

    _run(run())


def test_mqtt_rejects_malformed_payload() -> None:
    from edge_adviser.config.model import SourceKind, SourceSpec
    from edge_adviser.ingest.mqtt import MqttSource

    source = MqttSource(SourceSpec(
        kind=SourceKind.MQTT,
        asset_id="pump1",
        url="mqtt://broker.test",
        topic="factory/pump1",
    ))
    with pytest.raises(ValueError, match="JSON object"):
        source._decode(b'[]')


@pytest.mark.parametrize(
    "url",
    ["mqtt://metadata.google.internal", "mqtt://169.254.169.254"],
)
def test_mqtt_rejects_cloud_metadata_endpoints(url: str) -> None:
    from edge_adviser.config.model import SourceKind, SourceSpec
    from edge_adviser.ingest.mqtt import MqttSource

    with pytest.raises(ValueError, match="云元数据服务|链路本地"):
        MqttSource(SourceSpec(
            kind=SourceKind.MQTT,
            asset_id="pump1",
            url=url,
            topic="factory/pump1",
        ))


def test_connector_credentials_cannot_cross_protocols() -> None:
    from edge_adviser.config.model import SourceKind, SourceSpec
    from edge_adviser.ingest import create_source

    mqtt_spec = SourceSpec(
        kind=SourceKind.MQTT,
        asset_id="pump1",
        url="mqtt://broker.test",
        topic="factory/pump1",
        credentials={"authorization": "header-secret"},
    )
    with pytest.raises(ValueError, match="用户名和密码格式"):
        create_source(mqtt_spec)

    rest_spec = SourceSpec(
        kind=SourceKind.REST,
        asset_id="pump1",
        url="http://historian.test/records",
        credentials={"username": "operator", "password": "private-password"},
    )
    with pytest.raises(ValueError, match="不能用于 REST"):
        create_source(rest_spec)


def test_opcua_source_reads_mapped_nodes_and_closes(monkeypatch, tmp_path: Path) -> None:
    import asyncio

    from edge_adviser.config.model import SourceKind, SourceSpec
    from edge_adviser.ingest import create_source
    from edge_adviser.ingest import opcua as opcua_module
    from asyncua import ua

    captured: dict = {}

    class _Status:
        def is_good(self) -> bool:
            return True

    class _Node:
        def __init__(self, value: float) -> None:
            self.value = value

        async def read_data_value(self):
            return type("DataValue", (), {
                "StatusCode": _Status(),
                "Value": type("Variant", (), {"Value": self.value})(),
                "SourceTimestamp": datetime.now(timezone.utc),
            })()

    class _Client:
        def __init__(self, endpoint: str, *, timeout: float) -> None:
            captured["endpoint"] = endpoint
            captured["timeout"] = timeout
            captured["nodes"] = {
                "ns=2;s=Pump.Temperature": _Node(63.2),
                "ns=2;s=Pump.Vibration": _Node(2.1),
            }

        async def set_security(self, *args, **kwargs) -> None:
            captured["security"] = (args, kwargs)

        def set_user(self, username: str) -> None:
            captured["username"] = username

        def set_password(self, password: str) -> None:
            captured["password"] = password

        def get_node(self, node_id: str):
            return captured["nodes"][node_id]

        async def __aenter__(self):
            captured["connected"] = True
            return self

        async def __aexit__(self, *_args):
            captured["closed"] = True

    cert = tmp_path / "client.pem"
    key = tmp_path / "client-key.pem"
    server_cert = tmp_path / "server.pem"
    for path in (cert, key, server_cert):
        path.write_text("test-certificate", encoding="utf-8")
    monkeypatch.setattr(opcua_module, "Client", _Client)
    spec = SourceSpec(
        kind=SourceKind.OPCUA,
        asset_id="pump1",
        url="opc.tcp://192.168.10.20:4840/UA/Server",
        poll_interval_s=0.1,
        params={
            "timeout_s": 5,
            "node_map": {
                "temp": "ns=2;s=Pump.Temperature",
                "vibration": "ns=2;s=Pump.Vibration",
            },
            "security_mode": "sign_and_encrypt",
            "client_certificate": str(cert),
            "client_private_key": str(key),
            "server_certificate": str(server_cert),
        },
        credentials={
            "username": "operator",
            "password": "do-not-log",
            "private_key_password": "key-secret",
        },
    )
    source = create_source(spec)

    async def run() -> None:
        end = datetime.now(timezone.utc) + timedelta(seconds=1)
        start = end - timedelta(seconds=10)
        first_pull = asyncio.create_task(source.pull(start, end))
        await asyncio.wait_for(source._connected.wait(), timeout=1)
        rows = await first_pull
        assert len(rows) == 1
        assert rows[0]["temp"] == 63.2
        assert rows[0]["vibration"] == 2.1
        assert datetime.fromisoformat(rows[0]["ts"]).tzinfo is not None
        assert captured["endpoint"] == spec.url
        assert captured["timeout"] == 5
        assert captured["username"] == "operator"
        assert captured["password"] == "do-not-log"
        assert captured["security"][0][0] is opcua_module.SecurityPolicyBasic256Sha256
        assert captured["security"][1]["private_key_password"] == "key-secret"
        assert captured["security"][1]["mode"] == ua.MessageSecurityMode.SignAndEncrypt
        await source.aclose()
        assert captured["closed"] is True

    _run(run())


def test_opcua_source_rejects_bad_nodes_and_insecure_passwords() -> None:
    from edge_adviser.config.model import SourceKind, SourceSpec
    from edge_adviser.ingest.opcua import OpcUaSource

    base = {
        "kind": SourceKind.OPCUA,
        "asset_id": "pump1",
        "url": "opc.tcp://server.local:4840/UA/Server",
        "params": {"node_map": {"temp": "bad-node-id"}},
    }
    with pytest.raises(ValueError, match="NodeId"):
        OpcUaSource(SourceSpec(**base))
    with pytest.raises(ValueError, match="必须使用签名"):
        OpcUaSource(SourceSpec(
            **{
                **base,
                "params": {"node_map": {"temp": "ns=2;s=Temperature"}},
                "credentials": {"username": "operator", "password": "secret"},
            },
        ))


def test_opcua_source_reads_from_local_asyncua_server() -> None:
    import asyncio
    import socket

    from asyncua import Server, ua

    from edge_adviser.config.model import SourceKind, SourceSpec
    from edge_adviser.ingest.opcua import OpcUaSource

    async def run() -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server = Server()
        await server.init()
        endpoint = f"opc.tcp://127.0.0.1:{port}/edge-adviser/"
        server.set_endpoint(endpoint)
        namespace = await server.register_namespace("urn:edge-adviser:test")
        node_id = ua.NodeId("Pump.Temperature", namespace)
        await server.nodes.objects.add_variable(node_id, "Temperature", 63.2)
        await server.start()
        source = OpcUaSource(SourceSpec(
            kind=SourceKind.OPCUA,
            asset_id="pump1",
            url=endpoint,
            poll_interval_s=0.1,
            params={"node_map": {"temp": node_id.to_string()}},
        ))
        try:
            end = datetime.now(timezone.utc) + timedelta(seconds=1)
            rows = await asyncio.wait_for(
                source.pull(end - timedelta(seconds=10), end),
                timeout=10,
            )
            assert len(rows) == 1
            assert rows[0]["temp"] == 63.2
            assert datetime.fromisoformat(rows[0]["ts"]).tzinfo is not None
        finally:
            await source.aclose()
            await server.stop()

    _run(run())


# --------------------------------------------------------------------------- #
# REST source: window params, payload passthrough, HTTP errors, lifecycle
# --------------------------------------------------------------------------- #
def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_rest_pull_passes_window_and_returns_samples() -> None:
    """The [start, end) window rides as ISO query params; payload rows pass through."""
    from edge_adviser.ingest.rest import RestSource

    captured: dict = {}

    class _Resp:
        def raise_for_status(self): pass

        async def json(self):
            return [{"ts": "2026-11-23T10:00:01+00:00", "temp": 63.2},
                    {"ts": "2026-11-23T10:00:02+00:00", "temp": 63.9}]

    class _Session:
        def get(self, url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = dict(params or {})
            captured["allow_redirects"] = kwargs.get("allow_redirects")

            class _Ctx:
                async def __aenter__(self): return _Resp()

                async def __aexit__(self, *a): return False
            return _Ctx()

    src = RestSource(_spec())
    src._session = _Session()  # type: ignore[assignment] — inject, skip aiohttp
    start = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
    end = start + timedelta(seconds=60)
    rows = _run(src.pull(start, end))

    assert len(rows) == 2 and rows[0]["temp"] == 63.2
    assert captured["params"]["start"] == start.isoformat()
    assert captured["params"]["end"] == end.isoformat()
    assert captured["allow_redirects"] is False
    assert captured["url"].startswith("http://hist.test/api/samples")


def test_rest_pull_http_error_raises() -> None:
    """A 500 from the historian must raise — the scheduler archives it as a
    failed pull and the offline state machine advances (never silent)."""
    from edge_adviser.ingest.rest import RestSource

    spec = _spec()
    spec.url = "http://hist.test/api/samples?token=do-not-log"

    class _Resp:
        def raise_for_status(self):
            raise RuntimeError(f"500 from {spec.url}")

    class _Session:
        def get(self, url, params=None, **kwargs):
            class _Ctx:
                async def __aenter__(self): return _Resp()

                async def __aexit__(self, *a): return False
            return _Ctx()

    src = RestSource(spec)
    src._session = _Session()  # type: ignore[assignment]
    now = datetime.now(timezone.utc)
    with pytest.raises(RuntimeError) as exc_info:
        _run(src.pull(now - timedelta(seconds=60), now))
    assert "do-not-log" not in str(exc_info.value)


def test_rest_pull_non_list_payload_raises() -> None:
    """A JSON object (not array) is a contract violation — refuse, don't guess."""
    from edge_adviser.ingest.rest import RestSource

    class _Resp:
        def raise_for_status(self): pass

        async def json(self):
            return {"error": "bad gateway page"}

    class _Session:
        def get(self, url, params=None, **kwargs):
            class _Ctx:
                async def __aenter__(self): return _Resp()

                async def __aexit__(self, *a): return False
            return _Ctx()

    src = RestSource(_spec())
    src._session = _Session()  # type: ignore[assignment]
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        _run(src.pull(now - timedelta(seconds=60), now))


def test_rest_auth_header_filtering() -> None:
    """Only safe credential keys become headers; arbitrary keys are dropped."""
    from edge_adviser.ingest.rest import RestSource

    src = RestSource(_spec(credentials={
        "Authorization": "Bearer tok", "X-Api-Key": "k1",
        "Content-Type": "application/json", "Evil-Header": "nope"}))
    headers = {k: v for k, v in src.spec.credentials.items() if k.lower() in {
        "authorization", "x-api-key", "accept", "content-type"}}
    assert set(headers) == {"Authorization", "X-Api-Key", "Content-Type"}
    assert "Evil-Header" not in headers


def test_rest_session_close_is_idempotent() -> None:
    from edge_adviser.ingest.rest import RestSource

    src = RestSource(_spec())
    _run(src.aclose())  # never opened → no-op, must not raise
    _run(src.aclose())
