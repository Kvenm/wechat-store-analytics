#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import api.app as admin_app  # noqa: E402
from api.task_runner import TaskRunnerError  # noqa: E402


EXPECTED_GROUPS = {
    "product_list": ("product_list",),
    "orders": ("orders",),
    "fund_flows": ("fund_flows",),
    "transactions": ("transactions",),
    "product_data": (
        "product_core_conversion",
        "product_traffic_funnel",
        "product_detail",
    ),
    "compass_buyer_profile": ("compass_buyer_profile",),
}


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_six_business_groups_have_an_exact_contract,
        test_frontend_reads_only_the_submitted_form_selection,
        test_each_selected_group_expands_to_only_its_own_targets,
        test_server_overwrites_forged_or_stale_frontend_targets,
        test_empty_and_unknown_group_selections_are_rejected,
        test_create_task_passes_the_server_derived_targets_to_runner,
        test_legacy_export_request_without_target_groups_is_preserved,
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
        print(f"{failed} selected export target regression test(s) failed.")
        return 1
    print(f"{len(tests)} selected export target regression tests passed.")
    return 0


def export_request(*, groups: list[str], targets: list[str] | None = None) -> dict[str, Any]:
    return {
        "shop_id": "shop-selected-targets",
        "task_name": "selected targets regression",
        "source_type": "web_export",
        "params": {
            "mode": "export_only",
            "shop_name": "勾选测试店铺",
            "from": "2026-07-01",
            "to": "2026-07-14",
            "types": ["visible_tables"],
            "target_groups": groups,
            "targets": targets or [],
        },
    }


def test_six_business_groups_have_an_exact_contract() -> None:
    page = admin_app.root()
    assert admin_app.PAGE_EXPORT_TARGET_GROUPS == EXPECTED_GROUPS
    assert page.headers["cache-control"] == "no-store, max-age=0"

    html = page.body.decode("utf-8")
    form_start = html.index('<form id="orderTaskForm"')
    form = html[form_start:html.index("</form>", form_start)]
    checkbox_groups = re.findall(r'name="page_export_module" value="([^"]+)"', form)
    assert checkbox_groups == list(EXPECTED_GROUPS)
    assert form.count('name="page_export_module"') == 6


def test_frontend_reads_only_the_submitted_form_selection() -> None:
    html = admin_app.root().body.decode("utf-8")
    assert "function selectedPageExportSelection(form)" in html
    assert 'new FormData(form).getAll("page_export_module")' in html
    assert 'document.querySelectorAll(\'input[name="page_export_module"]:checked\')' not in html
    assert 'const form = event.currentTarget || $("orderTaskForm");' in html
    assert "target_groups: selection.groups" in html
    assert "const targets = selection.targets;" in html
    assert "if (!targets.length)" in html
    assert "请至少选择一个页面导出模块" in html


def test_each_selected_group_expands_to_only_its_own_targets() -> None:
    for group, expected_targets in EXPECTED_GROUPS.items():
        request = export_request(groups=[group], targets=["orders", "fund_flows"])
        normalized = admin_app.apply_page_export_target_selection(request)
        assert normalized["params"]["target_groups"] == [group]
        assert normalized["params"]["targets"] == list(expected_targets)


def test_server_overwrites_forged_or_stale_frontend_targets() -> None:
    original = export_request(
        groups=["orders", "compass_buyer_profile", "orders"],
        targets=[target for targets in EXPECTED_GROUPS.values() for target in targets],
    )
    normalized = admin_app.apply_page_export_target_selection(original)

    assert normalized["params"]["target_groups"] == ["orders", "compass_buyer_profile"]
    assert normalized["params"]["targets"] == ["orders", "compass_buyer_profile"]
    assert original["params"]["targets"] != normalized["params"]["targets"]
    assert original["params"]["target_groups"] == ["orders", "compass_buyer_profile", "orders"]


def test_empty_and_unknown_group_selections_are_rejected() -> None:
    for groups, message in (([], "至少需要勾选一个"), (["orders", "unknown"], "未知勾选项")):
        try:
            admin_app.apply_page_export_target_selection(export_request(groups=groups))
        except TaskRunnerError as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"selection should be rejected: {groups}")

    result = admin_app.create_task(admin_app.TaskRequest(**export_request(groups=[])))
    assert result["status"] == "error"
    assert "至少需要勾选一个" in result["message"]


def test_create_task_passes_the_server_derived_targets_to_runner() -> None:
    captured: list[dict[str, Any]] = []

    def fake_start(request: dict[str, Any]) -> dict[str, Any]:
        captured.append(request)
        return {"id": "selected-target-task", "spec": request["params"]}

    request = export_request(
        groups=["transactions", "product_data"],
        targets=list(target for targets in EXPECTED_GROUPS.values() for target in targets),
    )
    with patched_attributes(
        task_requires_web_login=lambda _request: False,
        start_web_export_task=fake_start,
    ):
        result = admin_app.create_task(admin_app.TaskRequest(**request))

    assert result["status"] == "ok"
    assert len(captured) == 1
    assert captured[0]["params"]["target_groups"] == ["transactions", "product_data"]
    assert captured[0]["params"]["targets"] == [
        "transactions",
        "product_core_conversion",
        "product_traffic_funnel",
        "product_detail",
    ]


def test_legacy_export_request_without_target_groups_is_preserved() -> None:
    request = export_request(groups=["orders"], targets=["orders"])
    del request["params"]["target_groups"]
    normalized = admin_app.apply_page_export_target_selection(request)
    assert normalized is request
    assert normalized["params"]["targets"] == ["orders"]


@contextmanager
def patched_attributes(**updates: Any) -> Iterator[None]:
    previous = {name: getattr(admin_app, name) for name in updates}
    try:
        for name, value in updates.items():
            setattr(admin_app, name, value)
        yield
    finally:
        for name, value in previous.items():
            setattr(admin_app, name, value)


if __name__ == "__main__":
    raise SystemExit(main())
