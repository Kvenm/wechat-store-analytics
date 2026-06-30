#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
import tempfile
import traceback
import types
import zipfile
from pathlib import Path
from typing import Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

if importlib.util.find_spec("pandas") is None:
    pandas_stub = types.ModuleType("pandas")
    pandas_stub.DataFrame = None
    pandas_stub.read_csv = lambda path, dtype=None: CsvFrame.from_path(path)
    pandas_stub.read_excel = _unsupported_read_excel = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("This regression script only uses CSV fixtures.")
    )
    sys.modules["pandas"] = pandas_stub

from ingestion.models import STANDARD_TABLES  # noqa: E402
from ingestion.pipeline import build_import_batch  # noqa: E402
from shared.analysis_capabilities import build_business_readiness  # noqa: E402
from warehouse.repository import connect, import_batch, initialize_database  # noqa: E402


SHOP_ID = "test-shop"
SHOP_NAME = "Test Shop"
TASK_ID = "test-task"


ORDERS_HEADERS = ("order_id", "created_at", "paid_amount", "status")
ORDERS_ROWS = (
    ("order-001", "2026-06-01 10:00:00", "12.30", "paid"),
    ("order-002", "2026-06-01 11:00:00", "45.60", "paid"),
)

PRODUCTS_HEADERS = ("product_id", "product_name", "price", "stock", "status")
PRODUCTS_ROWS = (
    ("product-001", "Coffee Mug", "19.90", "30", "online"),
    ("product-002", "Canvas Bag", "29.90", "12", "online"),
)

PRODUCT_LIST_HEADERS = (
    "商品ID",
    "商品名称",
    "SKU ID",
    "规格名称",
    "销售价",
    "可售库存",
    "商品类目",
    "上下架状态",
    "商家编码",
)
PRODUCT_LIST_ROWS = (
    ("p-001", "夏季连衣裙", "sku-001", "M码", "129.00", "20", "女装", "销售中", "B001"),
    ("p-001", "夏季连衣裙", "sku-002", "L码", "129.00", "12", "女装", "销售中", "B002"),
)

AUDIENCE_HEADERS = (
    "product_name",
    "segment_label",
    "visitor_count",
    "order_count",
    "payment_amount",
    "conversion_rate",
    "refund_rate",
)
AUDIENCE_ROWS = (
    ("Coffee Mug", "new buyers", "100", "8", "160.00", "0.08", "0.01"),
    ("Canvas Bag", "returning buyers", "80", "6", "180.00", "0.075", "0.00"),
)

SHOP_DAILY_PRODUCT_ANALYTICS_HEADERS = (
    "时间",
    "商品曝光人数",
    "商品点击人数",
    "商品点击次数",
    "下单金额",
    "成交金额",
    "成交退款金额",
)
SHOP_DAILY_PRODUCT_ANALYTICS_ROWS = (
    ("2026-06-21", "1000", "120", "150", "900.00", "800.00", "20.00"),
    ("2026-06-22", "1100", "130", "165", "950.00", "870.00", "10.00"),
)

FULL_ORDER_EXPORT_HEADERS = (
    "订单号",
    "下单时间",
    "付款时间",
    "订单状态",
    "实付金额",
    "退款金额",
    "商品ID",
    "商品名称",
    "SKU ID",
    "规格",
    "数量",
    "商品实付金额",
    "退款状态",
    "退款原因",
)
FULL_ORDER_EXPORT_ROWS = (
    (
        "wx-order-001",
        "2026-06-21 10:00:00",
        "2026-06-21 10:01:00",
        "已完成",
        "120.00",
        "20.00",
        "p-001",
        "夏季连衣裙",
        "sku-001",
        "M码",
        "1",
        "120.00",
        "退款成功",
        "尺码偏小",
    ),
    (
        "wx-order-002",
        "2026-06-21 11:00:00",
        "2026-06-21 11:01:00",
        "已发货",
        "88.00",
        "",
        "p-002",
        "防晒帽",
        "sku-002",
        "米色",
        "2",
        "88.00",
        "",
        "",
    ),
)

REVIEWS_HEADERS = (
    "评价编号",
    "订单号",
    "商品名称",
    "SKU ID",
    "评价星级",
    "评价详情",
    "评价创建时间",
    "商家回复内容",
)
REVIEWS_ROWS = (
    ("review-001", "wx-order-001", "夏季连衣裙", "sku-001", "2", "尺码偏小，面料薄", "2026-06-22 09:00:00", "已联系处理"),
)

AFTERSALE_HEADERS = (
    "售后编号",
    "订单号",
    "商品ID",
    "商品名称",
    "SKU ID",
    "售后状态",
    "实退金额",
    "申请售后时间",
    "退款成功时间",
    "原因说明",
)
AFTERSALE_ROWS = (
    ("as-001", "wx-order-001", "p-001", "夏季连衣裙", "sku-001", "退款成功", "20.00", "2026-06-22 10:00:00", "2026-06-22 12:00:00", "尺码偏小"),
)

FUND_FLOW_HEADERS = (
    "账单号",
    "记账时间",
    "账务类型",
    "业务类型",
    "订单号",
    "收入金额",
    "收支方向",
    "当前余额",
    "备注",
)
FUND_FLOW_ROWS = (
    ("flow-001", "2026-06-22 13:00:00", "收入", "订单结算", "wx-order-001", "100.00", "收入", "1000.00", "订单入账"),
)

AD_SPEND_HEADERS = (
    "投放日期",
    "推广计划ID",
    "推广计划名称",
    "推广单元名称",
    "商品名称",
    "曝光次数",
    "点击次数",
    "花费金额",
    "成交GMV",
    "成交订单量",
    "ROAS",
)
AD_SPEND_ROWS = (
    ("2026-06-22", "camp-001", "连衣裙放量", "华东女性", "夏季连衣裙", "10000", "320", "200.00", "800.00", "6", "4.0"),
)


class CsvColumns(list[str]):
    def tolist(self) -> list[str]:
        return list(self)


class CsvFrame:
    def __init__(self, columns: Iterable[str], rows: Iterable[dict[str, str | None]]) -> None:
        self.columns = columns
        self._rows = [dict(row) for row in rows]

    @property
    def columns(self) -> CsvColumns:
        return self._columns

    @columns.setter
    def columns(self, values: Iterable[str]) -> None:
        self._columns = CsvColumns(str(column) for column in values)

    @classmethod
    def from_path(cls, path: str | Path) -> "CsvFrame":
        with Path(path).open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return cls(reader.fieldnames or [], reader)

    @property
    def empty(self) -> bool:
        return not self._rows or not self.columns

    def copy(self) -> "CsvFrame":
        return CsvFrame(self.columns, self._rows)

    def dropna(self, *, axis: int, how: str) -> "CsvFrame":
        if how != "all":
            raise NotImplementedError("Only how='all' is needed by this regression script.")
        if axis == 0:
            rows = [row for row in self._rows if any(_has_value(row.get(column)) for column in self.columns)]
            return CsvFrame(self.columns, rows)
        if axis == 1:
            columns = [column for column in self.columns if any(_has_value(row.get(column)) for row in self._rows)]
            rows = [{column: row.get(column) for column in columns} for row in self._rows]
            return CsvFrame(columns, rows)
        raise NotImplementedError("Only axis 0 and 1 are needed by this regression script.")

    def iterrows(self):
        for index, row in enumerate(self._rows):
            yield index, row


def _has_value(value: object) -> bool:
    return value is not None and str(value).strip() != ""


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_no_manifest_uses_legacy_heuristic_import,
        test_manifest_imports_only_registered_files,
        test_table_hint_strong_conflict_skips_sheet,
        test_products_analytics_manifest_imports_shop_daily,
        test_hash_mismatch_skips_artifact,
        test_export_type_fallback_imports_mapped_table,
        test_product_list_export_imports_products_and_derives_skus,
        test_expected_types_skip_unexpected_tables_without_manifest,
        test_expected_types_auto_imports_all_detected_tables_without_manifest,
        test_auto_imports_reviews_refunds_funds_and_ads_without_manifest,
        test_import_inspection_describes_mixed_business_tables,
        test_import_inspection_tracks_derived_order_and_sku_tables,
        test_manifest_export_type_aliases_import_business_tables,
        test_auto_business_tables_import_into_sqlite,
        test_full_order_export_derives_items_and_refunds,
        test_zip_export_imports_nested_workbook,
        test_empty_manifest_imports_nothing,
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
        print(f"{failed} manifest import regression test(s) failed.")
        return 1

    print(f"{len(tests)} manifest import regression tests passed.")
    return 0


def test_no_manifest_uses_legacy_heuristic_import() -> None:
    with temp_source_dir() as source_dir:
        write_csv(Path(source_dir) / "orders.csv", ORDERS_HEADERS, ORDERS_ROWS)
        write_csv(Path(source_dir) / "products.csv", PRODUCTS_HEADERS, PRODUCTS_ROWS)

        batch = import_source(source_dir)

        assert_counts(batch, orders=2, products=2)
        assert warning_codes(batch) == set()


def test_manifest_imports_only_registered_files() -> None:
    with temp_source_dir() as source_dir:
        orders_path = Path(source_dir) / "orders.csv"
        write_csv(orders_path, ORDERS_HEADERS, ORDERS_ROWS)
        write_csv(Path(source_dir) / "products.csv", PRODUCTS_HEADERS, PRODUCTS_ROWS)
        write_manifest(source_dir, [artifact_for(orders_path, table_hint="orders", export_type="orders")])

        batch = import_source(source_dir)

        assert_counts(batch, orders=2, products=0)
        assert_has_warning(batch, "manifest_table_hint_used")
        assert_has_warning(batch, "unlisted_source_file_ignored")


def test_table_hint_strong_conflict_skips_sheet() -> None:
    with temp_source_dir() as source_dir:
        products_path = Path(source_dir) / "products.csv"
        write_csv(products_path, PRODUCTS_HEADERS, PRODUCTS_ROWS)
        write_manifest(source_dir, [artifact_for(products_path, table_hint="orders", export_type="orders")])

        batch = import_source(source_dir)

        assert_counts(batch, orders=0, products=0)
        assert_all_standard_tables_empty(batch)
        assert_has_warning(batch, "table_hint_conflict")


def test_products_analytics_manifest_imports_shop_daily() -> None:
    with temp_source_dir() as source_dir:
        analytics_path = Path(source_dir) / "products.csv"
        write_csv(
            analytics_path,
            SHOP_DAILY_PRODUCT_ANALYTICS_HEADERS,
            SHOP_DAILY_PRODUCT_ANALYTICS_ROWS,
        )
        write_manifest(
            source_dir,
            [artifact_for(analytics_path, table_hint="shop_daily", export_type="products")],
        )

        batch = import_source(source_dir)

        assert_counts(batch, shop_daily=2, products=0)
        assert_has_warning(batch, "manifest_table_hint_used")
        assert_no_warning(batch, "table_hint_conflict")
        first_row = batch.shop_daily[0]
        assert first_row["stat_date"].startswith("2026-06-21"), first_row
        assert first_row["exposure_user_count"] == 1000.0, first_row
        assert first_row["payment_amount"] == 800.0, first_row
        assert first_row["refund_amount"] == 20.0, first_row


def test_hash_mismatch_skips_artifact() -> None:
    with temp_source_dir() as source_dir:
        orders_path = Path(source_dir) / "orders.csv"
        write_csv(orders_path, ORDERS_HEADERS, ORDERS_ROWS)
        write_manifest(
            source_dir,
            [
                artifact_for(
                    orders_path,
                    table_hint="orders",
                    export_type="orders",
                    sha256_value="0" * 64,
                )
            ],
        )

        batch = import_source(source_dir)

        assert_counts(batch, orders=0)
        assert_all_standard_tables_empty(batch)
        assert_has_warning(batch, "artifact_hash_mismatch")


def test_export_type_fallback_imports_mapped_table() -> None:
    with temp_source_dir() as source_dir:
        audience_path = Path(source_dir) / "audience.csv"
        write_csv(audience_path, AUDIENCE_HEADERS, AUDIENCE_ROWS)
        write_manifest(source_dir, [artifact_for(audience_path, export_type="compass")])

        batch = import_source(source_dir)

        assert_counts(batch, audience_insights=2)
        assert_has_warning(batch, "manifest_export_type_used")


def test_product_list_export_imports_products_and_derives_skus() -> None:
    with temp_source_dir() as source_dir:
        product_list_path = Path(source_dir) / "导出商品.csv"
        write_csv(product_list_path, PRODUCT_LIST_HEADERS, PRODUCT_LIST_ROWS)
        write_manifest(source_dir, [artifact_for(product_list_path, table_hint="products", export_type="product_list")])

        batch = import_source(source_dir)

        assert_counts(batch, products=2, product_skus=2)
        assert_has_warning(batch, "product_list_skus_derived")
        first_product = batch.products[0]
        assert first_product["product_id"] == "p-001"
        assert first_product["product_name"] == "夏季连衣裙"
        assert first_product["price"] == 129.0
        assert first_product["stock"] == 20.0
        first_sku = batch.product_skus[0]
        assert first_sku["product_id"] == "p-001"
        assert first_sku["sku_id"] == "sku-001"
        assert first_sku["sku_name"] == "M码"
        assert first_sku["sku_price"] == 129.0
        assert first_sku["barcode"] == "B001"


def test_expected_types_skip_unexpected_tables_without_manifest() -> None:
    with temp_source_dir() as source_dir:
        product_list_path = Path(source_dir) / "导出商品.csv"
        orders_path = Path(source_dir) / "orders.csv"
        write_csv(product_list_path, PRODUCT_LIST_HEADERS, PRODUCT_LIST_ROWS)
        write_csv(orders_path, ORDERS_HEADERS, ORDERS_ROWS)

        batch = build_import_batch(
            source_dir=source_dir,
            shop_id=SHOP_ID,
            task_id=TASK_ID,
            shop_name=SHOP_NAME,
            expected_types=["product_list"],
            manifest_policy="ignore",
        )

        assert_counts(batch, orders=0, products=2, product_skus=2)
        assert_has_warning(batch, "unexpected_table_for_expected_types")
        assert_has_warning(batch, "product_list_skus_derived")


def test_expected_types_auto_imports_all_detected_tables_without_manifest() -> None:
    with temp_source_dir() as source_dir:
        product_list_path = Path(source_dir) / "导出商品.csv"
        orders_path = Path(source_dir) / "orders.csv"
        write_csv(product_list_path, PRODUCT_LIST_HEADERS, PRODUCT_LIST_ROWS)
        write_csv(orders_path, ORDERS_HEADERS, ORDERS_ROWS)

        batch = build_import_batch(
            source_dir=source_dir,
            shop_id=SHOP_ID,
            task_id=TASK_ID,
            shop_name=SHOP_NAME,
            expected_types=["auto"],
            manifest_policy="ignore",
        )

        assert_counts(batch, orders=2, products=2, product_skus=2)
        assert_no_warning(batch, "unexpected_table_for_expected_types")
        assert_has_warning(batch, "product_list_skus_derived")


def test_auto_imports_reviews_refunds_funds_and_ads_without_manifest() -> None:
    with temp_source_dir() as source_dir:
        write_csv(Path(source_dir) / "商品评价.csv", REVIEWS_HEADERS, REVIEWS_ROWS)
        write_csv(Path(source_dir) / "售后订单.csv", AFTERSALE_HEADERS, AFTERSALE_ROWS)
        write_csv(Path(source_dir) / "资金流水.csv", FUND_FLOW_HEADERS, FUND_FLOW_ROWS)
        write_csv(Path(source_dir) / "投放数据.csv", AD_SPEND_HEADERS, AD_SPEND_ROWS)

        batch = build_import_batch(
            source_dir=source_dir,
            shop_id=SHOP_ID,
            task_id=TASK_ID,
            shop_name=SHOP_NAME,
            expected_types=["auto"],
            manifest_policy="ignore",
        )

        assert_counts(batch, reviews=1, refunds=1, fund_flows=1, ad_spend=1)
        assert_no_warning(batch, "unexpected_table_for_expected_types")
        review = batch.reviews[0]
        assert review["review_id"] == "review-001"
        assert review["rating"] == 2.0
        assert review["review_content"] == "尺码偏小，面料薄"
        assert review["review_created_at"].startswith("2026-06-22")
        assert review["is_positive"] == "0"
        refund = batch.refunds[0]
        assert refund["refund_id"] == "as-001"
        assert refund["refund_amount"] == 20.0
        assert refund["reason"] == "尺码偏小"
        fund_flow = batch.fund_flows[0]
        assert fund_flow["flow_id"] == "flow-001"
        assert fund_flow["amount"] == 100.0
        assert fund_flow["currency"] == "CNY"
        ad = batch.ad_spend[0]
        assert ad["campaign_id"] == "camp-001"
        assert ad["spend_amount"] == 200.0
        assert ad["payment_amount"] == 800.0
        assert ad["roi"] == 4.0
        assert ad["platform"] == "wechat"


def test_import_inspection_describes_mixed_business_tables() -> None:
    with temp_source_dir() as source_dir:
        write_csv(Path(source_dir) / "商品评价.csv", REVIEWS_HEADERS, REVIEWS_ROWS)
        write_csv(Path(source_dir) / "售后订单.csv", AFTERSALE_HEADERS, AFTERSALE_ROWS)
        write_csv(Path(source_dir) / "资金流水.csv", FUND_FLOW_HEADERS, FUND_FLOW_ROWS)
        write_csv(Path(source_dir) / "投放数据.csv", AD_SPEND_HEADERS, AD_SPEND_ROWS)

        batch = build_import_batch(
            source_dir=source_dir,
            shop_id=SHOP_ID,
            task_id=TASK_ID,
            shop_name=SHOP_NAME,
            expected_types=["auto"],
            manifest_policy="ignore",
        )
        inspection = batch.inspection_summary(source_dir=source_dir)
        inspection["business_readiness"] = build_business_readiness(inspection)

        by_table = {item["selected_table"]: item for item in inspection["source_inspections"]}
        assert set(by_table) >= {"reviews", "refunds", "fund_flows", "ad_spend"}
        review = by_table["reviews"]
        assert review["status"] == "importable", review
        assert review["row_counts"]["raw_rows"] == 1
        assert review["row_counts"]["imported_rows"] == 1
        assert any(field["field"] == "review_content" and field["match_method"] == "alias" for field in review["field_matches"])
        readiness = {item["key"]: item for item in inspection["business_readiness"]}
        assert readiness["review_insights"]["status"] == "supported"
        assert readiness["refund_risk"]["status"] == "limited"
        assert readiness["ad_roi"]["status"] == "supported"
        assert readiness["fund_reconciliation"]["status"] == "supported"
        assert readiness["order_kpi"]["status"] == "blocked"


def test_import_inspection_tracks_derived_order_and_sku_tables() -> None:
    with temp_source_dir() as source_dir:
        write_csv(Path(source_dir) / "orders-full.csv", FULL_ORDER_EXPORT_HEADERS, FULL_ORDER_EXPORT_ROWS)
        write_csv(Path(source_dir) / "导出商品.csv", PRODUCT_LIST_HEADERS, PRODUCT_LIST_ROWS)

        batch = build_import_batch(
            source_dir=source_dir,
            shop_id=SHOP_ID,
            task_id=TASK_ID,
            shop_name=SHOP_NAME,
            expected_types=["auto"],
            manifest_policy="ignore",
        )
        inspection = batch.inspection_summary(source_dir=source_dir)

        order_inspection = next(item for item in inspection["source_inspections"] if item["selected_table"] == "orders")
        product_inspection = next(item for item in inspection["source_inspections"] if item["selected_table"] == "products")
        assert order_inspection["row_counts"]["imported_rows"] == 2
        assert order_inspection["row_counts"]["derived_tables"] == {"order_items": 2, "refunds": 1}
        assert product_inspection["row_counts"]["imported_rows"] == 2
        assert product_inspection["row_counts"]["derived_tables"] == {"product_skus": 2}
        assert inspection["totals"]["tables"]["orders"] == 2
        assert inspection["totals"]["tables"]["order_items"] == 2
        assert inspection["totals"]["tables"]["refunds"] == 1
        assert inspection["totals"]["tables"]["products"] == 2
        assert inspection["totals"]["tables"]["product_skus"] == 2


def test_manifest_export_type_aliases_import_business_tables() -> None:
    with temp_source_dir() as source_dir:
        review_path = Path(source_dir) / "comment.csv"
        refund_path = Path(source_dir) / "aftersale.csv"
        fund_path = Path(source_dir) / "bill.csv"
        ad_path = Path(source_dir) / "promotion.csv"
        write_csv(review_path, REVIEWS_HEADERS, REVIEWS_ROWS)
        write_csv(refund_path, AFTERSALE_HEADERS, AFTERSALE_ROWS)
        write_csv(fund_path, FUND_FLOW_HEADERS, FUND_FLOW_ROWS)
        write_csv(ad_path, AD_SPEND_HEADERS, AD_SPEND_ROWS)
        write_manifest(
            source_dir,
            [
                artifact_for(review_path, export_type="comment"),
                artifact_for(refund_path, export_type="aftersale"),
                artifact_for(fund_path, export_type="bill"),
                artifact_for(ad_path, export_type="promotion"),
            ],
        )

        batch = import_source(source_dir)

        assert_counts(batch, reviews=1, refunds=1, fund_flows=1, ad_spend=1)
        assert_has_warning(batch, "manifest_export_type_used")


def test_auto_business_tables_import_into_sqlite() -> None:
    with temp_source_dir() as source_dir:
        write_csv(Path(source_dir) / "商品评价.csv", REVIEWS_HEADERS, REVIEWS_ROWS)
        write_csv(Path(source_dir) / "售后订单.csv", AFTERSALE_HEADERS, AFTERSALE_ROWS)
        write_csv(Path(source_dir) / "资金流水.csv", FUND_FLOW_HEADERS, FUND_FLOW_ROWS)
        write_csv(Path(source_dir) / "投放数据.csv", AD_SPEND_HEADERS, AD_SPEND_ROWS)
        batch = build_import_batch(
            source_dir=source_dir,
            shop_id=SHOP_ID,
            task_id=TASK_ID,
            shop_name=SHOP_NAME,
            expected_types=["auto"],
            manifest_policy="ignore",
        )
        with connect(":memory:") as conn:
            initialize_database(conn)
            counts = import_batch(conn, batch)
            refund = conn.execute("SELECT refund_amount, reason FROM refunds").fetchone()
            review = conn.execute("SELECT rating, is_positive FROM reviews").fetchone()
            fund = conn.execute("SELECT amount, currency FROM fund_flows").fetchone()
            ad = conn.execute("SELECT spend_amount, platform FROM ad_spend").fetchone()

    assert counts["refunds"] == 1
    assert counts["reviews"] == 1
    assert counts["fund_flows"] == 1
    assert counts["ad_spend"] == 1
    assert refund["refund_amount"] == 20.0
    assert refund["reason"] == "尺码偏小"
    assert review["rating"] == 2.0
    assert review["is_positive"] == "0"
    assert fund["amount"] == 100.0
    assert fund["currency"] == "CNY"
    assert ad["spend_amount"] == 200.0
    assert ad["platform"] == "wechat"


def test_full_order_export_derives_items_and_refunds() -> None:
    with temp_source_dir() as source_dir:
        orders_path = Path(source_dir) / "orders-full.csv"
        write_csv(orders_path, FULL_ORDER_EXPORT_HEADERS, FULL_ORDER_EXPORT_ROWS)
        write_manifest(source_dir, [artifact_for(orders_path, table_hint="orders", export_type="orders")])

        batch = import_source(source_dir)

        assert_counts(batch, orders=2, order_items=2, refunds=1)
        assert_has_warning(batch, "orders_export_order_items_derived")
        assert_has_warning(batch, "orders_export_refunds_derived")
        first_order = batch.orders[0]
        assert first_order["order_id"] == "wx-order-001"
        assert first_order["payment_amount"] == 120.0
        first_item = batch.order_items[0]
        assert first_item["order_id"] == "wx-order-001"
        assert first_item["product_name"] == "夏季连衣裙"
        assert first_item["quantity"] == 1.0
        first_refund = batch.refunds[0]
        assert first_refund["order_id"] == "wx-order-001"
        assert first_refund["refund_amount"] == 20.0
        assert first_refund["reason"] == "尺码偏小"


def test_zip_export_imports_nested_workbook() -> None:
    with temp_source_dir() as source_dir:
        nested_csv = Path(source_dir) / "orders-full.csv"
        write_csv(nested_csv, FULL_ORDER_EXPORT_HEADERS, FULL_ORDER_EXPORT_ROWS)
        zip_path = Path(source_dir) / "orders-export.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(nested_csv, arcname="微信小店订单.csv")
        nested_csv.unlink()
        write_manifest(source_dir, [artifact_for(zip_path, table_hint="orders", export_type="orders")])

        batch = import_source(source_dir)

        assert_counts(batch, orders=2, order_items=2, refunds=1)
        assert_has_warning(batch, "orders_export_order_items_derived")
        assert_has_warning(batch, "orders_export_refunds_derived")


def test_empty_manifest_imports_nothing() -> None:
    with temp_source_dir() as source_dir:
        write_csv(Path(source_dir) / "orders.csv", ORDERS_HEADERS, ORDERS_ROWS)
        write_manifest(source_dir, [])

        batch = import_source(source_dir)

        assert_counts(batch, orders=0)
        assert_all_standard_tables_empty(batch)
        assert_has_warning(batch, "manifest_no_completed_export_files")


def temp_source_dir() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="wechat_manifest_regression_")


def import_source(source_dir: str | Path):
    return build_import_batch(
        source_dir=source_dir,
        shop_id=SHOP_ID,
        task_id=TASK_ID,
        shop_name=SHOP_NAME,
    )


def write_csv(path: Path, headers: Iterable[str], rows: Iterable[Iterable[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(tuple(headers))
        writer.writerows(tuple(row) for row in rows)


def write_manifest(source_dir: str | Path, artifacts: list[dict[str, object]]) -> None:
    path = Path(source_dir) / "artifacts-manifest.json"
    manifest = {
        "task_id": TASK_ID,
        "source_kinds": ["export_file", "ocr_file", "chart_image"],
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def artifact_for(
    path: Path,
    *,
    table_hint: str | None = None,
    export_type: str | None = None,
    sha256_value: str | None = None,
) -> dict[str, object]:
    artifact: dict[str, object] = {
        "source_kind": "export_file",
        "source_type": "export_file",
        "saved_path": path.name,
        "original_filename": path.name,
        "shop_id": SHOP_ID,
        "shop_name": SHOP_NAME,
        "status": "completed",
        "size_bytes": path.stat().st_size,
        "sha256": sha256_value if sha256_value is not None else sha256_file(path),
    }
    if table_hint is not None:
        artifact["table_hint"] = table_hint
    if export_type is not None:
        artifact["export_type"] = export_type
    return artifact


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_counts(batch, **expected: int) -> None:
    counts = batch.counts()
    for table, expected_count in expected.items():
        actual_count = counts.get(table)
        assert actual_count == expected_count, (
            f"expected {table}={expected_count}, got {actual_count}; "
            f"counts={counts}; warnings={batch.warnings}"
        )


def assert_all_standard_tables_empty(batch) -> None:
    counts = batch.counts()
    non_empty = {table: counts[table] for table in STANDARD_TABLES if counts[table] != 0}
    assert non_empty == {}, f"expected no standard rows, got {non_empty}; warnings={batch.warnings}"


def warning_codes(batch) -> set[str]:
    return {str(warning.get("code")) for warning in batch.warnings}


def assert_has_warning(batch, code: str) -> None:
    codes = warning_codes(batch)
    assert code in codes, f"expected warning {code!r}, got {sorted(codes)}; warnings={batch.warnings}"


def assert_no_warning(batch, code: str) -> None:
    codes = warning_codes(batch)
    assert code not in codes, f"expected no warning {code!r}, got {sorted(codes)}; warnings={batch.warnings}"


if __name__ == "__main__":
    raise SystemExit(main())
