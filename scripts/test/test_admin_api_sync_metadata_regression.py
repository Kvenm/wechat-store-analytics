#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import api.app as admin_app  # noqa: E402
from api.repository import LocalRepository  # noqa: E402
from warehouse.repository import (  # noqa: E402
    connect,
    initialize_database,
    upsert_raw_api_response,
    upsert_sync_run,
    upsert_sync_run_item,
)


SHOP_ID = "admin-sync-metadata-shop"
OTHER_SHOP_ID = "admin-sync-metadata-other-shop"
SYNC_RUN_ID = "admin-sync-metadata-run-001"
OTHER_SYNC_RUN_ID = "admin-sync-metadata-run-002"
SYNC_ITEM_ID = "admin-sync-metadata-item-001"
RAW_RESPONSE_ID = "admin-sync-metadata-raw-001"
SENSITIVE_TERMS = ("access_token", "authorization", "cookie", "secret", "session", "token")
SENSITIVE_SENTINELS = (
    "admin-api-access-token-sentinel",
    "admin-api-authorization-sentinel",
    "admin-api-cookie-sentinel",
    "admin-api-secret-sentinel",
    "admin-api-session-sentinel",
)


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_repository_lists_sync_metadata_with_filters_and_redaction,
        test_admin_route_functions_return_sync_metadata,
        test_admin_route_starts_configured_api_sync_without_leaking_token,
        test_admin_route_refreshes_expired_token_before_sync,
        test_empty_database_and_missing_run_do_not_raise,
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
        print(f"{failed} admin API sync metadata regression test(s) failed.")
        return 1

    print(f"{len(tests)} admin API sync metadata regression tests passed.")
    return 0


def test_repository_lists_sync_metadata_with_filters_and_redaction() -> None:
    with tempfile.TemporaryDirectory(prefix="wechat_admin_sync_metadata_") as temp_dir:
        db_path = Path(temp_dir) / "warehouse.sqlite"
        seed_sync_metadata(db_path)

        repo = LocalRepository(db_path=db_path)
        runs = repo.list_sync_runs(limit=1, shop_id=SHOP_ID)
        assert len(runs) == 1
        assert runs[0]["sync_run_id"] == SYNC_RUN_ID
        assert runs[0]["shop_id"] == SHOP_ID
        assert runs[0]["item_count"] == 1
        assert runs[0]["raw_response_count"] == 1

        detail = repo.get_sync_run(SYNC_RUN_ID)
        assert detail is not None
        assert detail["sync_run_id"] == SYNC_RUN_ID
        assert len(detail["items"]) == 1
        assert len(detail["raw_responses"]) == 1
        assert detail["items"][0]["sync_item_id"] == SYNC_ITEM_ID
        assert detail["raw_responses"][0]["raw_response_id"] == RAW_RESPONSE_ID

        items = repo.list_sync_run_items(SYNC_RUN_ID)
        assert len(items) == 1
        assert items[0]["raw_response_id"] == RAW_RESPONSE_ID

        raw_responses = repo.list_raw_api_responses(sync_item_id=SYNC_ITEM_ID, limit=10)
        assert len(raw_responses) == 1
        assert raw_responses[0]["sync_run_id"] == SYNC_RUN_ID

        other_shop_raw = repo.list_raw_api_responses(shop_id=OTHER_SHOP_ID, limit=10)
        assert len(other_shop_raw) == 1
        assert other_shop_raw[0]["sync_run_id"] == OTHER_SYNC_RUN_ID

        assert_no_sensitive_text(runs)
        assert_no_sensitive_text(detail)
        assert_no_sensitive_text(items)
        assert_no_sensitive_text(raw_responses)


def test_admin_route_functions_return_sync_metadata() -> None:
    with tempfile.TemporaryDirectory(prefix="wechat_admin_sync_routes_") as temp_dir:
        db_path = Path(temp_dir) / "warehouse.sqlite"
        seed_sync_metadata(db_path)
        repo = LocalRepository(db_path=db_path)
        original_repository = admin_app.repository
        admin_app.repository = lambda: repo
        try:
            runs_response = admin_app.sync_runs(limit=5, shop_id=SHOP_ID)
            assert runs_response["status"] == "ok"
            assert runs_response["data"][0]["sync_run_id"] == SYNC_RUN_ID

            detail_response = admin_app.sync_run_detail(SYNC_RUN_ID)
            assert detail_response["status"] == "ok"
            assert detail_response["data"]["items"][0]["sync_item_id"] == SYNC_ITEM_ID
            assert detail_response["data"]["raw_responses"][0]["raw_response_id"] == RAW_RESPONSE_ID

            items_response = admin_app.sync_run_items(SYNC_RUN_ID)
            assert items_response["status"] == "ok"
            assert items_response["data"][0]["sync_item_id"] == SYNC_ITEM_ID

            raw_response = admin_app.raw_api_responses(sync_run_id=SYNC_RUN_ID, limit=5)
            assert raw_response["status"] == "ok"
            assert raw_response["data"][0]["raw_response_id"] == RAW_RESPONSE_ID

            missing_response = admin_app.sync_run_detail("missing-sync-run")
            assert missing_response == {"status": "not_found", "message": "Sync run not found.", "data": None}

            missing_items_response = admin_app.sync_run_items("missing-sync-run")
            assert missing_items_response == {"status": "not_found", "message": "Sync run not found.", "data": None}

            assert_no_sensitive_text(runs_response)
            assert_no_sensitive_text(detail_response)
            assert_no_sensitive_text(items_response)
            assert_no_sensitive_text(raw_response)
        finally:
            admin_app.repository = original_repository


def test_admin_route_starts_configured_api_sync_without_leaking_token() -> None:
    token = "admin-route-token-must-not-leak"
    calls: list[dict[str, Any]] = []

    def fake_sync(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "sync_run_id": kwargs["sync_run_id"],
            "shop_id": kwargs["shop_id"],
            "source_kind": "api_pull",
            "connector": "wechat_api",
            "status": "completed",
            "items": [],
            "warnings": [],
        }

    with tempfile.TemporaryDirectory(prefix="wechat_admin_api_sync_start_") as temp_dir:
        temp_path = Path(temp_dir)
        env_path = temp_path / ".env.local"
        env_path.write_text(
            "\n".join(
                [
                    "WECHAT_STORE_ACCESS_TOKEN=admin-route-token-must-not-leak",
                    "WECHAT_STORE_API_BASE_URL=https://api.example.test/",
                    "WECHAT_STORE_RAW_ARCHIVE_DIR=data/raw/api-test",
                    "WECHAT_STORE_SHOP_ID=config-shop",
                    "WECHAT_STORE_SHOP_NAME=Config Shop",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        repo = LocalRepository(db_path=temp_path / "warehouse.sqlite", reports_dir=temp_path / "reports")
        original_env_path = admin_app.ENV_LOCAL_PATH
        original_repository = admin_app.repository
        original_sync = admin_app.run_wechat_api_sync
        admin_app.ENV_LOCAL_PATH = env_path
        admin_app.repository = lambda: repo
        admin_app.run_wechat_api_sync = fake_sync
        try:
            result = admin_app.create_api_sync_run(
                admin_app.ApiSyncRunRequest(
                    shop_id="payload-shop",
                    date_from="2026-06-01",
                    date_to="2026-06-02",
                    endpoints=["orders"],
                    endpoint_params={"orders": {"status": 20}},
                )
            )
        finally:
            admin_app.ENV_LOCAL_PATH = original_env_path
            admin_app.repository = original_repository
            admin_app.run_wechat_api_sync = original_sync

        assert result["status"] == "ok"
        assert calls and calls[0]["shop_id"] == "payload-shop"
        assert calls[0]["access_token"] == token
        assert calls[0]["api_base_url"] == "https://api.example.test"
        assert calls[0]["archive_dir"] == str(PROJECT_ROOT / "data" / "raw" / "api-test")
        assert calls[0]["endpoints"] == ["orders"]
        assert calls[0]["endpoint_params"] == {"orders": {"status": 20}}
        assert result["data"]["analysis_run_id"]
        assert result["data"]["report"]["report_id"]
        assert Path(result["data"]["report"]["markdown_path"]).exists()
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
        assert token not in payload


def test_admin_route_refreshes_expired_token_before_sync() -> None:
    calls: list[dict[str, Any]] = []

    def fake_sync(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "sync_run_id": kwargs["sync_run_id"],
            "shop_id": kwargs["shop_id"],
            "source_kind": "api_pull",
            "connector": "wechat_api",
            "status": "completed",
            "items": [],
            "warnings": [],
        }

    with tempfile.TemporaryDirectory(prefix="wechat_admin_api_sync_refresh_") as temp_dir:
        temp_path = Path(temp_dir)
        env_path = temp_path / ".env.local"
        env_path.write_text(
            "\n".join(
                [
                    "WECHAT_STORE_APP_ID=wx-refresh",
                    "WECHAT_STORE_APP_SECRET=secret-refresh",
                    "WECHAT_STORE_ACCESS_TOKEN=expired-token",
                    "WECHAT_STORE_ACCESS_TOKEN_EXPIRES_AT=2020-01-01T00:00:00Z",
                    "WECHAT_STORE_API_BASE_URL=https://api.example.test",
                    "WECHAT_STORE_SHOP_ID=config-shop",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        original_env_path = admin_app.ENV_LOCAL_PATH
        original_repository = admin_app.repository
        original_sync = admin_app.run_wechat_api_sync
        original_token_http = admin_app.request_stable_access_token
        original_store_info_http = admin_app.request_store_basic_info
        admin_app.ENV_LOCAL_PATH = env_path
        admin_app.repository = lambda: LocalRepository(db_path=temp_path / "warehouse.sqlite")
        admin_app.run_wechat_api_sync = fake_sync
        admin_app.request_stable_access_token = lambda **_: {"access_token": "fresh-token", "expires_in": 7200}
        admin_app.request_store_basic_info = lambda **_: {
            "errcode": 0,
            "errmsg": "ok",
            "info": {"nickname": "Refreshed Shop", "username": "official-refresh-shop"},
        }
        try:
            result = admin_app.create_api_sync_run(
                admin_app.ApiSyncRunRequest(date_from="2026-06-01", date_to="2026-06-01", generate_report=False)
            )
        finally:
            admin_app.ENV_LOCAL_PATH = original_env_path
            admin_app.repository = original_repository
            admin_app.run_wechat_api_sync = original_sync
            admin_app.request_stable_access_token = original_token_http
            admin_app.request_store_basic_info = original_store_info_http

        assert result["status"] == "ok"
        assert calls[0]["access_token"] == "fresh-token"
        assert admin_app.parse_env_file(env_path)["WECHAT_STORE_ACCESS_TOKEN"] == "fresh-token"


def test_empty_database_and_missing_run_do_not_raise() -> None:
    with tempfile.TemporaryDirectory(prefix="wechat_admin_sync_empty_") as temp_dir:
        db_path = Path(temp_dir) / "empty.sqlite"
        with connect(db_path) as conn:
            initialize_database(conn)

        repo = LocalRepository(db_path=db_path)
        assert repo.list_sync_runs() == []
        assert repo.get_sync_run("missing-sync-run") is None
        assert repo.list_sync_run_items("missing-sync-run") == []
        assert repo.list_raw_api_responses(sync_run_id="missing-sync-run") == []

        missing_db_repo = LocalRepository(db_path=Path(temp_dir) / "missing.sqlite")
        assert missing_db_repo.list_sync_runs() == []
        assert missing_db_repo.get_sync_run("missing-sync-run") is None
        assert missing_db_repo.list_sync_run_items("missing-sync-run") == []
        assert missing_db_repo.list_raw_api_responses() == []


def seed_sync_metadata(db_path: Path) -> None:
    with connect(db_path) as conn:
        initialize_database(conn)
        upsert_sync_run(
            conn,
            {
                "sync_run_id": SYNC_RUN_ID,
                "shop_id": SHOP_ID,
                "shop_name_snapshot": "Admin Metadata Shop",
                "source_kind": "api_pull",
                "connector": "mock_wechat",
                "status": "completed",
                "date_from": "2026-06-01",
                "date_to": "2026-06-07",
                "started_at": "2026-06-08 10:00:00",
                "finished_at": "2026-06-08 10:01:00",
                "params": {
                    "page_size": 100,
                    "access_token": SENSITIVE_SENTINELS[0],
                    "headers": {"Authorization": SENSITIVE_SENTINELS[1]},
                },
                "summary": {
                    "record_count": 2,
                    "nested": {"secret": SENSITIVE_SENTINELS[3]},
                    "message": f"Bearer {SENSITIVE_SENTINELS[0]}",
                },
                "warnings": [{"message": f"cookie={SENSITIVE_SENTINELS[2]}"}],
            },
        )
        upsert_sync_run_item(
            conn,
            {
                "sync_item_id": SYNC_ITEM_ID,
                "sync_run_id": SYNC_RUN_ID,
                "shop_id": SHOP_ID,
                "source_kind": "api_pull",
                "endpoint": "/channels-shop-order/getorderlist",
                "export_type": "orders",
                "table_hint": "orders",
                "status": "failed",
                "page_number": 1,
                "request_id": "request-001",
                "rid": "rid-001",
                "http_status": 401,
                "error_code": "401",
                "error_message": f"authorization failed for {SENSITIVE_SENTINELS[1]}",
                "raw_response_id": RAW_RESPONSE_ID,
                "row_count": 0,
                "started_at": "2026-06-08 10:00:01",
                "finished_at": "2026-06-08 10:00:02",
                "raw": {
                    "raw_response_id": RAW_RESPONSE_ID,
                    "session": SENSITIVE_SENTINELS[4],
                    "public_note": f"token should be redacted: {SENSITIVE_SENTINELS[0]}",
                },
            },
        )
        upsert_raw_api_response(
            conn,
            {
                "raw_response_id": RAW_RESPONSE_ID,
                "sync_run_id": SYNC_RUN_ID,
                "sync_item_id": SYNC_ITEM_ID,
                "shop_id": SHOP_ID,
                "source_kind": "api_pull",
                "endpoint": "/channels-shop-order/getorderlist",
                "request_id": "request-001",
                "rid": "rid-001",
                "status": "failed",
                "storage_path": "/tmp/raw-response-001.json",
                "sha256": "0" * 64,
                "size_bytes": 123,
                "record_count": 0,
                "schema_version": "v1",
                "pulled_at": "2026-06-08 10:00:02",
                "metadata": {
                    "content_type": "application/json",
                    "cookie": SENSITIVE_SENTINELS[2],
                    "public_error": f"secret was rejected: {SENSITIVE_SENTINELS[3]}",
                },
            },
        )
        upsert_sync_run(
            conn,
            {
                "sync_run_id": OTHER_SYNC_RUN_ID,
                "shop_id": OTHER_SHOP_ID,
                "shop_name_snapshot": "Other Shop",
                "source_kind": "api_pull",
                "connector": "mock_wechat",
                "status": "completed",
                "started_at": "2026-06-08 09:00:00",
                "params": {},
                "summary": {},
                "warnings": [],
            },
        )
        upsert_sync_run_item(
            conn,
            {
                "sync_item_id": "admin-sync-metadata-item-002",
                "sync_run_id": OTHER_SYNC_RUN_ID,
                "shop_id": OTHER_SHOP_ID,
                "source_kind": "api_pull",
                "endpoint": "/channels-shop-product/shop/getproductlist",
                "status": "completed",
                "raw_response_id": "admin-sync-metadata-raw-002",
                "row_count": 1,
            },
        )
        upsert_raw_api_response(
            conn,
            {
                "raw_response_id": "admin-sync-metadata-raw-002",
                "sync_run_id": OTHER_SYNC_RUN_ID,
                "sync_item_id": "admin-sync-metadata-item-002",
                "shop_id": OTHER_SHOP_ID,
                "source_kind": "api_pull",
                "endpoint": "/channels-shop-product/shop/getproductlist",
                "status": "completed",
                "storage_path": "/tmp/raw-response-002.json",
                "sha256": "1" * 64,
                "size_bytes": 456,
                "record_count": 1,
                "schema_version": "v1",
                "pulled_at": "2026-06-08 09:00:02",
                "metadata": {"content_type": "application/json"},
            },
        )


def assert_no_sensitive_text(value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True).casefold()
    for term in SENSITIVE_TERMS:
        assert term not in payload, f"sensitive term leaked in admin API output: {term}"
    for sentinel in SENSITIVE_SENTINELS:
        assert sentinel.casefold() not in payload, f"sensitive sentinel leaked in admin API output: {sentinel}"


if __name__ == "__main__":
    raise SystemExit(main())
