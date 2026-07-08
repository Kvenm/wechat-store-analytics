#!/usr/bin/env python3
from __future__ import annotations

import base64
import sys
import traceback
from collections.abc import Callable
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import api.app as admin_app  # noqa: E402


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_admin_auth_disabled_without_password,
        test_admin_basic_auth_accepts_expected_credentials,
        test_admin_basic_auth_rejects_bad_headers,
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
        print(f"{failed} admin basic auth regression test(s) failed.")
        return 1
    print(f"{len(tests)} admin basic auth regression tests passed.")
    return 0


def test_admin_auth_disabled_without_password() -> None:
    assert not admin_app.admin_auth_enabled({})
    assert admin_app.admin_basic_auth_valid("", {})


def test_admin_basic_auth_accepts_expected_credentials() -> None:
    env_values = {
        "WECHAT_STORE_ADMIN_USER": "owner",
        "WECHAT_STORE_ADMIN_PASSWORD": "secret-pass",
    }

    assert admin_app.admin_auth_enabled(env_values)
    assert admin_app.admin_basic_auth_valid(basic_header("owner", "secret-pass"), env_values)


def test_admin_basic_auth_rejects_bad_headers() -> None:
    env_values = {"WECHAT_STORE_ADMIN_PASSWORD": "secret-pass"}

    assert not admin_app.admin_basic_auth_valid("", env_values)
    assert not admin_app.admin_basic_auth_valid("Bearer nope", env_values)
    assert not admin_app.admin_basic_auth_valid("Basic !!!", env_values)
    assert not admin_app.admin_basic_auth_valid(basic_header("admin", "wrong"), env_values)
    assert not admin_app.admin_basic_auth_valid(basic_header("wrong", "secret-pass"), env_values)


def basic_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


if __name__ == "__main__":
    raise SystemExit(main())
