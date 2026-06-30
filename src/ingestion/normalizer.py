from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from shared.field_mapping import FieldSpec, guess_table, match_fields, specs_for
from shared.ids import fingerprint, stable_json
from shared.parsing import clean_cell, to_float, to_iso_datetime, to_text
from shared.table_catalog import FIELD_KIND_AMOUNT, FIELD_KIND_DATETIME, required_fields_for_table


def normalize_frame(
    frame: pd.DataFrame,
    *,
    table: str,
    shop_id: str,
    task_id: str | None,
    shop_name: str | None = None,
    source_file: str,
    source_sheet: str | None,
    custom_map: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    cleaned = _clean_frame(frame)
    if cleaned.empty:
        return [], [
            {
                "code": "empty_sheet",
                "table": table,
                "source_file": source_file,
                "source_sheet": source_sheet or "",
                "message": "文件或 sheet 没有可导入数据",
            }
        ]

    field_matches, warnings = match_fields(cleaned.columns.tolist(), table, custom_map)
    specs_by_name = {spec.name: spec for spec in specs_for(table, custom_map)}
    records: list[dict[str, Any]] = []

    for row_number, row in cleaned.iterrows():
        raw_row = {str(column): clean_cell(row[column]) for column in cleaned.columns}
        if all(value is None for value in raw_row.values()):
            continue

        record: dict[str, Any] = {
            "shop_id": shop_id,
            "task_id": task_id,
            "source_file": source_file,
            "source_sheet": source_sheet,
            "source_row_number": int(row_number) + 2,
            "raw_json": stable_json(raw_row),
        }
        if shop_name:
            record["shop_name_snapshot"] = shop_name
        for field_name, header in field_matches.items():
            spec = specs_by_name.get(field_name, FieldSpec(field_name, ()))
            record[field_name] = _coerce_value(raw_row.get(header), spec)

        _fill_defaults(table, record, raw_row, shop_id, task_id, source_file, source_sheet)
        record["row_fingerprint"] = _row_fingerprint(table, record, raw_row)
        row_warnings = _row_warnings(table, record, raw_row, row_number, source_file, source_sheet)
        warnings.extend(row_warnings)
        records.append(record)

    return records, warnings


def guess_table_for_frame(
    frame: pd.DataFrame,
    source_name: str,
    custom_map: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> tuple[str | None, dict[str, int]]:
    cleaned = _clean_frame(frame)
    return guess_table(cleaned.columns.tolist(), source_name, custom_map)


def _clean_frame(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.dropna(axis=0, how="all").dropna(axis=1, how="all").copy()
    cleaned.columns = [str(column).strip() for column in cleaned.columns]
    return cleaned


def _coerce_value(value: Any, spec: FieldSpec) -> Any:
    if spec.kind == FIELD_KIND_DATETIME:
        return to_iso_datetime(value)
    if spec.kind == FIELD_KIND_AMOUNT:
        return to_float(value)
    return to_text(value)


def _fill_defaults(
    table: str,
    record: dict[str, Any],
    raw_row: dict[str, Any],
    shop_id: str,
    task_id: str | None,
    source_file: str,
    source_sheet: str | None,
) -> None:
    if table == "orders":
        record.setdefault("currency", "CNY")
        if not record.get("order_id"):
            record["order_id"] = fingerprint(shop_id, source_file, source_sheet, record.get("source_row_number"), raw_row)
    elif table == "order_items":
        if not record.get("order_item_id"):
            record["order_item_id"] = fingerprint(shop_id, record.get("order_id"), record.get("product_id"), record.get("sku_id"), raw_row)
        if not record.get("product_id") and record.get("product_name"):
            record["product_id"] = fingerprint("product", shop_id, record.get("product_name"), record.get("sku_name"))
        if record.get("quantity") is None:
            record["quantity"] = 1.0
    elif table == "products":
        if not record.get("product_id") and record.get("product_name"):
            record["product_id"] = fingerprint("product", shop_id, record.get("product_name"), record.get("sku_name"))
    elif table == "product_skus":
        if not record.get("product_id") and record.get("product_name"):
            record["product_id"] = fingerprint("product", shop_id, record.get("product_name"))
        if not record.get("sku_id"):
            record["sku_id"] = fingerprint("sku", shop_id, record.get("product_id"), record.get("sku_name"), raw_row)
    elif table == "refunds":
        if not record.get("refund_id"):
            record["refund_id"] = fingerprint(shop_id, record.get("order_id"), record.get("product_id"), record.get("refund_amount"), raw_row)
        if not record.get("product_id") and record.get("product_name"):
            record["product_id"] = fingerprint("product", shop_id, record.get("product_name"), record.get("sku_id"))
    elif table == "reviews":
        if not record.get("review_id"):
            record["review_id"] = fingerprint(shop_id, record.get("order_id"), record.get("product_id"), record.get("buyer_id"), record.get("review_created_at"), raw_row)
        if not record.get("product_id") and record.get("product_name"):
            record["product_id"] = fingerprint("product", shop_id, record.get("product_name"), record.get("sku_id"))
        if not record.get("is_positive") and record.get("rating") is not None:
            record["is_positive"] = "1" if float(record["rating"]) >= 4 else "0"
    elif table == "shop_daily":
        if not record.get("stat_date"):
            record["stat_date"] = _date_from_source(source_file, source_sheet)
    elif table == "product_daily":
        if not record.get("stat_date"):
            record["stat_date"] = _date_from_source(source_file, source_sheet)
        if not record.get("product_id") and record.get("product_name"):
            record["product_id"] = fingerprint("product", shop_id, record.get("product_name"), record.get("sku_id"))
    elif table == "traffic_sources":
        if not record.get("stat_date"):
            record["stat_date"] = _date_from_source(source_file, source_sheet)
        if not record.get("source_name"):
            record["source_name"] = record.get("source_type") or "unknown"
    elif table == "fund_flows":
        record.setdefault("currency", "CNY")
        if not record.get("flow_id"):
            record["flow_id"] = fingerprint(shop_id, record.get("flow_date"), record.get("flow_type"), record.get("order_id"), record.get("amount"), raw_row)
    elif table == "ad_spend":
        if not record.get("stat_date"):
            record["stat_date"] = _date_from_source(source_file, source_sheet)
        if not record.get("platform"):
            record["platform"] = "wechat"
    elif table == "audience_insights":
        _fill_audience_defaults(record)
        if not record.get("insight_id"):
            record["insight_id"] = fingerprint(
                shop_id,
                record.get("product_id"),
                record.get("product_name"),
                record.get("dimension"),
                record.get("segment_label"),
                source_file,
                source_sheet,
                record.get("source_row_number"),
                raw_row,
            )
        if not record.get("product_id") and record.get("product_name"):
            record["product_id"] = fingerprint("product", shop_id, record.get("product_name"), record.get("sku_id"))
    elif table == "ai_reports":
        if not record.get("report_id"):
            record["report_id"] = fingerprint(shop_id, record.get("report_type"), record.get("report_title"), record.get("report_date"), raw_row)
    elif table == "collection_tasks":
        if not record.get("collection_task_id"):
            record["collection_task_id"] = task_id or fingerprint(shop_id, record.get("task_name"), record.get("started_at"), source_file, source_sheet, raw_row)
    elif table == "collection_task_items":
        if not record.get("collection_task_id"):
            record["collection_task_id"] = task_id
        if not record.get("item_id"):
            record["item_id"] = fingerprint(shop_id, record.get("collection_task_id"), record.get("table_name"), source_file, source_sheet, record.get("source_row_number"), raw_row)


def _row_fingerprint(table: str, record: dict[str, Any], raw_row: dict[str, Any]) -> str:
    natural_key = _natural_key(table, record)
    if natural_key is not None:
        return fingerprint(table, natural_key)
    return fingerprint(table, record.get("shop_id"), record.get("source_file"), record.get("source_sheet"), record.get("source_row_number"), raw_row)


def _natural_key(table: str, record: dict[str, Any]) -> tuple[Any, ...] | None:
    shop_id = record.get("shop_id")
    if table == "orders" and shop_id and record.get("order_id"):
        return (shop_id, record.get("order_id"))
    if table == "order_items" and shop_id and record.get("order_item_id"):
        return (shop_id, record.get("order_item_id"), record.get("order_id"))
    if table == "products" and shop_id and record.get("product_id"):
        return (shop_id, record.get("product_id"), record.get("sku_id"))
    if table == "product_skus" and shop_id and record.get("product_id") and record.get("sku_id"):
        return (shop_id, record.get("product_id"), record.get("sku_id"))
    if table == "refunds" and shop_id and record.get("refund_id"):
        return (shop_id, record.get("refund_id"), record.get("order_id"))
    if table == "reviews" and shop_id and record.get("review_id"):
        return (shop_id, record.get("review_id"))
    if table == "shop_daily" and shop_id and record.get("stat_date"):
        return (shop_id, record.get("stat_date"))
    if table == "product_daily" and shop_id and record.get("stat_date") and record.get("product_id"):
        return (shop_id, record.get("stat_date"), record.get("product_id"), record.get("sku_id"))
    if table == "traffic_sources" and shop_id and record.get("stat_date") and record.get("source_name"):
        return (shop_id, record.get("stat_date"), record.get("source_name"), record.get("product_id"))
    if table == "fund_flows" and shop_id and record.get("flow_id"):
        return (shop_id, record.get("flow_id"))
    if table == "ad_spend" and shop_id and record.get("stat_date") and (record.get("campaign_id") or record.get("campaign_name")):
        return (shop_id, record.get("stat_date"), record.get("platform"), record.get("campaign_id"), record.get("campaign_name"), record.get("ad_group_id"), record.get("product_id"))
    if table == "audience_insights" and shop_id and record.get("insight_id"):
        return (shop_id, record.get("insight_id"))
    if table == "ai_reports" and shop_id and record.get("report_id"):
        return (shop_id, record.get("report_id"))
    if table == "collection_tasks" and shop_id and record.get("collection_task_id"):
        return (shop_id, record.get("collection_task_id"))
    if table == "collection_task_items" and shop_id and record.get("collection_task_id") and record.get("item_id"):
        return (shop_id, record.get("collection_task_id"), record.get("item_id"))
    return None


def _row_warnings(
    table: str,
    record: dict[str, Any],
    raw_row: dict[str, Any],
    row_number: int,
    source_file: str,
    source_sheet: str | None,
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    for field_name in required_fields_for_table(table):
        if record.get(field_name) in (None, ""):
            warnings.append(
                {
                    "code": "missing_required_value",
                    "table": table,
                    "field": field_name,
                    "source_file": source_file,
                    "source_sheet": source_sheet or "",
                    "source_row_number": str(int(row_number) + 2),
                    "message": f"{table} 第 {int(row_number) + 2} 行缺少 {field_name}",
                }
            )
    raw_json = json.dumps(raw_row, ensure_ascii=False, default=str)
    if len(raw_json) > 50000:
        warnings.append(
            {
                "code": "large_raw_row",
                "table": table,
                "source_file": source_file,
                "source_sheet": source_sheet or "",
                "source_row_number": str(int(row_number) + 2),
                "message": "原始行 JSON 较大，请检查是否误读了表头或合并单元格",
            }
        )
    return warnings


def _fill_audience_defaults(record: dict[str, Any]) -> None:
    if not record.get("dimension"):
        for field_name, dimension in (
            ("gender", "gender"),
            ("age_group", "age_group"),
            ("region", "region"),
            ("consumption_level", "consumption_level"),
            ("active_hour", "active_hour"),
        ):
            if record.get(field_name):
                record["dimension"] = dimension
                break
    if not record.get("segment_label"):
        for field_name in ("gender", "age_group", "region", "consumption_level", "active_hour", "category"):
            if record.get(field_name):
                record["segment_label"] = record.get(field_name)
                break


def _date_from_source(source_file: str, source_sheet: str | None) -> str | None:
    text = f"{Path(source_file).stem} {source_sheet or ''}"
    digits = "".join(char if char.isdigit() else " " for char in text).split()
    for token in digits:
        if len(token) == 8:
            return f"{token[:4]}-{token[4:6]}-{token[6:8]}"
    return None
