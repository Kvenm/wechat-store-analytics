#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from datetime import UTC, datetime, timedelta
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
        test_web_login_auth_probe_result_is_exposed_and_cached,
        test_web_login_status_fresh_forces_auth_probe,
        test_login_page_contains_dedicated_scan_login_ui,
        test_open_web_login_returns_pid_profile_dir_and_login_url,
        test_open_web_login_restarts_existing_running_process_to_refresh_qr,
        test_close_web_login_stops_process_without_deleting_profile,
        test_web_login_status_marks_stale_qr_as_maybe_expired,
        test_export_verification_status_and_screenshot_are_challenge_bound,
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
        assert result["data"]["authenticated"] is False
        assert result["data"]["export_ready"] is False
        assert result["data"]["login_state"] == "cdp_unavailable"
        assert result["data"]["pid"] is None
        assert result["data"]["login_url"] == "https://store.weixin.qq.com/"
        assert result["data"]["screenshot_url"] == "/web-login/screenshot"
        assert SENSITIVE_COOKIE not in payload
        assert SENSITIVE_TOKEN not in payload
        assert "cookie-sentinel" not in payload
        assert "token-sentinel" not in payload
        assert "local storage" not in payload
        assert "localstorage" not in payload


def test_web_login_auth_probe_result_is_exposed_and_cached() -> None:
    original_script_path = admin_app.WEB_LOGIN_CHECK_SCRIPT_PATH
    original_run = admin_app.subprocess.run
    calls: list[list[str]] = []
    with tempfile.TemporaryDirectory(prefix="wechat_login_probe_") as temp_dir:
        script_path = Path(temp_dir) / "check_wechat_store_login.mjs"
        script_path.write_text("// probe fixture\n", encoding="utf-8")

        def fake_run(command: list[str], **_kwargs: object) -> object:
            calls.append(command)
            return admin_app.subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "state": "logged_in",
                        "authenticated": True,
                        "url": "https://store.weixin.qq.com/shop/home",
                        "title": "微信小店",
                        "message": "probe message",
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )

        try:
            admin_app.WEB_LOGIN_CHECK_SCRIPT_PATH = script_path
            admin_app.subprocess.run = fake_run
            admin_app.clear_web_login_auth_cache()
            first = admin_app.web_login_auth_state(cdp_available=True, force=True)
            second = admin_app.web_login_auth_state(cdp_available=True)
        finally:
            admin_app.WEB_LOGIN_CHECK_SCRIPT_PATH = original_script_path
            admin_app.subprocess.run = original_run
            admin_app.clear_web_login_auth_cache()

    assert first["state"] == "logged_in"
    assert first["authenticated"] is True
    assert first["message"] == "微信小店后台已登录，可以启动页面表格导出。"
    assert second == first
    assert len(calls) == 1


def test_web_login_status_fresh_forces_auth_probe() -> None:
    original_status_data = admin_app.web_login_status_data
    calls: list[bool] = []

    def fake_status_data(*, force_auth_check: bool = False) -> dict[str, object]:
        calls.append(force_auth_check)
        return {
            "authenticated": False,
            "export_ready": False,
            "login_state": "login_required",
        }

    try:
        admin_app.web_login_status_data = fake_status_data
        cached = admin_app.web_login_status()
        fresh = admin_app.web_login_status(fresh=True)
    finally:
        admin_app.web_login_status_data = original_status_data

    assert cached["status"] == "ok"
    assert fresh["status"] == "ok"
    assert calls == [False, True]


def test_login_page_contains_dedicated_scan_login_ui() -> None:
    response = admin_app.login_page()
    html = response.body.decode("utf-8")
    lowered = html.lower()

    expected_fragments = (
        "微信小店扫码登录",
        "打开/刷新扫码二维码",
        "二维码变灰表示已过期",
        "二维码可能已过期",
        "刷新扫码二维码",
        "qr_maybe_expired",
        "复制后台登录链接",
        "扫码截图",
        "/web-login/screenshot",
        "刷新登录状态",
        "authStatus",
        "已进入小店后台",
        "后台登录未读取",
        "data.authenticated",
        "data.login_state_message",
        "返回管理台",
        "/web-login/open",
        "/web-login/status",
        "https://store.weixin.qq.com/",
        "不会向你索要微信账号、密码",
        "不会保存微信密码",
        "服务器浏览器登录态",
        "不要手动关闭扫码页",
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
        "请先关闭官方后台登录窗口",
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
        assert result["data"]["screenshot_url"] == "/web-login/screenshot"
        assert result["data"]["qr_maybe_expired"] is False
        assert result["data"]["login_elapsed_seconds"] is not None
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


def test_open_web_login_restarts_existing_running_process_to_refresh_qr() -> None:
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
        assert second["data"]["already_running"] is False
        assert fake_popen.processes[0].terminated is True
        assert len(fake_popen.calls) == 2


def test_close_web_login_stops_process_without_deleting_profile() -> None:
    with patched_web_login_paths() as paths:
        paths.script_path.write_text("// test script\n", encoding="utf-8")
        paths.playwright_dir.mkdir(parents=True)
        fake_popen = FakePopenFactory(pid=76543)

        with patched_popen(fake_popen):
            admin_app.open_web_login()
            result = admin_app.close_web_login()

        assert result["status"] == "ok"
        assert result["data"]["was_running"] is True
        assert result["data"]["process_running"] is False
        assert result["data"]["profile_dir_exists"] is True
        assert fake_popen.processes[0].terminated is True


def test_web_login_status_marks_stale_qr_as_maybe_expired() -> None:
    with patched_web_login_paths() as paths:
        paths.script_path.write_text("// test script\n", encoding="utf-8")
        paths.playwright_dir.mkdir(parents=True)
        fake_popen = FakePopenFactory(pid=65432)

        with patched_popen(fake_popen):
            admin_app.open_web_login()

        admin_app._web_login_started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            seconds=admin_app.WEB_LOGIN_QR_STALE_SECONDS + 1
        )
        status = admin_app.web_login_status_data()

        assert status["qr_maybe_expired"] is True
        assert status["login_elapsed_seconds"] >= admin_app.WEB_LOGIN_QR_STALE_SECONDS


def test_export_verification_status_and_screenshot_are_challenge_bound() -> None:
    with patched_web_login_paths() as paths:
        challenge_id = "challenge-sentinel"
        paths.export_screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        paths.export_screenshot_path.write_bytes(b"png-sentinel")
        paths.export_status_path.write_text(
            json.dumps(
                {
                    "challenge_id": challenge_id,
                    "required": True,
                    "target_key": "fund_flows",
                    "target_label": "资金流水",
                    "control_label": "全部导出",
                    "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "timeout_ms": 300000,
                    "screenshot_path": str(paths.export_screenshot_path),
                }
            ),
            encoding="utf-8",
        )

        status = admin_app.web_login_status_data()["export_verification"]
        response = admin_app.web_login_export_verification_screenshot(challenge_id=challenge_id)
        wrong_challenge = admin_app.web_login_export_verification_screenshot(challenge_id="wrong")

        assert status["required"] is True
        assert status["challenge_id"] == challenge_id
        assert status["target_key"] == "fund_flows"
        assert status["target_label"] == "资金流水"
        assert status["control_label"] == "全部导出"
        assert "资金流水" in status["message"]
        assert status["screenshot_ready"] is True
        assert status["remaining_seconds"] is not None
        assert status["remaining_seconds"] > 0
        assert str(response.path) == str(paths.export_screenshot_path)
        assert response.headers["cache-control"] == "private, no-store, max-age=0"
        assert wrong_challenge.status_code == 404

        paths.export_status_path.write_text(
            json.dumps(
                {
                    "challenge_id": challenge_id,
                    "required": True,
                    "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "timeout_ms": 300000,
                }
            ),
            encoding="utf-8",
        )
        legacy_status = admin_app.web_login_export_verification_status()["data"]
        assert legacy_status["required"] is True
        assert legacy_status["target_key"] == ""
        assert legacy_status["target_label"] == ""
        assert legacy_status["control_label"] == ""
        assert legacy_status["message"] == "微信要求扫码确认本次导出，请使用当前二维码完成验证。"

        paths.export_status_path.write_text(
            json.dumps({"challenge_id": challenge_id, "required": False, "timeout_ms": 300000}),
            encoding="utf-8",
        )
        completed = admin_app.web_login_export_verification_status()
        assert completed["data"]["required"] is False
        assert admin_app.web_login_export_verification_screenshot(challenge_id=challenge_id).status_code == 404


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
        self.screenshot_path = self.temp_dir / "data" / "web-login-screenshot.png"
        self.export_screenshot_path = self.temp_dir / "data" / "export-verification-screenshot.png"
        self.export_status_path = self.temp_dir / "data" / "export-verification-status.json"
        self.original_script_path = admin_app.WEB_LOGIN_SCRIPT_PATH
        self.original_profile_dir = admin_app.WEB_LOGIN_PROFILE_DIR
        self.original_playwright_dir = admin_app.PLAYWRIGHT_NODE_MODULE_DIR
        self.original_screenshot_path = admin_app.WEB_LOGIN_SCREENSHOT_PATH
        self.original_export_screenshot_path = admin_app.EXPORT_VERIFICATION_SCREENSHOT_PATH
        self.original_export_status_path = admin_app.EXPORT_VERIFICATION_STATUS_PATH
        self.original_process = admin_app._web_login_process
        self.original_started_at = admin_app._web_login_started_at
        self.original_cdp_available = admin_app.web_login_cdp_available

    def __enter__(self) -> "patched_web_login_paths":
        self.script_path.parent.mkdir(parents=True)
        admin_app.WEB_LOGIN_SCRIPT_PATH = self.script_path
        admin_app.WEB_LOGIN_PROFILE_DIR = self.profile_dir
        admin_app.PLAYWRIGHT_NODE_MODULE_DIR = self.playwright_dir
        admin_app.WEB_LOGIN_SCREENSHOT_PATH = self.screenshot_path
        admin_app.EXPORT_VERIFICATION_SCREENSHOT_PATH = self.export_screenshot_path
        admin_app.EXPORT_VERIFICATION_STATUS_PATH = self.export_status_path
        admin_app._web_login_process = None
        admin_app._web_login_started_at = None
        admin_app.web_login_cdp_available = lambda: False
        admin_app.clear_web_login_auth_cache()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        admin_app.WEB_LOGIN_SCRIPT_PATH = self.original_script_path
        admin_app.WEB_LOGIN_PROFILE_DIR = self.original_profile_dir
        admin_app.PLAYWRIGHT_NODE_MODULE_DIR = self.original_playwright_dir
        admin_app.WEB_LOGIN_SCREENSHOT_PATH = self.original_screenshot_path
        admin_app.EXPORT_VERIFICATION_SCREENSHOT_PATH = self.original_export_screenshot_path
        admin_app.EXPORT_VERIFICATION_STATUS_PATH = self.original_export_status_path
        admin_app._web_login_process = self.original_process
        admin_app._web_login_started_at = self.original_started_at
        admin_app.web_login_cdp_available = self.original_cdp_available
        admin_app.clear_web_login_auth_cache()
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
        self.processes: list[FakeProcess] = []

    def __call__(self, cmd: list[str], **kwargs: object) -> "FakeProcess":
        self.calls.append({"cmd": cmd, **kwargs})
        self.process = FakeProcess(self.pid)
        self.processes.append(self.process)
        return self.process


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: int | None = None) -> int:
        return self.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
