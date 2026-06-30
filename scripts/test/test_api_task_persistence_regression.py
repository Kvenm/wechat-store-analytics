#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from api.repository import LocalRepository  # noqa: E402
from warehouse.repository import (  # noqa: E402
    connect,
    ensure_shop,
    initialize_database,
    upsert_api_task_run,
    upsert_api_task_step,
)


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_api_task_run_and_steps_survive_runtime_memory_loss,
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
        print(f"{failed} API task persistence regression test(s) failed.")
        return 1

    print(f"{len(tests)} API task persistence regression tests passed.")
    return 0


def test_api_task_run_and_steps_survive_runtime_memory_loss() -> None:
    with tempfile.TemporaryDirectory(prefix="wechat_api_task_persistence_") as temp_dir:
        db_path = Path(temp_dir) / "tasks.sqlite"
        task = api_task()
        with connect(db_path) as conn:
            initialize_database(conn)
            ensure_shop(conn, task["shop_id"], task["shop_name_snapshot"])
            upsert_api_task_run(conn, task)
            upsert_api_task_step(
                conn,
                task_id=task["task_id"],
                step_key="analyze",
                step={
                    "status": "completed",
                    "started_at": "2026-06-29 10:00:01",
                    "completed_at": "2026-06-29 10:00:02",
                    "parsed": {"analysis_run_id": "ar_persisted"},
                },
            )

        repo = LocalRepository(db_path=db_path)
        loaded = repo.get_task(task["task_id"])
        listed = repo.list_tasks()

    assert loaded is not None
    assert loaded["source"] == "api_task_runs"
    assert loaded["state"] == "completed"
    assert loaded["date_range"] == {"from": "2026-06-01", "to": "2026-06-03"}
    assert loaded["types"] == ["orders"]
    assert loaded["result"]["analysis_run_id"] == "ar_persisted"
    assert loaded["steps"]["analyze"]["parsed"]["analysis_run_id"] == "ar_persisted"
    assert listed and listed[0]["task_id"] == task["task_id"]


def api_task() -> dict[str, object]:
    return {
        "id": "ui_collect_shop-1_2026-06-01_2026-06-03_persisted",
        "task_id": "ui_collect_shop-1_2026-06-01_2026-06-03_persisted",
        "shop_id": "shop-1",
        "shop_name_snapshot": "Shop 1",
        "task_name": "Persisted API Task",
        "source_type": "local_export",
        "state": "completed",
        "status": "completed",
        "created_at": "2026-06-29 10:00:00",
        "started_at": "2026-06-29 10:00:01",
        "completed_at": "2026-06-29 10:00:02",
        "date_range": {"from": "2026-06-01", "to": "2026-06-03"},
        "spec": {
            "from": "2026-06-01",
            "to": "2026-06-03",
            "types": ["orders"],
            "headless": False,
        },
        "result": {"analysis_run_id": "ar_persisted"},
        "steps": {},
    }


if __name__ == "__main__":
    raise SystemExit(main())
