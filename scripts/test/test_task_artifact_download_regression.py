#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import base64
import hashlib
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import api.app as admin_app  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.responses import FileResponse, Response  # noqa: E402


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_completed_export_artifact_downloads,
        test_bundle_and_page_snapshot_artifacts_download,
        test_persisted_download_record_is_used,
        test_out_of_range_and_incomplete_artifacts_are_rejected,
        test_failed_export_keeps_completed_artifact_downloadable,
        test_task_without_completed_state_is_rejected,
        test_download_uses_middleware_auth_snapshot,
        test_download_fails_closed_without_admin_auth,
        test_path_traversal_and_cross_task_files_are_rejected,
        test_symlink_and_unsupported_files_are_rejected,
        test_size_and_hash_mismatch_are_rejected,
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
        print(f"{failed} task artifact download regression test(s) failed.")
        return 1
    print(f"{len(tests)} task artifact download regression tests passed.")
    return 0


def test_completed_export_artifact_downloads() -> None:
    with download_fixture() as fixture:
        task, file_path = fixture.valid_task()
        with patched_download_task(task):
            result = admin_app.download_task_artifact(authenticated_request(), "download-task", 0)

        assert isinstance(result, FileResponse)
        assert Path(result.path).resolve() == file_path.resolve()
        assert result.status_code == 200
        assert result.headers["cache-control"] == "private, no-store"
        assert result.headers["x-content-type-options"] == "nosniff"
        assert "attachment" in result.headers["content-disposition"]


def test_bundle_and_page_snapshot_artifacts_download() -> None:
    with download_fixture() as fixture:
        task, _ = fixture.valid_task()
        bundle_path = fixture.source_dir / "微信小店数据导出.zip"
        snapshot_dir = fixture.source_dir / "compass_buyer_profile"
        snapshot_dir.mkdir()
        snapshot_path = snapshot_dir / "买家人群特征.csv"
        bundle_path.write_bytes(b"bundle")
        snapshot_path.write_text("画像", encoding="utf-8")
        task["result"]["artifacts"] = [
            {
                "status": "completed",
                "source_kind": "export_bundle",
                "source_type": "export_bundle",
                "saved_path": str(bundle_path),
                "relative_path": str(bundle_path.relative_to(fixture.source_dir)),
                "size_bytes": bundle_path.stat().st_size,
                "sha256": sha256(bundle_path),
            },
            {
                "status": "completed",
                "source_kind": "page_snapshot",
                "source_type": "page_snapshot",
                "saved_path": str(snapshot_path),
                "relative_path": str(snapshot_path.relative_to(fixture.source_dir)),
                "size_bytes": snapshot_path.stat().st_size,
                "sha256": sha256(snapshot_path),
            },
        ]
        with patched_download_task(task):
            bundle_response = admin_app.download_task_artifact(authenticated_request(), "download-task", 0)
            snapshot_response = admin_app.download_task_artifact(authenticated_request(), "download-task", 1)

        assert isinstance(bundle_response, FileResponse)
        assert Path(bundle_response.path).resolve() == bundle_path.resolve()
        assert isinstance(snapshot_response, FileResponse)
        assert Path(snapshot_response.path).resolve() == snapshot_path.resolve()


def test_persisted_download_record_is_used() -> None:
    with download_fixture() as fixture:
        task, file_path = fixture.valid_task()
        original_runtime = admin_app.get_runtime_task
        original_repository = admin_app.repository

        class FakeRepository:
            def get_api_task_run_download_record(self, task_id: str):
                assert task_id == "download-task"
                return task

        try:
            admin_app.get_runtime_task = lambda _task_id: None
            admin_app.repository = lambda: FakeRepository()
            result = admin_app.download_task_artifact(authenticated_request(), "download-task", 0)
        finally:
            admin_app.get_runtime_task = original_runtime
            admin_app.repository = original_repository

        assert isinstance(result, FileResponse)
        assert Path(result.path).resolve() == file_path.resolve()


def test_out_of_range_and_incomplete_artifacts_are_rejected() -> None:
    with download_fixture() as fixture:
        task, _ = fixture.valid_task()
        with patched_download_task(task):
            assert_not_found(admin_app.download_task_artifact(authenticated_request(), "download-task", -1))
            assert_not_found(admin_app.download_task_artifact(authenticated_request(), "download-task", 4))

        task["state"] = "running"
        with patched_download_task(task):
            assert_not_found(admin_app.download_task_artifact(authenticated_request(), "download-task", 0))

        task["state"] = "completed"
        task["result"]["artifacts"][0]["status"] = "failed"
        with patched_download_task(task):
            assert_not_found(admin_app.download_task_artifact(authenticated_request(), "download-task", 0))


def test_task_without_completed_state_is_rejected() -> None:
    with download_fixture() as fixture:
        task, _ = fixture.valid_task()
        task.pop("state")
        task.pop("status")
        with patched_download_task(task):
            assert_not_found(admin_app.download_task_artifact(authenticated_request(), "download-task", 0))


def test_failed_export_keeps_completed_artifact_downloadable() -> None:
    with download_fixture() as fixture:
        task, file_path = fixture.valid_task()
        task["state"] = "failed"
        task["status"] = "failed"
        task["result"]["outcome"] = "failed"
        with patched_download_task(task):
            result = admin_app.download_task_artifact(authenticated_request(), "download-task", 0)

        assert isinstance(result, FileResponse)
        assert Path(result.path).resolve() == file_path.resolve()


def test_download_uses_middleware_auth_snapshot() -> None:
    with download_fixture() as fixture:
        task, _ = fixture.valid_task()
        request = make_request(
            "/tasks/download-task/artifacts/0/download",
            basic_header("admin", "test-password"),
        )
        original_parse_env_file = admin_app.parse_env_file
        parse_count = 0

        def counted_parse_env_file(path: Path) -> dict[str, str]:
            nonlocal parse_count
            parse_count += 1
            return original_parse_env_file(path)

        async def call_next(current_request: Request) -> Response:
            fixture.env_path.write_text("", encoding="utf-8")
            return admin_app.download_task_artifact(current_request, "download-task", 0)

        try:
            admin_app.parse_env_file = counted_parse_env_file
            with patched_download_task(task):
                result = asyncio.run(admin_app.require_admin_basic_auth(request, call_next))
        finally:
            admin_app.parse_env_file = original_parse_env_file

        assert parse_count == 1
        assert isinstance(result, FileResponse)


def test_download_fails_closed_without_admin_auth() -> None:
    with download_fixture() as fixture:
        task, _ = fixture.valid_task()
        with patched_download_task(task):
            result = admin_app.download_task_artifact(make_request("/download"), "download-task", 0)

        assert isinstance(result, Response)
        assert not isinstance(result, FileResponse)
        assert result.status_code == 401
        assert result.headers["www-authenticate"].startswith("Basic ")


def test_path_traversal_and_cross_task_files_are_rejected() -> None:
    with download_fixture() as fixture:
        task, _ = fixture.valid_task()
        task["result"]["artifacts"][0].pop("saved_path")
        task["result"]["artifacts"][0]["relative_path"] = "../outside.zip"
        with patched_download_task(task):
            assert_not_found(admin_app.download_task_artifact(authenticated_request(), "download-task", 0))

        task, file_path = fixture.valid_task()
        task["source_dir"] = str(fixture.raw_root)
        task["result"]["source_dir"] = str(fixture.raw_root)
        task["result"]["artifacts"][0]["relative_path"] = str(file_path.relative_to(fixture.raw_root))
        with patched_download_task(task):
            assert_not_found(admin_app.download_task_artifact(authenticated_request(), "download-task", 0))

        other_dir = fixture.raw_root / "other-task"
        other_dir.mkdir()
        other_file = other_dir / "other.zip"
        other_file.write_bytes(b"other task")
        task, _ = fixture.valid_task()
        task["result"]["artifacts"][0]["relative_path"] = ""
        task["result"]["artifacts"][0]["saved_path"] = str(other_file)
        task["result"]["artifacts"][0]["size_bytes"] = other_file.stat().st_size
        task["result"]["artifacts"][0]["sha256"] = sha256(other_file)
        with patched_download_task(task):
            assert_not_found(admin_app.download_task_artifact(authenticated_request(), "download-task", 0))


def test_symlink_and_unsupported_files_are_rejected() -> None:
    with download_fixture() as fixture:
        task, file_path = fixture.valid_task()
        link_path = file_path.with_name("linked.zip")
        link_path.symlink_to(file_path)
        task["result"]["artifacts"][0].update(
            {
                "relative_path": str(link_path.relative_to(fixture.source_dir)),
                "saved_path": str(link_path),
            }
        )
        with patched_download_task(task):
            assert_not_found(admin_app.download_task_artifact(authenticated_request(), "download-task", 0))

        text_file = fixture.source_dir / "orders" / "debug.txt"
        text_file.write_text("debug", encoding="utf-8")
        task["result"]["artifacts"][0].update(
            {
                "relative_path": str(text_file.relative_to(fixture.source_dir)),
                "saved_path": str(text_file),
                "size_bytes": text_file.stat().st_size,
                "sha256": sha256(text_file),
            }
        )
        with patched_download_task(task):
            assert_not_found(admin_app.download_task_artifact(authenticated_request(), "download-task", 0))


def test_size_and_hash_mismatch_are_rejected() -> None:
    with download_fixture() as fixture:
        task, file_path = fixture.valid_task()
        task["result"]["artifacts"][0]["size_bytes"] = file_path.stat().st_size + 1
        with patched_download_task(task):
            assert_not_found(admin_app.download_task_artifact(authenticated_request(), "download-task", 0))

        task["result"]["artifacts"][0]["size_bytes"] = file_path.stat().st_size
        task["result"]["artifacts"][0]["sha256"] = "0" * 64
        with patched_download_task(task):
            assert_not_found(admin_app.download_task_artifact(authenticated_request(), "download-task", 0))


def assert_not_found(result: object) -> None:
    assert isinstance(result, Response)
    assert not isinstance(result, FileResponse)
    assert result.status_code == 404
    assert b"/home/" not in result.body
    assert b"data/raw" not in result.body


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def basic_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def make_request(path: str, authorization: str = "") -> Request:
    headers = [(b"authorization", authorization.encode("ascii"))] if authorization else []
    return Request({"type": "http", "method": "GET", "path": path, "headers": headers})


def authenticated_request() -> Request:
    request = make_request("/download")
    request.state.admin_auth_enabled = True
    request.state.admin_authenticated = True
    return request


class download_fixture:
    def __init__(self) -> None:
        self.temp_dir_context = tempfile.TemporaryDirectory(prefix="wechat_task_download_")
        self.temp_dir = Path(self.temp_dir_context.name)
        self.raw_root = self.temp_dir / "data" / "raw"
        self.source_dir = self.raw_root / "export_only_download_task"
        self.original_root = admin_app.TASK_ARTIFACT_DOWNLOAD_ROOT
        self.env_path = self.temp_dir / ".env.local"
        self.original_env_path = admin_app.ENV_LOCAL_PATH

    def __enter__(self) -> "download_fixture":
        self.source_dir.mkdir(parents=True)
        self.env_path.write_text("WECHAT_STORE_ADMIN_PASSWORD=test-password\n", encoding="utf-8")
        admin_app.TASK_ARTIFACT_DOWNLOAD_ROOT = self.raw_root
        admin_app.ENV_LOCAL_PATH = self.env_path
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        admin_app.TASK_ARTIFACT_DOWNLOAD_ROOT = self.original_root
        admin_app.ENV_LOCAL_PATH = self.original_env_path
        self.temp_dir_context.cleanup()

    def valid_task(self) -> tuple[dict[str, object], Path]:
        orders_dir = self.source_dir / "orders"
        orders_dir.mkdir(exist_ok=True)
        file_path = orders_dir / "orders.zip"
        file_path.write_bytes(b"valid export artifact")
        task: dict[str, object] = {
            "id": "download-task",
            "task_id": "download-task",
            "state": "completed",
            "status": "completed",
            "source_dir": str(self.source_dir),
            "result": {
                "mode": "export_only",
                "source_dir": str(self.source_dir),
                "artifacts": [
                    {
                        "status": "completed",
                        "source_kind": "export_file",
                        "source_type": "export_file",
                        "relative_path": str(file_path.relative_to(self.source_dir)),
                        "saved_path": str(file_path),
                        "original_filename": "orders.zip",
                        "size_bytes": file_path.stat().st_size,
                        "sha256": sha256(file_path),
                    }
                ],
            },
        }
        return task, file_path


class patched_download_task:
    def __init__(self, task: dict[str, object]) -> None:
        self.task = task
        self.original = admin_app.task_download_record

    def __enter__(self) -> None:
        admin_app.task_download_record = lambda _task_id: self.task

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        admin_app.task_download_record = self.original


if __name__ == "__main__":
    raise SystemExit(main())
