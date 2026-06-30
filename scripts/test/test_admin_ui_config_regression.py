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


INITIAL_SECRET = "initial-app-secret-sentinel"
INITIAL_TOKEN = "initial-access-token-sentinel"
UPDATED_SECRET = "updated-app-secret-sentinel"
UPDATED_TOKEN = "updated-access-token-sentinel"
SHOP_ID = "yijia-baihuo"
SHOP_NAME = "艺家百货甄选店"


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_get_api_config_does_not_leak_secret_or_token_values,
        test_post_api_config_writes_env_local,
        test_empty_secret_and_token_do_not_overwrite_existing_values,
        test_root_html_contains_core_admin_ui_elements,
        test_root_html_marks_local_export_entry_frontend_pending,
        test_task_check_accepts_only_supported_order_export,
        test_task_check_accepts_source_type_local_export,
        test_task_check_accepts_params_mode_local_export,
        test_task_check_rejects_out_of_bounds_local_export_source_dir,
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
        print(f"{failed} admin UI config regression test(s) failed.")
        return 1

    print(f"{len(tests)} admin UI config regression tests passed.")
    return 0


def test_get_api_config_does_not_leak_secret_or_token_values() -> None:
    with patched_env_file(
        "\n".join(
            [
                "WECHAT_STORE_APP_ID=wx-local-admin",
                f"WECHAT_STORE_APP_SECRET={INITIAL_SECRET}",
                f"WECHAT_STORE_ACCESS_TOKEN={INITIAL_TOKEN}",
                "WECHAT_STORE_API_BASE_URL=https://api.weixin.qq.com",
                "WECHAT_STORE_SYNC_DRY_RUN=true",
                "WECHAT_STORE_RAW_ARCHIVE_DIR=data/raw/api",
                "WECHAT_STORE_SHOP_ID=shop-001",
                'WECHAT_STORE_SHOP_NAME="本地运营店"',
            ]
        )
        + "\n"
    ) as env_path:
        result = admin_app.api_config()
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True)

        assert result["status"] == "ok"
        assert result["data"]["env_path"] == str(env_path)
        assert result["data"]["exists"] is True
        assert result["data"]["values"]["app_id"] == "wx-local-admin"
        assert result["data"]["values"]["api_base_url"] == "https://api.weixin.qq.com"
        assert result["data"]["values"]["sync_dry_run"] is True
        assert result["data"]["values"]["raw_archive_dir"] == "data/raw/api"
        assert result["data"]["values"]["shop_id"] == "shop-001"
        assert result["data"]["values"]["shop_name"] == "本地运营店"
        assert result["data"]["secrets"]["app_secret"] == {"configured": True}
        assert result["data"]["secrets"]["access_token"]["configured"] is True
        assert INITIAL_SECRET not in payload
        assert INITIAL_TOKEN not in payload


def test_post_api_config_writes_env_local() -> None:
    with patched_env_file("") as env_path:
        result = admin_app.update_api_config(
            admin_app.ApiConfigRequest(
                app_id="wx-posted-admin",
                app_secret=UPDATED_SECRET,
                access_token=UPDATED_TOKEN,
                api_base_url="https://api.weixin.qq.com",
                sync_dry_run=False,
                raw_archive_dir="data/raw/api archive",
                shop_id="shop-posted",
                shop_name="Posted Shop",
            )
        )

        written = admin_app.parse_env_file(env_path)
        assert result["status"] == "ok"
        assert result["data"]["values"]["app_id"] == "wx-posted-admin"
        assert result["data"]["values"]["raw_archive_dir"] == "data/raw/api archive"
        assert result["data"]["values"]["sync_dry_run"] is False
        assert result["data"]["secrets"]["app_secret"] == {"configured": True}
        assert result["data"]["secrets"]["access_token"]["configured"] is True
        assert written == {
            "WECHAT_STORE_APP_ID": "wx-posted-admin",
            "WECHAT_STORE_APP_SECRET": UPDATED_SECRET,
            "WECHAT_STORE_ACCESS_TOKEN": UPDATED_TOKEN,
            "WECHAT_STORE_API_BASE_URL": "https://api.weixin.qq.com",
            "WECHAT_STORE_SYNC_DRY_RUN": "false",
            "WECHAT_STORE_RAW_ARCHIVE_DIR": "data/raw/api archive",
            "WECHAT_STORE_SHOP_ID": "shop-posted",
            "WECHAT_STORE_SHOP_NAME": "Posted Shop",
        }
        assert "WECHAT_STORE_RAW_ARCHIVE_DIR=\"data/raw/api archive\"" in env_path.read_text(encoding="utf-8")


def test_empty_secret_and_token_do_not_overwrite_existing_values() -> None:
    with patched_env_file(
        "\n".join(
            [
                "WECHAT_STORE_APP_ID=wx-before",
                f"WECHAT_STORE_APP_SECRET={INITIAL_SECRET}",
                f"WECHAT_STORE_ACCESS_TOKEN={INITIAL_TOKEN}",
                "WECHAT_STORE_API_BASE_URL=https://old.example.test",
                "WECHAT_STORE_SYNC_DRY_RUN=true",
                "WECHAT_STORE_RAW_ARCHIVE_DIR=data/raw/old",
                "WECHAT_STORE_SHOP_ID=shop-before",
                "WECHAT_STORE_SHOP_NAME=Before Shop",
            ]
        )
        + "\n"
    ) as env_path:
        result = admin_app.update_api_config(
            admin_app.ApiConfigRequest(
                app_id="wx-after",
                app_secret="",
                access_token="   ",
                api_base_url="https://new.example.test",
                sync_dry_run=True,
                raw_archive_dir="data/raw/new",
                shop_id="shop-after",
                shop_name="After Shop",
            )
        )

        written = admin_app.parse_env_file(env_path)
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
        assert written["WECHAT_STORE_APP_ID"] == "wx-after"
        assert written["WECHAT_STORE_APP_SECRET"] == INITIAL_SECRET
        assert written["WECHAT_STORE_ACCESS_TOKEN"] == INITIAL_TOKEN
        assert written["WECHAT_STORE_API_BASE_URL"] == "https://new.example.test"
        assert written["WECHAT_STORE_SYNC_DRY_RUN"] == "true"
        assert written["WECHAT_STORE_RAW_ARCHIVE_DIR"] == "data/raw/new"
        assert written["WECHAT_STORE_SHOP_ID"] == "shop-after"
        assert written["WECHAT_STORE_SHOP_NAME"] == "After Shop"
        assert result["data"]["secrets"]["app_secret"] == {"configured": True}
        assert result["data"]["secrets"]["access_token"]["configured"] is True
        assert INITIAL_SECRET not in payload
        assert INITIAL_TOKEN not in payload


def test_root_html_contains_core_admin_ui_elements() -> None:
    response = admin_app.root()
    html = response.body.decode("utf-8")

    expected_fragments = (
        "微信小店数据本地管理台",
        "服务状态",
        "/health",
        "开放 API 配置",
        "AppID",
        "AppSecret",
        "AccessToken",
        "获取 AccessToken",
        "/api-token/fetch",
        "需要先填 AppID/AppSecret",
        "页面只显示过期时间和来源，不显示 token 明文",
        "API Base URL",
        "Raw Archive Dir",
        "Dry Run",
        "Shop ID",
        "Shop Name",
        "配置状态",
        "已配置",
        "未配置",
        "同步记录",
        "/sync-runs",
        "/raw-api-responses",
        "网页导出需要扫码登录",
        "开放 API token 测试不需要扫码登录",
        "网页后台扫码登录",
        'href="/login"',
        "扫码登录",
        "打开扫码登录窗口",
        "会打开微信小店网页版，手动扫码登录；登录态保存在 data/browser-profile；不是开放 API token",
        "/web-login/status",
        "/web-login/open",
        "订单真实采集与分析",
        "启动订单导出分析",
        "orderTaskForm",
        "collectFrom",
        "collectTo",
        "startOrderTask",
        "采集任务",
        "导出/本地文件",
        "导入订单",
        "生成报告",
    )
    for fragment in expected_fragments:
        assert fragment in html, f"root HTML missing core element: {fragment}"


def test_root_html_marks_local_export_entry_frontend_pending() -> None:
    response = admin_app.root()
    html = response.body.decode("utf-8")

    expected_fragments = (
        "离线导入",
        "本地导出复跑",
        "local_export",
        "source_dir",
    )
    for fragment in expected_fragments:
        assert fragment in html, f"root HTML missing local export element: {fragment}"


def test_task_check_accepts_only_supported_order_export() -> None:
    valid_result = admin_app.check_task_request(
        admin_app.TaskRequest(
            shop_id=SHOP_ID,
            source_type="web_export",
            params={
                "from": "2026-06-01",
                "to": "2026-06-03",
                "types": ["orders"],
                "shop_name": SHOP_NAME,
            },
        )
    )
    invalid_result = admin_app.check_task_request(
        admin_app.TaskRequest(
            shop_id=SHOP_ID,
            source_type="web_export",
            params={
                "from": "2026-06-01",
                "to": "2026-06-03",
                "types": ["reviews"],
            },
        )
    )
    mixed_invalid_result = admin_app.check_task_request(
        admin_app.TaskRequest(
            shop_id=SHOP_ID,
            source_type="web_export",
            params={
                "from": "2026-06-01",
                "to": "2026-06-03",
                "types": ["orders", "products", "reviews"],
            },
        )
    )

    assert valid_result["status"] == "ok"
    assert valid_result["data"]["shop_id"] == SHOP_ID
    assert valid_result["data"]["types"] == ["orders"]
    assert invalid_result["status"] == "error"
    assert "只开放 orders" in invalid_result["message"]
    assert mixed_invalid_result["status"] == "error"
    assert "只开放 orders" in mixed_invalid_result["message"]
    assert "products" in mixed_invalid_result["message"]
    assert "reviews" in mixed_invalid_result["message"]


def test_task_check_accepts_source_type_local_export() -> None:
    with local_export_source_dir() as source_dir:
        result = admin_app.check_task_request(
            admin_app.TaskRequest(
                shop_id=SHOP_ID,
                source_type="local_export",
                task_name="复跑本地导出",
                params={
                    "from": "2026-06-01",
                    "to": "2026-06-03",
                    "types": ["orders"],
                    "shop_name": SHOP_NAME,
                    "source_dir": str(source_dir),
                },
            )
        )

    assert result["status"] == "ok"
    assert result["data"]["shop_id"] == SHOP_ID
    assert result["data"]["source_type"] == "local_export"
    assert Path(result["data"]["source_dir"]).resolve() == source_dir.resolve()
    assert result["data"]["types"] == ["orders"]


def test_task_check_accepts_params_mode_local_export() -> None:
    with local_export_source_dir() as source_dir:
        result = admin_app.check_task_request(
            admin_app.TaskRequest(
                shop_id=SHOP_ID,
                task_name="按 mode 复跑本地导出",
                params={
                    "mode": "local_export",
                    "from": "2026-06-01",
                    "to": "2026-06-03",
                    "types": ["orders"],
                    "shop_name": SHOP_NAME,
                    "source_dir": str(source_dir),
                },
            )
        )

    assert result["status"] == "ok"
    assert result["data"]["shop_id"] == SHOP_ID
    assert result["data"]["source_type"] == "local_export"
    assert Path(result["data"]["source_dir"]).resolve() == source_dir.resolve()
    assert result["data"]["types"] == ["orders"]


def test_task_check_rejects_out_of_bounds_local_export_source_dir() -> None:
    with tempfile.TemporaryDirectory(prefix="wechat_local_export_outside_") as source_dir:
        result = admin_app.check_task_request(
            admin_app.TaskRequest(
                shop_id=SHOP_ID,
                source_type="local_export",
                params={
                    "from": "2026-06-01",
                    "to": "2026-06-03",
                    "types": ["orders"],
                    "source_dir": source_dir,
                },
            )
        )

    assert result["status"] == "error"
    message = result["message"]
    assert (
        "source_dir" in message
        or "源目录" in message
        or "越界" in message
        or "不允许" in message
    ), message


class patched_env_file:
    def __init__(self, initial_content: str) -> None:
        self.initial_content = initial_content
        self.temp_dir_context = tempfile.TemporaryDirectory(prefix="wechat_admin_ui_config_")
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


class local_export_source_dir:
    def __init__(self) -> None:
        local_export_root = admin_app.PROJECT_ROOT / "data" / "raw"
        local_export_root.mkdir(parents=True, exist_ok=True)
        self.temp_dir_context = tempfile.TemporaryDirectory(
            prefix="wechat_local_export_rerun_",
            dir=local_export_root,
        )
        self.source_dir = Path(self.temp_dir_context.name)

    def __enter__(self) -> Path:
        orders_path = self.source_dir / "orders.csv"
        orders_path.write_text(
            "\n".join(
                [
                    "订单号,下单时间,订单状态,支付金额",
                    "order-001,2026-06-01 10:00:00,已完成,12.30",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        artifact = {
            "source_kind": "export_file",
            "source_type": "export_file",
            "export_type": "orders",
            "table_hint": "orders",
            "saved_path": orders_path.name,
            "original_filename": orders_path.name,
            "shop_id": SHOP_ID,
            "shop_name": SHOP_NAME,
            "status": "completed",
            "size_bytes": orders_path.stat().st_size,
        }
        manifest = {
            "task_id": "local-export-rerun-regression",
            "source_kinds": ["export_file", "ocr_file", "chart_image"],
            "artifact_count": 1,
            "artifacts": [artifact],
        }
        metadata = {
            "task_id": "local-export-rerun-regression",
            "status": "completed",
            "shop": {"id": SHOP_ID, "name": SHOP_NAME},
            "date_range": {"from": "2026-06-01", "to": "2026-06-03"},
            "artifacts": [artifact],
        }
        (self.source_dir / "artifacts-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (self.source_dir / "task-metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return self.source_dir

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.temp_dir_context.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
