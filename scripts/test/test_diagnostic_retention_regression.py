#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from api import diagnostics  # noqa: E402
from api import app as admin_app  # noqa: E402


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_old_diagnostics_are_deleted_and_business_files_are_preserved,
        test_symlinks_are_not_followed_or_deleted,
        test_environment_bounds_and_cleanup_summary,
        test_diagnostic_status_endpoint_contract,
    )
    failed = 0
    for test in tests:
        try:
            test()
        except Exception:
            failed += 1
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
        else:
            print(f"PASS {test.__name__}")
    if failed:
        print(f"{failed} diagnostic retention regression test(s) failed.")
        return 1
    print(f"{len(tests)} diagnostic retention regression tests passed.")
    return 0


def test_old_diagnostics_are_deleted_and_business_files_are_preserved() -> None:
    now = time.time()
    old_time = now - 20 * 24 * 60 * 60
    with tempfile.TemporaryDirectory(prefix="diagnostic-retention-") as temp_dir:
        root = Path(temp_dir) / "data" / "raw"
        task_dir = root / "task-1" / "orders"
        task_dir.mkdir(parents=True)
        old_diagnostics = [
            root / "task-1" / "execution-log.jsonl",
            task_dir / "failure-01-old.json",
            task_dir / "failure-01-old.png",
            task_dir / "control-inventory-old.json",
        ]
        new_diagnostic = task_dir / "failure-02-new.json"
        protected = [
            root / "task-1" / "download.zip",
            root / "task-1" / "orders.xlsx",
            root / "task-1" / "task-metadata.json",
            root / "task-1" / "export-results.json",
            root / "task-1" / "artifacts-manifest.json",
        ]
        for path in [*old_diagnostics, new_diagnostic, *protected]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"diagnostic-test")
        for path in [*old_diagnostics, *protected]:
            os.utime(path, (old_time, old_time))

        summary = diagnostics.cleanup_diagnostic_files(root, retention_days=14, now=now)

        assert all(not path.exists() for path in old_diagnostics)
        assert new_diagnostic.exists()
        assert all(path.exists() for path in protected)
        assert summary["deleted_file_count"] == len(old_diagnostics)
        assert summary["retained_file_count"] == 1
        assert summary["failure_count"] == 0


def test_symlinks_are_not_followed_or_deleted() -> None:
    if not hasattr(os, "symlink"):
        return
    now = time.time()
    old_time = now - 20 * 24 * 60 * 60
    with tempfile.TemporaryDirectory(prefix="diagnostic-symlink-") as temp_dir:
        base = Path(temp_dir)
        root = base / "raw"
        outside = base / "outside"
        root.mkdir()
        outside.mkdir()
        outside_failure = outside / "failure-outside.json"
        outside_failure.write_text("outside", encoding="utf-8")
        os.utime(outside_failure, (old_time, old_time))
        file_link = root / "failure-linked.json"
        dir_link = root / "linked-task"
        file_link.symlink_to(outside_failure)
        dir_link.symlink_to(outside, target_is_directory=True)

        summary = diagnostics.cleanup_diagnostic_files(root, retention_days=14, now=now)

        assert file_link.is_symlink()
        assert dir_link.is_symlink()
        assert outside_failure.exists()
        assert summary["deleted_file_count"] == 0
        assert summary["skipped_symlink_count"] == 2


def test_environment_bounds_and_cleanup_summary() -> None:
    assert diagnostics.diagnostic_retention_days({}) == 14
    assert diagnostics.diagnostic_retention_days({diagnostics.RETENTION_DAYS_ENV: "0"}) == 1
    assert diagnostics.diagnostic_retention_days({diagnostics.RETENTION_DAYS_ENV: "200"}) == 90
    assert diagnostics.diagnostic_retention_days({diagnostics.RETENTION_DAYS_ENV: "bad"}) == 14
    assert diagnostics.diagnostic_cleanup_interval_seconds({}) == 21600
    assert diagnostics.diagnostic_cleanup_interval_seconds({diagnostics.CLEANUP_INTERVAL_ENV: "1"}) == 60
    assert diagnostics.diagnostic_cleanup_interval_seconds({diagnostics.CLEANUP_INTERVAL_ENV: "9999999"}) == 604800
    with tempfile.TemporaryDirectory(prefix="diagnostic-summary-") as temp_dir:
        summary = diagnostics.cleanup_diagnostic_files(Path(temp_dir), retention_days=14, now=1_700_000_000)
    assert summary["retention_days"] == 14
    assert summary["scanned_file_count"] == 0
    assert summary["deleted_file_count"] == 0
    assert summary["completed_at"]


def test_diagnostic_status_endpoint_contract() -> None:
    result = admin_app.diagnostics_status()
    assert result["status"] == "ok"
    assert result["data"]["retention_days"] >= 1
    assert result["data"]["cleanup_interval_seconds"] >= 60
    assert "zip" in result["data"]["protected_file_types"]
    assert any(getattr(route, "path", None) == "/diagnostics/status" for route in admin_app.app.routes)


if __name__ == "__main__":
    raise SystemExit(main())
