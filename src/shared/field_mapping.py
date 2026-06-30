from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping, Sequence

from shared.table_catalog import (
    FIELD_SPECS,
    FILE_HINTS,
    HEURISTIC_TOKENS,
    STANDARD_TABLES,
    FieldSpec,
    specs_for_table,
)


def normalize_header(value: object) -> str:
    text = str(value or "").strip().lower().replace("\ufeff", "")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[()（）\[\]【】{}:：/\\|_\-.,，。]", "", text)
    return text


def load_custom_field_map(path: str | Path | None) -> dict[str, dict[str, list[str]]]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    result: dict[str, dict[str, list[str]]] = {}
    for table, fields in data.items():
        if not isinstance(fields, Mapping):
            continue
        result[table] = {}
        for field, aliases in fields.items():
            if isinstance(aliases, str):
                result[table][field] = [aliases]
            elif isinstance(aliases, Sequence):
                result[table][field] = [str(alias) for alias in aliases]
    return result


def specs_for(table: str, custom_map: Mapping[str, Mapping[str, Sequence[str]]] | None = None) -> tuple[FieldSpec, ...]:
    specs = list(specs_for_table(table))
    if not custom_map or table not in custom_map:
        return tuple(specs)
    custom_fields = custom_map[table]
    merged: list[FieldSpec] = []
    for spec in specs:
        extra_aliases = tuple(custom_fields.get(spec.name, ()))
        merged.append(FieldSpec(spec.name, spec.aliases + extra_aliases, spec.kind, spec.required))
    return tuple(merged)


def match_fields(
    headers: Sequence[object],
    table: str,
    custom_map: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
    include_warnings: bool = True,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    details, warnings = match_fields_detail(
        headers,
        table,
        custom_map,
        include_warnings=include_warnings,
    )
    matches = {
        str(detail["field"]): str(detail["header"])
        for detail in details
        if detail.get("header")
    }
    return matches, warnings


def match_fields_detail(
    headers: Sequence[object],
    table: str,
    custom_map: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
    include_warnings: bool = True,
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    source_headers = [str(header).strip() for header in headers if str(header).strip()]
    normalized_to_header: dict[str, str] = {}
    for header in source_headers:
        normalized = normalize_header(header)
        if normalized and normalized not in normalized_to_header:
            normalized_to_header[normalized] = header

    details: list[dict[str, object]] = []
    used_headers: set[str] = set()
    custom_fields = custom_map.get(table, {}) if custom_map else {}
    for spec in specs_for_table(table):
        header: str | None = None
        match_method = "missing"
        for alias in spec.aliases:
            header = _header_for_alias(normalized_to_header, alias, used_headers)
            if header:
                match_method = "alias"
                break

        if not header:
            for alias in custom_fields.get(spec.name, ()):
                header = _header_for_alias(normalized_to_header, alias, used_headers)
                if header:
                    match_method = "custom"
                    break

        if not header:
            heuristic_header = _match_by_tokens(source_headers, spec.name, used_headers)
            if heuristic_header:
                header = heuristic_header
                match_method = "heuristic"
                if include_warnings:
                    warnings.append(
                        {
                            "code": "heuristic_field_match",
                            "field": spec.name,
                            "header": heuristic_header,
                            "message": f"字段 {spec.name} 使用启发式匹配到列 {heuristic_header}",
                        }
                    )

        if header:
            used_headers.add(header)

        details.append(
            {
                "field": spec.name,
                "header": header or "",
                "kind": spec.kind,
                "required": bool(spec.required),
                "match_method": match_method,
            }
        )

        if include_warnings and spec.required and not header:
            warnings.append(
                {
                    "code": "missing_required_column",
                    "field": spec.name,
                    "table": table,
                    "message": f"{table} 缺少关键字段 {spec.name}",
                }
            )
    return details, warnings


def _header_for_alias(
    normalized_to_header: Mapping[str, str],
    alias: str,
    used_headers: set[str],
) -> str | None:
    normalized_alias = normalize_header(alias)
    header = normalized_to_header.get(normalized_alias)
    if header and header not in used_headers:
        return header
    return None


def guess_table(
    headers: Sequence[object],
    source_name: str,
    custom_map: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> tuple[str | None, dict[str, int]]:
    normalized_name = normalize_header(source_name)
    scores: dict[str, int] = {}
    guessable_tables = [table for table in FIELD_SPECS if table in STANDARD_TABLES]
    for table in guessable_tables:
        matches, _ = match_fields(headers, table, custom_map, include_warnings=False)
        score = len(matches)
        for hint in FILE_HINTS.get(table, ()):
            if normalize_header(hint) in normalized_name:
                score += 2
        if table == "refunds" and {"refund_amount", "order_id"} <= set(matches):
            score += 2
        if table == "orders" and {"order_id", "payment_amount"} <= set(matches):
            score += 2
        if table == "products" and {"product_name", "price"} <= set(matches):
            score += 1
        if table == "product_skus" and {"product_name", "sku_id"} <= set(matches):
            score += 2
        if table == "reviews" and {"review_content", "review_created_at"} <= set(matches):
            score += 2
        if table == "shop_daily" and {"stat_date", "visitor_count", "payment_amount"} <= set(matches):
            score += 3
        if table == "shop_daily" and {"stat_date", "exposure_user_count", "payment_amount"} <= set(matches):
            score += 3
        if table == "product_daily" and {"stat_date", "product_name", "payment_amount"} <= set(matches):
            score += 3
        if table == "traffic_sources" and {"source_name", "visitor_count"} <= set(matches):
            score += 3
        if table == "fund_flows" and {"flow_date", "amount"} <= set(matches):
            score += 3
        if table == "ad_spend" and {"stat_date", "spend_amount"} <= set(matches):
            score += 3
        if table == "audience_insights" and (
            {"product_name", "visitor_count"} <= set(matches)
            or {"segment_label", "visitor_count"} <= set(matches)
            or {"age_group", "conversion_rate"} <= set(matches)
        ):
            score += 2
        scores[table] = score

    best_table = max(scores, key=scores.get)
    if scores[best_table] < 2:
        return None, scores
    return best_table, scores


def _match_by_tokens(headers: Sequence[str], field: str, used_headers: set[str]) -> str | None:
    token_groups = HEURISTIC_TOKENS.get(field, ())
    for header in headers:
        if header in used_headers:
            continue
        normalized = normalize_header(header)
        for tokens in token_groups:
            if all(normalize_header(token) in normalized for token in tokens):
                return header
    return None
