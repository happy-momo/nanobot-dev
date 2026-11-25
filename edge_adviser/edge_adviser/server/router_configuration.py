"""Asset and connector configuration APIs."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import Response
from loguru import logger
from pydantic import BaseModel, Field

from ..config.loader import asset_from_dict
from ..config.model import AssetSpec, SourceKind, SourceSpec
from ..core.runlog import log_run_event
from ..ingest import create_source
from ..server.deps import AppState
from .schemas import asset_to_dict, fail, is_secret_name, ok, source_to_dict

router = APIRouter(prefix="/api", tags=["configuration"])
_ASSET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_METRIC_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_ALLOWED_HEADERS = {"authorization", "x-api-key"}


class MetricInput(BaseModel):
    key: str
    unit: str = ""
    description: str = ""
    norm_min: float | None = None
    norm_max: float | None = None
    warn_min: float | None = None
    warn_max: float | None = None
    crit_min: float | None = None
    crit_max: float | None = None
    persist: int = 1
    min_samples: int = 0


class SourceInput(BaseModel):
    kind: SourceKind
    url: str = ""
    topic: str = ""
    poll_interval_s: float = 1.0
    params: dict[str, Any] = Field(default_factory=dict)
    credential_id: str = ""


class AssetInput(BaseModel):
    asset_id: str
    name: str
    kind: str = "machine"
    description: str = ""
    sample_freq_s: float = 1.0
    window_s: int = 60
    downtime_windows: list[dict[str, Any]] = Field(default_factory=list)
    scenario_notes: list[str] = Field(default_factory=list)
    enabled: bool = True
    metrics: list[MetricInput]
    source: SourceInput


class CredentialInput(BaseModel):
    headers: dict[str, str]


class TestConnectorInput(BaseModel):
    asset_id: str
    name: str = "连接测试"
    kind: str = "machine"
    sample_freq_s: float = 1.0
    window_s: int = 60
    metrics: list[MetricInput]
    source: SourceInput


class DeleteInput(BaseModel):
    expected_revision: str


async def _state(request: Request) -> AppState:
    return request.app.state.edge


def _config_path(state: AppState) -> Path:
    path = state.config.defaults.get("_source_file")
    if not path or not Path(path).is_file():
        raise RuntimeError("configuration file is unavailable; editing is disabled")
    return Path(path)


def _read_raw(state: AppState) -> dict[str, Any]:
    try:
        raw = json.loads(_config_path(state).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read configuration: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("configuration root must be a JSON object")
    raw.setdefault("assets", [])
    raw.setdefault("sources", [])
    if not isinstance(raw["assets"], list) or not isinstance(raw["sources"], list):
        raise RuntimeError("configuration assets and sources must be arrays")
    return raw


def _write_raw(state: AppState, raw: dict[str, Any]) -> None:
    path = _config_path(state)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(raw, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        with open(tmp, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _source_record(source: SourceSpec) -> dict[str, Any]:
    return source_to_dict(source)


def _model_dict(model: BaseModel) -> dict[str, Any]:
    dump = getattr(model, "model_dump", None)
    if callable(dump):
        return dump()
    return model.dict()


def _asset_record(body: AssetInput) -> dict[str, Any]:
    return {
        "asset_id": body.asset_id,
        "name": body.name,
        "kind": body.kind,
        "description": body.description,
        "sample_freq_s": body.sample_freq_s,
        "window_s": body.window_s,
        "downtime_windows": body.downtime_windows,
        "scenario_notes": body.scenario_notes,
        "enabled": body.enabled,
        "metrics": [_model_dict(metric) for metric in body.metrics],
    }


def _validate_asset(body: AssetInput) -> tuple[AssetSpec, SourceSpec]:
    if not _ASSET_ID.fullmatch(body.asset_id):
        raise ValueError("设备 ID 仅允许字母、数字、下划线和连字符，且须以字母或数字开头")
    if not body.name.strip():
        raise ValueError("设备名称不能为空")
    if not math.isfinite(body.sample_freq_s) or body.sample_freq_s <= 0:
        raise ValueError("采样间隔必须是大于 0 的有限数值")
    if body.window_s < 1 or body.window_s > 86400:
        raise ValueError("分析窗口须在 1 到 86400 秒之间")
    if not body.metrics:
        raise ValueError("至少配置一个指标")
    keys: set[str] = set()
    for metric in body.metrics:
        if not _METRIC_KEY.fullmatch(metric.key):
            raise ValueError(f"指标键无效：{metric.key}")
        if metric.key in keys:
            raise ValueError(f"指标键重复：{metric.key}")
        keys.add(metric.key)
        if metric.persist < 1 or metric.min_samples < 0:
            raise ValueError(f"指标 {metric.key} 的样本要求无效")
        values = [getattr(metric, name) for name in (
            "norm_min", "norm_max", "warn_min", "warn_max", "crit_min", "crit_max")]
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError(f"指标 {metric.key} 的阈值必须是有限数字")
        if metric.warn_min is not None and metric.warn_max is not None \
                and metric.warn_min > metric.warn_max:
            raise ValueError(f"指标 {metric.key} 的警告下限不能大于警告上限")
        if metric.crit_min is not None and metric.crit_max is not None \
                and metric.crit_min > metric.crit_max:
            raise ValueError(f"指标 {metric.key} 的严重下限不能大于严重上限")
        if metric.warn_min is not None and metric.warn_max is not None \
                and metric.crit_min is not None and metric.crit_max is not None \
                and metric.crit_max > metric.warn_min and metric.crit_min < metric.warn_max:
            raise ValueError(f"指标 {metric.key} 的严重区间与警告区间重叠")

    source = body.source
    if source.kind not in (SourceKind.SIMULATOR, SourceKind.REST):
        raise ValueError(f"{source.kind.value} 尚未接入，当前不能启用")
    if source.credential_id and not _ASSET_ID.fullmatch(source.credential_id):
        raise ValueError("凭据标识格式无效")
    if source.kind == SourceKind.REST:
        parsed = urlparse(source.url)
        if len(source.url) > 2048 or parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("REST 地址必须是有效的 HTTP 或 HTTPS URL")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("REST 地址端口无效") from exc
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("REST 地址端口无效")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("REST 地址不能包含用户名、密码或片段")
        if parsed.hostname.lower() == "metadata.google.internal":
            raise ValueError("REST 地址不允许指向云元数据服务")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address and (address.is_link_local or address.is_unspecified or address.is_multicast):
            raise ValueError("REST 地址不允许使用链路本地、未指定或组播地址")
        if any(is_secret_name(key) for key, _ in parse_qsl(parsed.query)):
            raise ValueError("REST 地址不能在查询参数中携带凭据，请改用后端凭据配置")
        allowed_params = {
            "timeout_s", "start_param", "end_param", "timestamp_field",
            "response_path", "field_map",
        }
        unknown_params = set(source.params) - allowed_params
        if unknown_params:
            raise ValueError(f"REST 配置包含不支持的参数：{', '.join(sorted(unknown_params))}")
        if source.poll_interval_s <= 0 or not math.isfinite(source.poll_interval_s):
            raise ValueError("采集间隔必须是大于 0 的有限数值")
        if not str(source.params.get("start_param", "start")).strip() \
                or not str(source.params.get("end_param", "end")).strip() \
                or source.params.get("start_param", "start") == source.params.get("end_param", "end"):
            raise ValueError("REST 开始和结束时间参数必须非空且不能相同")
        field_map = source.params.get("field_map", {})
        if not isinstance(field_map, dict):
            raise ValueError("REST 指标字段映射必须是对象")
        unknown_metrics = set(field_map) - keys
        if unknown_metrics:
            raise ValueError(f"REST 字段映射引用了未配置指标：{', '.join(sorted(unknown_metrics))}")
        if any(not isinstance(value, str) or not value.strip() for value in field_map.values()):
            raise ValueError("REST 来源字段名不能为空")
        if source.credential_id and not _ASSET_ID.fullmatch(source.credential_id):
            raise ValueError("凭据标识格式无效")
        timeout_s = source.params.get("timeout_s", 10)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) \
                or not math.isfinite(timeout_s) or not 1 <= timeout_s <= 30:
            raise ValueError("连接超时须在 1 到 30 秒之间")
    else:
        allowed_params = {"regime", "sample_freq_s"}
        unknown_params = set(source.params) - allowed_params
        if unknown_params:
            raise ValueError(f"模拟数据配置包含不支持的参数：{', '.join(sorted(unknown_params))}")
        if any(metric.key not in {"temp", "vibration", "rpm"} for metric in body.metrics):
            raise ValueError("模拟数据仅支持 temp、vibration、rpm 指标")
        simulated_frequency = source.params.get("sample_freq_s", body.sample_freq_s)
        if not isinstance(simulated_frequency, (int, float)) or isinstance(simulated_frequency, bool) \
                or not math.isfinite(simulated_frequency) or simulated_frequency <= 0:
            raise ValueError("模拟数据采样间隔必须是大于 0 的有限数值")
        regime = source.params.get("regime", "clean")
        if regime not in {"clean", "shift", "spike", "degrade", "offline", "fault"}:
            raise ValueError("模拟数据工况无效")
    asset = asset_from_dict(_asset_record(body))
    spec = SourceSpec(
        kind=source.kind,
        asset_id=body.asset_id,
        params=source.params,
        url=source.url,
        topic=source.topic,
        poll_interval_s=source.poll_interval_s,
        credential_id=source.credential_id,
    )
    return asset, spec


def _revision(asset: dict[str, Any], source: dict[str, Any] | None) -> str:
    raw = json.dumps({"asset": asset, "source": source}, sort_keys=True,
                     ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _audit_configuration_change(
    state: AppState, asset_id: str, reason: str,
) -> str | None:
    try:
        log_run_event(
            state.store, asset_id, "asset_config", "ok", actor="hmi", reason=reason,
        )
    except OSError as exc:
        logger.error("configuration audit write failed for {a}: {e}", a=asset_id, e=exc)
        return "配置已保存，但审计记录写入失败"
    return None


def _find_pair(state: AppState, asset_id: str) -> tuple[AssetSpec | None, SourceSpec | None]:
    asset = state.config.asset(asset_id)
    source = next((s for s in state.config.sources if s.asset_id == asset_id), None)
    return asset, source


@router.get("/configuration")
async def configuration(request: Request) -> Any:
    state = await _state(request)
    assets = []
    for asset in state.config.assets:
        source = next((s for s in state.config.sources if s.asset_id == asset.asset_id), None)
        assets.append({
            **asset_to_dict(asset),
            "source": source_to_dict(source, redact_secrets=True) if source else None,
            "credential_configured": bool(source and source.credential_id),
        })
    return ok(data={
        "assets": assets,
        "credential_store_available": state.credential_vault.available,
        "protocols": {
            "simulator": "ready", "rest": "ready",
            "mqtt": "planned", "opcua": "planned", "modbus": "planned",
        },
    })


@router.put("/connector-credentials/{credential_id}")
async def put_credentials(request: Request, credential_id: str, body: CredentialInput) -> Any:
    state = await _state(request)
    if not _ASSET_ID.fullmatch(credential_id):
        return fail("凭据标识格式无效", status=400)
    if not body.headers:
        return fail("至少填写一个认证请求头", status=400)
    cleaned: dict[str, str] = {}
    for name, value in body.headers.items():
        key = name.lower().strip()
        if key not in _ALLOWED_HEADERS:
            return fail(f"不支持的认证请求头：{name}", status=400)
        if not value or len(value) > 4096 or "\r" in value or "\n" in value:
            return fail("认证值不能为空、不能换行且长度不能超过 4096 字符", status=400)
        cleaned[key] = value
    async with state.configuration_lock:
        if any(source.credential_id == credential_id for source in state.config.sources):
            return fail("已绑定凭据不可直接覆盖，请创建新凭据并更新设备配置", status=409)
        try:
            state.credential_vault.put(credential_id, cleaned)
        except (RuntimeError, ValueError, OSError) as exc:
            return fail(str(exc), status=400)
    return ok(data={"credential_id": credential_id, "configured": True})


@router.delete("/connector-credentials/{credential_id}")
async def delete_credentials(request: Request, credential_id: str) -> Any:
    state = await _state(request)
    if not _ASSET_ID.fullmatch(credential_id):
        return fail("凭据标识格式无效", status=400)
    async with state.configuration_lock:
        if any(source.credential_id == credential_id for source in state.config.sources):
            return fail("该凭据仍被设备配置引用，不能删除", status=409)
        try:
            state.credential_vault.delete(credential_id)
        except (RuntimeError, ValueError, OSError) as exc:
            return fail(str(exc), status=400)
    return ok(data={"deleted": True})


@router.post("/connectors/test")
async def test_connector(request: Request, body: TestConnectorInput) -> Any:
    state = await _state(request)
    if body.window_s < 5 or body.window_s > 300:
        return fail("测试窗口须在 5 到 300 秒之间", status=400)
    if body.source.kind not in (SourceKind.SIMULATOR, SourceKind.REST):
        return fail("该通信协议尚未接入", status=400)
    try:
        test_asset = AssetInput(
            asset_id=body.asset_id, name=body.name, kind=body.kind,
            sample_freq_s=body.sample_freq_s, window_s=body.window_s,
            metrics=body.metrics, source=body.source,
        )
        asset, spec = _validate_asset(test_asset)
        credentials = state.credential_vault.get(spec.credential_id) if spec.credential_id else {}
        src = create_source(spec, credentials=credentials)
    except (KeyError, RuntimeError, TypeError, ValueError, NotImplementedError) as exc:
        return fail(str(exc), status=400)

    checks: list[dict[str, Any]] = []
    total_samples = 0
    issues: list[str] = []
    preview_samples: list[dict[str, Any]] = []
    latest_timestamp: datetime | None = None
    metric_sample_counts = {metric.key: 0 for metric in asset.metrics}
    now = datetime.now(timezone.utc)
    try:
        for index in range(3):
            end = now - timedelta(seconds=body.window_s * (2 - index))
            start = end - timedelta(seconds=body.window_s)
            try:
                samples = await asyncio.wait_for(
                    src.pull(start, end),
                    timeout=min(float(spec.params.get("timeout_s", 10)), 30),
                )
            except asyncio.TimeoutError:
                issues.append(f"第 {index + 1} 次采集超时")
                checks.append({"run": index + 1, "ok": False, "error": "请求超时"})
                continue
            except Exception as exc:
                issues.append(f"第 {index + 1} 次采集失败")
                checks.append({
                    "run": index + 1, "ok": False,
                    "error": f"连接器返回 {type(exc).__name__}",
                })
                continue
            total_samples += len(samples)
            preview_samples = [
                {key: row[key] for key in ("ts", *metric_sample_counts) if key in row}
                for row in samples[-5:]
            ]
            run_issues: list[str] = []
            parsed_times: list[datetime] = []
            metric_counts = {metric.key: 0 for metric in asset.metrics}
            expected_samples = max(1, math.ceil(body.window_s / asset.sample_freq_s))
            sample_coverage = len(samples) / expected_samples
            if sample_coverage < state.config.coverage_threshold:
                run_issues.append(
                    f"第 {index + 1} 次采集覆盖率不足："
                    f"{sample_coverage:.0%}/{state.config.coverage_threshold:.0%}")
            for row in samples:
                stamp = row.get("ts")
                try:
                    parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    parsed_times.append(parsed.astimezone(timezone.utc))
                except (TypeError, ValueError):
                    run_issues.append("时间戳缺失或格式无效")
                    continue
                if parsed < start or parsed >= end:
                    run_issues.append("样本时间戳超出请求窗口")
                if latest_timestamp is None or parsed > latest_timestamp:
                    latest_timestamp = parsed
                for metric in asset.metrics:
                    value = row.get(metric.key)
                    if value is None:
                        continue
                    if (
                        isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                    ):
                        run_issues.append(f"指标 {metric.key} 不是有效数值")
                    else:
                        metric_counts[metric.key] += 1
                        metric_sample_counts[metric.key] += 1
            if not samples:
                run_issues.append("采集窗口无样本")
            if parsed_times and parsed_times != sorted(parsed_times):
                run_issues.append("样本时间顺序异常")
            if len(set(parsed_times)) != len(parsed_times):
                run_issues.append("样本时间戳重复")
            for metric in asset.metrics:
                required = metric.min_samples
                if metric_counts[metric.key] < required:
                    run_issues.append(
                        f"指标 {metric.key} 有效样本不足：{metric_counts[metric.key]}/{required}")
                metric_coverage = metric_counts[metric.key] / expected_samples
                if metric_coverage < state.config.coverage_threshold:
                    run_issues.append(
                        f"指标 {metric.key} 覆盖率不足："
                        f"{metric_coverage:.0%}/{state.config.coverage_threshold:.0%}")
            issues.extend(run_issues)
            checks.append({
                "run": index + 1,
                "ok": not run_issues,
                "sample_count": len(samples),
                "coverage": round(sample_coverage, 3),
                "metric_coverage": {
                    metric.key: round(metric_counts[metric.key] / expected_samples, 3)
                    for metric in asset.metrics
                },
                "issues": run_issues,
            })
    finally:
        await src.aclose()

    if latest_timestamp is not None:
        age_s = (now - latest_timestamp).total_seconds()
        if age_s > state.config.data_freshness_s:
            issues.append(f"最新样本已过期：{int(age_s)} 秒")
    for metric in asset.metrics:
        if metric_sample_counts[metric.key] == 0:
            issues.append(f"指标 {metric.key} 没有收到有效数值")
    return ok(data={
        "ok": not issues and total_samples > 0,
        "runs": checks,
        "total_samples": total_samples,
        "issues": list(dict.fromkeys(issues)),
        "sample_preview": preview_samples,
    })


@router.post("/assets")
async def create_asset(request: Request, body: AssetInput) -> Any:
    state = await _state(request)
    try:
        asset, source = _validate_asset(body)
    except ValueError as exc:
        return fail(str(exc), status=400)
    async with state.configuration_lock:
        if state.config.asset(asset.asset_id):
            return fail("设备 ID 已存在", status=409)
        try:
            prepared = state.supervisor.prepare_source(source)
            if asset.enabled:
                state.store.ensure_asset(asset)
            else:
                await prepared.aclose()
            raw = _read_raw(state)
            raw["assets"].append(_asset_record(body))
            raw["sources"] = [s for s in raw["sources"] if s.get("asset_id") != asset.asset_id]
            raw["sources"].append(_source_record(source))
            _write_raw(state, raw)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, NotImplementedError) as exc:
            return fail(str(exc), status=400)
        state.config.assets.append(asset)
        state.config.sources = [s for s in state.config.sources if s.asset_id != asset.asset_id]
        state.config.sources.append(source)
        warnings: list[str] = []
        if asset.enabled:
            warning = await state.supervisor.install_source(asset.asset_id, prepared)
            if warning:
                warnings.append(warning)
        audit_warning = _audit_configuration_change(state, asset.asset_id, "设备配置已新增")
        if audit_warning:
            warnings.append(audit_warning)
    return ok(data={"asset_id": asset.asset_id, "warnings": warnings})


@router.put("/assets/{asset_id}/configuration")
async def update_asset(request: Request, asset_id: str, body: AssetInput) -> Any:
    state = await _state(request)
    if asset_id != body.asset_id:
        return fail("URL 与配置中的设备 ID 不一致", status=400)
    try:
        asset, source = _validate_asset(body)
    except ValueError as exc:
        return fail(str(exc), status=400)
    async with state.configuration_lock:
        old_asset, old_source = _find_pair(state, asset_id)
        if old_asset is None:
            return fail("设备不存在", status=404)
        try:
            prepared = state.supervisor.prepare_source(source)
            if asset.enabled:
                state.store.ensure_asset(asset)
            else:
                await prepared.aclose()
            raw = _read_raw(state)
            raw["assets"] = [
                _asset_record(body) if item.get("asset_id") == asset_id else item
                for item in raw["assets"]
            ]
            raw["sources"] = [s for s in raw["sources"] if s.get("asset_id") != asset_id]
            raw["sources"].append(_source_record(source))
            _write_raw(state, raw)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, NotImplementedError) as exc:
            return fail(str(exc), status=400)
        index = state.config.assets.index(old_asset)
        state.config.assets[index] = asset
        state.config.sources = [s for s in state.config.sources if s.asset_id != asset_id]
        state.config.sources.append(source)
        warnings: list[str] = []
        if asset.enabled:
            warning = await state.supervisor.install_source(asset_id, prepared)
            if warning:
                warnings.append(warning)
        else:
            warning = await state.supervisor.remove_source(asset_id)
            if warning:
                warnings.append(warning)
        if old_source and old_source.credential_id \
                and old_source.credential_id != source.credential_id \
                and not any(s.credential_id == old_source.credential_id for s in state.config.sources):
            try:
                state.credential_vault.delete(old_source.credential_id)
            except (RuntimeError, ValueError, OSError) as exc:
                logger.warning("orphaned connector credential could not be removed: {e}", e=exc)
                warnings.append("旧凭据未能清理，请管理员检查凭据库")
        audit_warning = _audit_configuration_change(state, asset_id, "设备配置已更新")
        if audit_warning:
            warnings.append(audit_warning)
    return ok(data={"asset_id": asset_id, "warnings": warnings})


@router.get("/assets/{asset_id}/backup.json")
async def download_asset_backup(request: Request, asset_id: str) -> Any:
    state = await _state(request)
    asset, source = _find_pair(state, asset_id)
    if asset is None:
        return fail("设备不存在", status=404)
    asset_record = asset_to_dict(asset)
    source_record = _source_record(source) if source else None
    revision = _revision(asset_record, source_record)
    backup_source = (
        source_to_dict(source, redact_secrets=True, include_credential_id=False)
        if source else None
    )
    payload = {
        "format": "edge-adviser-asset-backup",
        "version": 1,
        "config_revision": revision,
        "asset": asset_record,
        "source": backup_source,
        "credentials_included": False,
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{asset_id}-backup.json"'},
    )


@router.delete("/assets/{asset_id}/configuration")
async def delete_asset(request: Request, asset_id: str, body: DeleteInput) -> Any:
    state = await _state(request)
    async with state.configuration_lock:
        asset, source = _find_pair(state, asset_id)
        if asset is None:
            return fail("设备不存在", status=404)
        asset_record = asset_to_dict(asset)
        source_record = _source_record(source) if source else None
        if body.expected_revision != _revision(asset_record, source_record):
            return fail("配置已发生变化，请重新下载备份后再确认删除", status=409)
        try:
            raw = _read_raw(state)
            raw["assets"] = [a for a in raw["assets"] if a.get("asset_id") != asset_id]
            raw["sources"] = [s for s in raw["sources"] if s.get("asset_id") != asset_id]
            _write_raw(state, raw)
        except (OSError, RuntimeError, ValueError) as exc:
            return fail(str(exc), status=400)
        state.config.assets = [a for a in state.config.assets if a.asset_id != asset_id]
        state.config.sources = [s for s in state.config.sources if s.asset_id != asset_id]
        warnings: list[str] = []
        warning = await state.supervisor.remove_source(asset_id)
        if warning:
            warnings.append(warning)
        if source and source.credential_id \
                and not any(s.credential_id == source.credential_id for s in state.config.sources):
            try:
                state.credential_vault.delete(source.credential_id)
            except (RuntimeError, ValueError, OSError) as exc:
                logger.warning("orphaned connector credential could not be removed: {e}", e=exc)
                warnings.append("凭据未能清理，请管理员检查凭据库")
        audit_warning = _audit_configuration_change(
            state, asset_id, "设备配置已删除；历史数据保留",
        )
        if audit_warning:
            warnings.append(audit_warning)
    return ok(data={"asset_id": asset_id, "history_preserved": True, "warnings": warnings})
