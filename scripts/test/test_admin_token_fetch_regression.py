#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
import tempfile
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import api.app as admin_app  # noqa: E402


OLD_TOKEN = "old-access-token-sentinel"
NEW_TOKEN = "new-access-token-sentinel"
STORE_INFO_ERROR_SECRET = "store-info-error-token-sentinel"


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_fetch_token_missing_app_id_or_secret_returns_error_without_http,
        test_connect_uses_saved_secret_for_legacy_single_id,
        test_connect_rejects_changed_id_without_new_secret,
        test_fetch_token_uses_legacy_shop_id_as_app_id,
        test_fetch_token_success_writes_env_and_does_not_leak_token,
        test_fetch_token_wechat_error_does_not_overwrite_existing_token,
        test_store_basic_info_failure_keeps_token_and_returns_safe_warning,
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
        print(f"{failed} admin token fetch regression test(s) failed.")
        return 1

    print(f"{len(tests)} admin token fetch regression tests passed.")
    return 0


def test_fetch_token_missing_app_id_or_secret_returns_error_without_http() -> None:
    def should_not_call_http(**_: Any) -> dict[str, Any]:
        raise AssertionError("token HTTP function should not be called when credentials are missing")

    with patched_env_file("WECHAT_STORE_APP_ID=\nWECHAT_STORE_APP_SECRET=\n") as env_path:
        with patched_token_http(should_not_call_http), patched_store_info_http(should_not_call_http):
            result = admin_app.fetch_api_token()

        written = admin_app.parse_env_file(env_path)
        assert result["status"] == "error"
        assert result["data"]["missing"] == {"app_id": True, "app_secret": True}
        assert written["WECHAT_STORE_APP_ID"] == ""
        assert written["WECHAT_STORE_APP_SECRET"] == ""
        assert "WECHAT_STORE_ACCESS_TOKEN" not in written


def test_connect_uses_saved_secret_for_legacy_single_id() -> None:
    def fake_token_http(**_: Any) -> dict[str, Any]:
        return {"access_token": NEW_TOKEN, "expires_in": 7200}

    def fake_store_info(**_: Any) -> dict[str, Any]:
        return {
            "errcode": 0,
            "errmsg": "ok",
            "info": {"nickname": "自动连接店铺", "username": "official-auto-shop"},
        }

    with patched_env_file(
        "WECHAT_STORE_SHOP_ID=wx-legacy-connect\n"
        "WECHAT_STORE_APP_SECRET=saved-secret\n"
        "WECHAT_STORE_API_BASE_URL=https://api.example.test\n"
    ) as env_path:
        with patched_token_http(fake_token_http), patched_store_info_http(fake_store_info):
            result = admin_app.connect_api_config(
                admin_app.ApiConnectRequest(credential_id="wx-legacy-connect", app_secret="")
            )

        written = admin_app.parse_env_file(env_path)
        assert result["status"] == "ok"
        assert result["data"]["credentials_saved"] is True
        assert result["data"]["token"]["shop_info"]["loaded"] is True
        assert written["WECHAT_STORE_APP_ID"] == "wx-legacy-connect"
        assert written["WECHAT_STORE_APP_SECRET"] == "saved-secret"
        assert written["WECHAT_STORE_SHOP_ID"] == "official-auto-shop"
        assert written["WECHAT_STORE_SHOP_NAME"] == "自动连接店铺"


def test_connect_rejects_changed_id_without_new_secret() -> None:
    def should_not_call_http(**_: Any) -> dict[str, Any]:
        raise AssertionError("HTTP must not be called when changed ID has no new secret")

    with patched_env_file(
        "WECHAT_STORE_APP_ID=wx-before\n"
        "WECHAT_STORE_SHOP_ID=official-before\n"
        "WECHAT_STORE_APP_SECRET=old-secret\n"
    ) as env_path:
        before = env_path.read_text(encoding="utf-8")
        with patched_token_http(should_not_call_http), patched_store_info_http(should_not_call_http):
            result = admin_app.connect_api_config(
                admin_app.ApiConnectRequest(credential_id="wx-after", app_secret="")
            )

        assert result["status"] == "error"
        assert result["data"]["missing"]["app_secret"] is True
        assert env_path.read_text(encoding="utf-8") == before


def test_fetch_token_uses_legacy_shop_id_as_app_id() -> None:
    token_calls: list[dict[str, Any]] = []

    def fake_token_http(**kwargs: Any) -> dict[str, Any]:
        token_calls.append(kwargs)
        return {
            "access_token": NEW_TOKEN,
            "expires_in": 7200,
        }

    def fake_store_info(**_: Any) -> dict[str, Any]:
        return {
            "errcode": 0,
            "errmsg": "ok",
            "info": {
                "nickname": "旧配置自动识别店铺",
                "username": "wx-legacy-shop-id",
            },
        }

    with patched_env_file(
        "\n".join(
            [
                "WECHAT_STORE_SHOP_ID=wx-legacy-shop-id",
                "WECHAT_STORE_APP_SECRET=legacy-secret",
                "WECHAT_STORE_API_BASE_URL=https://api.example.test/",
            ]
        )
        + "\n"
    ) as env_path:
        with patched_token_http(fake_token_http), patched_store_info_http(fake_store_info):
            result = admin_app.fetch_api_token()

        written = admin_app.parse_env_file(env_path)

        assert result["status"] == "ok"
        assert token_calls == [
            {
                "base_url": "https://api.example.test",
                "app_id": "wx-legacy-shop-id",
                "app_secret": "legacy-secret",
            }
        ]
        assert written["WECHAT_STORE_APP_ID"] == "wx-legacy-shop-id"
        assert written["WECHAT_STORE_SHOP_ID"] == "wx-legacy-shop-id"
        assert written["WECHAT_STORE_ACCESS_TOKEN"] == NEW_TOKEN


def test_fetch_token_success_writes_env_and_does_not_leak_token() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_token_http(**kwargs: Any) -> dict[str, Any]:
        calls.append(("token", kwargs))
        return {
            "access_token": NEW_TOKEN,
            "expires_in": 7200,
        }

    def fake_store_info(**kwargs: Any) -> dict[str, Any]:
        calls.append(("store_info", kwargs))
        return {
            "errcode": 0,
            "errmsg": "ok",
            "info": {
                "nickname": "自动识别店铺",
                "username": "official-shop-username",
            },
        }

    with patched_env_file(
        "\n".join(
            [
                "WECHAT_STORE_APP_ID=wx-success",
                "WECHAT_STORE_APP_SECRET=secret-success",
                "WECHAT_STORE_API_BASE_URL=https://api.example.test/",
                f"WECHAT_STORE_ACCESS_TOKEN={OLD_TOKEN}",
            ]
        )
        + "\n"
    ) as env_path:
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        with (
            patched_token_http(fake_token_http),
            patched_store_info_http(fake_store_info),
            redirect_stdout(captured_stdout),
            redirect_stderr(captured_stderr),
        ):
            result = admin_app.fetch_api_token()

        written = admin_app.parse_env_file(env_path)
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
        captured_logs = captured_stdout.getvalue() + captured_stderr.getvalue()

        assert result["status"] == "ok"
        assert result["data"]["configured"] is True
        assert result["data"]["expires_in"] == "7200"
        assert result["data"]["source"] == "stable_token"
        assert result["data"]["expires_at"].endswith("Z")
        assert result["data"]["fetched_at"].endswith("Z")
        assert result["data"]["shop_info"]["loaded"] is True
        assert result["data"]["shop_info"]["shop_name"] == "自动识别店铺"
        assert result["data"]["shop_info"]["shop_id"] == "official-shop-username"
        assert NEW_TOKEN not in payload
        assert OLD_TOKEN not in payload
        assert NEW_TOKEN not in captured_logs
        assert OLD_TOKEN not in captured_logs
        assert written["WECHAT_STORE_ACCESS_TOKEN"] == NEW_TOKEN
        assert written["WECHAT_STORE_ACCESS_TOKEN_EXPIRES_IN"] == "7200"
        assert written["WECHAT_STORE_ACCESS_TOKEN_FETCHED_AT"].endswith("Z")
        assert written["WECHAT_STORE_ACCESS_TOKEN_EXPIRES_AT"].endswith("Z")
        assert written["WECHAT_STORE_ACCESS_TOKEN_SOURCE"] == "stable_token"
        assert written["WECHAT_STORE_SHOP_NAME"] == "自动识别店铺"
        assert written["WECHAT_STORE_SHOP_ID"] == "official-shop-username"
        assert calls == [
            (
                "token",
                {
                    "base_url": "https://api.example.test",
                    "app_id": "wx-success",
                    "app_secret": "secret-success",
                },
            ),
            (
                "store_info",
                {
                    "base_url": "https://api.example.test",
                    "access_token": NEW_TOKEN,
                },
            ),
        ]


def test_fetch_token_wechat_error_does_not_overwrite_existing_token() -> None:
    def fake_token_http(**_: Any) -> dict[str, Any]:
        return {
            "errcode": 40013,
            "errmsg": "invalid appid",
        }

    def should_not_fetch_store_info(**_: Any) -> dict[str, Any]:
        raise AssertionError("store basic info must not be requested after token failure")

    with patched_env_file(
        "\n".join(
            [
                "WECHAT_STORE_APP_ID=wx-error",
                "WECHAT_STORE_APP_SECRET=secret-error",
                "WECHAT_STORE_API_BASE_URL=https://api.weixin.qq.com",
                f"WECHAT_STORE_ACCESS_TOKEN={OLD_TOKEN}",
                "WECHAT_STORE_ACCESS_TOKEN_EXPIRES_IN=3600",
                "WECHAT_STORE_ACCESS_TOKEN_EXPIRES_AT=2026-06-28T10:00:00Z",
                "WECHAT_STORE_ACCESS_TOKEN_SOURCE=manual",
            ]
        )
        + "\n"
    ) as env_path:
        before = env_path.read_text(encoding="utf-8")
        with patched_token_http(fake_token_http), patched_store_info_http(should_not_fetch_store_info):
            result = admin_app.fetch_api_token()
        after = env_path.read_text(encoding="utf-8")
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True)

        assert result["status"] == "error"
        assert result["data"]["errcode"] == 40013
        assert result["data"]["errmsg"] == "invalid appid"
        assert result["data"]["configured"] is True
        assert OLD_TOKEN not in payload
        assert after == before
        assert admin_app.parse_env_file(env_path)["WECHAT_STORE_ACCESS_TOKEN"] == OLD_TOKEN


def test_store_basic_info_failure_keeps_token_and_returns_safe_warning() -> None:
    def fake_token_http(**_: Any) -> dict[str, Any]:
        return {
            "access_token": NEW_TOKEN,
            "expires_in": 7200,
        }

    def failing_store_info(**_: Any) -> dict[str, Any]:
        raise RuntimeError(
            f"store info request failed: access_token={NEW_TOKEN}; detail={STORE_INFO_ERROR_SECRET}"
        )

    with patched_env_file(
        "\n".join(
            [
                "WECHAT_STORE_APP_ID=wx-store-info-warning",
                "WECHAT_STORE_APP_SECRET=secret-store-info-warning",
                "WECHAT_STORE_API_BASE_URL=https://api.example.test",
                "WECHAT_STORE_SHOP_ID=existing-shop-id",
                "WECHAT_STORE_SHOP_NAME=已有店铺名称",
                f"WECHAT_STORE_ACCESS_TOKEN={OLD_TOKEN}",
            ]
        )
        + "\n"
    ) as env_path:
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        with (
            patched_token_http(fake_token_http),
            patched_store_info_http(failing_store_info),
            redirect_stdout(captured_stdout),
            redirect_stderr(captured_stderr),
        ):
            result = admin_app.fetch_api_token()

        written = admin_app.parse_env_file(env_path)
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
        captured_logs = captured_stdout.getvalue() + captured_stderr.getvalue()

        assert result["status"] == "ok"
        assert result["data"]["configured"] is True
        assert result["data"]["shop_info"]["loaded"] is False
        assert result["data"]["shop_info"]["message"]
        assert written["WECHAT_STORE_ACCESS_TOKEN"] == NEW_TOKEN
        assert written["WECHAT_STORE_ACCESS_TOKEN_EXPIRES_AT"].endswith("Z")
        assert written["WECHAT_STORE_SHOP_ID"] == "existing-shop-id"
        assert written["WECHAT_STORE_SHOP_NAME"] == "已有店铺名称"
        for sensitive_value in (NEW_TOKEN, OLD_TOKEN, STORE_INFO_ERROR_SECRET):
            assert sensitive_value not in payload
            assert sensitive_value not in captured_logs


class patched_env_file:
    def __init__(self, initial_content: str) -> None:
        self.initial_content = initial_content
        self.temp_dir_context = tempfile.TemporaryDirectory(prefix="wechat_admin_token_fetch_")
        self.temp_dir = Path(self.temp_dir_context.name)
        self.env_path = self.temp_dir / ".env.local"
        self.original_path = admin_app.ENV_LOCAL_PATH

    def __enter__(self) -> Path:
        self.env_path.write_text(self.initial_content, encoding="utf-8")
        admin_app.ENV_LOCAL_PATH = self.env_path
        return self.env_path

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        admin_app.ENV_LOCAL_PATH = self.original_path
        self.temp_dir_context.cleanup()


class patched_token_http:
    def __init__(self, replacement: Callable[..., dict[str, Any]]) -> None:
        self.replacement = replacement
        self.original = admin_app.request_stable_access_token

    def __enter__(self) -> None:
        admin_app.request_stable_access_token = self.replacement

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        admin_app.request_stable_access_token = self.original


class patched_store_info_http:
    def __init__(self, replacement: Callable[..., dict[str, Any]]) -> None:
        self.replacement = replacement
        self.original = admin_app.request_store_basic_info

    def __enter__(self) -> None:
        admin_app.request_store_basic_info = self.replacement

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        admin_app.request_store_basic_info = self.original


if __name__ == "__main__":
    raise SystemExit(main())
