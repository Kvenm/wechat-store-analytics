from __future__ import annotations

import base64
import binascii
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from shared.ids import new_id
from shared.paths import PROJECT_ROOT, ensure_dir


COLLECTOR_JOBS_DIR = PROJECT_ROOT / "data" / "collector_jobs"
COLLECTOR_UPLOAD_DIR = PROJECT_ROOT / "data" / "raw" / "collector_uploads"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ALLOWED_UPLOAD_SUFFIXES = {".csv", ".xlsx", ".xls", ".zip", ".json", ".png"}
ALLOWED_UPLOAD_NAMES = {"task-metadata.json", "artifacts-manifest.json"}
_lock = threading.RLock()


def create_job(payload: Mapping[str, Any]) -> dict[str, Any]:
    shop_id = _text(payload.get("shop_id"))
    shop_name = _text(payload.get("shop_name")) or shop_id
    date_from = _text(payload.get("date_from") or payload.get("from"))
    date_to = _text(payload.get("date_to") or payload.get("to"))
    types = _types(payload.get("types") or ["orders"])
    _validate_job(shop_id, date_from, date_to, types)

    job_id = new_id("collector_job")
    now = _now()
    job = {
        "job_id": job_id,
        "status": "pending",
        "shop_id": shop_id,
        "shop_name": shop_name,
        "date_from": date_from,
        "date_to": date_to,
        "types": types,
        "headless": bool(payload.get("headless", False)),
        "created_at": now,
        "updated_at": now,
    }
    with _lock:
        _write_job(job)
    return job


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        jobs = [_read_json(path) for path in _jobs_dir().glob("*.json")]
    return sorted(jobs, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)[:limit]


def claim_job(collector_id: str) -> dict[str, Any] | None:
    collector = _text(collector_id)
    if not collector:
        raise ValueError("collector_id is required")
    with _lock:
        pending = [
            job for job in list_jobs(limit=500)
            if job.get("status") == "pending"
        ]
        if not pending:
            return None
        job = sorted(pending, key=lambda item: str(item.get("created_at") or ""))[0]
        now = _now()
        job.update({"status": "running", "collector_id": collector, "started_at": now, "updated_at": now})
        _write_job(job)
        return job


def heartbeat(collector_id: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    collector = _text(collector_id)
    if not collector:
        raise ValueError("collector_id is required")
    data = {
        "collector_id": collector,
        "status": _text((payload or {}).get("status")) or "online",
        "version": _text((payload or {}).get("version")),
        "message": _text((payload or {}).get("message")),
        "updated_at": _now(),
    }
    path = ensure_dir(COLLECTOR_JOBS_DIR / "collectors") / f"{_safe_segment(collector)}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return data


def complete_job(job_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    status = _text(payload.get("status")) or "completed"
    with _lock:
        job = _load_job(job_id)
        collector_id = _text(payload.get("collector_id"))
        if job.get("collector_id") and collector_id != job.get("collector_id"):
            raise ValueError("collector_id does not match claimed job")
        if status != "completed":
            job.update({"status": "failed", "message": _text(payload.get("message")), "updated_at": _now()})
            _write_job(job)
            return {"job": job, "source_dir": None}

        source_dir = _save_uploaded_files(job["job_id"], payload.get("files") or [])
        job.update({
            "status": "uploaded",
            "message": _text(payload.get("message")),
            "source_dir": str(source_dir),
            "uploaded_at": _now(),
            "updated_at": _now(),
        })
        _write_job(job)
        return {"job": job, "source_dir": str(source_dir)}


def attach_import_task(job_id: str, task: Mapping[str, Any]) -> dict[str, Any]:
    with _lock:
        job = _load_job(job_id)
        job.update({
            "status": "importing",
            "import_task_id": task.get("id") or task.get("task_id"),
            "import_task": _compact_import_task(task),
            "updated_at": _now(),
        })
        _write_job(job)
        return job


def sync_import_task(job_id: str, task: Mapping[str, Any]) -> dict[str, Any]:
    with _lock:
        job = _load_job(job_id)
        task_snapshot = _compact_import_task(task)
        state = _text(task_snapshot.get("state") or task_snapshot.get("status"))
        updates: dict[str, Any] = {
            "import_task_id": task_snapshot.get("id") or task_snapshot.get("task_id") or job.get("import_task_id"),
            "import_task": task_snapshot,
            "updated_at": _now(),
        }
        if state == "completed":
            updates["status"] = "completed"
            updates["import_completed_at"] = task_snapshot.get("completed_at") or updates["updated_at"]
        elif state == "failed":
            updates["status"] = "failed"
            updates["import_error"] = _task_error_message(task_snapshot)
        else:
            updates["status"] = "importing"

        job.update(updates)
        _write_job(job)
        return job


def mark_import_error(job_id: str, message: str) -> dict[str, Any]:
    with _lock:
        job = _load_job(job_id)
        job.update({"status": "uploaded", "import_error": message, "updated_at": _now()})
        _write_job(job)
        return job


def _save_uploaded_files(job_id: str, files: Any) -> Path:
    if not isinstance(files, list) or not files:
        raise ValueError("completed collector job must upload at least one file")
    upload_dir = ensure_dir(COLLECTOR_UPLOAD_DIR / _safe_segment(job_id))
    for item in files:
        if not isinstance(item, Mapping):
            raise ValueError("uploaded file item must be an object")
        relative_path = _safe_relative_path(_text(item.get("path") or item.get("filename") or item.get("name")))
        target = upload_dir / relative_path
        if target.suffix.lower() not in ALLOWED_UPLOAD_SUFFIXES and target.name not in ALLOWED_UPLOAD_NAMES:
            raise ValueError(f"unsupported upload file type: {relative_path}")
        try:
            content = base64.b64decode(_text(item.get("content_base64")), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"invalid base64 for {relative_path}") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return upload_dir


def _validate_job(shop_id: str, date_from: str, date_to: str, types: list[str]) -> None:
    if not shop_id:
        raise ValueError("shop_id is required")
    if not DATE_PATTERN.match(date_from or "") or not DATE_PATTERN.match(date_to or ""):
        raise ValueError("date_from and date_to must use YYYY-MM-DD")
    if date_from > date_to:
        raise ValueError("date_from cannot be later than date_to")
    if not types:
        raise ValueError("types is required")


def _safe_relative_path(value: str) -> Path:
    if not value:
        raise ValueError("uploaded file path is required")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe upload path: {value}")
    return path


def _load_job(job_id: str) -> dict[str, Any]:
    path = _job_path(job_id)
    if not path.is_file():
        raise ValueError(f"collector job not found: {job_id}")
    return _read_json(path)


def _write_job(job: Mapping[str, Any]) -> None:
    _job_path(str(job["job_id"])).write_text(json.dumps(dict(job), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _job_path(job_id: str) -> Path:
    return _jobs_dir() / f"{_safe_segment(job_id)}.json"


def _jobs_dir() -> Path:
    return ensure_dir(COLLECTOR_JOBS_DIR / "jobs")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _types(value: Any) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list):
        items = value
    else:
        items = []
    return [item for item in (_text(item) for item in items) if item]


def _safe_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "unknown").strip("._") or "unknown"


def _compact_import_task(task: Mapping[str, Any]) -> dict[str, Any]:
    steps = task.get("steps") if isinstance(task.get("steps"), Mapping) else {}
    return {
        "id": task.get("id"),
        "task_id": task.get("task_id"),
        "state": task.get("state"),
        "status": task.get("status"),
        "task_name": task.get("task_name"),
        "source_type": task.get("source_type"),
        "date_range": task.get("date_range"),
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at"),
        "error": task.get("error"),
        "result": _compact_import_result(task.get("result")),
        "steps": {
            str(key): {
                "status": value.get("status"),
                "started_at": value.get("started_at"),
                "completed_at": value.get("completed_at"),
            }
            for key, value in steps.items()
            if isinstance(value, Mapping)
        },
    }


def _compact_import_result(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, Mapping):
        return None
    return {
        "collection_task_id": result.get("collection_task_id"),
        "artifact_count": result.get("artifact_count"),
        "analysis_run_id": result.get("analysis_run_id"),
        "report": _compact_report_result(result.get("report")),
    }


def _compact_report_result(report: Any) -> dict[str, Any] | None:
    if not isinstance(report, Mapping):
        return None
    return {
        "report_id": report.get("report_id"),
        "analysis_run_id": report.get("analysis_run_id"),
        "markdown_path": report.get("markdown_path"),
        "summary_csv_path": report.get("summary_csv_path"),
    }


def _task_error_message(task: Mapping[str, Any]) -> str:
    error = task.get("error")
    if isinstance(error, Mapping):
        return _text(error.get("message")) or _text(error.get("type"))
    return _text(error)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
