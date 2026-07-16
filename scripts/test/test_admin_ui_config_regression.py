#!/usr/bin/env python3
from __future__ import annotations

import json
import os
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
import api.task_runner as task_runner  # noqa: E402


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
        test_root_html_contains_export_completion_modal,
        test_root_html_contains_repeating_export_verification_modal,
        test_root_html_uses_left_menu_sections,
        test_root_html_presents_capabilities_by_acquisition_method,
        test_root_html_maps_page_export_modules_to_targets,
        test_root_html_contains_auth_settings_entry,
        test_root_html_marks_local_export_entry_frontend_pending,
        test_root_html_contains_api_sync_entry,
        test_capabilities_exposes_registry_without_secrets,
        test_web_export_accepts_manual_shop_without_shops_json,
        test_web_export_accepts_manual_shop_not_in_shops_json,
        test_web_export_forces_headless_on_linux_without_display,
        test_create_task_requires_running_scan_login_browser,
        test_create_task_requires_authenticated_store_shell,
        test_create_task_starts_after_authenticated_store_shell,
        test_negative_collect_exit_code_is_explained_as_interruption,
        test_structured_export_failure_message_is_concise,
        test_export_only_failure_skips_non_collection_steps,
        test_run_step_enforces_hard_timeout,
        test_export_only_task_check_accepts_visible_page_tables,
        test_export_only_task_check_rejects_missing_targets,
        test_export_only_targets_are_validated_and_passed_to_collector,
        test_export_only_empty_shop_name_is_not_replaced_by_shop_id,
        test_export_only_completion_skips_import_analysis_and_report,
        test_export_only_nonzero_collect_preserves_failed_metadata_result,
        test_export_only_targets_without_pages_report_missing_results,
        test_task_check_accepts_only_supported_order_export,
        test_task_check_accepts_local_export_product_list,
        test_task_check_accepts_local_export_auto,
        test_visible_viewport_local_export_is_not_imported,
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
        "微信小店数据助手",
        "店铺 ID",
        "店铺名称",
        "shopInfoSummary",
        'id="collectShopId" name="shop_id" type="hidden"',
        'id="collectShopName" name="shop_name" type="hidden"',
        'href="/login"',
        "扫码登录",
        "页面表格导出",
        "启动页面表格导出",
        "校验登录并启动导出",
        "orderTaskForm",
        "exportActionStatus",
        "exportActionMessage",
        'role="status" aria-live="polite"',
        "正在实时校验微信小店后台登录状态",
        "button.disabled = true",
        'button.setAttribute("aria-busy", "true")',
        "resetOrderTaskButton",
        "requestError.data = payload.data",
        "loginStatus?.login_state",
        "未创建导出任务",
        "订单明细",
        "本轮只导出，不导入、不分析、不生成报告",
        "renderExportOnlyResult",
        "renderExportArtifactDownloads",
        "dedupeExportArtifacts",
        "formatFileSize",
        "/artifacts/${originalIndex}/download",
        "已自动隐藏",
        "本次没有生成可下载文件",
        "任务记录显示已有文件，但可下载清单缺失",
        "visible_tables",
        "webLoginAuthStatus",
        "后台待扫码",
        "正在确认微信小店后台登录状态",
        "conciseTaskErrorMessage",
        "taskStepsForDisplay",
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
        "本地采集助手",
        "collectorSection",
        "createCollectorJob",
        "loadCollectorJobs",
        "/collector/jobs",
        "scripts/collector/local_client.py",
        "window.location.origin",
        "WECHAT_STORE_ADMIN_USER",
        "WECHAT_STORE_ADMIN_PASSWORD",
        "本地可见订单兜底（仅原始文件）",
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

    assert 'href="${artifact.saved_path}' not in html
    assert 'href="${artifact.relative_path}' not in html
    assert "--server http://127.0.0.1:8001 --once" not in html


def test_root_html_contains_export_completion_modal() -> None:
    response = admin_app.root()
    html = response.body.decode("utf-8")

    expected_fragments = (
        'id="exportCompletionModal"',
        'role="dialog"',
        'aria-modal="true"',
        'id="exportCompletionFileCount"',
        'id="exportCompletionFailureCount"',
        'id="exportCompletionIcon"',
        'id="exportCompletionDetails"',
        'id="exportCompletionDownload"',
        "立即下载文件",
        "查看任务详情",
        "completionWatchTaskIds: new Set()",
        "completionModalShownTaskIds: new Set()",
        "function maybeShowExportCompletion(task)",
        "state.completionModalShownTaskIds.has(taskId)",
        "state.completionModalShownTaskIds.add(taskId)",
        "0 个文件不计为成功",
        "任务执行中出现错误",
        'task.result?.mode === "export_only"',
        "renderExportOnlyResult(task, task.result)",
        "一键下载全部 ZIP",
        "全部文件已整理为 1 个压缩包",
        "查看单个文件（",
        "isExportBundleArtifact",
    )
    for fragment in expected_fragments:
        assert fragment in html, f"root HTML missing export completion behavior: {fragment}"

    result_branch = html.index('if (task.result?.mode === "export_only")')
    error_branch = html.index("if (task.error?.message)", result_branch)
    assert result_branch < error_branch
    assert '.completion-modal[hidden]' in html
    assert "maybeShowExportCompletion(task);" in html


def test_root_html_contains_repeating_export_verification_modal() -> None:
    response = admin_app.root()
    html = response.body.decode("utf-8")

    expected_fragments = (
        'id="exportVerificationModal"',
        'id="exportVerificationImage"',
        'id="exportVerificationTarget"',
        'id="exportVerificationControl"',
        'id="refreshExportVerification"',
        "当前需要验证",
        "需要扫码确认本次导出",
        "后续模块可能再次要求验证",
        "/web-login/export-verification-status",
        "/web-login/export-verification-screenshot",
        "exportVerificationChallengeId",
        "exportVerificationDismissedChallengeId",
        "function pollExportVerificationStatus",
        "function showExportVerificationModal",
        "function refreshExportVerificationImage",
        'verification.target_label || ""',
        'verification.control_label || ""',
        '`需要扫码确认：${targetLabel}`',
        '`触发操作：${controlLabel}`',
        "verification.challenge_id !== state.exportVerificationChallengeId",
        "await pollExportVerificationStatus({ forceClose: terminal });",
    )
    for fragment in expected_fragments:
        assert fragment in html, f"root HTML missing export verification behavior: {fragment}"


def test_root_html_uses_left_menu_sections() -> None:
    response = admin_app.root()
    html = response.body.decode("utf-8")

    assert '<main class="app-shell">' in html
    assert '<main class="shell">' not in html
    assert 'class="side-nav-item menu-trigger"' in html
    assert 'document.querySelectorAll(".menu-section")' in html
    assert 'document.querySelectorAll(".menu-trigger, .workflow-jump")' in html
    assert 'classList.toggle("menu-hidden"' in html
    assert 'classList.toggle("active"' in html
    assert 'selectMenuSection("authSection")' in html
    assert 'function selectMenuSection(targetId = "authSection")' in html
    assert 'data-flow-step="connect"' in html
    assert 'data-flow-step="login"' in html
    assert 'data-flow-step="export"' in html
    assert 'data-flow-step="download"' in html
    assert 'class="mobile-bottom-nav"' in html
    assert 'id="mobileMenuButton"' in html
    assert 'class="workspace-surface"' in html
    assert 'class="connection-layout"' in html
    assert 'class="export-layout"' in html
    assert "function updateWorkflowState(targetId)" in html
    assert "function navigateToMenuSection(targetId)" in html
    assert 'role="progressbar"' in html
    assert 'aria-valuenow="0"' in html
    assert html.index('id="collectShopId"') < html.index('id="authSection"')
    assert html.index('id="collectFrom"') < html.index('id="authSection"')

    expected_sections = {
        "apiSyncSection",
        "authSection",
        "localExportSection",
        "webOrderSection",
        "collectorSection",
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


def test_root_html_presents_capabilities_by_acquisition_method() -> None:
    response = admin_app.root()
    html = response.body.decode("utf-8")

    expected_fragments = (
        "按获取方式查看哪些数据可直接导出",
        "可直接页面导出",
        "通过文件导入",
        "页面导出 · 文件导入",
        "当前页面未发现可用导出按钮",
        "page_export_status",
        "capability-group",
        "capability-method",
        "capability-state",
    )
    for fragment in expected_fragments:
        assert fragment in html, f"root HTML missing capability presentation: {fragment}"

    assert "无需订单导出" not in html
    assert "支持订单导出" not in html


def test_root_html_maps_page_export_modules_to_targets() -> None:
    response = admin_app.root()
    html = response.body.decode("utf-8")

    expected_fragments = (
        '本次导出数据（6 类）',
        'name="page_export_module" value="product_list"',
        'name="page_export_module" value="orders"',
        'name="page_export_module" value="fund_flows"',
        'name="page_export_module" value="transactions"',
        'name="page_export_module" value="product_data"',
        'name="page_export_module" value="compass_buyer_profile"',
        '商品管理 &gt; 商品列表（商品数据）',
        '订单明细',
        '资金流水',
        '交易数据（含页面内多张表）',
        '店铺数据 &gt; 商品数据（3 张表）',
        '电商罗盘 &gt; 买家人群特征（页面快照）',
        '6 个业务组会展开为 8 个采集页面',
        'product_list: ["product_list"]',
        'orders: ["orders"]',
        'fund_flows: ["fund_flows"]',
        'transactions: ["transactions"]',
        'product_data: ["product_core_conversion", "product_traffic_funnel", "product_detail"]',
        'compass_buyer_profile: ["compass_buyer_profile"]',
        "function selectedPageExportTargets()",
        "targets,",
        "请至少选择一个页面导出模块",
        "这里勾选的数据模块只控制官方接口同步，不会影响“页面表格导出”",
    )
    for fragment in expected_fragments:
        assert fragment in html, f"root HTML missing page export target mapping: {fragment}"

    page_export_form = html[html.index('id="orderTaskForm"'):html.index('</form>', html.index('id="orderTaskForm"'))]
    obsolete_modules = (
        'value="aftersale"',
        '售后退款',
        '店铺经营概览',
        '商品表现分析',
        '客户画像',
    )
    for fragment in obsolete_modules:
        assert fragment not in page_export_form, f"page export form still exposes obsolete module: {fragment}"


def test_root_html_contains_auth_settings_entry() -> None:
    response = admin_app.root()
    html = response.body.decode("utf-8")

    expected_fragments = (
        "authSection",
        "接口配置",
        "扫码登录用于页面表格导出",
        "只需填写 AppID 和 AppSecret",
        "系统会自动获取接口授权、有效期、店铺名称和店铺原始 ID",
        "微信小店后台首页没有这个授权按钮",
        "微信小店 AppID",
        "微信小店 AppSecret",
        "保存并连接微信小店",
        "saveAndFetchToken",
        "buildConnectPayload",
        'id="credentialId"',
        'id="appSecret"',
        'type="password"',
        'id="shopId" name="shop_id" type="hidden"',
        'id="shopName" name="shop_name" type="hidden"',
        "接口密钥未读取",
        "接口授权未读取",
        "refreshConfig",
        "/api-config/connect",
        "正在保存 AppID 和密钥，并自动获取授权与店铺信息",
        "微信小店连接成功",
        "接口配置已刷新",
        "发起同步时系统会按需刷新授权",
    )
    for fragment in expected_fragments:
        assert fragment in html, f"root HTML missing auth setting element: {fragment}"

    hidden_fragments = (
        "此处只展示授权结果",
        "授权状态已刷新",
        'id="appId"',
        "保存并获取接口授权",
        ">保存配置</button>",
    )
    for fragment in hidden_fragments:
        assert fragment not in html, f"root HTML should not expose editable auth setting: {fragment}"

    assert '$("collectShopId").value = data.values.shop_id || "";' in html
    assert '$("collectShopName").value = data.values.shop_name || "";' in html
    assert '$("collectShopId").value = $("collectShopId").value ||' not in html
    assert '$("collectShopName").value = $("collectShopName").value ||' not in html


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
        "taskProgressBar",
        "taskProgressPercent",
        "taskHistoryPanel",
        "renderTaskProgress",
        "renderTaskHistory",
        "loadTaskDetail",
        "taskExecutionTimeText",
        "existingTaskIdFromError",
        "openExistingTaskProgress",
        "最近导出任务",
        "执行时间",
        "已有页面导出任务正在运行，已切换到任务进度。",
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
        "validateSyncRange",
        "resetCurrentApiSyncView(payload)",
        "markDateRangeChanged",
        "renderApiSyncResult",
        "renderTaskSteps(Object.fromEntries",
        "请选择开始日期和结束日期。",
        "开始日期不能晚于结束日期。",
        "本次报告生成后会自动显示。",
        "loadRecords(result.report?.report_id || null)",
        "focusReportId === null",
        'collectFrom")?.addEventListener("change", markDateRangeChanged)',
        'collectTo")?.addEventListener("change", markDateRangeChanged)',
        "任务\", \"数据同步",
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
    assert capabilities["orders"]["page_export_status"] == "verified"
    assert capabilities["product_list"]["web_enabled"] is False
    assert capabilities["product_list"]["import_enabled"] is True
    assert capabilities["product_list"]["page_export_status"] == "verified"
    assert capabilities["funds"]["page_export_status"] == "verified"
    assert capabilities["refunds"]["web_enabled"] is False
    assert capabilities["refunds"]["import_enabled"] is True
    assert capabilities["refunds"]["page_export_status"] == "not_available"
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


def test_web_export_forces_headless_on_linux_without_display() -> None:
    task_runner_sys = admin_app.normalize_web_export_payload.__globals__["sys"]
    original_platform = task_runner_sys.platform
    original_display = os.environ.get("DISPLAY")
    task_runner_sys.platform = "linux"
    os.environ.pop("DISPLAY", None)
    try:
        result = admin_app.check_task_request(
            admin_app.TaskRequest(
                shop_id=SHOP_ID,
                source_type="web_export",
                params={
                    "from": "2026-06-01",
                    "to": "2026-06-03",
                    "types": ["orders"],
                    "shop_name": SHOP_NAME,
                    "headless": False,
                },
            )
        )
    finally:
        task_runner_sys.platform = original_platform
        if original_display is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = original_display

    assert result["status"] == "ok"
    assert result["data"]["headless"] is True


def test_create_task_requires_running_scan_login_browser() -> None:
    original_cdp_check = admin_app.web_login_cdp_available
    try:
        admin_app.web_login_cdp_available = lambda: False
        result = admin_app.create_task(
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
    finally:
        admin_app.web_login_cdp_available = original_cdp_check

    assert result["status"] == "error"
    assert "扫码登录浏览器未运行" in result["message"]
    assert result["data"]["login_browser_ready"] is False


def test_create_task_requires_authenticated_store_shell() -> None:
    original_status = admin_app.web_login_status_data
    original_start = admin_app.start_web_export_task
    started: list[dict[str, object]] = []
    status_calls: list[dict[str, object]] = []

    def fake_status(**kwargs: object) -> dict[str, object]:
        status_calls.append(kwargs)
        return {
            "cdp_available": True,
            "login_browser_ready": True,
            "authenticated": False,
            "export_ready": False,
            "login_state": "login_required",
            "login_state_message": "微信小店后台仍停留在二维码页，请先扫码。",
        }

    try:
        admin_app.web_login_status_data = fake_status
        admin_app.start_web_export_task = lambda payload: started.append(payload) or {"id": "unexpected"}
        result = admin_app.create_task(
            admin_app.TaskRequest(
                shop_id=SHOP_ID,
                source_type="web_export",
                params={
                    "mode": "export_only",
                    "from": "2026-06-01",
                    "to": "2026-06-03",
                    "types": ["visible_tables"],
                    "target_groups": ["orders"],
                    "targets": ["orders"],
                    "shop_name": SHOP_NAME,
                },
            )
        )
    finally:
        admin_app.web_login_status_data = original_status
        admin_app.start_web_export_task = original_start

    assert result["status"] == "error"
    assert "二维码页" in result["message"]
    assert result["data"]["authenticated"] is False
    assert started == []
    assert status_calls == [{"force_auth_check": True}]


def test_create_task_starts_after_authenticated_store_shell() -> None:
    original_status = admin_app.web_login_status_data
    original_start = admin_app.start_web_export_task
    started: list[dict[str, object]] = []
    status_calls: list[dict[str, object]] = []

    def fake_status(**kwargs: object) -> dict[str, object]:
        status_calls.append(kwargs)
        return {
            "cdp_available": True,
            "login_browser_ready": True,
            "authenticated": True,
            "export_ready": True,
            "login_state": "logged_in",
            "login_state_message": "微信小店后台已登录。",
        }

    try:
        admin_app.web_login_status_data = fake_status
        admin_app.start_web_export_task = lambda payload: started.append(payload) or {"id": "export-ready-task"}
        result = admin_app.create_task(
            admin_app.TaskRequest(
                shop_id=SHOP_ID,
                source_type="web_export",
                params={
                    "mode": "export_only",
                    "from": "2026-06-01",
                    "to": "2026-06-03",
                    "types": ["visible_tables"],
                    "target_groups": ["orders"],
                    "targets": ["orders"],
                    "shop_name": SHOP_NAME,
                },
            )
        )
    finally:
        admin_app.web_login_status_data = original_status
        admin_app.start_web_export_task = original_start

    assert result["status"] == "ok"
    assert result["data"]["id"] == "export-ready-task"
    assert len(started) == 1
    assert status_calls == [{"force_auth_check": True}]


def test_negative_collect_exit_code_is_explained_as_interruption() -> None:
    message = task_runner._command_failure_message("collect", -15, "stderr sentinel")

    assert "执行被中断" in message
    assert "signal=15" in message
    assert "服务重启" in message
    assert "stderr sentinel" in message


def test_structured_export_failure_message_is_concise() -> None:
    detail = "微信小店后台仍停留在扫码/登录页；请先完成扫码登录。"
    message = task_runner._command_failure_message(
        "collect",
        1,
        '{"time":"sentinel","stage":"export_only_failed","error":"日志错误"}',
        json.dumps({"status": "failed", "error": {"message": detail}}, ensure_ascii=False),
    )

    assert detail in message
    assert '"stage"' not in message
    assert "stderr:" not in message


def test_export_only_failure_skips_non_collection_steps() -> None:
    spec = task_runner.normalize_web_export_payload(
        {
            "shop_id": SHOP_ID,
            "source_type": "web_export",
            "params": {
                "mode": "export_only",
                "from": "2026-06-01",
                "to": "2026-06-03",
                "types": ["visible_tables"],
                "targets": ["orders"],
                "shop_name": SHOP_NAME,
            },
        }
    )
    task = task_runner._build_runtime_task(spec)
    task["state"] = "running"
    task["status"] = "running"
    task["steps"]["collect"].update({"status": "failed", "error": "login required"})
    original_tasks = task_runner._tasks
    original_persist_task = task_runner._persist_task
    original_persist_step = task_runner._persist_step
    try:
        task_runner._tasks = {task["id"]: task}
        task_runner._persist_task = lambda _task: None
        task_runner._persist_step = lambda _task_id, _step_key, _step: None
        task_runner._fail_task(task["id"], task_runner.TaskRunnerError("login required"))
        failed = task_runner.get_runtime_task(task["id"])
    finally:
        task_runner._tasks = original_tasks
        task_runner._persist_task = original_persist_task
        task_runner._persist_step = original_persist_step

    assert failed is not None
    assert failed["state"] == "failed"
    assert failed["steps"]["collect"]["status"] == "failed"
    for step_key in ("import_metadata", "import_files", "analyze", "report"):
        assert failed["steps"][step_key]["status"] == "skipped"


def test_run_step_enforces_hard_timeout() -> None:
    spec = task_runner.normalize_web_export_payload(
        {
            "shop_id": SHOP_ID,
            "source_type": "web_export",
            "params": {
                "from": "2026-06-01",
                "to": "2026-06-03",
                "types": ["orders"],
                "shop_name": SHOP_NAME,
            },
        }
    )
    task = task_runner._build_runtime_task(spec)
    original_tasks = task_runner._tasks
    original_persist_task = task_runner._persist_task
    original_persist_step = task_runner._persist_step
    try:
        task_runner._tasks = {task["id"]: task}
        task_runner._persist_task = lambda _task: None
        task_runner._persist_step = lambda _task_id, _step_key, _step: None
        try:
            task_runner._run_step(
                task["id"],
                "collect",
                [sys.executable, "-c", "import time; time.sleep(2)"],
                timeout_seconds=1,
            )
        except task_runner.TaskRunnerError as exc:
            message = str(exc)
        else:
            raise AssertionError("collect timeout should raise TaskRunnerError")
        timed_out = task_runner.get_runtime_task(task["id"])
    finally:
        task_runner._tasks = original_tasks
        task_runner._persist_task = original_persist_task
        task_runner._persist_step = original_persist_step

    assert "超过 1 秒" in message
    assert timed_out is not None
    assert timed_out["steps"]["collect"]["status"] == "failed"
    assert "已终止本次任务" in timed_out["steps"]["collect"]["error"]


def test_export_only_task_check_accepts_visible_page_tables() -> None:
    result = admin_app.check_task_request(
        admin_app.TaskRequest(
            shop_id=SHOP_ID,
            source_type="web_export",
            params={
                "mode": "export_only",
                "from": "2026-06-01",
                "to": "2026-06-03",
                "types": ["visible_tables"],
                "targets": ["orders"],
                "shop_name": SHOP_NAME,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["data"]["mode"] == "export_only"
    assert result["data"]["types"] == ["visible_tables"]
    assert result["data"]["shop_id"] == SHOP_ID


def test_export_only_task_check_rejects_missing_targets() -> None:
    result = admin_app.check_task_request(
        admin_app.TaskRequest(
            shop_id=SHOP_ID,
            source_type="web_export",
            params={
                "mode": "export_only",
                "from": "2026-06-01",
                "to": "2026-06-03",
                "types": ["visible_tables"],
            },
        )
    )

    assert result["status"] == "error"
    assert "必须明确选择至少一个数据模块" in result["message"]


def test_export_only_targets_are_validated_and_passed_to_collector() -> None:
    requested_targets = [
        "product_list",
        "orders",
        "fund_flows",
        "transactions",
        "product_core_conversion",
        "product_traffic_funnel",
        "product_detail",
        "compass_buyer_profile",
    ]
    spec = task_runner.normalize_web_export_payload(
        {
            "shop_id": SHOP_ID,
            "source_type": "web_export",
            "params": {
                "mode": "export_only",
                "from": "2026-06-01",
                "to": "2026-06-03",
                "types": ["visible_tables"],
                "targets": requested_targets,
                "shop_name": SHOP_NAME,
            },
        }
    )
    assert spec["targets"] == requested_targets

    invalid = admin_app.check_task_request(
        admin_app.TaskRequest(
            shop_id=SHOP_ID,
            source_type="web_export",
            params={
                "mode": "export_only",
                "from": "2026-06-01",
                "to": "2026-06-03",
                "types": ["visible_tables"],
                "targets": ["orders", "not-a-real-target"],
            },
        )
    )
    assert invalid["status"] == "error"
    assert "not-a-real-target" in invalid["message"]

    for extra_target in (
        "audience",
        "compass",
        "after_sales",
        "reviews",
        "shop_ads",
        "shop_boost",
        "repurchase",
        "alliance_data",
    ):
        rejected = admin_app.check_task_request(
            admin_app.TaskRequest(
                shop_id=SHOP_ID,
                source_type="web_export",
                params={
                    "mode": "export_only",
                    "from": "2026-06-01",
                    "to": "2026-06-03",
                    "types": ["visible_tables"],
                    "targets": [extra_target],
                },
            )
        )
        assert rejected["status"] == "error", extra_target
        assert extra_target in rejected["message"], extra_target

    task_runner.RAW_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="export_targets_command_",
        dir=task_runner.RAW_EXPORT_DIR,
    ) as temp_dir:
        metadata_path = Path(temp_dir) / "task-metadata.json"
        metadata_path.write_text(
            json.dumps({"task_id": "targets-test", "status": "completed", "pages": [], "artifacts": []}),
            encoding="utf-8",
        )
        captured: list[list[str]] = []
        original_run_step = task_runner._run_step

        def fake_run_step(_task_id: str, _step_key: str, command: list[str], **_kwargs: object) -> dict[str, object]:
            captured.append(command)
            return {"metadata_path": str(metadata_path)}

        try:
            task_runner._run_step = fake_run_step
            task_runner._prepare_task_source("targets-test", spec)
        finally:
            task_runner._run_step = original_run_step

    assert len(captured) == 1
    target_index = captured[0].index("--targets")
    assert captured[0][target_index + 1] == ",".join(requested_targets)


def test_export_only_empty_shop_name_is_not_replaced_by_shop_id() -> None:
    spec = task_runner.normalize_web_export_payload(
        {
            "shop_id": SHOP_ID,
            "source_type": "web_export",
            "params": {
                "mode": "export_only",
                "from": "2026-06-01",
                "to": "2026-06-03",
                "types": ["visible_tables"],
                "targets": ["orders"],
            },
        }
    )

    assert spec["shop_id"] == SHOP_ID
    assert spec["shop_name"] is None


def test_export_only_completion_skips_import_analysis_and_report() -> None:
    spec = task_runner.normalize_web_export_payload(
        {
            "shop_id": SHOP_ID,
            "source_type": "web_export",
            "params": {
                "mode": "export_only",
                "from": "2026-06-01",
                "to": "2026-06-03",
                "types": ["visible_tables"],
                "targets": ["orders"],
                "shop_name": SHOP_NAME,
            },
        }
    )
    task = task_runner._build_runtime_task(spec)
    task["state"] = "running"
    task["status"] = "running"
    original_tasks = task_runner._tasks
    original_persist_task = task_runner._persist_task
    original_persist_step = task_runner._persist_step
    try:
        task_runner._tasks = {task["id"]: task}
        task_runner._persist_task = lambda _task: None
        task_runner._persist_step = lambda _task_id, _step_key, _step: None
        task_runner._finish_export_only_task(
            task_id=task["id"],
            metadata_path=task_runner.RAW_EXPORT_DIR / "export-only-test" / "task-metadata.json",
            metadata={
                "status": "completed_with_item_errors",
                "task_id": "export-only-test",
                "artifacts": [{"status": "completed", "saved_path": "orders.xlsx"}],
                "bundle_artifact": {
                    "status": "completed",
                    "source_kind": "export_bundle",
                    "saved_path": "all-files.zip",
                },
                "items": [
                    {"page_name": "订单管理", "status": "completed"},
                    {"page_name": "账户资产", "status": "skipped"},
                    {"page_name": "电商罗盘", "status": "failed"},
                ],
            },
            collection_task_id="export-only-test",
        )
        completed = task_runner.get_runtime_task(task["id"])
    finally:
        task_runner._tasks = original_tasks
        task_runner._persist_task = original_persist_task
        task_runner._persist_step = original_persist_step

    assert completed is not None
    assert completed["state"] == "completed"
    assert completed["result"]["raw_only"] is True
    assert completed["result"]["artifact_count"] == 1
    assert completed["result"]["download_artifact_count"] == 2
    assert completed["result"]["bundle_available"] is True
    assert completed["result"]["bundle_artifact_index"] == 0
    assert completed["result"]["artifacts"][0]["source_kind"] == "export_bundle"
    assert completed["result"]["success_count"] == 1
    assert completed["result"]["skipped_count"] == 1
    assert completed["result"]["failed_count"] == 1
    for step_key in ("import_metadata", "import_files", "analyze", "report"):
        assert completed["steps"][step_key]["status"] == "skipped"


def test_export_only_nonzero_collect_preserves_failed_metadata_result() -> None:
    spec = task_runner.normalize_web_export_payload(
        {
            "shop_id": SHOP_ID,
            "source_type": "web_export",
            "params": {
                "mode": "export_only",
                "from": "2026-06-01",
                "to": "2026-06-03",
                "types": ["visible_tables"],
                "targets": ["orders"],
                "shop_name": SHOP_NAME,
            },
        }
    )
    task = task_runner._build_runtime_task(spec)
    original_tasks = task_runner._tasks
    original_persist_task = task_runner._persist_task
    original_persist_step = task_runner._persist_step
    original_subprocess_run = task_runner.subprocess.run
    original_raw_export_dir = task_runner.RAW_EXPORT_DIR

    with tempfile.TemporaryDirectory(prefix="wechat_export_result_") as temp_dir:
        raw_root = Path(temp_dir) / "data" / "raw"
        source_dir = raw_root / "failed-export-task"
        source_dir.mkdir(parents=True)
        metadata_path = source_dir / "task-metadata.json"
        metadata = {
            "task_id": "failed-export-task",
            "status": "failed",
            "targets": ["orders", "products"],
            "artifact_count": 1,
            "success_count": 0,
            "skipped_count": 1,
            "failed_count": 1,
            "fatal_error": True,
            "scan_completed": False,
            "execution_log_path": str(source_dir / "execution-log.jsonl"),
            "pages": [
                {"page_name": "订单管理", "status": "failed", "reason": "需要登录"},
                {"page_name": "商品列表", "status": "skipped", "reason": "未执行"},
            ],
            "artifacts": [
                {"status": "completed", "saved_path": str(source_dir / "orders.xlsx")},
            ],
            "error": {
                "name": "ExportOnlyFatalError",
                "code": "LOGIN_REQUIRED",
                "message": "微信小店后台尚未登录。",
            },
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

        class FailedCompletedProcess:
            returncode = 1
            stdout = "collector diagnostic\n" + json.dumps(
                {
                    "status": "failed",
                    "metadata_path": str(metadata_path),
                    "error": metadata["error"],
                },
                ensure_ascii=False,
            )
            stderr = '{"stage":"export_only_failed"}\n'

        def failed_collect(*_args: object, **_kwargs: object) -> FailedCompletedProcess:
            return FailedCompletedProcess()

        try:
            task_runner._tasks = {task["id"]: task}
            task_runner._persist_task = lambda _task: None
            task_runner._persist_step = lambda _task_id, _step_key, _step: None
            task_runner.subprocess.run = failed_collect
            task_runner.RAW_EXPORT_DIR = raw_root
            task_runner._run_task(task["id"])
            failed = task_runner.get_runtime_task(task["id"])
        finally:
            task_runner._tasks = original_tasks
            task_runner._persist_task = original_persist_task
            task_runner._persist_step = original_persist_step
            task_runner.subprocess.run = original_subprocess_run
            task_runner.RAW_EXPORT_DIR = original_raw_export_dir

    assert failed is not None
    assert failed["state"] == "failed"
    assert failed["collector_status"] == "failed"
    assert failed["steps"]["collect"]["status"] == "failed"
    assert failed["steps"]["collect"]["exit_code"] == 1
    assert failed["result"]["pages"] == metadata["pages"]
    assert failed["result"]["artifacts"] == metadata["artifacts"]
    assert failed["result"]["artifact_count"] == 1
    assert failed["result"]["success_count"] == 0
    assert failed["result"]["skipped_count"] == 1
    assert failed["result"]["failed_count"] == 1
    assert failed["result"]["diagnostic_log_path"] == metadata["execution_log_path"]
    assert failed["error"]["type"] == "ExportOnlyFatalError"
    assert failed["error"]["code"] == "LOGIN_REQUIRED"
    assert failed["error"]["message"] == "微信小店后台尚未登录。"
    assert failed["error"]["details"]["collect_step"]["exit_code"] == 1


def test_export_only_targets_without_pages_report_missing_results() -> None:
    spec = task_runner.normalize_web_export_payload(
        {
            "shop_id": SHOP_ID,
            "source_type": "web_export",
            "params": {
                "mode": "export_only",
                "from": "2026-06-01",
                "to": "2026-06-03",
                "types": ["visible_tables"],
                "targets": ["orders"],
                "shop_name": SHOP_NAME,
            },
        }
    )
    task = task_runner._build_runtime_task(spec)
    task["state"] = "running"
    task["status"] = "running"
    task["steps"]["collect"].update({"status": "completed", "exit_code": 0})
    original_tasks = task_runner._tasks
    original_persist_task = task_runner._persist_task
    original_persist_step = task_runner._persist_step
    try:
        task_runner._tasks = {task["id"]: task}
        task_runner._persist_task = lambda _task: None
        task_runner._persist_step = lambda _task_id, _step_key, _step: None
        task_runner._finish_export_only_task(
            task_id=task["id"],
            metadata_path=task_runner.RAW_EXPORT_DIR / "missing-pages" / "task-metadata.json",
            metadata={
                "status": "completed",
                "task_id": "missing-pages",
                "targets": ["orders", "products"],
                "pages": [],
                "artifacts": [],
                "artifact_count": 0,
                "success_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
            },
            collection_task_id="missing-pages",
        )
        failed = task_runner.get_runtime_task(task["id"])
    finally:
        task_runner._tasks = original_tasks
        task_runner._persist_task = original_persist_task
        task_runner._persist_step = original_persist_step

    assert failed is not None
    assert failed["state"] == "failed"
    assert failed["result"]["pages"] == []
    assert failed["result"]["target_count"] == 2
    assert failed["error"]["type"] == "ExportOnlyResultError"
    assert failed["error"]["code"] == "EXPORT_RESULTS_MISSING"
    assert "结果清单缺失" in failed["error"]["message"]
    assert "2 个导出目标" in failed["error"]["message"]
    assert failed["steps"]["collect"]["status"] == "failed"


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


def test_visible_viewport_local_export_is_not_imported() -> None:
    with local_export_source_dir() as source_dir:
        metadata_path = source_dir / "task-metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["data_coverage"] = "visible_viewport"
        metadata["date_filter_applied"] = False
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

        result = admin_app.check_task_request(
            admin_app.TaskRequest(
                shop_id=SHOP_ID,
                source_type="local_export",
                params={"source_dir": str(source_dir), "types": ["orders"]},
            )
        )

    assert result["status"] == "error"
    assert "页面可见区域" in result["message"]


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
