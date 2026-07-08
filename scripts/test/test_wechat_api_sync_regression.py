#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import traceback
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from sync.wechat_api import create_api_sync_report, run_wechat_api_sync  # noqa: E402


SHOP_ID = "wechat-api-sync-shop"
SHOP_NAME = "Wechat API Sync Shop"
SYNC_RUN_ID = "wechat-api-sync-regression"
ACCESS_TOKEN = "wechat-api-access-token-must-not-persist-9c4d"
DETAIL_SECRET = "wechat-api-detail-secret-must-not-persist-1b2a"


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_wechat_api_sync_fetches_details_and_persists_standard_tables,
        test_wechat_api_sync_supports_generic_compass_standard_table,
        test_wechat_api_sync_supports_custom_generic_standard_table_endpoint,
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
        print(f"{failed} WeChat API sync regression test(s) failed.")
        return 1
    print(f"{len(tests)} WeChat API sync regression tests passed.")
    return 0


def test_wechat_api_sync_fetches_details_and_persists_standard_tables() -> None:
    with tempfile.TemporaryDirectory(prefix="wechat_api_sync_regression_") as temp_dir:
        temp_path = Path(temp_dir)
        db_path = temp_path / "wechat.sqlite"
        archive_dir = temp_path / "raw-api"
        fake_http = FakeWechatHttp()

        run_sync(db_path, archive_dir, fake_http)
        assert_core_tables(db_path)
        assert_api_sync_report(db_path, temp_path / "reports")
        assert_sync_metadata(db_path)
        first_snapshot = snapshot_counts(db_path)
        assert_no_sensitive_values(db_path, archive_dir)

        run_sync(db_path, archive_dir, fake_http)
        assert snapshot_counts(db_path) == first_snapshot
        assert_no_sensitive_values(db_path, archive_dir)
        assert {call["path"] for call in fake_http.calls} >= {
            "/channels/ec/product/list/get",
            "/channels/ec/product/get",
            "/channels/ec/order/list/get",
            "/channels/ec/order/get",
            "/channels/ec/aftersale/getaftersalelist",
            "/channels/ec/aftersale/getaftersaleorder",
            "/channels/ec/funds/getfundsflowlist",
            "/channels/ec/funds/getfundsflowdetail",
        }


def test_wechat_api_sync_supports_generic_compass_standard_table() -> None:
    with tempfile.TemporaryDirectory(prefix="wechat_api_sync_compass_") as temp_dir:
        temp_path = Path(temp_dir)
        db_path = temp_path / "wechat.sqlite"
        archive_dir = temp_path / "raw-api"
        fake_http = FakeWechatHttp()
        result = run_wechat_api_sync(
            db_path=db_path,
            archive_dir=archive_dir,
            shop_id=SHOP_ID,
            shop_name=SHOP_NAME,
            date_from="2026-06-01",
            date_to="2026-06-01",
            sync_run_id=SYNC_RUN_ID,
            access_token=ACCESS_TOKEN,
            api_base_url="https://fake.weixin.test",
            endpoints=["compass_shop"],
            http_post=fake_http.post_json,
        )
        assert result["status"] == "completed"
        with connect(db_path) as conn:
            assert table_count(conn, "shop_daily") == 1
            row = conn.execute("SELECT stat_date, visitor_count, payment_amount FROM shop_daily").fetchone()
            assert row["stat_date"].startswith("2026-06-01")
            assert row["visitor_count"] == 42.0
            assert row["payment_amount"] == 899.0
        assert_no_sensitive_values(db_path, archive_dir)


def test_wechat_api_sync_supports_custom_generic_standard_table_endpoint() -> None:
    with tempfile.TemporaryDirectory(prefix="wechat_api_sync_custom_generic_") as temp_dir:
        temp_path = Path(temp_dir)
        db_path = temp_path / "wechat.sqlite"
        archive_dir = temp_path / "raw-api"
        fake_http = FakeWechatHttp()
        result = run_wechat_api_sync(
            db_path=db_path,
            archive_dir=archive_dir,
            shop_id=SHOP_ID,
            shop_name=SHOP_NAME,
            date_from="2026-06-01",
            date_to="2026-06-01",
            sync_run_id=SYNC_RUN_ID,
            access_token=ACCESS_TOKEN,
            api_base_url="https://fake.weixin.test",
            endpoints=["reviews:/channels/custom/reviews/list"],
            http_post=fake_http.post_json,
        )
        assert result["status"] == "completed"
        with connect(db_path) as conn:
            assert table_count(conn, "reviews") == 1
            row = conn.execute("SELECT review_id, rating, review_content FROM reviews").fetchone()
            assert row["review_id"] == "api-review-001"
            assert row["rating"] == 5.0
            assert row["review_content"] == "好评"
        assert_no_sensitive_values(db_path, archive_dir)


def run_sync(db_path: Path, archive_dir: Path, fake_http: "FakeWechatHttp") -> dict[str, Any]:
    return run_wechat_api_sync(
        db_path=db_path,
        archive_dir=archive_dir,
        shop_id=SHOP_ID,
        shop_name=SHOP_NAME,
        date_from="2026-06-01",
        date_to="2026-06-01",
        sync_run_id=SYNC_RUN_ID,
        access_token=ACCESS_TOKEN,
        api_base_url="https://fake.weixin.test",
        endpoints=["products", "orders", "aftersale", "funds"],
        http_post=fake_http.post_json,
    )


class FakeWechatHttp:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post_json(self, url: str, body: Mapping[str, Any], timeout: int) -> tuple[int, Mapping[str, Any]]:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        assert "access_token=" in parsed.query
        assert ACCESS_TOKEN not in json.dumps(body, ensure_ascii=False, default=str)
        self.calls.append({"path": path, "body": dict(body), "timeout": timeout})
        if path == "/channels/ec/product/list/get":
            return 200, {"errcode": 0, "errmsg": "ok", "request_id": "req-products", "product_ids": ["api-p001"], "has_more": False}
        if path == "/channels/ec/product/get":
            return 200, {
                "errcode": 0,
                "errmsg": "ok",
                "product": {
                    "product_id": body["product_id"],
                    "title": "API 连衣裙",
                    "status": "online",
                    "price": 199,
                    "stock": 9,
                    "access_token": DETAIL_SECRET,
                    "skus": [{"sku_id": "api-sku-p001-red-m", "sku_name": "红色 / M", "price": 199, "stock": 3}],
                },
            }
        if path == "/channels/ec/order/list/get":
            assert "create_time_range" in body
            return 200, {"errcode": 0, "errmsg": "ok", "request_id": "req-orders", "order_id_list": ["api-o001"], "has_more": False}
        if path == "/channels/ec/order/get":
            return 200, {
                "errcode": 0,
                "errmsg": "ok",
                "order": {
                    "order_id": body["order_id"],
                    "openid": "buyer-api-001",
                    "status": "paid",
                    "create_time": "2026-06-01 10:00:00",
                    "pay_time": "2026-06-01 10:01:00",
                    "order_detail": {
                        "price_info": {"order_price": 199},
                        "product_infos": [
                            {
                                "product_id": "api-p001",
                                "title": "API 连衣裙",
                                "sku_id": "api-sku-p001-red-m",
                                "sku_name": "红色 / M",
                                "sku_cnt": 1,
                                "sale_price": 199,
                                "cookie": DETAIL_SECRET,
                            }
                        ],
                    },
                },
            }
        if path == "/channels/ec/aftersale/getaftersalelist":
            assert "begin_create_time" in body
            return 200, {"errcode": 0, "errmsg": "ok", "after_sale_order_id_list": ["api-r001"], "has_more": False}
        if path == "/channels/ec/aftersale/getaftersaleorder":
            return 200, {
                "errcode": 0,
                "errmsg": "ok",
                "aftersale_order": {
                    "aftersale_order_id": body["after_sale_order_id"],
                    "order_id": "api-o001",
                    "product_id": "api-p001",
                    "sku_id": "api-sku-p001-red-m",
                    "refund_status": "completed",
                    "refund_amount": 20,
                    "create_time": "2026-06-01 12:00:00",
                    "secret": DETAIL_SECRET,
                },
            }
        if path == "/channels/ec/funds/getfundsflowlist":
            assert "start_time" in body
            return 200, {"errcode": 0, "errmsg": "ok", "flow_ids": ["api-f001"], "has_more": False}
        if path == "/channels/ec/funds/getfundsflowdetail":
            return 200, {
                "errcode": 0,
                "errmsg": "ok",
                "funds_flow": {
                    "flow_id": body["flow_id"],
                    "bookkeeping_time": "2026-06-02 16:00:00",
                    "flow_type": 1,
                    "amount": 199,
                    "balance": 199,
                    "remark": "订单入账",
                    "related_info_list": [{"order_id": "api-o001", "related_type": 1}],
                    "session": DETAIL_SECRET,
                },
            }
        if path == "/channels/ec/compass/shop/overall/get":
            return 200, {
                "errcode": 0,
                "errmsg": "ok",
                "data": {
                    "stat_date": "2026-06-01",
                    "visitor_count": 42,
                    "payment_amount": 899,
                    "token": DETAIL_SECRET,
                },
                "has_more": False,
            }
        if path == "/channels/custom/reviews/list":
            return 200, {
                "errcode": 0,
                "errmsg": "ok",
                "records": [
                    {
                        "review_id": "api-review-001",
                        "rating": 5,
                        "review_content": "好评",
                        "review_created_at": "2026-06-01 09:00:00",
                        "token": DETAIL_SECRET,
                    }
                ],
                "has_more": False,
            }
        raise AssertionError(f"unexpected fake endpoint: {path}")


def assert_core_tables(db_path: Path) -> None:
    with connect(db_path) as conn:
        expected = {
            "products": 1,
            "product_skus": 1,
            "orders": 1,
            "order_items": 1,
            "refunds": 1,
            "fund_flows": 1,
        }
        assert {table: table_count(conn, table) for table in expected} == expected
        order = conn.execute("SELECT order_id, payment_amount FROM orders").fetchone()
        assert order["order_id"] == "api-o001"
        assert order["payment_amount"] == 199.0
        refund = conn.execute("SELECT refund_id, refund_amount FROM refunds").fetchone()
        assert refund["refund_id"] == "api-r001"
        assert refund["refund_amount"] == 20.0
        fund = conn.execute("SELECT flow_id, flow_date, direction, order_id FROM fund_flows").fetchone()
        assert fund["flow_id"] == "api-f001"
        assert fund["flow_date"] == "2026-06-02 16:00:00"
        assert fund["direction"] == "in"
        assert fund["order_id"] == "api-o001"


def assert_sync_metadata(db_path: Path) -> None:
    with connect(db_path) as conn:
        assert table_count(conn, "sync_runs") == 1
        assert table_count(conn, "sync_run_items") == 4
        assert table_count(conn, "raw_api_responses") == 4
        run = conn.execute("SELECT connector, status FROM sync_runs").fetchone()
        assert run["connector"] == "wechat_api"
        assert run["status"] == "completed"


def assert_api_sync_report(db_path: Path, reports_dir: Path) -> None:
    result = create_api_sync_report(
        db_path=db_path,
        reports_dir=reports_dir,
        shop_id=SHOP_ID,
        date_from="2026-06-01",
        date_to="2026-06-01",
        sync_run_id=SYNC_RUN_ID,
    )
    assert result["analysis_run_id"]
    markdown_path = Path(result["report"]["markdown_path"])
    assert markdown_path.exists()
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "微信小店运营分析报告" in markdown
    assert "api-o001" not in markdown


def snapshot_counts(db_path: Path) -> dict[str, int]:
    with connect(db_path) as conn:
        tables = (
            "sync_runs",
            "sync_run_items",
            "raw_api_responses",
            "products",
            "product_skus",
            "orders",
            "order_items",
            "refunds",
            "fund_flows",
        )
        return {table: table_count(conn, table) for table in tables}


def assert_no_sensitive_values(db_path: Path, archive_dir: Path) -> None:
    sensitive = (ACCESS_TOKEN, DETAIL_SECRET)
    db_bytes = db_path.read_bytes()
    for value in sensitive:
        assert value.encode("utf-8") not in db_bytes
    for path in archive_dir.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        for value in sensitive:
            assert value not in text, f"sensitive value leaked into {path}"


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])


if __name__ == "__main__":
    raise SystemExit(main())
