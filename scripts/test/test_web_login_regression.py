#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import api.app as admin_app  # noqa: E402


SENSITIVE_COOKIE = "cookie-sentinel-value"
SENSITIVE_TOKEN = "token-sentinel-value"


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_web_login_status_does_not_read_or_leak_browser_profile_secrets,
        test_login_page_contains_dedicated_scan_login_ui,
        test_open_web_login_returns_pid_profile_dir_and_login_url,
        test_open_web_login_reuses_existing_running_process,
        test_open_web_login_script_missing_returns_error_without_starting_process,
        test_open_web_login_playwright_missing_returns_error_without_starting_process,
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
        print(f"{failed} web login regression test(s) failed.")
        return 1

    print(f"{len(tests)} web login regression tests passed.")
    return 0


def test_web_login_status_does_not_read_or_leak_browser_profile_secrets() -> None:
    with patched_web_login_paths() as paths:
        paths.profile_dir.mkdir(parents=True)
        (paths.profile_dir / "Local Storage").mkdir()
        (paths.profile_dir / "Cookies").write_text(SENSITIVE_COOKIE, encoding="utf-8")
        (paths.profile_dir / "Local Storage" / "leveldb.log").write_text(SENSITIVE_TOKEN, encoding="utf-8")

        result = admin_app.web_login_status()
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True).lower()

        assert result["status"] == "ok"
        assert result["data"]["profile_dir"] == str(paths.profile_dir)
        assert result["data"]["profile_dir_exists"] is True
        assert result["data"]["process_running"] is False
        assert result["data"]["pid"] is None
        assert result["data"]["login_url"] == "https://store.weixin.qq.com/"
        assert SENSITIVE_COOKIE not in payload
        assert SENSITIVE_TOKEN not in payload
        assert "cookie-sentinel" not in payload
        assert "token-sentinel" not in payload
        assert "local storage" not in payload
        assert "localstorage" not in payload


def test_login_page_contains_dedicated_scan_login_ui() -> None:
    response = admin_app.login_page()
    html = response.body.decode("utf-8")
    lowered = html.lower()

    expected_fragments = (
        "微信小店扫码登录",
        "打开微信小店扫码登录窗口",
        "复制后台登录链接",
        "刷新登录状态",
        "返回管理台",
        "/web-login/open",
        "/web-login/status",
        "https://store.weixin.qq.com/",
        "不会向你索要微信账号、密码",
        "不会保存微信密码",
    )
    for fragment in expected_fragments:
        assert fragment in html, f"login page missing element: {fragment}"

    forbidden_fragments = (
        "一键登录",
        "授权登录",
        "绑定微信小店",
        "登录后自动同步数据",
        "输入微信账号密码",
        "cookie-sentinel",
        "token-sentinel",
        "local storage",
        "localstorage",
    )
    for fragment in forbidden_fragments:
        assert fragment.lower() not in lowered, f"login page has misleading or sensitive text: {fragment}"


def test_open_web_login_returns_pid_profile_dir_and_login_url() -> None:
    with patched_web_login_paths() as paths:
        paths.script_path.write_text("// test script\n", encoding="utf-8")
        paths.playwright_dir.mkdir(parents=True)
        fake_popen = FakePopenFactory(pid=43210)

        with patched_popen(fake_popen):
            result = admin_app.open_web_login()

        assert result["status"] == "ok"
        assert result["data"]["pid"] == 43210
        assert result["data"]["profile_dir"] == str(paths.profile_dir)
        assert result["data"]["profile_dir_exists"] is True
        assert result["data"]["process_running"] is True
        assert result["data"]["login_url"] == "https://store.weixin.qq.com/"
        assert result["data"]["already_running"] is False
        assert fake_popen.calls == [
            {
                "cmd": ["node", str(paths.script_path)],
                "cwd": str(PROJECT_ROOT),
                "stdin": admin_app.subprocess.DEVNULL,
                "stdout": admin_app.subprocess.DEVNULL,
                "stderr": admin_app.subprocess.DEVNULL,
                "start_new_session": True,
            }
        ]


def test_open_web_login_reuses_existing_running_process() -> None:
    with patched_web_login_paths() as paths:
        paths.script_path.write_text("// test script\n", encoding="utf-8")
        paths.playwright_dir.mkdir(parents=True)
        fake_popen = FakePopenFactory(pid=54321)

        with patched_popen(fake_popen):
            first = admin_app.open_web_login()
            second = admin_app.open_web_login()

        assert first["status"] == "ok"
        assert second["status"] == "ok"
        assert second["data"]["pid"] == 54321
        assert second["data"]["process_running"] is True
        assert second["data"]["already_running"] is True
        assert len(fake_popen.calls) == 1


def test_open_web_login_script_missing_returns_error_without_starting_process() -> None:
    with patched_web_login_paths() as paths:
        paths.playwright_dir.mkdir(parents=True)
        fake_popen = FakePopenFactory(pid=111)

        with patched_popen(fake_popen):
            result = admin_app.open_web_login()

        assert result["status"] == "error"
        assert result["data"]["missing"] == "script"
        assert result["data"]["script_path"] == str(paths.script_path)
        assert result["data"]["profile_dir"] == str(paths.profile_dir)
        assert result["data"]["login_url"] == "https://store.weixin.qq.com/"
        assert fake_popen.calls == []


def test_open_web_login_playwright_missing_returns_error_without_starting_process() -> None:
    with patched_web_login_paths() as paths:
        paths.script_path.write_text("// test script\n", encoding="utf-8")
        fake_popen = FakePopenFactory(pid=222)

        with patched_popen(fake_popen):
            result = admin_app.open_web_login()

        assert result["status"] == "error"
        assert result["data"]["missing"] == "playwright"
        assert result["data"]["playwright_dir"] == str(paths.playwright_dir)
        assert result["data"]["profile_dir"] == str(paths.profile_dir)
        assert result["data"]["login_url"] == "https://store.weixin.qq.com/"
        assert fake_popen.calls == []


class patched_web_login_paths:
    def __init__(self) -> None:
        self.temp_dir_context = tempfile.TemporaryDirectory(prefix="wechat_web_login_")
        self.temp_dir = Path(self.temp_dir_context.name)
        self.script_path = self.temp_dir / "scripts" / "auth" / "open_wechat_store_login.mjs"
        self.profile_dir = self.temp_dir / "data" / "browser-profile"
        self.playwright_dir = self.temp_dir / "node_modules" / "playwright"
        self.original_script_path = admin_app.WEB_LOGIN_SCRIPT_PATH
        self.original_profile_dir = admin_app.WEB_LOGIN_PROFILE_DIR
        self.original_playwright_dir = admin_app.PLAYWRIGHT_NODE_MODULE_DIR
        self.original_process = admin_app._web_login_process

    def __enter__(self) -> "patched_web_login_paths":
        self.script_path.parent.mkdir(parents=True)
        admin_app.WEB_LOGIN_SCRIPT_PATH = self.script_path
        admin_app.WEB_LOGIN_PROFILE_DIR = self.profile_dir
        admin_app.PLAYWRIGHT_NODE_MODULE_DIR = self.playwright_dir
        admin_app._web_login_process = None
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        admin_app.WEB_LOGIN_SCRIPT_PATH = self.original_script_path
        admin_app.WEB_LOGIN_PROFILE_DIR = self.original_profile_dir
        admin_app.PLAYWRIGHT_NODE_MODULE_DIR = self.original_playwright_dir
        admin_app._web_login_process = self.original_process
        self.temp_dir_context.cleanup()


class patched_popen:
    def __init__(self, replacement: Callable[..., object]) -> None:
        self.replacement = replacement
        self.original = admin_app.subprocess.Popen

    def __enter__(self) -> None:
        admin_app.subprocess.Popen = self.replacement

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        admin_app.subprocess.Popen = self.original


class FakePopenFactory:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.calls: list[dict[str, object]] = []
        self.process: FakeProcess | None = None

    def __call__(self, cmd: list[str], **kwargs: object) -> "FakeProcess":
        self.calls.append({"cmd": cmd, **kwargs})
        self.process = FakeProcess(self.pid)
        return self.process


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def poll(self) -> None:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
