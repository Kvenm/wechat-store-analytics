#!/usr/bin/env python3
from __future__ import annotations

import sys
import sqlite3
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import api.task_runner as task_runner  # noqa: E402


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_queued_task_cancellation_is_immediate_and_idempotent,
        test_running_step_terminates_process_and_preserves_written_file,
        test_concurrent_repeated_cancellation_is_safe,
        test_late_worker_failure_cannot_replace_cancelled_state,
        test_completed_task_cannot_be_cancelled,
        test_cancelled_state_and_steps_are_persisted,
    )
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception:  # pragma: no cover - standalone regression reporting
            failures += 1
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    print(f"task cancellation regression: {len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


@contextmanager
def isolated_runtime(task: dict[str, object]) -> Iterator[None]:
    original_tasks = task_runner._tasks
    original_processes = task_runner._active_processes
    original_persist_task = task_runner._persist_task
    original_persist_step = task_runner._persist_step
    try:
        task_runner._tasks = {str(task["id"]): task}
        task_runner._active_processes = {}
        task_runner._persist_task = lambda _task: None
        task_runner._persist_step = lambda _task_id, _step_key, _step: None
        yield
    finally:
        for _step_key, process in list(task_runner._active_processes.values()):
            task_runner._terminate_process(process, grace_seconds=0.1)
        task_runner._tasks = original_tasks
        task_runner._active_processes = original_processes
        task_runner._persist_task = original_persist_task
        task_runner._persist_step = original_persist_step


def make_task() -> dict[str, object]:
    return task_runner._build_runtime_task(
        {
            "shop_id": "cancel-test-shop",
            "shop_name": "取消回归店铺",
            "task_name": "取消回归任务",
            "source_type": "web_export",
            "mode": "export_only",
            "from": "2026-07-01",
            "to": "2026-07-14",
            "types": ["visible_tables"],
            "targets": ["orders"],
            "headless": True,
        }
    )


def test_queued_task_cancellation_is_immediate_and_idempotent() -> None:
    task = make_task()
    with isolated_runtime(task):
        first = task_runner.cancel_runtime_task(str(task["id"]))
        second = task_runner.cancel_runtime_task(str(task["id"]))

    assert first["state"] == "cancelled"
    assert first["status"] == "cancelled"
    assert first["can_cancel"] is False
    assert first["error"]["code"] == "TASK_CANCELLED"
    assert first["result"]["files_preserved"] is True
    assert first["cancelled_at"] == second["cancelled_at"]
    assert all(step["status"] == "skipped" for step in first["steps"].values())


def test_running_step_terminates_process_and_preserves_written_file() -> None:
    task = make_task()
    task["state"] = "running"
    task["status"] = "running"
    task["started_at"] = task_runner._now()
    worker_errors: list[BaseException] = []

    with tempfile.TemporaryDirectory(prefix="task-cancel-regression-") as temp_dir:
        artifact_path = Path(temp_dir) / "partial-export.xlsx"
        code = (
            "from pathlib import Path; import sys, time; "
            "Path(sys.argv[1]).write_text('partial export', encoding='utf-8'); "
            "time.sleep(30)"
        )

        def run_step() -> None:
            try:
                task_runner._run_step(
                    str(task["id"]),
                    "collect",
                    [sys.executable, "-c", code, str(artifact_path)],
                    timeout_seconds=60,
                )
            except BaseException as exc:  # captured for assertion in main thread
                worker_errors.append(exc)

        with isolated_runtime(task):
            worker = threading.Thread(target=run_step, daemon=True)
            worker.start()
            assert wait_until(
                lambda: artifact_path.exists() and str(task["id"]) in task_runner._active_processes,
                timeout=5,
            ), "test subprocess did not start"
            cancelled = task_runner.cancel_runtime_task(str(task["id"]))
            worker.join(timeout=5)
            snapshot = task_runner.get_runtime_task(str(task["id"]))
            active_after = str(task["id"]) in task_runner._active_processes

        assert not worker.is_alive(), "cancelled subprocess worker did not stop"
        assert worker_errors and isinstance(worker_errors[0], task_runner.TaskCancelledError)
        assert snapshot is not None
        assert snapshot["state"] == "cancelled"
        assert snapshot["steps"]["collect"]["status"] == "cancelled"
        assert cancelled["cancelled_step"] == "collect"
        assert not active_after
        assert artifact_path.read_text(encoding="utf-8") == "partial export"


def test_concurrent_repeated_cancellation_is_safe() -> None:
    task = make_task()
    task["state"] = "running"
    task["status"] = "running"
    results: list[dict[str, object]] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def cancel() -> None:
        try:
            barrier.wait(timeout=3)
            results.append(task_runner.cancel_runtime_task(str(task["id"])))
        except BaseException as exc:
            errors.append(exc)

    with isolated_runtime(task):
        threads = [threading.Thread(target=cancel) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

    assert not errors
    assert len(results) == 8
    assert {result["state"] for result in results} == {"cancelled"}
    assert len({result["cancelled_at"] for result in results}) == 1


def test_late_worker_failure_cannot_replace_cancelled_state() -> None:
    task = make_task()
    task["state"] = "running"
    task["status"] = "running"
    task["steps"]["collect"]["status"] = "running"
    with isolated_runtime(task):
        task_runner.cancel_runtime_task(str(task["id"]))
        task_runner._fail_task(str(task["id"]), task_runner.TaskRunnerError("late failure"))
        task_runner._update_task(
            str(task["id"]),
            state="completed",
            status="completed",
            completed_at=task_runner._now(),
            result={"outcome": "exported"},
            error=None,
        )
        snapshot = task_runner.get_runtime_task(str(task["id"]))

    assert snapshot is not None
    assert snapshot["state"] == "cancelled"
    assert snapshot["status"] == "cancelled"
    assert snapshot["steps"]["collect"]["status"] == "cancelled"
    assert snapshot["result"]["outcome"] == "cancelled"


def test_completed_task_cannot_be_cancelled() -> None:
    task = make_task()
    task["state"] = "completed"
    task["status"] = "completed"
    with isolated_runtime(task):
        try:
            task_runner.cancel_runtime_task(str(task["id"]))
        except task_runner.TaskRunnerError as exc:
            message = str(exc)
        else:
            raise AssertionError("completed task cancellation should be rejected")

    assert "只能取消排队中或执行中的任务" in message
    assert "completed" in message


def test_cancelled_state_and_steps_are_persisted() -> None:
    task = make_task()
    original_tasks = task_runner._tasks
    original_processes = task_runner._active_processes
    original_db_path = task_runner.DEFAULT_DB_PATH
    with tempfile.TemporaryDirectory(prefix="task-cancel-db-") as temp_dir:
        db_path = Path(temp_dir) / "warehouse.db"
        try:
            task_runner._tasks = {str(task["id"]): task}
            task_runner._active_processes = {}
            task_runner.DEFAULT_DB_PATH = db_path
            task_runner._persist_task(task)
            task_runner.cancel_runtime_task(str(task["id"]))
            with sqlite3.connect(db_path) as conn:
                run_row = conn.execute(
                    "SELECT state, status, result_json, error_json FROM api_task_runs WHERE task_id = ?",
                    (str(task["id"]),),
                ).fetchone()
                step_rows = conn.execute(
                    "SELECT step_key, status FROM api_task_run_steps WHERE task_id = ? ORDER BY step_key",
                    (str(task["id"]),),
                ).fetchall()
        finally:
            task_runner._tasks = original_tasks
            task_runner._active_processes = original_processes
            task_runner.DEFAULT_DB_PATH = original_db_path

    assert run_row is not None
    assert run_row[0:2] == ("cancelled", "cancelled")
    assert '"outcome":"cancelled"' in run_row[2]
    assert '"code":"TASK_CANCELLED"' in run_row[3]
    assert len(step_rows) == len(task_runner.STEP_KEYS)
    assert {status for _step_key, status in step_rows} == {"skipped"}


def wait_until(predicate: Callable[[], bool], *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


if __name__ == "__main__":
    raise SystemExit(main())
