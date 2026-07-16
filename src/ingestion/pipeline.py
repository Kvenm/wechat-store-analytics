from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Mapping, Sequence

from ingestion.models import ALL_BATCH_TABLES, STANDARD_TABLES, ImportBatch
from ingestion.normalizer import guess_table_for_frame, normalize_frame
from ingestion.readers import ArtifactMatch, iter_source_files, load_artifact_manifest_index, read_workbook_tables
from shared.field_mapping import load_custom_field_map, match_fields, match_fields_detail
from shared.ids import fingerprint, stable_json
from shared.parsing import clean_cell, to_float, to_iso_datetime, to_text
from shared.paths import PROJECT_ROOT, ensure_dir
from shared.table_catalog import required_fields_for_table, table_for_export_type


def build_import_batch(
    *,
    source_dir: str | Path,
    shop_id: str,
    task_id: str | None,
    shop_name: str | None = None,
    field_map_path: str | Path | None = None,
    expected_types: Sequence[str] | None = None,
    manifest_policy: str = "auto",
) -> ImportBatch:
    custom_map = load_custom_field_map(field_map_path)
    batch = ImportBatch(shop_id=shop_id, task_id=task_id, shop_name=shop_name)
    allowed_tables = _allowed_tables_for_expected_types(expected_types)
    files = iter_source_files(source_dir)
    if not files:
        batch.warnings.append(
            {
                "code": "no_source_files",
                "source_dir": str(source_dir),
                "message": "source-dir 下未找到 xlsx/xls/csv 文件",
            }
        )
        return batch

    if manifest_policy not in {"auto", "ignore"}:
        raise ValueError(f"Unsupported manifest_policy: {manifest_policy}")

    manifest_index, manifest_warnings = (
        (None, [])
        if manifest_policy == "ignore"
        else load_artifact_manifest_index(source_dir)
    )
    batch.warnings.extend(manifest_warnings)
    has_manifest_without_importable_artifacts = bool(manifest_warnings) and any(
        str(warning.get("code", "")).startswith(("invalid_", "unsupported_", "artifact_"))
        or warning.get("code") == "manifest_no_completed_export_files"
        for warning in manifest_warnings
    )
    if manifest_index is None and has_manifest_without_importable_artifacts:
        batch.warnings.append(
            {
                "code": "manifest_no_completed_export_files",
                "source_dir": str(source_dir),
                "message": "已发现 artifacts-manifest.json，但没有可导入的 completed export_file artifact，已跳过目录内表格",
            }
        )
        return batch

    for path in files:
        artifact_match = None
        artifact_warnings: list[dict[str, object]] = []
        if manifest_index:
            artifact_match, artifact_warnings = manifest_index.match_file(path)
            batch.warnings.extend(artifact_warnings)
            if artifact_match is None:
                warning_code = (
                    "unlisted_source_file_ignored"
                    if any(warning.get("code") == "manifest_artifact_not_matched" for warning in artifact_warnings)
                    else "manifest_source_file_ignored"
                )
                batch.warnings.append(
                    {
                        "code": warning_code,
                        "source_file": str(path),
                        "message": "已读取 artifacts-manifest.json，但该文件未唯一匹配可导入 artifact，已跳过",
                    }
                )
                continue
            if artifact_match is not None:
                valid_artifact, validation_warnings = manifest_index.validate_match(
                    artifact_match,
                    path,
                    shop_id=shop_id,
                    task_id=task_id,
                )
                batch.warnings.extend(validation_warnings)
                if not valid_artifact:
                    continue

        for sheet_name, frame in read_workbook_tables(path):
            guessed_table, scores = guess_table_for_frame(
                frame,
                f"{path.parent.name} {path.name} {sheet_name or ''}",
                custom_map,
            )
            table = guessed_table
            source_file = _canonical_source_file(path)
            inspection = _base_source_inspection(
                frame,
                source_file=source_file,
                source_sheet=sheet_name,
                guessed_table=guessed_table,
                scores=scores,
            )
            selected_reason = "heuristic" if guessed_table else "unrecognized"
            inspection_warnings: list[dict[str, object]] = []
            if artifact_match:
                table, table_warnings, skip_sheet = _table_from_artifact_hint(
                    artifact_match=artifact_match,
                    guessed_table=guessed_table,
                    scores=scores,
                    source_file=source_file,
                    source_sheet=sheet_name,
                )
                batch.warnings.extend(table_warnings)
                inspection_warnings.extend(table_warnings)
                selected_reason = _selected_reason_from_warnings(table_warnings, fallback=selected_reason)
                if skip_sheet:
                    inspection.update(
                        {
                            "status": "skipped",
                            "selected_table": table or "",
                            "selected_reason": selected_reason,
                            "skip_reason": "table_hint_conflict",
                            "warnings": _compact_warnings(table_warnings),
                        }
                    )
                    batch.inspections.append(inspection)
                    continue
            if table is None:
                warning = {
                    "code": "unknown_table",
                    "source_file": source_file,
                    "source_sheet": sheet_name or "",
                    "scores_json": json.dumps(scores, ensure_ascii=False),
                    "message": "无法识别文件类型，未导入该表",
                }
                batch.warnings.append(warning)
                inspection.update(
                    {
                        "status": "unrecognized",
                        "selected_table": "",
                        "selected_reason": selected_reason,
                        "skip_reason": "unknown_table",
                        "warnings": _compact_warnings([warning]),
                    }
                )
                batch.inspections.append(inspection)
                continue
            _apply_field_inspection(inspection, table, custom_map)
            if allowed_tables is not None and table not in allowed_tables:
                warning = {
                    "code": "unexpected_table_for_expected_types",
                    "source_file": source_file,
                    "source_sheet": sheet_name or "",
                    "table": table,
                    "expected_types_json": json.dumps(list(expected_types or ()), ensure_ascii=False),
                    "allowed_tables_json": json.dumps(sorted(allowed_tables), ensure_ascii=False),
                    "message": "文件表头识别出的标准表不在本次选择的导入类型范围内，已跳过",
                }
                batch.warnings.append(warning)
                inspection.update(
                    {
                        "status": "skipped",
                        "selected_table": table,
                        "selected_reason": selected_reason,
                        "skip_reason": "unexpected_table_for_expected_types",
                        "warnings": _compact_warnings([warning]),
                    }
                )
                batch.inspections.append(inspection)
                continue
            records, warnings = normalize_frame(
                frame,
                table=table,
                shop_id=shop_id,
                shop_name=shop_name,
                task_id=task_id,
                source_file=source_file,
                source_sheet=sheet_name,
                custom_map=custom_map,
            )
            if table == "orders":
                records, dedupe_warnings = dedupe_order_records(records, source_file=source_file, source_sheet=sheet_name)
                warnings.extend(dedupe_warnings)
            batch.extend_table(table, records)
            batch.warnings.extend(warnings)
            derived_counts: dict[str, int] = {}
            source_warnings = [*inspection_warnings, *warnings]
            if table == "orders":
                derived_records, derived_warnings = derive_order_related_records(
                    frame,
                    shop_id=shop_id,
                    shop_name=shop_name,
                    task_id=task_id,
                    source_file=source_file,
                    source_sheet=sheet_name,
                    custom_map=custom_map,
                )
                for derived_table, derived_rows in derived_records.items():
                    batch.extend_table(derived_table, derived_rows)
                    if derived_rows:
                        derived_counts[derived_table] = len(derived_rows)
                batch.warnings.extend(derived_warnings)
                source_warnings.extend(derived_warnings)
            if table == "products":
                sku_records, sku_warnings = derive_product_sku_records(
                    frame,
                    shop_id=shop_id,
                    shop_name=shop_name,
                    task_id=task_id,
                    source_file=source_file,
                    source_sheet=sheet_name,
                    custom_map=custom_map,
                )
                batch.extend_table("product_skus", sku_records)
                if sku_records:
                    derived_counts["product_skus"] = len(sku_records)
                batch.warnings.extend(sku_warnings)
                source_warnings.extend(sku_warnings)
            row_counts = dict(inspection.get("row_counts") or {})
            row_counts["imported_rows"] = len(records)
            row_counts["derived_tables"] = derived_counts
            inspection.update(
                {
                    "status": _inspection_status(records, inspection, source_warnings, derived_counts),
                    "selected_table": table,
                    "selected_reason": selected_reason,
                    "row_counts": row_counts,
                    "missing_required_values": _missing_required_value_counts(source_warnings),
                    "warnings": _compact_warnings(source_warnings),
                }
            )
            batch.inspections.append(inspection)
    return batch


def _allowed_tables_for_expected_types(expected_types: Sequence[str] | None) -> set[str] | None:
    normalized = [str(item).strip() for item in expected_types or () if str(item).strip()]
    if not normalized:
        return None
    if any(item.lower() == "auto" for item in normalized):
        return None

    allowed: set[str] = set()
    for export_type in normalized:
        table = table_for_export_type(export_type)
        allowed.add(table)
        if table == "orders":
            allowed.update({"order_items", "refunds"})
        elif table == "products":
            allowed.add("product_skus")
    return allowed


def _canonical_source_file(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(resolved)


def _base_source_inspection(
    frame,
    *,
    source_file: str,
    source_sheet: str | None,
    guessed_table: str | None,
    scores: Mapping[str, int],
) -> dict[str, object]:
    cleaned = frame.dropna(axis=0, how="all").dropna(axis=1, how="all").copy()
    raw_rows = 0 if cleaned.empty else sum(1 for _, _row in cleaned.iterrows())
    columns = [] if cleaned.empty else [str(column).strip() for column in cleaned.columns]
    return {
        "source_id": fingerprint("source_inspection", source_file, source_sheet or ""),
        "source_file": source_file,
        "source_sheet": source_sheet or "",
        "status": "empty" if cleaned.empty else "pending",
        "selected_table": "",
        "selected_reason": "",
        "guessed_table": guessed_table or "",
        "scores": dict(scores),
        "headers": columns,
        "row_counts": {
            "raw_rows": raw_rows,
            "imported_rows": 0,
            "derived_tables": {},
        },
        "field_matches": [],
        "matched_fields": [],
        "missing_required_columns": [],
        "missing_required_values": [],
        "warnings": [],
    }


def _apply_field_inspection(
    inspection: dict[str, object],
    table: str,
    custom_map: Mapping[str, Mapping[str, Sequence[str]]] | None,
) -> None:
    details, _warnings = match_fields_detail(
        inspection.get("headers") or [],
        table,
        custom_map,
        include_warnings=False,
    )
    matched_details = [detail for detail in details if detail.get("header")]
    matched_fields = [str(detail["field"]) for detail in matched_details]
    required = set(required_fields_for_table(table))
    missing_required = [
        str(detail["field"])
        for detail in details
        if detail.get("required") or detail.get("field") in required
        if not detail.get("header")
    ]
    inspection["field_matches"] = matched_details
    inspection["matched_fields"] = matched_fields
    inspection["missing_required_columns"] = missing_required


def _selected_reason_from_warnings(
    warnings: Sequence[Mapping[str, object]],
    *,
    fallback: str,
) -> str:
    codes = {str(warning.get("code") or "") for warning in warnings}
    if "manifest_table_hint_used" in codes:
        return "manifest_table_hint"
    if "manifest_export_type_used" in codes:
        return "manifest_export_type"
    if "missing_manifest_table_hint" in codes:
        return fallback
    if "unsupported_table_hint" in codes or "unsupported_export_type" in codes:
        return fallback
    return fallback


def _inspection_status(
    records: Sequence[Mapping[str, object]],
    inspection: Mapping[str, object],
    warnings: Sequence[Mapping[str, object]],
    derived_counts: Mapping[str, int],
) -> str:
    if not records and not derived_counts:
        return "empty"
    missing_columns = inspection.get("missing_required_columns") or []
    missing_values = _missing_required_value_counts(warnings)
    if missing_columns or missing_values:
        return "needs_review"
    return "importable"


def _missing_required_value_counts(
    warnings: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    counts: dict[str, int] = {}
    for warning in warnings:
        if warning.get("code") != "missing_required_value":
            continue
        field = str(warning.get("field") or "")
        if not field:
            continue
        counts[field] = counts.get(field, 0) + 1
    return [
        {"field": field, "count": count}
        for field, count in sorted(counts.items())
    ]


def _compact_warnings(
    warnings: Sequence[Mapping[str, object]],
    *,
    limit: int = 8,
) -> list[dict[str, object]]:
    compacted: list[dict[str, object]] = []
    for warning in warnings[:limit]:
        compacted.append(
            {
                "code": str(warning.get("code") or "warning"),
                "field": str(warning.get("field") or ""),
                "message": str(warning.get("message") or ""),
            }
        )
    if len(warnings) > limit:
        compacted.append(
            {
                "code": "more_warnings",
                "field": "",
                "message": f"还有 {len(warnings) - limit} 条 warning 未在摘要中展示",
            }
        )
    return compacted


def dedupe_order_records(
    records: list[dict[str, object]],
    *,
    source_file: str,
    source_sheet: str | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    by_key: dict[tuple[object, object], dict[str, object]] = {}
    duplicate_count = 0
    skipped_missing_identity_count = 0
    for record in records:
        if record.get("_generated_order_id") and not record.get("order_created_at"):
            skipped_missing_identity_count += 1
            continue
        key = (record.get("shop_id"), record.get("order_id") or record.get("row_fingerprint"))
        if key not in by_key:
            by_key[key] = record
            continue
        duplicate_count += 1
        by_key[key] = _merge_order_record(by_key[key], record)

    warnings: list[dict[str, object]] = []
    if duplicate_count:
        warnings.append(
            {
                "code": "orders_export_rows_deduped",
                "source_file": source_file,
                "source_sheet": source_sheet or "",
                "duplicate_row_count": duplicate_count,
                "message": "订单导出表存在同一订单多行，订单表已按订单号合并；商品行保留在订单明细表",
            }
        )
    if skipped_missing_identity_count:
        warnings.append(
            {
                "code": "orders_export_rows_without_identity_skipped",
                "source_file": source_file,
                "source_sheet": source_sheet or "",
                "skipped_row_count": skipped_missing_identity_count,
                "message": "订单导出表存在缺少订单号和下单时间的页面重复行，已从订单表排除",
            }
        )
    return list(by_key.values()), warnings


def _merge_order_record(existing: dict[str, object], incoming: dict[str, object]) -> dict[str, object]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key in {"source_row_number", "raw_json", "row_fingerprint", "created_at", "updated_at"}:
            continue
        if merged.get(key) in (None, "") and value not in (None, ""):
            merged[key] = value

    for amount_key in ("payment_amount", "shipping_amount", "discount_amount", "refund_amount"):
        existing_amount = to_float(merged.get(amount_key)) or 0.0
        incoming_amount = to_float(incoming.get(amount_key)) or 0.0
        if incoming_amount > existing_amount:
            merged[amount_key] = incoming.get(amount_key)
    return merged


def derive_order_related_records(
    frame,
    *,
    shop_id: str,
    shop_name: str | None,
    task_id: str | None,
    source_file: str,
    source_sheet: str | None,
    custom_map: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> tuple[dict[str, list[dict[str, object]]], list[dict[str, object]]]:
    """Derive order_items/refunds from a full order export sheet when columns exist."""
    cleaned = frame.dropna(axis=0, how="all").dropna(axis=1, how="all").copy()
    if cleaned.empty:
        return {"order_items": [], "refunds": []}, []
    cleaned.columns = [str(column).strip() for column in cleaned.columns]

    order_matches, _ = match_fields(cleaned.columns.tolist(), "orders", custom_map, include_warnings=False)
    item_matches, _ = match_fields(cleaned.columns.tolist(), "order_items", custom_map, include_warnings=False)
    refund_matches, _ = match_fields(cleaned.columns.tolist(), "refunds", custom_map, include_warnings=False)

    derived = {"order_items": [], "refunds": []}
    warnings: list[dict[str, object]] = []
    can_derive_items = bool(item_matches.get("product_name") or item_matches.get("product_id"))
    can_derive_refunds = bool(refund_matches.get("refund_amount") or refund_matches.get("refund_status") or refund_matches.get("refund_id") or refund_matches.get("reason"))

    for row_number, row in cleaned.iterrows():
        raw_row = {str(column): clean_cell(row[column]) for column in cleaned.columns}
        if all(value is None for value in raw_row.values()):
            continue

        order_id = _matched_text(raw_row, order_matches, "order_id")
        if not order_id:
            continue

        base = {
            "shop_id": shop_id,
            "task_id": task_id,
            "source_file": source_file,
            "source_sheet": source_sheet,
            "source_row_number": int(row_number) + 2,
            "raw_json": stable_json(raw_row),
        }
        if shop_name:
            base["shop_name_snapshot"] = shop_name

        if can_derive_items:
            item = {
                **base,
                "order_id": order_id,
                "order_item_id": _matched_text(raw_row, item_matches, "order_item_id"),
                "product_id": _matched_text(raw_row, item_matches, "product_id"),
                "product_name": _matched_text(raw_row, item_matches, "product_name"),
                "sku_id": _matched_text(raw_row, item_matches, "sku_id"),
                "sku_name": _matched_text(raw_row, item_matches, "sku_name"),
                "quantity": _matched_amount(raw_row, item_matches, "quantity") or 1.0,
                "item_amount": _matched_amount(raw_row, item_matches, "item_amount"),
                "refund_amount": _matched_amount(raw_row, item_matches, "refund_amount"),
            }
            if not item.get("product_id") and item.get("product_name"):
                item["product_id"] = fingerprint("product", shop_id, item.get("product_name"), item.get("sku_name"))
            if not item.get("order_item_id"):
                item["order_item_id"] = fingerprint(
                    "order_item",
                    shop_id,
                    order_id,
                    item.get("product_id"),
                    item.get("sku_id"),
                    item.get("product_name"),
                    item.get("sku_name"),
                    item.get("source_row_number"),
                )
            item["row_fingerprint"] = fingerprint("order_items", (shop_id, item["order_item_id"], order_id))
            derived["order_items"].append(item)

        refund_amount = _matched_amount(raw_row, refund_matches, "refund_amount")
        refund_status = _matched_text(raw_row, refund_matches, "refund_status")
        refund_id = _matched_text(raw_row, refund_matches, "refund_id")
        refund_reason = _matched_text(raw_row, refund_matches, "reason")
        if can_derive_refunds and _has_refund_signal(refund_amount, refund_status, refund_id, refund_reason):
            refund = {
                **base,
                "refund_id": refund_id,
                "order_id": order_id,
                "product_id": _matched_text(raw_row, refund_matches, "product_id") or _matched_text(raw_row, item_matches, "product_id"),
                "product_name": _matched_text(raw_row, refund_matches, "product_name") or _matched_text(raw_row, item_matches, "product_name"),
                "sku_id": _matched_text(raw_row, refund_matches, "sku_id") or _matched_text(raw_row, item_matches, "sku_id"),
                "refund_status": refund_status,
                "refund_amount": refund_amount,
                "refund_created_at": _matched_datetime(raw_row, refund_matches, "refund_created_at"),
                "refund_completed_at": _matched_datetime(raw_row, refund_matches, "refund_completed_at"),
                "reason": refund_reason,
            }
            if not refund.get("product_id") and refund.get("product_name"):
                refund["product_id"] = fingerprint("product", shop_id, refund.get("product_name"), refund.get("sku_id"))
            if not refund.get("refund_id"):
                refund["refund_id"] = fingerprint(
                    "refund",
                    shop_id,
                    order_id,
                    refund.get("product_id"),
                    refund.get("refund_amount"),
                    refund.get("refund_status"),
                    refund.get("source_row_number"),
                )
            refund["row_fingerprint"] = fingerprint("refunds", (shop_id, refund["refund_id"], order_id))
            derived["refunds"].append(refund)

    if derived["order_items"]:
        warnings.append(
            {
                "code": "orders_export_order_items_derived",
                "source_file": source_file,
                "source_sheet": source_sheet or "",
                "derived_row_count": len(derived["order_items"]),
                "message": "订单导出表包含商品字段，已同步拆出订单明细",
            }
        )
    if derived["refunds"]:
        warnings.append(
            {
                "code": "orders_export_refunds_derived",
                "source_file": source_file,
                "source_sheet": source_sheet or "",
                "derived_row_count": len(derived["refunds"]),
                "message": "订单导出表包含售后/退款字段，已同步拆出退款记录",
            }
        )
    return derived, warnings


def derive_product_sku_records(
    frame,
    *,
    shop_id: str,
    shop_name: str | None,
    task_id: str | None,
    source_file: str,
    source_sheet: str | None,
    custom_map: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Derive product_skus from a product-list export when SKU-level columns exist."""
    cleaned = frame.dropna(axis=0, how="all").dropna(axis=1, how="all").copy()
    if cleaned.empty:
        return [], []
    cleaned.columns = [str(column).strip() for column in cleaned.columns]

    sku_matches, _ = match_fields(cleaned.columns.tolist(), "product_skus", custom_map, include_warnings=False)
    product_matches, _ = match_fields(cleaned.columns.tolist(), "products", custom_map, include_warnings=False)
    if not (sku_matches.get("sku_id") or sku_matches.get("sku_name")):
        return [], []

    records: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    for row_number, row in cleaned.iterrows():
        raw_row = {str(column): clean_cell(row[column]) for column in cleaned.columns}
        if all(value is None for value in raw_row.values()):
            continue

        product_name = _matched_text(raw_row, sku_matches, "product_name") or _matched_text(raw_row, product_matches, "product_name")
        product_id = _matched_text(raw_row, sku_matches, "product_id") or _matched_text(raw_row, product_matches, "product_id")
        sku_id = _matched_text(raw_row, sku_matches, "sku_id")
        sku_name = _matched_text(raw_row, sku_matches, "sku_name")
        if not product_name:
            continue
        if not product_id:
            product_id = fingerprint("product", shop_id, product_name)
        if not sku_id:
            sku_id = fingerprint("sku", shop_id, product_id, sku_name, raw_row)

        record = {
            "shop_id": shop_id,
            "task_id": task_id,
            "product_id": product_id,
            "product_name": product_name,
            "sku_id": sku_id,
            "sku_name": sku_name,
            "category": _matched_text(raw_row, sku_matches, "category") or _matched_text(raw_row, product_matches, "category"),
            "status": _matched_text(raw_row, sku_matches, "status") or _matched_text(raw_row, product_matches, "status"),
            "sku_price": _matched_amount(raw_row, sku_matches, "sku_price") or _matched_amount(raw_row, product_matches, "price"),
            "stock": _matched_amount(raw_row, sku_matches, "stock") or _matched_amount(raw_row, product_matches, "stock"),
            "barcode": _matched_text(raw_row, sku_matches, "barcode"),
            "source_file": source_file,
            "source_sheet": source_sheet,
            "source_row_number": int(row_number) + 2,
            "raw_json": stable_json(raw_row),
            "row_fingerprint": fingerprint("product_skus", (shop_id, product_id, sku_id)),
        }
        if shop_name:
            record["shop_name_snapshot"] = shop_name
        records.append(record)

    if records:
        warnings.append(
            {
                "code": "product_list_skus_derived",
                "source_file": source_file,
                "source_sheet": source_sheet or "",
                "derived_row_count": len(records),
                "message": "商品列表导出包含 SKU 字段，已同步拆出 SKU 明细",
            }
        )
    return records, warnings


def _matched_text(raw_row: Mapping[str, object], matches: Mapping[str, str], field: str) -> str | None:
    return to_text(raw_row.get(matches[field])) if field in matches else None


def _matched_amount(raw_row: Mapping[str, object], matches: Mapping[str, str], field: str) -> float | None:
    return to_float(raw_row.get(matches[field])) if field in matches else None


def _matched_datetime(raw_row: Mapping[str, object], matches: Mapping[str, str], field: str) -> str | None:
    return to_iso_datetime(raw_row.get(matches[field])) if field in matches else None


def _has_refund_signal(
    refund_amount: float | None,
    refund_status: str | None,
    refund_id: str | None,
    refund_reason: str | None,
) -> bool:
    if refund_id:
        return True
    if refund_amount is not None and refund_amount > 0:
        return True
    text = " ".join(value for value in [refund_status, refund_reason] if value)
    return bool(text and any(token in text for token in ("退款", "退货", "售后", "refund", "return")))


def _table_from_artifact_hint(
    *,
    artifact_match: ArtifactMatch,
    guessed_table: str | None,
    scores: Mapping[str, int],
    source_file: str,
    source_sheet: str | None,
) -> tuple[str | None, list[dict[str, object]], bool]:
    artifact = artifact_match.artifact
    table_hint = artifact.table_hint.casefold()
    warnings: list[dict[str, object]] = []

    if table_hint:
        if table_hint in STANDARD_TABLES:
            return _evaluate_manifest_table(
                table=table_hint,
                hint_source="table_hint",
                artifact_match=artifact_match,
                guessed_table=guessed_table,
                scores=scores,
                source_file=source_file,
                source_sheet=source_sheet,
            )
        warnings.append(
            {
                **_manifest_warning_base(artifact_match, source_file, source_sheet),
                "code": "unsupported_table_hint",
                "table_hint": artifact.table_hint,
                "guessed_table": guessed_table or "",
                "message": "manifest table_hint 不是已知标准表，尝试使用 export_type 或启发式猜表",
            }
        )

    export_type = artifact.export_type.casefold()
    export_table = table_for_export_type(export_type) if export_type else ""
    if export_table in STANDARD_TABLES:
        table, table_warnings, skip_sheet = _evaluate_manifest_table(
            table=export_table,
            hint_source="export_type",
            artifact_match=artifact_match,
            guessed_table=guessed_table,
            scores=scores,
            source_file=source_file,
            source_sheet=source_sheet,
        )
        return table, warnings + table_warnings, skip_sheet
    if export_type:
        warnings.append(
            {
                **_manifest_warning_base(artifact_match, source_file, source_sheet),
                "code": "unsupported_export_type",
                "export_type": artifact.export_type,
                "guessed_table": guessed_table or "",
                "message": "manifest export_type 无法映射到标准表，回退启发式猜表",
            }
        )
    else:
        warnings.append(
            {
                **_manifest_warning_base(artifact_match, source_file, source_sheet),
                "code": "missing_manifest_table_hint",
                "guessed_table": guessed_table or "",
                "message": "manifest artifact 未提供 table_hint/export_type，回退启发式猜表",
            }
        )
    return guessed_table, warnings, False


def _evaluate_manifest_table(
    *,
    table: str,
    hint_source: str,
    artifact_match: ArtifactMatch,
    guessed_table: str | None,
    scores: Mapping[str, int],
    source_file: str,
    source_sheet: str | None,
) -> tuple[str, list[dict[str, object]], bool]:
    base = _manifest_warning_base(artifact_match, source_file, source_sheet)
    hint_score = scores.get(table, 0)
    best_table, best_score = _best_scored_table(scores)
    score_context = {
        "table_hint": table,
        "hint_source": hint_source,
        "guessed_table": guessed_table or "",
        "best_table": best_table or "",
        "hint_score": hint_score,
        "best_score": best_score,
        "scores_json": json.dumps(scores, ensure_ascii=False),
    }

    if _has_strong_table_conflict(table, scores):
        return table, [
            {
                **base,
                **score_context,
                "code": "table_hint_conflict",
                "message": "manifest 提示表与表头强冲突，已跳过该 sheet，避免误导入",
            }
        ], True

    used_code = "manifest_table_hint_used" if hint_source == "table_hint" else "manifest_export_type_used"
    warnings: list[dict[str, object]] = [
        {
            **base,
            **score_context,
            "code": used_code,
            "message": "使用 artifacts-manifest.json 的表类型提示识别标准表",
        }
    ]

    if hint_score < 2:
        warnings.append(
            {
                **base,
                **score_context,
                "code": "low_confidence_table_hint",
                "message": "manifest 提示表对应表头匹配分较低，已导入但需要人工复核",
            }
        )

    if guessed_table and guessed_table != table:
        mismatch_code = "manifest_table_hint_mismatch" if hint_source == "table_hint" else "manifest_export_type_mismatch"
        warnings.append(
            {
                **base,
                **score_context,
                "code": mismatch_code,
                "message": "manifest 提示表与启发式猜表结果不一致，已优先使用 manifest",
            }
        )
    return table, warnings, False


def _has_strong_table_conflict(table: str, scores: Mapping[str, int]) -> bool:
    best_table, best_score = _best_scored_table(scores)
    if not best_table or best_table == table or best_score < 3:
        return False
    hint_score = scores.get(table, 0)
    return hint_score < 2 or best_score >= hint_score + 2


def _best_scored_table(scores: Mapping[str, int]) -> tuple[str | None, int]:
    if not scores:
        return None, 0
    best_table = max(scores, key=scores.get)
    return best_table, scores.get(best_table, 0)


def _manifest_warning_base(
    artifact_match: ArtifactMatch,
    source_file: str,
    source_sheet: str | None,
) -> dict[str, object]:
    artifact = artifact_match.artifact
    return {
        "source_file": source_file,
        "source_sheet": source_sheet or "",
        "manifest_path": str(artifact.manifest_path),
        "artifact_index": artifact.artifact_index,
        "match_type": artifact_match.match_type,
        "export_type": artifact.export_type,
    }


def write_standard_tables(batch: ImportBatch, output_dir: str | Path) -> dict[str, str]:
    output = ensure_dir(Path(output_dir))
    paths: dict[str, str] = {}
    for table in ALL_BATCH_TABLES:
        rows = getattr(batch, table)
        if not rows:
            continue
        path = output / f"{batch.shop_id}_{batch.task_id or 'manual'}_{table}.csv"
        _write_rows(path, rows)
        paths[table] = str(path)

    if batch.warnings:
        warning_path = output / f"{batch.shop_id}_{batch.task_id or 'manual'}_warnings.csv"
        _write_rows(warning_path, batch.warnings)
        paths["warnings"] = str(warning_path)
    return paths


def _write_rows(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    ensure_dir(path.parent)
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
