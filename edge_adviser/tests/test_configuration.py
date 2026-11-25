from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from edge_adviser.core.credential_vault import CredentialVault
from edge_adviser.server.app import create_app


class StubGateway:
    async def astart(self) -> None:
        pass

    async def aclose(self) -> None:
        pass


def _client(tmp_path: Path, *, pin: str = "2468") -> tuple[TestClient, dict[str, str], Path]:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "workspace": str(tmp_path / "workspace"),
        "assets": [],
        "sources": [],
        "hmi_pin": pin,
        "data_freshness_s": 3600,
    }), encoding="utf-8")
    app = create_app(config_path, gateway=StubGateway())
    client = TestClient(app)
    if pin == "none":
        return client, {}, config_path
    response = client.post("/api/auth/pin", json={"pin": pin})
    assert response.status_code == 200
    token = response.json()["data"]["token"]
    return client, {"Authorization": f"Bearer {token}"}, config_path


def _rest_asset(*, name: str = "Pump", credential_id: str = "cred-1") -> dict[str, Any]:
    return {
        "asset_id": "pump-1",
        "name": name,
        "kind": "pump",
        "description": "test asset",
        "sample_freq_s": 1,
        "window_s": 10,
        "enabled": True,
        "metrics": [{"key": "temp", "unit": "C", "min_samples": 1}],
        "source": {
            "kind": "rest",
            "url": "http://127.0.0.1:8123/read",
            "poll_interval_s": 1,
            "credential_id": credential_id,
            "params": {
                "timeout_s": 2,
                "start_param": "from",
                "end_param": "to",
                "timestamp_field": "time",
                "response_path": "",
                "field_map": {"temp": "temperature"},
            },
        },
    }


def test_credential_vault_encrypts_and_restricts_file_permissions(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode("ascii")
    path = tmp_path / ".connector_credentials.enc"
    vault = CredentialVault(path, key)
    vault.put("cred-1", {"authorization": "Bearer highly-secret"})

    assert b"highly-secret" not in path.read_bytes()
    if os.name == "posix":  # chmod is a no-op on Windows (mode stays 0o666)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert CredentialVault(path, key).get("cred-1") == {
        "authorization": "Bearer highly-secret",
    }


def test_cli_source_resolves_backend_credentials(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from edge_adviser.cli import _make_source
    from edge_adviser.config.model import SourceKind, SourceSpec

    vault = CredentialVault(
        tmp_path / ".connector_credentials.enc",
        Fernet.generate_key().decode("ascii"),
    )
    vault.put("cred-1", {"authorization": "Bearer cli-secret"})
    spec = SourceSpec(
        kind=SourceKind.REST,
        asset_id="pump-1",
        url="http://localhost:8123/read",
        credential_id="cred-1",
    )
    context = SimpleNamespace(
        config=SimpleNamespace(sources=[spec]),
        credential_vault=vault,
    )

    source = _make_source(context, "pump-1")
    assert source.spec.credentials["authorization"] == "Bearer cli-secret"


def test_configuration_crud_backup_revision_and_secret_redaction(
    tmp_path: Path, monkeypatch,
) -> None:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("EDGE_ADVISER_CREDENTIAL_KEY", key)
    client, headers, config_path = _client(tmp_path)
    credentials = {"headers": {"Authorization": "Bearer highly-secret"}}
    response = client.put(
        "/api/connector-credentials/cred-1", json=credentials, headers=headers,
    )
    assert response.status_code == 200

    response = client.post("/api/assets", json=_rest_asset(), headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["asset_id"] == "pump-1"
    assert client.put(
        "/api/connector-credentials/cred-1",
        json={"headers": {"Authorization": "Bearer changed"}},
        headers=headers,
    ).status_code == 409

    configuration = client.get("/api/configuration", headers=headers).json()["data"]
    serialized = json.dumps(configuration)
    assert "highly-secret" not in serialized
    assert configuration["assets"][0]["credential_configured"] is True

    backup_response = client.get("/api/assets/pump-1/backup.json", headers=headers)
    backup = backup_response.json()
    assert backup_response.status_code == 200
    assert backup["credentials_included"] is False
    assert "credential_id" not in backup["source"]
    assert "highly-secret" not in backup_response.text

    updated = _rest_asset(name="Updated Pump")
    response = client.put(
        "/api/assets/pump-1/configuration", json=updated, headers=headers,
    )
    assert response.status_code == 200, response.text
    stale_delete = client.request(
        "DELETE", "/api/assets/pump-1/configuration",
        json={"expected_revision": backup["config_revision"]}, headers=headers,
    )
    assert stale_delete.status_code == 409

    app_state = client.app.state.edge
    snapshot = {
        "asset_id": "pump-1",
        "window_end": "2026-03-20T12:00:00+00:00",
    }
    saved_snapshot = app_state.store.save_snapshot("pump-1", snapshot)
    fresh_backup = client.get("/api/assets/pump-1/backup.json", headers=headers).json()
    deleted = client.request(
        "DELETE", "/api/assets/pump-1/configuration",
        json={"expected_revision": fresh_backup["config_revision"]}, headers=headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"]["history_preserved"] is True
    assert saved_snapshot.exists()
    with pytest.raises(KeyError):
        app_state.credential_vault.get("cred-1")
    disk_config = json.loads(config_path.read_text(encoding="utf-8"))
    assert disk_config["assets"] == []


def test_configuration_endpoints_require_pin_when_hmi_is_open(tmp_path: Path) -> None:
    client, headers, _ = _client(tmp_path, pin="none")
    assert not headers
    assert client.get("/api/configuration").status_code == 403
    assert client.put(
        "/api/connector-credentials/cred-1",
        json={"headers": {"Authorization": "Bearer secret"}},
    ).status_code == 403


def test_source_queries_are_redacted_from_asset_configuration_and_backup(tmp_path: Path) -> None:
    client, headers, _ = _client(tmp_path)
    payload = _rest_asset(credential_id="")
    assert client.post("/api/assets", json=payload, headers=headers).status_code == 200
    state = client.app.state.edge
    state.config.sources[0].url = (
        "https://historian.example/read?token=private-value&site=west"
    )

    configuration = client.get("/api/configuration", headers=headers).json()["data"]
    asset = client.get("/api/assets/pump-1", headers=headers).json()["data"]
    backup = client.get("/api/assets/pump-1/backup.json", headers=headers).text
    assert "private-value" not in json.dumps(configuration)
    assert "private-value" not in json.dumps(asset)
    assert "private-value" not in backup
    assert "site=west" in configuration["assets"][0]["source"]["url"]


def test_connector_test_checks_samples_and_redacts_unmapped_fields(
    tmp_path: Path, monkeypatch,
) -> None:
    client, headers, _ = _client(tmp_path)
    from edge_adviser.server import router_configuration

    class FakeSource:
        async def pull(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
            return [{
                "ts": (start + timedelta(seconds=index + 1)).isoformat(),
                "temp": float(index),
                "private_payload": "must-not-be-previewed",
            } for index in range(4)]

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(router_configuration, "create_source", lambda *_args, **_kwargs: FakeSource())
    payload = _rest_asset(credential_id="")
    payload["window_s"] = 5
    payload["metrics"][0]["min_samples"] = 2
    response = client.post("/api/connectors/test", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["ok"] is True
    assert result["total_samples"] == 12
    assert all(run["coverage"] == 0.8 for run in result["runs"])
    assert all(set(row) <= {"ts", "temp"} for row in result["sample_preview"])
    assert "private_payload" not in response.text

    class SparseSource(FakeSource):
        async def pull(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
            return (await super().pull(start, end))[:2]

    monkeypatch.setattr(
        router_configuration, "create_source", lambda *_args, **_kwargs: SparseSource(),
    )
    sparse = client.post("/api/connectors/test", json=payload, headers=headers).json()["data"]
    assert sparse["ok"] is False
    assert any("覆盖率不足" in issue for issue in sparse["issues"])


def test_mqtt_configuration_and_backend_credentials(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("EDGE_ADVISER_CREDENTIAL_KEY", Fernet.generate_key().decode("ascii"))
    client, headers, config_path = _client(tmp_path)
    response = client.put(
        "/api/connector-credentials/mqtt-cred",
        json={"username": "operator", "password": "private-password"},
        headers=headers,
    )
    assert response.status_code == 200
    assert client.put(
        "/api/connector-credentials/incomplete-mqtt-cred",
        json={"username": "operator"},
        headers=headers,
    ).status_code == 400

    payload = _rest_asset(credential_id="mqtt-cred")
    payload["source"].update({
        "kind": "mqtt",
        "url": "mqtts://broker.example:8883",
        "topic": "plant/pump-1",
        "params": {
            "timeout_s": 5,
            "qos": 1,
            "timestamp_field": "time",
            "response_path": "data",
            "field_map": {"temp": "temperature"},
        },
    })
    created = client.post("/api/assets", json=payload, headers=headers)
    assert created.status_code == 200, created.text

    data = client.get("/api/configuration", headers=headers).json()["data"]
    assert data["protocols"]["mqtt"] == "ready"
    assert data["assets"][0]["credential_configured"] is True
    assert "private-password" not in json.dumps(data)
    assert client.app.state.edge.credential_vault.get("mqtt-cred") == {
        "username": "operator",
        "password": "private-password",
    }

    invalid = dict(payload)
    invalid["source"] = {**payload["source"], "params": {"qos": 3}}
    assert client.post("/api/assets", json=invalid, headers=headers).status_code == 400
    assert json.loads(config_path.read_text(encoding="utf-8"))["assets"]


def test_mqtt_connection_test_waits_for_a_live_sample(tmp_path: Path, monkeypatch) -> None:
    client, headers, _ = _client(tmp_path)
    from edge_adviser.server import router_configuration

    calls: list[tuple[datetime, datetime]] = []

    class LiveSource:
        async def pull(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
            calls.append((start, end))
            timestamp = datetime.now(timezone.utc)
            return [{"ts": timestamp.isoformat(), "temp": 63.2}]

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(
        router_configuration, "create_source", lambda *_args, **_kwargs: LiveSource(),
    )
    payload = _rest_asset(credential_id="")
    payload["window_s"] = 5
    payload["source"].update({
        "kind": "mqtt",
        "url": "mqtt://broker.example:1883",
        "topic": "plant/pump-1",
        "params": {"timeout_s": 2, "qos": 0},
    })
    response = client.post("/api/connectors/test", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["ok"] is True
    assert len(result["runs"]) == 1
    assert result["total_samples"] == 1
    assert len(calls) == 1
    start, end = calls[0]
    assert start < datetime.now(timezone.utc) < end


def test_modbus_configuration_and_live_connection_test(
    tmp_path: Path, monkeypatch,
) -> None:
    client, headers, config_path = _client(tmp_path)
    payload = _rest_asset(credential_id="")
    payload["source"].update({
        "kind": "modbus",
        "url": "modbus://192.168.10.20:502",
        "params": {
            "timeout_s": 5,
            "unit_id": 1,
            "register_map": {
                "temp": {
                    "area": "holding_registers",
                    "address": 0,
                    "type": "uint16",
                },
            },
        },
    })
    created = client.post("/api/assets", json=payload, headers=headers)
    assert created.status_code == 200, created.text
    data = client.get("/api/configuration", headers=headers).json()["data"]
    assert data["protocols"]["modbus"] == "ready"

    calls: list[tuple[datetime, datetime]] = []

    class LiveSource:
        async def pull(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
            calls.append((start, end))
            return [{
                "ts": datetime.now(timezone.utc).isoformat(),
                "temp": 63.2,
            }]

        async def aclose(self) -> None:
            pass

    from edge_adviser.server import router_configuration
    monkeypatch.setattr(
        router_configuration, "create_source", lambda *_args, **_kwargs: LiveSource(),
    )
    tested = client.post("/api/connectors/test", json=payload, headers=headers)
    assert tested.status_code == 200, tested.text
    result = tested.json()["data"]
    assert result["ok"] is True
    assert result["runs"][0]["ok"] is True
    assert result["total_samples"] == 1
    assert len(calls) == 1
    start, end = calls[0]
    assert start < datetime.now(timezone.utc) < end

    invalid = {
        **payload,
        "asset_id": "pump2",
        "source": {
            **payload["source"],
            "params": {
                **payload["source"]["params"],
                "register_map": {
                    "temp": {
                        "area": "holding_registers",
                        "address": 65535,
                        "type": "float32",
                    },
                },
            },
        },
    }
    rejected = client.post("/api/assets", json=invalid, headers=headers)
    assert rejected.status_code == 400
    assert len(json.loads(config_path.read_text(encoding="utf-8"))["assets"]) == 1


def test_opcua_configuration_and_live_connection_test(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("EDGE_ADVISER_CREDENTIAL_KEY", Fernet.generate_key().decode("ascii"))
    client, headers, _ = _client(tmp_path)
    from edge_adviser.server import router_configuration
    credentials = {
        "username": "opc-operator",
        "password": "opc-password",
        "private_key_password": "private-key-password",
    }
    saved_credentials = client.put(
        "/api/connector-credentials/opcua-cred",
        json=credentials,
        headers=headers,
    )
    assert saved_credentials.status_code == 200, saved_credentials.text
    assert all(value not in saved_credentials.text for value in credentials.values())
    assert client.app.state.edge.credential_vault.get("opcua-cred") == credentials

    payload = _rest_asset(credential_id="")
    payload["source"].update({
        "kind": "opcua",
        "url": "opc.tcp://192.168.10.20:4840/UA/Server",
        "poll_interval_s": 1,
        "params": {
            "timeout_s": 5,
            "security_mode": "none",
            "node_map": {"temp": "ns=2;s=Pump.Temperature"},
        },
    })
    created = client.post("/api/assets", json=payload, headers=headers)
    assert created.status_code == 200, created.text
    data = client.get("/api/configuration", headers=headers).json()["data"]
    assert data["protocols"]["opcua"] == "ready"

    calls: list[tuple[datetime, datetime]] = []

    class LiveSource:
        async def pull(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
            calls.append((start, end))
            return [{
                "ts": datetime.now(timezone.utc).isoformat(),
                "temp": 63.2,
            }]

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(
        router_configuration, "create_source", lambda *_args, **_kwargs: LiveSource(),
    )
    test_response = client.post(
        "/api/connectors/test", json=payload, headers=headers,
    )
    assert test_response.status_code == 200, test_response.text
    result = test_response.json()["data"]
    assert result["ok"] is True
    assert len(result["runs"]) == 1
    assert result["total_samples"] == 1
    assert len(calls) == 1

    invalid = {
        **payload,
        "source": {
            **payload["source"],
            "params": {"node_map": {"unexpected": "ns=2;s=Pump.Temperature"}},
        },
    }
    assert client.post("/api/assets", json=invalid, headers=headers).status_code == 400
