#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import api.app as admin_app  # noqa: E402
from api.task_runner import TaskRunnerError  # noqa: E402


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_cancel_endpoint_returns_cancelled_snapshot,
        test_cancel_endpoint_maps_missing_and_terminal_tasks,
        test_admin_ui_exposes_cancel_flow_and_cancelled_state,
        test_cancelled_export_can_still_download_registered_artifacts,
    )
    failures = 0
    for test in tests:
        try:
            test()
        except Exception:
            failures += 1
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
        else:
            print(f"PASS {test.__name__}")
    print(f"task cancel UI regression: {len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


def test_cancel_endpoint_returns_cancelled_snapshot() -> None:
    expected = {
        "id": "cancel-ui-task",
        "state": "cancelled",
        "status": "cancelled",
        "can_cancel": False,
        "result": {"mode": "export_only", "files_preserved": True},
    }
    with patched(cancel_runtime_task=lambda task_id: expected if task_id == "cancel-ui-task" else None):
        result = admin_app.cancel_task("cancel-ui-task")

    assert result["status"] == "ok"
    assert result["data"] == expected
    assert "文件会继续保留" in result["message"]


def test_cancel_endpoint_maps_missing_and_terminal_tasks() -> None:
    for message, status_code in (
        ("任务不存在：missing。", 404),
        ("只能取消排队中或执行中的任务，当前状态为 completed。", 409),
    ):
        def fail(_task_id: str, *, error_message: str = message) -> dict[str, Any]:
            raise TaskRunnerError(error_message)

        with patched(cancel_runtime_task=fail):
            result = admin_app.cancel_task("task-id")

        assert result.status_code == status_code
        payload = json.loads(result.body)
        assert payload["status"] == "error"
        assert payload["message"] == message


def test_admin_ui_exposes_cancel_flow_and_cancelled_state() -> None:
    html = admin_app.root().body.decode("utf-8")
    for fragment in (
        'id="cancelActiveTask" class="danger" hidden',
        'function syncCancelTaskButton(task)',
        'async function cancelActiveTask()',
        '/cancel`, {',
        'method: "POST"',
        'cancelled: "已取消"',
        '["completed", "failed", "cancelled"]',
        'pollExportVerificationStatus({ forceClose: true })',
        '$("cancelActiveTask")?.addEventListener("click", cancelActiveTask)',
        '取消前已经生成的文件会保留',
    ):
        assert fragment in html, fragment


def test_cancelled_export_can_still_download_registered_artifacts() -> None:
    original_root = admin_app.TASK_ARTIFACT_DOWNLOAD_ROOT
    with tempfile.TemporaryDirectory(prefix="cancelled-artifact-") as temp_dir:
        root = Path(temp_dir) / "raw"
        source_dir = root / "cancelled-task"
        source_dir.mkdir(parents=True)
        artifact = source_dir / "partial-orders.xlsx"
        artifact.write_bytes(b"partial export")
        task = {
            "state": "cancelled",
            "source_dir": str(source_dir),
            "result": {
                "mode": "export_only",
                "source_dir": str(source_dir),
                "artifacts": [
                    {
                        "status": "completed",
                        "source_kind": "export_file",
                        "saved_path": str(artifact),
                    }
                ],
            },
        }
        try:
            admin_app.TASK_ARTIFACT_DOWNLOAD_ROOT = root
            resolved = admin_app.resolve_task_artifact_path(task, 0)
        finally:
            admin_app.TASK_ARTIFACT_DOWNLOAD_ROOT = original_root

    assert resolved == artifact.resolve()


@contextmanager
def patched(**updates: Any) -> Iterator[None]:
    previous = {name: getattr(admin_app, name) for name in updates}
    try:
        for name, value in updates.items():
            setattr(admin_app, name, value)
        yield
    finally:
        for name, value in previous.items():
            setattr(admin_app, name, value)


if __name__ == "__main__":
    raise SystemExit(main())
