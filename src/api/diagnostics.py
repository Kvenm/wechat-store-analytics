from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.paths import PROJECT_ROOT


LOGGER = logging.getLogger(__name__)
DIAGNOSTIC_ROOT = PROJECT_ROOT / "data" / "raw"
RETENTION_DAYS_ENV = "WECHAT_STORE_DIAGNOSTIC_RETENTION_DAYS"
CLEANUP_INTERVAL_ENV = "WECHAT_STORE_DIAGNOSTIC_CLEANUP_INTERVAL_SECONDS"
DEFAULT_RETENTION_DAYS = 14
DEFAULT_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 90
MIN_CLEANUP_INTERVAL_SECONDS = 60
MAX_CLEANUP_INTERVAL_SECONDS = 7 * 24 * 60 * 60

_cleanup_lock = threading.Lock()
_state_lock = threading.Lock()
_cleanup_stop = threading.Event()
_cleanup_thread: threading.Thread | None = None
_last_cleanup: dict[str, Any] | None = None


def diagnostic_retention_days(environ: dict[str, str] | None = None) -> int:
    values = os.environ if environ is None else environ
    return _bounded_int(
        values.get(RETENTION_DAYS_ENV),
        default=DEFAULT_RETENTION_DAYS,
        minimum=MIN_RETENTION_DAYS,
        maximum=MAX_RETENTION_DAYS,
    )


def diagnostic_cleanup_interval_seconds(environ: dict[str, str] | None = None) -> int:
    values = os.environ if environ is None else environ
    return _bounded_int(
        values.get(CLEANUP_INTERVAL_ENV),
        default=DEFAULT_CLEANUP_INTERVAL_SECONDS,
        minimum=MIN_CLEANUP_INTERVAL_SECONDS,
        maximum=MAX_CLEANUP_INTERVAL_SECONDS,
    )


def is_diagnostic_file(path: Path) -> bool:
    name = path.name.lower()
    if name == "execution-log.jsonl":
        return True
    if name.startswith("failure-") and path.suffix.lower() in {".json", ".png"}:
        return True
    if name.startswith("control-inventory-") and path.suffix.lower() == ".json":
        return True
    return False


def cleanup_diagnostic_files(
    root: Path = DIAGNOSTIC_ROOT,
    *,
    retention_days: int | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    days = diagnostic_retention_days() if retention_days is None else max(
        MIN_RETENTION_DAYS,
        min(MAX_RETENTION_DAYS, int(retention_days)),
    )
    current_time = time.time() if now is None else float(now)
    cutoff = current_time - days * 24 * 60 * 60
    resolved_root = root.resolve()
    summary: dict[str, Any] = {
        "root": str(resolved_root),
        "retention_days": days,
        "cutoff_at": _iso_time(cutoff),
        "started_at": _iso_time(current_time),
        "completed_at": None,
        "scanned_file_count": 0,
        "diagnostic_file_count": 0,
        "deleted_file_count": 0,
        "deleted_bytes": 0,
        "retained_file_count": 0,
        "skipped_symlink_count": 0,
        "failure_count": 0,
        "errors": [],
    }
    if not resolved_root.exists():
        summary["completed_at"] = _iso_time(time.time())
        return summary

    with _cleanup_lock:
        for directory, directory_names, filenames in os.walk(resolved_root, followlinks=False):
            directory_path = Path(directory)
            retained_directories: list[str] = []
            for name in directory_names:
                candidate = directory_path / name
                if candidate.is_symlink():
                    summary["skipped_symlink_count"] += 1
                    continue
                retained_directories.append(name)
            directory_names[:] = retained_directories

            for filename in filenames:
                candidate = directory_path / filename
                summary["scanned_file_count"] += 1
                try:
                    if candidate.is_symlink():
                        summary["skipped_symlink_count"] += 1
                        continue
                    if not is_diagnostic_file(candidate):
                        continue
                    summary["diagnostic_file_count"] += 1
                    stat = candidate.stat()
                    if stat.st_mtime >= cutoff:
                        summary["retained_file_count"] += 1
                        continue
                    candidate.unlink()
                    summary["deleted_file_count"] += 1
                    summary["deleted_bytes"] += stat.st_size
                except OSError as exc:
                    summary["failure_count"] += 1
                    if len(summary["errors"]) < 10:
                        summary["errors"].append({"path": str(candidate), "error": str(exc)})

    summary["completed_at"] = _iso_time(time.time())
    return summary


def start_diagnostic_cleanup_thread() -> dict[str, Any]:
    global _cleanup_thread
    with _state_lock:
        if _cleanup_thread is not None and _cleanup_thread.is_alive():
            pass
        else:
            _cleanup_stop.clear()
            _cleanup_thread = threading.Thread(
                target=_cleanup_loop,
                daemon=True,
                name="wechat-store-diagnostic-cleanup",
            )
            _cleanup_thread.start()
    return diagnostic_cleanup_status()


def diagnostic_cleanup_status() -> dict[str, Any]:
    with _state_lock:
        thread_alive = _cleanup_thread is not None and _cleanup_thread.is_alive()
        last_cleanup = dict(_last_cleanup) if _last_cleanup is not None else None
    return {
        "root": str(DIAGNOSTIC_ROOT.resolve()),
        "retention_days": diagnostic_retention_days(),
        "cleanup_interval_seconds": diagnostic_cleanup_interval_seconds(),
        "cleanup_thread_alive": thread_alive,
        "last_cleanup": last_cleanup,
        "protected_file_types": ["zip", "xls", "xlsx", "csv", "task-metadata.json", "export-results.json", "artifacts-manifest.json"],
    }


def _cleanup_loop() -> None:
    global _last_cleanup
    while not _cleanup_stop.is_set():
        try:
            result = cleanup_diagnostic_files()
        except Exception as exc:  # pragma: no cover - final safety net for daemon continuity
            result = {
                "started_at": _iso_time(time.time()),
                "completed_at": _iso_time(time.time()),
                "failure_count": 1,
                "errors": [{"error": str(exc)}],
            }
            LOGGER.exception("Diagnostic cleanup failed")
        with _state_lock:
            _last_cleanup = result
        if _cleanup_stop.wait(diagnostic_cleanup_interval_seconds()):
            break


def _bounded_int(value: str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _iso_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")
