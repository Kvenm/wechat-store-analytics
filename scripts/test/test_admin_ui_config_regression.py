#!/usr/bin/env python3
from __future__ import annotations

import json
import re
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
        test_root_html_uses_left_menu_sections,
        test_root_html_contains_auth_settings_entry,
        test_root_html_marks_local_export_entry_frontend_pending,
        test_root_html_contains_api_sync_entry,
        test_capabilities_exposes_registry_without_secrets,
        test_web_export_accepts_manual_shop_without_shops_json,
        test_web_export_accepts_manual_shop_not_in_shops_json,
        test_task_check_accepts_only_supported_order_export,
        test_task_check_accepts_local_export_product_list,
        test_task_check_accepts_local_export_auto,
        test_local_export_deep_check_reads_files_without_starting_task,
        test_manual_export_without_metadata_can_be_checked,
        test_manual_export_auto_detects_mixed_tables,
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
        "微信小店经营分析工作台",
        "店铺 ID",
        "店铺名称",
        "shopInfoSummary",
        'id="collectShopId" name="shop_id" type="hidden"',
        'id="collectShopName" name="shop_name" type="hidden"',
        'href="/login"',
        "扫码登录",
        "订单导出",
        "启动订单导出",
        "orderTaskForm",
        "collectFrom",
        "collectTo",
        "startOrderTask",
        "任务进度",
        "获取数据",
        "整理数据",
        "生成报告",
        "经营数据中心",
        "功能菜单",
        "数据同步",
        "授权状态",
        "报告中心",
        "reportsPanel",
        "reportDetail",
        "/reports",
        "side-nav",
        "main-content",
        "selectMenuSection",
        "menu-hidden",
    )
    for fragment in expected_fragments:
        assert fragment in html, f"root HTML missing core element: {fragment}"


def test_root_html_uses_left_menu_sections() -> None:
    response = admin_app.root()
    html = response.body.decode("utf-8")

    assert '<main class="app-shell">' in html
    assert '<main class="shell">' not in html
    assert 'class="side-nav-item menu-trigger"' in html
    assert 'document.querySelectorAll(".menu-section")' in html
    assert 'document.querySelectorAll(".side-nav-item")' in html
    assert 'classList.toggle("menu-hidden"' in html
    assert 'classList.toggle("active"' in html
    assert 'selectMenuSection("apiSyncSection")' in html

    expected_sections = {
        "apiSyncSection",
        "authSection",
        "localExportSection",
        "webOrderSection",
        "capabilitySection",
        "statusSection",
        "recordsSection",
    }
    targets = set(re.findall(r'data-menu-target="([^"]+)"', html))
    section_ids = set(re.findall(r'<section id="([^"]+)" class="[^"]*menu-section', html))

    assert targets == expected_sections
    assert expected_sections <= section_ids
    for section_id in expected_sections:
      assert f'aria-controls="{section_id}"' in html

    hidden_admin_fragments = (
        "开放 API 配置",
        "状态总览",
        "同步记录",
        "API 配置",
        "/docs",
    )
    for fragment in hidden_admin_fragments:
        assert fragment not in html, f"root HTML should not expose admin element: {fragment}"


def test_root_html_contains_auth_settings_entry() -> None:
    response = admin_app.root()
    html = response.body.decode("utf-8")

    expected_fragments = (
        "authSection",
        "授权状态",
        "扫码登录只用于订单导出",
        "此处只展示授权结果",
        "当前页面只展示授权状态，不提供手动填写",
        "接口密钥未读取",
        "接口授权未读取",
        "refreshConfig",
        "/api-config",
        "授权状态已刷新",
    )
    for fragment in expected_fragments:
        assert fragment in html, f"root HTML missing auth setting element: {fragment}"

    hidden_fragments = (
        "接口应用 ID",
        "保存授权设置",
        "保存并获取接口授权",
        "saveAndFetchToken",
        "buildConfigPayload",
        'id="appId"',
        'id="appSecret"',
    )
    for fragment in hidden_fragments:
        assert fragment not in html, f"root HTML should not expose editable auth setting: {fragment}"


def test_root_html_marks_local_export_entry_frontend_pending() -> None:
    response = admin_app.root()
    html = response.body.decode("utf-8")

    expected_fragments = (
        "文件导入",
        "localExportSection",
        "导出文件夹",
        "支持 Excel / CSV",
        "local_export",
        "source_dir",
        "localExportTypes",
        "自动识别导出文件",
        "capabilitiesPanel",
        "/capabilities",
        "数据范围",
        "检查文件",
        "导入并分析",
        "/tasks/local-export/check",
        "renderImportInspection",
        "renderBusinessReadiness",
        "文件检查完成",
        "缺少必要列",
        "已识别字段",
        "没有匹配到可用字段",
    )
    for fragment in expected_fragments:
        assert fragment in html, f"root HTML missing local export element: {fragment}"

    hidden_fragments = (
        "识别依据",
        "缺关键列",
        "选择已放入导出文件的本地文件夹",
        "data/raw/collect",
    )
    for fragment in hidden_fragments:
        assert fragment not in html, f"root HTML should not expose local export detail: {fragment}"


def test_root_html_contains_api_sync_entry() -> None:
    response = admin_app.root()
    html = response.body.decode("utf-8")

    expected_fragments = (
        "数据同步",
        "apiSyncSection",
        "apiSyncForm",
        'name="api_sync_endpoint"',
        "apiSyncGenerateReport",
        "startApiSyncTask",
        "selectedApiSyncEndpoints",
        "renderApiSyncResult",
        "/api-sync/runs",
        "完成后生成分析报告",
        "开始数据同步",
        'value="compass_shop"',
        'value="compass_product"',
        'value="compass_audience"',
        "店铺经营概览",
        "商品表现分析",
        "客户画像",
    )
    for fragment in expected_fragments:
        assert fragment in html, f"root HTML missing API sync element: {fragment}"

    hidden_fragments = (
        "官方 API 同步",
        "同步后生成报告",
        "店铺罗盘",
        "商品罗盘",
        "可追加 compass_",
    )
    for fragment in hidden_fragments:
        assert fragment not in html, f"root HTML should not expose API sync detail: {fragment}"


def test_capabilities_exposes_registry_without_secrets() -> None:
    result = admin_app.capabilities()
    assert result["status"] == "ok"
    capabilities = {item["export_type"]: item for item in result["data"]}
    assert capabilities["orders"]["web_enabled"] is True
    assert capabilities["orders"]["import_enabled"] is True
    assert capabilities["product_list"]["web_enabled"] is False
    assert capabilities["product_list"]["import_enabled"] is True
    assert capabilities["refunds"]["web_enabled"] is False
    assert capabilities["refunds"]["import_enabled"] is True
    assert capabilities["refunds"]["table_hint"] == "refunds"
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert "token" not in payload.lower()
    assert "secret" not in payload.lower()


def test_web_export_accepts_manual_shop_without_shops_json() -> None:
    with patched_shops_config_missing():
        result = admin_app.check_task_request(
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

    assert result["status"] == "ok"
    assert result["data"]["shop_id"] == SHOP_ID
    assert result["data"]["shop_name"] == SHOP_NAME


def test_web_export_accepts_manual_shop_not_in_shops_json() -> None:
    with patched_shops_config(
        {
            "shops": [
                {"id": "configured-shop", "name": "Configured Shop", "enabled": True},
            ]
        }
    ):
        result = admin_app.check_task_request(
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

    assert result["status"] == "ok"
    assert result["data"]["shop_id"] == SHOP_ID
    assert result["data"]["shop_name"] == SHOP_NAME


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
    assert "未开放的模块" in invalid_result["message"]
    assert mixed_invalid_result["status"] == "error"
    assert "未开放的模块" in mixed_invalid_result["message"]
    assert "products" in mixed_invalid_result["message"]
    assert "reviews" in mixed_invalid_result["message"]


def test_task_check_accepts_local_export_product_list() -> None:
    with local_export_source_dir(export_type="product_list", table_hint="products") as source_dir:
        result = admin_app.check_task_request(
            admin_app.TaskRequest(
                shop_id=SHOP_ID,
                source_type="local_export",
                task_name="商品列表本地导入",
                params={
                    "from": "2026-06-01",
                    "to": "2026-06-03",
                    "types": ["product_list"],
                    "shop_name": SHOP_NAME,
                    "source_dir": str(source_dir),
                },
            )
        )

    assert result["status"] == "ok"
    assert result["data"]["source_type"] == "local_export"
    assert result["data"]["types"] == ["product_list"]


def test_task_check_accepts_local_export_auto() -> None:
    with manual_product_list_source_dir(include_order_file=True) as source_dir:
        result = admin_app.check_task_request(
            admin_app.TaskRequest(
                shop_id=SHOP_ID,
                source_type="local_export",
                task_name="自动识别本地导入",
                params={
                    "from": "2026-06-01",
                    "to": "2026-06-03",
                    "types": ["auto"],
                    "shop_name": SHOP_NAME,
                    "source_dir": str(source_dir),
                },
            )
        )

    assert result["status"] == "ok"
    assert result["data"]["source_type"] == "local_export"
    assert result["data"]["local_export_mode"] == "manual_export"
    assert result["data"]["types"] == ["auto"]


def test_local_export_deep_check_reads_files_without_starting_task() -> None:
    with local_export_source_dir() as source_dir:
        result = admin_app.check_local_export(
            admin_app.TaskRequest(
                shop_id=SHOP_ID,
                source_type="local_export",
                task_name="深度校验本地导出",
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
    assert result["data"]["mode"] == "check_only"
    assert result["data"]["db_path"] is None
    assert result["data"]["batch_counts"]["orders"] == 1
    assert result["data"]["db_counts"] == {}
    inspection = result["data"]["inspection"]
    assert inspection["inspection_version"] == 1
    assert inspection["totals"]["tables"]["orders"] == 1
    assert inspection["source_inspections"][0]["selected_table"] == "orders"
    assert inspection["source_inspections"][0]["status"] == "importable"
    readiness = {item["key"]: item for item in inspection["business_readiness"]}
    assert readiness["order_kpi"]["status"] == "supported"
    assert readiness["product_contribution"]["status"] == "blocked"


def test_manual_export_without_metadata_can_be_checked() -> None:
    with manual_product_list_source_dir(include_order_file=True) as source_dir:
        result = admin_app.check_local_export(
            admin_app.TaskRequest(
                shop_id=SHOP_ID,
                source_type="local_export",
                task_name="手动商品列表校验",
                params={
                    "from": "2026-06-01",
                    "to": "2026-06-03",
                    "types": ["product_list"],
                    "shop_name": SHOP_NAME,
                    "source_dir": str(source_dir),
                },
            )
        )

    assert result["status"] == "ok"
    assert result["data"]["mode"] == "check_only"
    assert result["data"]["expected_types"] == ["product_list"]
    assert result["data"]["manifest_policy"] == "ignore"
    assert result["data"]["batch_counts"]["orders"] == 0
    assert result["data"]["batch_counts"]["products"] == 2
    assert result["data"]["batch_counts"]["product_skus"] == 2
    assert result["data"]["db_counts"] == {}
    source_inspections = result["data"]["inspection"]["source_inspections"]
    product_inspection = next(item for item in source_inspections if item["selected_table"] == "products")
    assert product_inspection["row_counts"]["derived_tables"] == {"product_skus": 2}
    assert any(field["field"] == "product_name" for field in product_inspection["field_matches"])
    warning_codes = {warning.get("code") for warning in result["data"]["warnings"]}
    assert "unexpected_table_for_expected_types" in warning_codes


def test_manual_export_auto_detects_mixed_tables() -> None:
    with manual_product_list_source_dir(include_order_file=True) as source_dir:
        result = admin_app.check_local_export(
            admin_app.TaskRequest(
                shop_id=SHOP_ID,
                source_type="local_export",
                task_name="手动目录自动识别",
                params={
                    "from": "2026-06-01",
                    "to": "2026-06-03",
                    "types": ["auto"],
                    "shop_name": SHOP_NAME,
                    "source_dir": str(source_dir),
                },
            )
        )

    assert result["status"] == "ok"
    assert result["data"]["mode"] == "check_only"
    assert result["data"]["expected_types"] == []
    assert result["data"]["manifest_policy"] == "ignore"
    assert result["data"]["batch_counts"]["orders"] == 1
    assert result["data"]["batch_counts"]["products"] == 2
    assert result["data"]["batch_counts"]["product_skus"] == 2
    readiness = {item["key"]: item for item in result["data"]["inspection"]["business_readiness"]}
    assert readiness["order_kpi"]["status"] == "supported"
    assert readiness["product_profile"]["status"] == "supported"
    warning_codes = {warning.get("code") for warning in result["data"]["warnings"]}
    assert "unexpected_table_for_expected_types" not in warning_codes


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


class patched_shops_config_missing:
    def __enter__(self) -> Path:
        self.temp_dir_context = tempfile.TemporaryDirectory(prefix="wechat_missing_shops_config_")
        self.project_root = Path(self.temp_dir_context.name)
        (self.project_root / "config").mkdir(parents=True, exist_ok=True)
        self.original_task_runner_root = admin_app.normalize_web_export_payload.__globals__["PROJECT_ROOT"]
        admin_app.normalize_web_export_payload.__globals__["PROJECT_ROOT"] = self.project_root
        return self.project_root

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        admin_app.normalize_web_export_payload.__globals__["PROJECT_ROOT"] = self.original_task_runner_root
        self.temp_dir_context.cleanup()


class patched_shops_config:
    def __init__(self, data: dict[str, object]) -> None:
        self.data = data

    def __enter__(self) -> Path:
        self.temp_dir_context = tempfile.TemporaryDirectory(prefix="wechat_shops_config_")
        self.project_root = Path(self.temp_dir_context.name)
        config_dir = self.project_root / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "shops.json").write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        self.original_task_runner_root = admin_app.normalize_web_export_payload.__globals__["PROJECT_ROOT"]
        admin_app.normalize_web_export_payload.__globals__["PROJECT_ROOT"] = self.project_root
        return self.project_root

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        admin_app.normalize_web_export_payload.__globals__["PROJECT_ROOT"] = self.original_task_runner_root
        self.temp_dir_context.cleanup()


class local_export_source_dir:
    def __init__(self, export_type: str = "orders", table_hint: str = "orders") -> None:
        self.export_type = export_type
        self.table_hint = table_hint
        local_export_root = admin_app.PROJECT_ROOT / "data" / "raw"
        local_export_root.mkdir(parents=True, exist_ok=True)
        self.temp_dir_context = tempfile.TemporaryDirectory(
            prefix="wechat_local_export_rerun_",
            dir=local_export_root,
        )
        self.source_dir = Path(self.temp_dir_context.name)

    def __enter__(self) -> Path:
        export_path = self.source_dir / f"{self.export_type}.csv"
        export_path.write_text(
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
            "export_type": self.export_type,
            "table_hint": self.table_hint,
            "saved_path": export_path.name,
            "original_filename": export_path.name,
            "shop_id": SHOP_ID,
            "shop_name": SHOP_NAME,
            "status": "completed",
            "size_bytes": export_path.stat().st_size,
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


class manual_product_list_source_dir:
    def __init__(self, include_order_file: bool = False) -> None:
        self.include_order_file = include_order_file
        local_export_root = admin_app.PROJECT_ROOT / "data" / "raw"
        local_export_root.mkdir(parents=True, exist_ok=True)
        self.temp_dir_context = tempfile.TemporaryDirectory(
            prefix="manual_product_list_2026-06-01_2026-06-03_",
            dir=local_export_root,
        )
        self.source_dir = Path(self.temp_dir_context.name)

    def __enter__(self) -> Path:
        export_path = self.source_dir / "导出商品.csv"
        export_path.write_text(
            "\n".join(
                [
                    "商品ID,商品名称,SKU ID,规格名称,销售价,可售库存,商品类目,上下架状态,商家编码",
                    "p-001,夏季连衣裙,sku-001,M码,129.00,20,女装,销售中,B001",
                    "p-001,夏季连衣裙,sku-002,L码,129.00,12,女装,销售中,B002",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        if self.include_order_file:
            order_path = self.source_dir / "orders.csv"
            order_path.write_text(
                "\n".join(
                    [
                        "订单号,下单时间,订单状态,支付金额",
                        "order-001,2026-06-01 10:00:00,已完成,12.30",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
        return self.source_dir

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.temp_dir_context.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
