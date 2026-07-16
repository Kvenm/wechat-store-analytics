#!/usr/bin/env python3
from __future__ import annotations

import base64
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import api.app as admin_app  # noqa: E402
from api import collector_jobs  # noqa: E402


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_collector_job_claim_upload_starts_local_import,
        test_collector_job_import_status_syncs_from_completed_task,
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
        print(f"{failed} collector job regression test(s) failed.")
        return 1
    print(f"{len(tests)} collector job regression tests passed.")
    return 0


def test_collector_job_claim_upload_starts_local_import() -> None:
    with tempfile.TemporaryDirectory(prefix="collector_jobs_regression_") as temp_dir:
        temp_path = Path(temp_dir)
        original_jobs_dir = collector_jobs.COLLECTOR_JOBS_DIR
        original_upload_dir = collector_jobs.COLLECTOR_UPLOAD_DIR
        original_start = admin_app.start_web_export_task
        calls = []

        def fake_start_web_export_task(payload):
            calls.append(payload)
            return {"id": "task-local-import-001", "status": "queued"}

        collector_jobs.COLLECTOR_JOBS_DIR = temp_path / "jobs"
        collector_jobs.COLLECTOR_UPLOAD_DIR = temp_path / "raw" / "collector_uploads"
        admin_app.start_web_export_task = fake_start_web_export_task
        try:
            heartbeat = admin_app.collector_heartbeat(admin_app.CollectorHeartbeatRequest(collector_id="client-a"))
            assert heartbeat["status"] == "ok"

            created = admin_app.create_collector_job(
                admin_app.CollectorJobRequest(
                    shop_id="shop-001",
                    shop_name="测试店铺",
                    date_from="2026-06-01",
                    date_to="2026-06-07",
                )
            )
            assert created["status"] == "ok"
            job_id = created["data"]["job_id"]

            claimed = admin_app.claim_collector_job(admin_app.CollectorClaimRequest(collector_id="client-a"))
            assert claimed["data"]["job_id"] == job_id
            assert claimed["data"]["status"] == "running"

            completed = admin_app.complete_collector_job(
                job_id,
                admin_app.CollectorCompleteRequest(
                    collector_id="client-a",
                    files=[
                        admin_app.CollectorUploadFile(
                            path="orders.csv",
                            content_base64=base64.b64encode(
                                "订单号,下单时间,订单状态,支付金额\norder-001,2026-06-01 10:00:00,已完成,10.00\n".encode("utf-8")
                            ).decode("ascii"),
                        )
                    ],
                ),
            )
            assert completed["status"] == "ok"
            assert completed["data"]["import_task"]["id"] == "task-local-import-001"
            assert Path(completed["data"]["source_dir"], "orders.csv").is_file()
            assert calls[0]["source_type"] == "local_export"
            assert calls[0]["params"]["types"] == ["orders"]
        finally:
            collector_jobs.COLLECTOR_JOBS_DIR = original_jobs_dir
            collector_jobs.COLLECTOR_UPLOAD_DIR = original_upload_dir
            admin_app.start_web_export_task = original_start


def test_collector_job_import_status_syncs_from_completed_task() -> None:
    with tempfile.TemporaryDirectory(prefix="collector_jobs_regression_") as temp_dir:
        temp_path = Path(temp_dir)
        original_jobs_dir = collector_jobs.COLLECTOR_JOBS_DIR
        original_upload_dir = collector_jobs.COLLECTOR_UPLOAD_DIR
        original_repository = admin_app.repository
        collector_jobs.COLLECTOR_JOBS_DIR = temp_path / "jobs"
        collector_jobs.COLLECTOR_UPLOAD_DIR = temp_path / "raw" / "collector_uploads"

        class FakeRepository:
            def get_task(self, task_id: str):
                assert task_id == "stale-import-task"
                return None

            def list_tasks(self):
                return [{
                    "id": "task-local-import-002",
                    "task_id": "task-local-import-002",
                    "state": "completed",
                    "status": "completed",
                    "task_name": "本地采集助手订单导入",
                    "source_type": "local_export",
                    "source_dir": "/tmp/collector-source",
                    "completed_at": "2026-06-07 10:00:00",
                    "steps": {
                        "collect": {"status": "completed"},
                        "import_files": {"status": "completed"},
                        "report": {"status": "completed"},
                    },
                    "result": {
                        "analysis_run_id": "ar_sync",
                        "report": {"report_id": "sr_sync", "markdown_path": "/tmp/report.md"},
                    },
                }]

        admin_app.repository = lambda: FakeRepository()
        try:
            job = collector_jobs.create_job(
                {
                    "shop_id": "shop-001",
                    "shop_name": "测试店铺",
                    "date_from": "2026-06-01",
                    "date_to": "2026-06-07",
                }
            )
            importing_job = collector_jobs.attach_import_task(
                job["job_id"],
                {"id": "stale-import-task", "task_id": "stale-import-task", "state": "queued", "status": "queued"},
            )
            importing_job["source_dir"] = "/tmp/collector-source"

            synced = admin_app.sync_collector_import_jobs([importing_job])

            assert synced[0]["status"] == "completed"
            assert synced[0]["import_task_id"] == "task-local-import-002"
            assert synced[0]["import_completed_at"] == "2026-06-07 10:00:00"
            assert synced[0]["import_task"]["result"]["analysis_run_id"] == "ar_sync"
            assert synced[0]["import_task"]["result"]["report"]["report_id"] == "sr_sync"
        finally:
            collector_jobs.COLLECTOR_JOBS_DIR = original_jobs_dir
            collector_jobs.COLLECTOR_UPLOAD_DIR = original_upload_dir
            admin_app.repository = original_repository


if __name__ == "__main__":
    raise SystemExit(main())
