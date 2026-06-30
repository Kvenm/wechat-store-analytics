from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from shared.module_registry import get_module_capability, import_enabled_types, unsupported_types_message, web_enabled_types
from shared.paths import DEFAULT_DB_PATH, DEFAULT_REPORTS_DIR, DEFAULT_STANDARD_DIR, PROJECT_ROOT
from warehouse.repository import connect, initialize_database, upsert_api_task_run, upsert_api_task_step


STEP_KEYS = ("collect", "import_metadata", "import_files", "analyze", "report")
RUNNING_STATES = {"queued", "running"}
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TASK_OUTPUT_TAIL_LENGTH = 8000
RAW_EXPORT_DIR = PROJECT_ROOT / "data" / "raw"

_tasks: dict[str, dict[str, Any]] = {}
_lock = threading.RLock()


class TaskRunnerError(ValueError):
    pass


def start_web_export_task(payload: Mapping[str, Any]) -> dict[str, Any]:
    spec = normalize_web_export_payload(payload)
    with _lock:
        active_task = _active_task_locked()
        if active_task is not None:
            raise TaskRunnerError(
                f"已有任务正在运行：{active_task['id']}。请等待完成后再启动新的任务。"
            )

        task = _build_runtime_task(spec)
        _tasks[task["id"]] = task
        _persist_task(task)
        thread = threading.Thread(
            target=_run_task,
            args=(task["id"],),
            daemon=True,
            name=f"wechat-store-task-{task['id'][:24]}",
        )
        task["thread_name"] = thread.name
        _persist_task(task)
        thread.start()
        return _public_task(task)


def check_local_export_files(payload: Mapping[str, Any]) -> dict[str, Any]:
    spec = normalize_web_export_payload(payload)
    if spec.get("source_type") != "local_export":
        raise TaskRunnerError("深度文件校验只支持 source_type=local_export 或 params.mode=local_export。")

    collection_task_id = _local_export_collection_task_id(spec)
    return _run_command_for_json(
        [
            sys.executable,
            "scripts/import/import_files.py",
            "--source-dir",
            spec["source_dir"],
            "--shop-id",
            spec["shop_id"],
            "--shop-name",
            spec["shop_name"],
            "--task-id",
            collection_task_id,
            "--expected-types",
            ",".join(str(item) for item in spec.get("types") or []),
            "--manifest-policy",
            "ignore" if spec.get("local_export_mode") == "manual_export" else "auto",
            "--check-only",
        ],
        step_key="local_export_check",
    )


def list_runtime_tasks() -> list[dict[str, Any]]:
    with _lock:
        return sorted(
            [_public_task(task) for task in _tasks.values()],
            key=lambda item: item.get("created_at") or "",
            reverse=True,
        )


def get_runtime_task(task_id: str) -> dict[str, Any] | None:
    with _lock:
        task = _tasks.get(task_id)
        return _public_task(task) if task is not None else None


def normalize_web_export_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    params = _mapping(payload.get("params"))
    requested_source_type = _text(payload.get("source_type"))
    mode = _text(params.get("mode"))
    if requested_source_type == "local_export" or mode == "local_export":
        return _normalize_local_export_payload(payload, params)

    source_type = requested_source_type or "web_export"
    if source_type != "web_export":
        raise TaskRunnerError(
            "当前任务入口只支持 source_type=web_export、source_type=local_export，"
            "或 params.mode=local_export。"
        )

    date_from = _text(params.get("from") or params.get("date_from"))
    date_to = _text(params.get("to") or params.get("date_to"))
    _validate_date_range(date_from, date_to)

    requested_types = _normalize_types(params.get("types") or ["orders"])
    unsupported = [item for item in requested_types if item not in web_enabled_types()]
    if unsupported:
        raise TaskRunnerError(
            "本轮页面真实采集只开放已校准模块，暂不启动未校准模块。"
            + unsupported_types_message(unsupported, mode="web")
        )

    shop_id = _text(payload.get("shop_id") or params.get("shop_id"))
    shop_name = _text(params.get("shop_name"))
    configured_shops = _configured_shops()
    if not shop_id:
        enabled_shops = [shop for shop in configured_shops if shop.get("enabled") is not False]
        if len(enabled_shops) == 1:
            shop_id = _text(enabled_shops[0].get("id"))
            shop_name = shop_name or _text(enabled_shops[0].get("name"))
        else:
            raise TaskRunnerError("请选择一个店铺后再启动真实采集。")

    configured_shop = _shop_by_id(configured_shops, shop_id)
    if configured_shop is None:
        raise TaskRunnerError(f"店铺 {shop_id} 不在 config/shops.json 中，暂不允许页面直接采集。")
    if configured_shop.get("enabled") is False:
        raise TaskRunnerError(f"店铺 {shop_id} 已在 config/shops.json 中禁用。")

    shop_name = shop_name or _text(configured_shop.get("name")) or shop_id
    headless = bool(params.get("headless", False))
    task_name = _text(payload.get("task_name")) or f"订单导出分析 {date_from} 至 {date_to}"

    return {
        "shop_id": shop_id,
        "shop_name": shop_name,
        "task_name": task_name,
        "source_type": source_type,
        "from": date_from,
        "to": date_to,
        "types": requested_types,
        "headless": headless,
    }


def _normalize_local_export_payload(
    payload: Mapping[str, Any],
    params: Mapping[str, Any],
) -> dict[str, Any]:
    source_dir, metadata_path = _resolve_local_export_source_dir(
        _first_text(
            params.get("source_dir"),
            params.get("sourceDir"),
            params.get("raw_dir"),
            params.get("rawDir"),
            params.get("export_dir"),
            params.get("exportDir"),
            payload.get("source_dir"),
        )
    )
    metadata = _read_json(metadata_path) if metadata_path is not None else {}

    shop_id = _first_text(
        payload.get("shop_id"),
        params.get("shop_id"),
        metadata.get("shop_id"),
        _metadata_shop_id(metadata),
    )
    shop_name = _first_text(
        payload.get("shop_name"),
        params.get("shop_name"),
        metadata.get("shop_name"),
        _metadata_shop_name(metadata, shop_id),
    )
    date_from = _first_text(
        params.get("from"),
        params.get("date_from"),
        _metadata_date_value(metadata, "from"),
        _date_from_source_dir(source_dir, "from"),
    )
    date_to = _first_text(
        params.get("to"),
        params.get("date_to"),
        _metadata_date_value(metadata, "to"),
        _date_from_source_dir(source_dir, "to"),
    )

    _require_local_export_field("shop_id", shop_id, source_dir, "params.shop_id 或 metadata.shop_id")
    _require_local_export_field(
        "shop_name",
        shop_name,
        source_dir,
        "params.shop_name 或 metadata.shops/artifacts/items 中的 shop_name",
    )
    _require_local_export_field("from", date_from, source_dir, "params.from 或 metadata.date_range.from")
    _require_local_export_field("to", date_to, source_dir, "params.to 或 metadata.date_range.to")
    _validate_date_range(date_from, date_to)

    requested_types = _normalize_types(
        params.get("types") or metadata.get("types") or _metadata_export_types(metadata) or ["orders"]
    )
    auto_detect = _is_auto_types(requested_types)
    unsupported = [
        item for item in requested_types
        if item not in import_enabled_types() and item.lower() != "auto"
    ]
    if unsupported:
        raise TaskRunnerError(
            "本地导出模式只允许导入已登记的数据模块。"
            + unsupported_types_message(unsupported, mode="import")
        )
    task_name = _text(payload.get("task_name")) or f"本地导出分析 {date_from} 至 {date_to}"

    return {
        "shop_id": shop_id,
        "shop_name": shop_name,
        "task_name": task_name,
        "source_type": "local_export",
        "mode": "local_export",
        "local_export_mode": "collector_export" if metadata_path is not None else "manual_export",
        "from": date_from,
        "to": date_to,
        "types": ["auto"] if auto_detect else requested_types,
        "headless": False,
        "source_dir": str(source_dir),
        "metadata_path": str(metadata_path) if metadata_path is not None else None,
        "collection_task_id": _text(metadata.get("task_id")) or _manual_export_task_id(
            source_dir=source_dir,
            shop_id=shop_id,
            date_from=date_from,
            date_to=date_to,
            requested_types=requested_types,
        ),
    }


def _run_task(task_id: str) -> None:
    task = _task_for_update(task_id)
    if task is None:
        return

    _update_task(task_id, state="running", started_at=_now())
    spec = task["spec"]

    try:
        metadata_path, metadata = _prepare_task_source(task_id, spec)
        collection_task_id = _text(metadata.get("task_id")) or metadata_path.parent.name
        _update_task(
            task_id,
            collection_task_id=collection_task_id,
            source_dir=str(metadata_path.parent),
            metadata_path=str(metadata_path),
            collector_status=metadata.get("status"),
        )

        _run_step(
            task_id,
            "import_metadata",
            [
                sys.executable,
                "scripts/import/import_task_metadata.py",
                "--metadata-path",
                str(metadata_path),
                "--shop-id",
                spec["shop_id"],
                "--shop-name",
                spec["shop_name"],
            ],
        )

        artifacts = [
            artifact
            for artifact in metadata.get("artifacts") or []
            if isinstance(artifact, Mapping) and artifact.get("status") == "completed"
        ]
        if metadata.get("status") != "completed" or not artifacts:
            raise TaskRunnerError(
                f"订单导出未生成可导入文件，collector status={metadata.get('status') or 'unknown'}。"
            )

        standard_dir = DEFAULT_STANDARD_DIR / collection_task_id
        import_result = _run_step(
            task_id,
            "import_files",
            [
                sys.executable,
                "scripts/import/import_files.py",
                "--source-dir",
                str(metadata_path.parent),
                "--shop-id",
                spec["shop_id"],
                "--shop-name",
                spec["shop_name"],
                "--task-id",
                collection_task_id,
                "--standard-dir",
                str(standard_dir),
                "--expected-types",
                ",".join(str(item) for item in spec.get("types") or []),
                "--manifest-policy",
                "ignore" if spec.get("local_export_mode") == "manual_export" else "auto",
            ],
        )

        analysis_result = _run_step(
            task_id,
            "analyze",
            [
                sys.executable,
                "scripts/analyze/analyze.py",
                "--shop-id",
                spec["shop_id"],
                "--from",
                spec["from"],
                "--to",
                spec["to"],
            ],
        )
        analysis_run_id = _text(analysis_result.get("analysis_run_id"))
        if not analysis_run_id:
            raise TaskRunnerError("分析脚本未返回 analysis_run_id。")

        report_result = _run_step(
            task_id,
            "report",
            [
                sys.executable,
                "scripts/report/report.py",
                "--analysis-run-id",
                analysis_run_id,
            ],
        )

        _update_task(
            task_id,
            state="completed",
            status="completed",
            completed_at=_now(),
            result={
                "collection_task_id": collection_task_id,
                "source_dir": str(metadata_path.parent),
                "metadata_path": str(metadata_path),
                "standard_dir": str(standard_dir),
                "artifact_count": len(artifacts),
                "import_summary": import_result,
                "analysis_run_id": analysis_run_id,
                "report": report_result,
                "db_path": str(DEFAULT_DB_PATH),
                "reports_dir": str(DEFAULT_REPORTS_DIR),
            },
        )
    except Exception as exc:
        _fail_task(task_id, exc)


def _prepare_task_source(task_id: str, spec: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    if spec.get("source_type") == "local_export":
        metadata_path = _ensure_local_export_metadata(spec)
        metadata = _read_json(metadata_path)
        collection_task_id = _text(metadata.get("task_id")) or _text(spec.get("collection_task_id")) or metadata_path.parent.name
        _set_step(
            task_id,
            "collect",
            status="completed",
            started_at=_now(),
            completed_at=_now(),
            parsed={
                "mode": "local_export",
                "local_export_mode": spec.get("local_export_mode"),
                "message": "已跳过在线采集，使用 data/raw 下的本地导出目录。",
                "source_dir": str(metadata_path.parent),
                "metadata_path": str(metadata_path),
                "collection_task_id": collection_task_id,
            },
        )
        return metadata_path, metadata

    collect_started_at = time.time()
    _run_step(
        task_id,
        "collect",
        [
            "node",
            "scripts/collect/collect.mjs",
            "--shop-id",
            spec["shop_id"],
            "--from",
            spec["from"],
            "--to",
            spec["to"],
            "--types",
            ",".join(spec["types"]),
            "--headless",
            "true" if spec["headless"] else "false",
        ],
        parse_json=False,
    )

    metadata_path = _find_latest_metadata_path(
        shop_id=spec["shop_id"],
        date_from=spec["from"],
        date_to=spec["to"],
        started_at=collect_started_at,
    )
    return metadata_path, _read_json(metadata_path)


def _run_step(
    task_id: str,
    step_key: str,
    command: list[str],
    *,
    parse_json: bool = True,
) -> dict[str, Any]:
    _set_step(task_id, step_key, status="running", started_at=_now(), command=_public_command(command))
    started_at = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=_subprocess_env(),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        _set_step(task_id, step_key, status="failed", completed_at=_now(), error=str(exc))
        raise

    duration_ms = int((time.time() - started_at) * 1000)
    stdout_tail = _tail(completed.stdout)
    stderr_tail = _tail(completed.stderr)
    if completed.returncode != 0:
        message = (
            f"{step_key} 执行失败，exit_code={completed.returncode}。"
            f"{(' stderr: ' + stderr_tail) if stderr_tail else ''}"
        )
        _set_step(
            task_id,
            step_key,
            status="failed",
            completed_at=_now(),
            exit_code=completed.returncode,
            duration_ms=duration_ms,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            error=message,
        )
        raise TaskRunnerError(message)

    parsed: dict[str, Any] = {}
    if parse_json:
        parsed = _parse_json_stdout(completed.stdout, step_key)

    _set_step(
        task_id,
        step_key,
        status="completed",
        completed_at=_now(),
        exit_code=completed.returncode,
        duration_ms=duration_ms,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        parsed=parsed,
    )
    return parsed


def _run_command_for_json(command: list[str], *, step_key: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=_subprocess_env(),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise TaskRunnerError(f"{step_key} 启动失败：{exc}") from exc

    if completed.returncode != 0:
        stderr_tail = _tail(completed.stderr)
        raise TaskRunnerError(
            f"{step_key} 执行失败，exit_code={completed.returncode}。"
            f"{(' stderr: ' + stderr_tail) if stderr_tail else ''}"
        )
    return _parse_json_stdout(completed.stdout, step_key)


def _build_runtime_task(spec: dict[str, Any]) -> dict[str, Any]:
    created_at = _now()
    task_id = "_".join(
        [
            "ui_collect",
            _safe_path_segment(spec["shop_id"]),
            spec["from"],
            spec["to"],
            _compact_timestamp(),
        ]
    )
    return {
        "id": task_id,
        "task_id": task_id,
        "state": "queued",
        "status": "queued",
        "created_at": created_at,
        "started_at": None,
        "completed_at": None,
        "spec": spec,
        "shop_id": spec["shop_id"],
        "shop_name_snapshot": spec["shop_name"],
        "task_name": spec["task_name"],
        "source_type": spec["source_type"],
        "date_range": {"from": spec["from"], "to": spec["to"]},
        "steps": {
            key: {"status": "pending", "started_at": None, "completed_at": None}
            for key in STEP_KEYS
        },
        "result": None,
        "error": None,
    }


def _active_task_locked() -> dict[str, Any] | None:
    for task in _tasks.values():
        if task.get("state") in RUNNING_STATES:
            return task
    return None


def _task_for_update(task_id: str) -> dict[str, Any] | None:
    with _lock:
        return _tasks.get(task_id)


def _update_task(task_id: str, **updates: Any) -> None:
    task_to_persist: dict[str, Any] | None = None
    with _lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        task.update(updates)
        task["updated_at"] = _now()
        task_to_persist = dict(task)
    _persist_task(task_to_persist)


def _set_step(task_id: str, step_key: str, **updates: Any) -> None:
    task_to_persist: dict[str, Any] | None = None
    step_to_persist: dict[str, Any] | None = None
    with _lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        step = task["steps"].setdefault(step_key, {})
        step.update(updates)
        task["updated_at"] = _now()
        task_to_persist = dict(task)
        step_to_persist = dict(step)
    _persist_task(task_to_persist)
    _persist_step(task_id, step_key, step_to_persist)


def _fail_task(task_id: str, exc: Exception) -> None:
    task_to_persist: dict[str, Any] | None = None
    changed_steps: list[tuple[str, dict[str, Any]]] = []
    with _lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        task["state"] = "failed"
        task["status"] = "failed"
        task["completed_at"] = _now()
        task["updated_at"] = task["completed_at"]
        task["error"] = {
            "type": exc.__class__.__name__,
            "message": str(exc),
        }
        for step_key, step in task["steps"].items():
            if step.get("status") == "running":
                step["status"] = "failed"
                step["completed_at"] = task["completed_at"]
                step["error"] = str(exc)
                changed_steps.append((step_key, dict(step)))
        task_to_persist = dict(task)
    _persist_task(task_to_persist)
    for step_key, step in changed_steps:
        _persist_step(task_id, step_key, step)


def _public_task(task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": task.get("id"),
        "task_id": task.get("task_id"),
        "collection_task_id": task.get("collection_task_id"),
        "state": task.get("state"),
        "status": task.get("status"),
        "shop_id": task.get("shop_id"),
        "shop_name_snapshot": task.get("shop_name_snapshot"),
        "task_name": task.get("task_name"),
        "source_type": task.get("source_type"),
        "date_range": task.get("date_range"),
        "types": _mapping(task.get("spec")).get("types"),
        "headless": _mapping(task.get("spec")).get("headless"),
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at"),
        "updated_at": task.get("updated_at"),
        "thread_name": task.get("thread_name"),
        "steps": task.get("steps"),
        "source_dir": task.get("source_dir"),
        "metadata_path": task.get("metadata_path"),
        "collector_status": task.get("collector_status"),
        "result": task.get("result"),
        "error": task.get("error"),
        "persist_warnings": task.get("persist_warnings"),
    }


def _persist_task(task: Mapping[str, Any] | None) -> None:
    if not task:
        return
    try:
        with connect(DEFAULT_DB_PATH) as conn:
            initialize_database(conn)
            upsert_api_task_run(conn, task)
            steps = _mapping(task.get("steps"))
            for step_key, step in steps.items():
                if isinstance(step, Mapping):
                    upsert_api_task_step(conn, task_id=str(task.get("task_id") or task.get("id")), step_key=str(step_key), step=step)
    except Exception as exc:
        _remember_persist_warning(task, exc)


def _persist_step(task_id: str, step_key: str, step: Mapping[str, Any] | None) -> None:
    if not step:
        return
    try:
        with connect(DEFAULT_DB_PATH) as conn:
            initialize_database(conn)
            upsert_api_task_step(conn, task_id=task_id, step_key=step_key, step=step)
    except Exception as exc:
        _remember_persist_warning({"task_id": task_id}, exc)


def _remember_persist_warning(task: Mapping[str, Any], exc: Exception) -> None:
    task_id = _text(task.get("task_id") or task.get("id"))
    if not task_id:
        return
    with _lock:
        current = _tasks.get(task_id)
        if current is None:
            return
        warnings = current.setdefault("persist_warnings", [])
        if isinstance(warnings, list):
            warnings.append(
                {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                    "occurred_at": _now(),
                }
            )


def _find_latest_metadata_path(*, shop_id: str, date_from: str, date_to: str, started_at: float) -> Path:
    prefix = f"collect_{_safe_path_segment(shop_id)}_{date_from}_{date_to}_"
    candidates = []
    for metadata_path in RAW_EXPORT_DIR.glob(f"{prefix}*/task-metadata.json"):
        try:
            stat = metadata_path.stat()
        except OSError:
            continue
        if stat.st_mtime >= started_at - 5:
            candidates.append((stat.st_mtime, metadata_path))
    if not candidates:
        raise TaskRunnerError(f"未找到 collector 生成的 task-metadata.json，匹配前缀：{prefix}")
    return sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]


def _ensure_local_export_metadata(spec: Mapping[str, Any]) -> Path:
    metadata_path_value = _text(spec.get("metadata_path"))
    if metadata_path_value:
        return Path(metadata_path_value)

    source_dir = Path(str(spec["source_dir"]))
    metadata_path = source_dir / "task-metadata.json"
    if metadata_path.exists():
        return metadata_path

    metadata = _manual_export_metadata(spec, metadata_path)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return metadata_path


def _manual_export_metadata(spec: Mapping[str, Any], metadata_path: Path) -> dict[str, Any]:
    collection_task_id = _local_export_collection_task_id(spec)
    artifacts = _manual_export_artifacts(
        source_dir=metadata_path.parent,
        shop_id=str(spec["shop_id"]),
        shop_name=str(spec["shop_name"]),
        requested_types=[str(item) for item in spec.get("types") or []],
    )
    return {
        "task_id": collection_task_id,
        "status": "completed",
        "source_type": "manual_export",
        "shop_id": spec["shop_id"],
        "shop_name": spec["shop_name"],
        "date_range": {"from": spec["from"], "to": spec["to"]},
        "types": list(spec.get("types") or []),
        "artifacts": artifacts,
        "items": [
            {
                "shop_id": spec["shop_id"],
                "shop_name": spec["shop_name"],
                "type": artifact.get("export_type"),
                "label": artifact.get("export_type"),
                "status": "completed",
                "export": artifact,
                "error": None,
            }
            for artifact in artifacts
        ],
        "created_at": _now(),
    }


def _manual_export_artifacts(
    *,
    source_dir: Path,
    shop_id: str,
    shop_name: str,
    requested_types: list[str],
) -> list[dict[str, Any]]:
    files = [
        path
        for path in sorted(source_dir.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in {".csv", ".xlsx", ".xls", ".zip"}
        and path.name not in {"task-metadata.json", "artifacts-manifest.json"}
        and not path.name.startswith("~$")
    ]
    if not files:
        return []

    export_type = requested_types[0] if len(requested_types) == 1 and requested_types[0] != "auto" else None
    artifacts: list[dict[str, Any]] = []
    for path in files:
        relative_path = path.relative_to(source_dir)
        artifact: dict[str, Any] = {
            "source_kind": "export_file",
            "source_type": "export_file",
            "saved_path": str(relative_path),
            "original_filename": path.name,
            "shop_id": shop_id,
            "shop_name": shop_name,
            "status": "completed",
            "size_bytes": path.stat().st_size,
        }
        if export_type:
            artifact["export_type"] = export_type
            capability = get_module_capability(export_type)
            if capability is not None:
                artifact["table_hint"] = capability.table_hint
        artifacts.append(artifact)
    return artifacts


def _local_export_collection_task_id(spec: Mapping[str, Any]) -> str:
    metadata_path_value = _text(spec.get("metadata_path"))
    if metadata_path_value:
        metadata = _read_json(Path(metadata_path_value))
        return _text(metadata.get("task_id")) or Path(metadata_path_value).parent.name
    return _manual_export_task_id(
        source_dir=Path(str(spec["source_dir"])),
        shop_id=str(spec["shop_id"]),
        date_from=str(spec["from"]),
        date_to=str(spec["to"]),
        requested_types=[str(item) for item in spec.get("types") or []],
    )


def _manual_export_task_id(
    *,
    source_dir: Path,
    shop_id: str,
    date_from: str,
    date_to: str,
    requested_types: list[str],
) -> str:
    type_segment = "-".join(_safe_path_segment(item) for item in requested_types) or "manual"
    return "_".join(
        [
            "manual_export",
            _safe_path_segment(shop_id),
            date_from,
            date_to,
            type_segment,
            _safe_path_segment(source_dir.name),
        ]
    )


def _date_from_source_dir(source_dir: Path, key: str) -> str | None:
    matches = re.findall(r"\d{4}-\d{2}-\d{2}", source_dir.name)
    if key == "from" and matches:
        return matches[0]
    if key == "to" and matches:
        return matches[-1]
    return None


def _resolve_local_export_source_dir(value: str | None) -> tuple[Path, Path | None]:
    if not value:
        raise TaskRunnerError("本地导出模式需要提供 params.source_dir。")

    raw_root = RAW_EXPORT_DIR.resolve()
    source_dir = Path(value)
    if not source_dir.is_absolute():
        source_dir = PROJECT_ROOT / source_dir
    try:
        resolved_source_dir = source_dir.resolve(strict=True)
    except OSError as exc:
        raise TaskRunnerError(f"本地导出目录不存在：{source_dir}") from exc

    if not resolved_source_dir.is_dir():
        raise TaskRunnerError(f"本地导出 source_dir 必须是目录：{resolved_source_dir}")
    if resolved_source_dir != raw_root and raw_root not in resolved_source_dir.parents:
        raise TaskRunnerError(f"本地导出 source_dir 必须位于项目 data/raw 下：{resolved_source_dir}")

    metadata_path = resolved_source_dir / "task-metadata.json"
    return resolved_source_dir, metadata_path if metadata_path.is_file() else None


def _metadata_date_value(metadata: Mapping[str, Any], key: str) -> str | None:
    date_range = _mapping(metadata.get("date_range"))
    return _text(date_range.get(key))


def _metadata_shop_id(metadata: Mapping[str, Any]) -> str | None:
    for source in _metadata_shop_sources(metadata):
        shop_id = _text(source.get("shop_id") or source.get("id"))
        if shop_id:
            return shop_id
    return None


def _metadata_shop_name(metadata: Mapping[str, Any], shop_id: str | None) -> str | None:
    fallback: str | None = None
    for source in _metadata_shop_sources(metadata):
        candidate_name = _text(source.get("shop_name") or source.get("name"))
        candidate_id = _text(source.get("shop_id") or source.get("id"))
        if candidate_name and shop_id and candidate_id == shop_id:
            return candidate_name
        fallback = fallback or candidate_name
    return fallback


def _metadata_shop_sources(metadata: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sources: list[Mapping[str, Any]] = []
    for key in ("shops", "artifacts", "items", "exports"):
        values = metadata.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, Mapping):
                sources.append(value)
                export = value.get("export")
                if isinstance(export, Mapping):
                    sources.append(export)
    shop_selection = _mapping(metadata.get("shop_selection"))
    selection_shops = shop_selection.get("shops")
    if isinstance(selection_shops, list):
        sources.extend(shop for shop in selection_shops if isinstance(shop, Mapping))
    return sources


def _metadata_export_types(metadata: Mapping[str, Any]) -> list[str]:
    export_types: list[str] = []
    for key in ("artifacts", "items", "exports"):
        values = metadata.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, Mapping):
                continue
            export = _mapping(value.get("export"))
            export_type = _text(
                value.get("export_type")
                or value.get("type")
                or export.get("export_type")
                or export.get("type")
            )
            if export_type:
                export_types.append(export_type)
    return list(dict.fromkeys(export_types))


def _require_local_export_field(
    field_name: str,
    value: str | None,
    source_dir: Path,
    expected_source: str,
) -> None:
    if value:
        return
    raise TaskRunnerError(
        f"本地导出目录 {source_dir} 缺少必要字段 {field_name}，请提供 {expected_source}。"
    )


def _parse_json_stdout(stdout: str, step_key: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TaskRunnerError(f"{step_key} 未输出可解析 JSON：{exc}") from exc
    if not isinstance(parsed, dict):
        raise TaskRunnerError(f"{step_key} 输出 JSON 不是对象。")
    return parsed


def _read_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TaskRunnerError(f"读取 {path} 失败：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise TaskRunnerError(f"{path} 不是合法 JSON：{exc}") from exc
    if not isinstance(parsed, dict):
        raise TaskRunnerError(f"{path} JSON 不是对象。")
    return parsed


def _configured_shops() -> list[dict[str, Any]]:
    path = PROJECT_ROOT / "config" / "shops.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TaskRunnerError(f"读取 config/shops.json 失败：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise TaskRunnerError(f"config/shops.json 不是合法 JSON：{exc}") from exc
    shops = data.get("shops") if isinstance(data, Mapping) else None
    return [dict(shop) for shop in shops if isinstance(shop, Mapping)] if isinstance(shops, list) else []


def _shop_by_id(shops: list[dict[str, Any]], shop_id: str) -> dict[str, Any] | None:
    for shop in shops:
        if _text(shop.get("id")) == shop_id:
            return shop
    return None


def _validate_date_range(date_from: str | None, date_to: str | None) -> None:
    if not date_from or not DATE_PATTERN.match(date_from):
        raise TaskRunnerError("开始日期必须使用 YYYY-MM-DD 格式。")
    if not date_to or not DATE_PATTERN.match(date_to):
        raise TaskRunnerError("结束日期必须使用 YYYY-MM-DD 格式。")
    if date_from > date_to:
        raise TaskRunnerError("开始日期不能晚于结束日期。")


def _normalize_types(value: Any) -> list[str]:
    if isinstance(value, str):
        types = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        types = [_text(item) or "" for item in value]
    else:
        types = []
    normalized = [item for item in types if item]
    if not normalized:
        raise TaskRunnerError("至少需要选择一个采集类型。")
    return list(dict.fromkeys(normalized))


def _is_auto_types(types: list[str]) -> bool:
    return any(item.lower() == "auto" for item in types)


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _public_command(command: list[str]) -> list[str]:
    return [str(part) for part in command]


def _tail(value: str | None) -> str:
    text = value or ""
    return text[-TASK_OUTPUT_TAIL_LENGTH:]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = _text(value)
        if text:
            return text
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_path_segment(value: Any) -> str:
    return re.sub(r"(^_+|_+$)", "", re.sub(r"[^\w.-]+", "_", str(value or "").strip()))[:120]


def _compact_timestamp() -> str:
    return datetime.utcnow().isoformat(timespec="seconds").replace("-", "").replace(":", "").replace("T", "T")


def _now() -> str:
    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")
