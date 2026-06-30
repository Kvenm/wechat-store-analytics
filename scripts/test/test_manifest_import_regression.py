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
