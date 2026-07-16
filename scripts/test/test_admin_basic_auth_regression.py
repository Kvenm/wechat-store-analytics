#!/usr/bin/env python3
from __future__ import annotations

import base64
import os
import sys
import tempfile
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
        test_env_updates_are_atomic_and_preserve_permissions,
        test_new_env_file_is_private,
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


def test_env_updates_are_atomic_and_preserve_permissions() -> None:
    with tempfile.TemporaryDirectory(prefix="wechat_admin_auth_env_") as temp_dir:
        path = Path(temp_dir) / ".env.local"
        path.write_text("UNCHANGED=yes\nWECHAT_STORE_ADMIN_PASSWORD=old\n", encoding="utf-8")
        path.chmod(0o640)
        original_inode = path.stat().st_ino
        original_replace = os.replace
        replaced: list[tuple[Path, Path]] = []

        def recording_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
            replaced.append((Path(source), Path(target)))
            original_replace(source, target)

        try:
            os.replace = recording_replace
            admin_app.write_env_updates(path, {"WECHAT_STORE_ADMIN_PASSWORD": "new"})
        finally:
            os.replace = original_replace

        assert admin_app.parse_env_file(path) == {
            "UNCHANGED": "yes",
            "WECHAT_STORE_ADMIN_PASSWORD": "new",
        }
        assert path.stat().st_mode & 0o7777 == 0o640
        assert path.stat().st_ino != original_inode
        assert replaced and replaced[0][0].parent == path.parent
        assert replaced[0][1] == path


def test_new_env_file_is_private() -> None:
    with tempfile.TemporaryDirectory(prefix="wechat_admin_auth_env_") as temp_dir:
        path = Path(temp_dir) / ".env.local"
        admin_app.write_env_updates(path, {"WECHAT_STORE_ADMIN_PASSWORD": "new"})

        assert path.stat().st_mode & 0o7777 == 0o600


def basic_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


if __name__ == "__main__":
    raise SystemExit(main())
