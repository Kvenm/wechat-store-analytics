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


OLD_TOKEN = "old-access-token-sentinel"
NEW_TOKEN = "new-access-token-sentinel"


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_fetch_token_missing_app_id_or_secret_returns_error_without_http,
        test_fetch_token_success_writes_env_and_does_not_leak_token,
        test_fetch_token_wechat_error_does_not_overwrite_existing_token,
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
        with patched_token_http(should_not_call_http):
            result = admin_app.fetch_api_token()

        written = admin_app.parse_env_file(env_path)
        assert result["status"] == "error"
        assert result["data"]["missing"] == {"app_id": True, "app_secret": True}
        assert written["WECHAT_STORE_APP_ID"] == ""
        assert written["WECHAT_STORE_APP_SECRET"] == ""
        assert "WECHAT_STORE_ACCESS_TOKEN" not in written


def test_fetch_token_success_writes_env_and_does_not_leak_token() -> None:
    calls: list[dict[str, Any]] = []

    def fake_http(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "access_token": NEW_TOKEN,
            "expires_in": 7200,
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
        with patched_token_http(fake_http):
            result = admin_app.fetch_api_token()

        written = admin_app.parse_env_file(env_path)
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True)

        assert result["status"] == "ok"
        assert result["data"]["configured"] is True
        assert result["data"]["expires_in"] == "7200"
        assert result["data"]["source"] == "stable_token"
        assert result["data"]["expires_at"].endswith("Z")
        assert result["data"]["fetched_at"].endswith("Z")
        assert NEW_TOKEN not in payload
        assert OLD_TOKEN not in payload
        assert written["WECHAT_STORE_ACCESS_TOKEN"] == NEW_TOKEN
        assert written["WECHAT_STORE_ACCESS_TOKEN_EXPIRES_IN"] == "7200"
        assert written["WECHAT_STORE_ACCESS_TOKEN_FETCHED_AT"].endswith("Z")
        assert written["WECHAT_STORE_ACCESS_TOKEN_EXPIRES_AT"].endswith("Z")
        assert written["WECHAT_STORE_ACCESS_TOKEN_SOURCE"] == "stable_token"
        assert calls == [
            {
                "base_url": "https://api.example.test",
                "app_id": "wx-success",
                "app_secret": "secret-success",
            }
        ]


def test_fetch_token_wechat_error_does_not_overwrite_existing_token() -> None:
    def fake_http(**_: Any) -> dict[str, Any]:
        return {
            "errcode": 40013,
            "errmsg": "invalid appid",
        }

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
        with patched_token_http(fake_http):
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


if __name__ == "__main__":
    raise SystemExit(main())
