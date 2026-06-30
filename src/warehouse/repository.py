from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from shared.ids import fingerprint, new_id, stable_json
from shared.paths import ensure_parent
from shared.table_catalog import table_for_export_type
from ingestion.models import STANDARD_TABLES
from warehouse.schema import INSERTABLE_COLUMNS, initialize_schema


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    ensure_parent(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database(conn: sqlite3.Connection) -> None:
    initialize_schema(conn)
    conn.commit()


def ensure_shop(conn: sqlite3.Connection, shop_id: str, name: str | None = None) -> None:
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO shops (id, name, raw_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = COALESCE(excluded.name, shops.name),
            updated_at = excluded.updated_at
        """,
        (shop_id, name, json.dumps({"source": "local_import"}, ensure_ascii=False), now, now),
    )


def upsert_records(conn: sqlite3.Connection, table: str, records: Iterable[dict[str, Any]]) -> int:
    rows = [dict(record) for record in records]
    if not rows:
        return 0
    columns = INSERTABLE_COLUMNS[table]
    now = _utc_now()
    for row in rows:
        row.setdefault("created_at", now)
        row["updated_at"] = now

    placeholders = ", ".join(["?"] * len(columns))
    column_sql = ", ".join(columns)
    update_columns = [column for column in columns if column not in {"row_fingerprint", "created_at"}]
    update_sql = ", ".join([f"{column} = excluded.{column}" for column in update_columns])
    sql = f"""
        INSERT INTO {table} ({column_sql})
        VALUES ({placeholders})
        ON CONFLICT(row_fingerprint) DO UPDATE SET {update_sql}
    """
    try:
        conn.executemany(sql, [[row.get(column) for column in columns] for row in rows])
    except sqlite3.OperationalError as exc:
        if "ON CONFLICT clause does not match" in str(exc):
            raise RuntimeError(
                f"{table} lacks a unique row_fingerprint constraint; "
                "initialize_schema could not add it, likely because the old table has duplicate fingerprints"
            ) from exc
        raise
    return len(rows)


def import_batch(conn: sqlite3.Connection, batch: Any) -> dict[str, int]:
    ensure_shop(conn, batch.shop_id, getattr(batch, "shop_name", None))
    counts = {table: upsert_records(conn, table, getattr(batch, table)) for table in STANDARD_TABLES}
    conn.commit()
    return counts


def upsert_sync_run(
    conn: sqlite3.Connection,
    run: Mapping[str, Any],
) -> int:
    shop_id = _required_text(run.get("shop_id"), "sync run missing shop_id")
    ensure_shop(conn, shop_id, _text(run.get("shop_name_snapshot")))
    row = {
        "sync_run_id": _required_text(run.get("sync_run_id"), "sync run missing sync_run_id"),
        "shop_id": shop_id,
        "shop_name_snapshot": _text(run.get("shop_name_snapshot")),
        "source_kind": _required_text(run.get("source_kind"), "sync run missing source_kind"),
        "connector": _required_text(run.get("connector"), "sync run missing connector"),
        "status": _required_text(run.get("status"), "sync run missing status"),
        "date_from": _text(run.get("date_from")),
        "date_to": _text(run.get("date_to")),
        "started_at": _text(run.get("started_at")),
        "finished_at": _text(run.get("finished_at")),
        "params_json": _stable_local_json(run.get("params") or run.get("params_json") or {}),
        "summary_json": _stable_local_json(run.get("summary") or run.get("summary_json") or {}),
        "warnings_json": _stable_local_json(run.get("warnings") or run.get("warnings_json") or []),
        "row_fingerprint": fingerprint("sync_runs", shop_id, run.get("sync_run_id")),
    }
    count = upsert_records(conn, "sync_runs", [row])
    conn.commit()
    return count


def upsert_sync_run_item(
    conn: sqlite3.Connection,
    item: Mapping[str, Any],
) -> int:
    shop_id = _required_text(item.get("shop_id"), "sync item missing shop_id")
    row = {
        "sync_item_id": _required_text(item.get("sync_item_id"), "sync item missing sync_item_id"),
        "sync_run_id": _required_text(item.get("sync_run_id"), "sync item missing sync_run_id"),
        "shop_id": shop_id,
        "source_kind": _required_text(item.get("source_kind"), "sync item missing source_kind"),
        "endpoint": _required_text(item.get("endpoint"), "sync item missing endpoint"),
        "export_type": _text(item.get("export_type")),
        "table_hint": _text(item.get("table_hint")),
        "status": _required_text(item.get("status"), "sync item missing status"),
        "cursor": _text(item.get("cursor")),
        "page_number": _int_or_none(item.get("page_number")),
        "request_id": _text(item.get("request_id")),
        "rid": _text(item.get("rid")),
        "http_status": _int_or_none(item.get("http_status")),
        "error_code": _text(item.get("error_code")),
        "error_message": _text(item.get("error_message")),
        "raw_response_id": _text(item.get("raw_response_id")),
        "row_count": _int_or_none(item.get("row_count")),
        "started_at": _text(item.get("started_at")),
        "finished_at": _text(item.get("finished_at")),
        "row_fingerprint": fingerprint("sync_run_items", shop_id, item.get("sync_run_id"), item.get("sync_item_id")),
        "raw_json": _stable_local_json(item.get("raw") or item),
    }
    count = upsert_records(conn, "sync_run_items", [row])
    conn.commit()
    return count


def upsert_raw_api_response(
    conn: sqlite3.Connection,
    raw_response: Mapping[str, Any],
) -> int:
    shop_id = _required_text(raw_response.get("shop_id"), "raw response missing shop_id")
    row = {
        "raw_response_id": _required_text(raw_response.get("raw_response_id"), "raw response missing raw_response_id"),
        "sync_run_id": _required_text(raw_response.get("sync_run_id"), "raw response missing sync_run_id"),
        "sync_item_id": _required_text(raw_response.get("sync_item_id"), "raw response missing sync_item_id"),
        "shop_id": shop_id,
        "source_kind": _required_text(raw_response.get("source_kind"), "raw response missing source_kind"),
        "endpoint": _required_text(raw_response.get("endpoint"), "raw response missing endpoint"),
        "request_id": _text(raw_response.get("request_id")),
        "rid": _text(raw_response.get("rid")),
        "status": _required_text(raw_response.get("status"), "raw response missing status"),
        "storage_path": _required_text(raw_response.get("storage_path"), "raw response missing storage_path"),
        "sha256": _text(raw_response.get("sha256")),
        "size_bytes": _int_or_none(raw_response.get("size_bytes")),
        "record_count": _int_or_none(raw_response.get("record_count")),
        "schema_version": _text(raw_response.get("schema_version")),
        "pulled_at": _text(raw_response.get("pulled_at")),
        "row_fingerprint": fingerprint("raw_api_responses", shop_id, raw_response.get("sync_run_id"), raw_response.get("raw_response_id")),
        "raw_json": _stable_local_json(raw_response.get("metadata") or raw_response),
    }
    count = upsert_records(conn, "raw_api_responses", [row])
    conn.commit()
    return count


def upsert_task_metadata(
    conn: sqlite3.Connection,
    metadata: Mapping[str, Any],
    *,
    shop_id_override: str | None = None,
    shop_name_override: str | None = None,
    metadata_path: str | Path | None = None,
) -> dict[str, int]:
    task_id = _text(metadata.get("task_id") or metadata.get("collection_task_id"))
    if not task_id:
        raise ValueError("task metadata missing required task_id")

    source_file = _metadata_source_file(metadata, metadata_path)
    local_metadata = dict(metadata)
    if source_file:
        local_metadata["metadata_path"] = source_file

    shop_snapshots = _shop_snapshots_from_metadata(
        metadata,
        shop_id_override=shop_id_override,
        shop_name_override=shop_name_override,
    )
    if not shop_snapshots:
        raise ValueError("task metadata missing shop_id; pass --shop-id to import it")

    for shop_id, shop_name in shop_snapshots:
        ensure_shop(conn, shop_id, shop_name)

    collection_task_id = task_id
    task_rows = [
        {
            "shop_id": shop_id,
            "shop_name_snapshot": shop_name,
            "task_id": task_id,
            "collection_task_id": collection_task_id,
            "task_name": _task_name(metadata),
            "status": _text(metadata.get("status")),
            "started_at": _text(metadata.get("started_at")),
            "finished_at": _text(metadata.get("finished_at") or metadata.get("completed_at")),
            "source_type": "collector_task_metadata",
            "source_file": source_file,
            "source_sheet": None,
            "source_row_number": None,
            "row_fingerprint": fingerprint("collection_tasks", shop_id, collection_task_id),
            "raw_json": _local_metadata_json(local_metadata),
        }
        for shop_id, shop_name in shop_snapshots
    ]

    task_counts = upsert_records(conn, "collection_tasks", task_rows)
    item_rows = _task_item_rows(
        metadata=metadata,
        task_id=task_id,
        collection_task_id=collection_task_id,
        source_file=source_file,
        shop_snapshots=dict(shop_snapshots),
        shop_id_override=shop_id_override,
        shop_name_override=shop_name_override,
    )
    for row in item_rows:
        ensure_shop(conn, row["shop_id"], row.get("shop_name_snapshot"))
    item_counts = upsert_records(conn, "collection_task_items", item_rows)

    conn.commit()
    return {
        "collection_tasks": task_counts,
        "collection_task_items": item_counts,
    }


def create_analysis_run(
    conn: sqlite3.Connection,
    shop_id: str,
    date_from: str | None,
    date_to: str | None,
    params: dict[str, Any],
    metrics: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> str:
    ensure_shop(conn, shop_id)
    run_id = new_id("ar")
    conn.execute(
        """
        INSERT INTO analysis_runs (
            id, shop_id, date_from, date_to, params_json, metrics_json, warnings_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            shop_id,
            date_from,
            date_to,
            json.dumps(params, ensure_ascii=False, default=str),
            json.dumps(metrics, ensure_ascii=False, default=str),
            json.dumps(warnings, ensure_ascii=False, default=str),
            _utc_now(),
        ),
    )
    conn.commit()
    return run_id


def upsert_api_task_run(conn: sqlite3.Connection, task: Mapping[str, Any]) -> int:
    shop_id = _required_text(task.get("shop_id"), "api task missing shop_id")
    task_id = _required_text(task.get("task_id") or task.get("id"), "api task missing task_id")
    ensure_shop(conn, shop_id, _text(task.get("shop_name_snapshot")))
    spec = task.get("spec") if isinstance(task.get("spec"), Mapping) else {}
    date_range = task.get("date_range") if isinstance(task.get("date_range"), Mapping) else {}
    row = {
        "task_id": task_id,
        "shop_id": shop_id,
        "shop_name_snapshot": _text(task.get("shop_name_snapshot")),
        "task_name": _text(task.get("task_name")),
        "source_type": _text(task.get("source_type")),
        "status": _text(task.get("status")),
        "state": _text(task.get("state")),
        "date_from": _text(date_range.get("from") or spec.get("from")),
        "date_to": _text(date_range.get("to") or spec.get("to")),
        "types_json": _stable_local_json(spec.get("types") or []),
        "headless": _int_or_none(spec.get("headless")),
        "thread_name": _text(task.get("thread_name")),
        "collection_task_id": _text(task.get("collection_task_id")),
        "source_dir": _text(task.get("source_dir")),
        "metadata_path": _text(task.get("metadata_path")),
        "collector_status": _text(task.get("collector_status")),
        "started_at": _text(task.get("started_at")),
        "completed_at": _text(task.get("completed_at")),
        "result_json": _stable_local_json(task.get("result") or {}),
        "error_json": _stable_local_json(task.get("error") or {}),
        "spec_json": _stable_local_json(spec),
        "row_fingerprint": fingerprint("api_task_runs", task_id),
    }
    count = upsert_records(conn, "api_task_runs", [row])
    conn.commit()
    return count


def upsert_api_task_step(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    step_key: str,
    step: Mapping[str, Any],
) -> int:
    row = {
        "task_id": task_id,
        "step_key": step_key,
        "status": _text(step.get("status")),
        "started_at": _text(step.get("started_at")),
        "completed_at": _text(step.get("completed_at")),
        "command_json": _stable_local_json(step.get("command") or []),
        "exit_code": _int_or_none(step.get("exit_code")),
        "duration_ms": _int_or_none(step.get("duration_ms")),
        "stdout_tail": _text(step.get("stdout_tail")),
        "stderr_tail": _text(step.get("stderr_tail")),
        "parsed_json": _stable_local_json(step.get("parsed") or {}),
        "error_text": _text(step.get("error")),
        "row_fingerprint": fingerprint("api_task_run_steps", task_id, step_key),
    }
    count = upsert_records(conn, "api_task_run_steps", [row])
    conn.commit()
    return count


def get_analysis_run(conn: sqlite3.Connection, analysis_run_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM analysis_runs WHERE id = ?",
        (analysis_run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"analysis_run_id not found: {analysis_run_id}")
    return dict(row)


def create_strategy_report(
    conn: sqlite3.Connection,
    analysis_run_id: str,
    shop_id: str,
    markdown_path: str,
    summary_csv_path: str,
    product_csv_path: str,
    audience_csv_path: str | None,
    warnings: list[dict[str, Any]],
) -> str:
    report_id = new_id("sr")
    conn.execute(
        """
        INSERT INTO strategy_reports (
            id, analysis_run_id, shop_id, markdown_path, summary_csv_path,
            product_csv_path, audience_csv_path, warnings_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report_id,
            analysis_run_id,
            shop_id,
            markdown_path,
            summary_csv_path,
            product_csv_path,
            audience_csv_path,
            json.dumps(warnings, ensure_ascii=False, default=str),
            _utc_now(),
        ),
    )
    conn.commit()
    return report_id


def fetch_rows(conn: sqlite3.Connection, table: str, shop_id: str) -> list[dict[str, Any]]:
    cursor = conn.execute(f"SELECT * FROM {table} WHERE shop_id = ?", (shop_id,))
    return [dict(row) for row in cursor.fetchall()]


def _utc_now() -> str:
    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")


def _task_item_rows(
    *,
    metadata: Mapping[str, Any],
    task_id: str,
    collection_task_id: str,
    source_file: str | None,
    shop_snapshots: dict[str, str | None],
    shop_id_override: str | None,
    shop_name_override: str | None,
) -> list[dict[str, Any]]:
    items = metadata.get("items")
    if not isinstance(items, list) or not items:
        items = _items_from_exports(metadata.get("exports"))

    rows: list[dict[str, Any]] = []
    for index, item_value in enumerate(items, start=1):
        if not isinstance(item_value, Mapping):
            item = {"value": item_value}
        else:
            item = item_value

        shop_id = _item_shop_id(item, metadata, shop_id_override)
        if not shop_id:
            continue

        item_source_file = _item_source_file(item) or source_file
        item_id = _text(item.get("item_id") or item.get("id"))
        if not item_id:
            item_id = fingerprint(
                "collection_task_item",
                task_id,
                shop_id,
                item.get("type"),
                item.get("label"),
                item_source_file,
                index,
            )

        rows.append(
            {
                "shop_id": shop_id,
                "shop_name_snapshot": _item_shop_name(
                    item,
                    metadata,
                    shop_snapshots,
                    shop_id,
                    shop_name_override,
                ),
                "task_id": task_id,
                "collection_task_id": collection_task_id,
                "item_id": item_id,
                "table_name": _item_table_name(item),
                "item_status": _text(item.get("item_status") or item.get("status")),
                "source_file": item_source_file,
                "source_sheet": None,
                "source_row_number": index,
                "error_message": _error_message(item.get("error")),
                "row_fingerprint": fingerprint("collection_task_items", shop_id, collection_task_id, item_id),
                "raw_json": _local_metadata_json(item),
            }
        )
    return rows


def _items_from_exports(exports: Any) -> list[Mapping[str, Any]]:
    if not isinstance(exports, list):
        return []
    items: list[Mapping[str, Any]] = []
    for export in exports:
        if not isinstance(export, Mapping):
            continue
        items.append(
            {
                "shop_id": export.get("shop_id"),
                "shop_name": export.get("shop_name"),
                "type": export.get("type"),
                "label": export.get("label"),
                "status": "completed",
                "export": export,
                "error": None,
            }
        )
    return items


def _shop_snapshots_from_metadata(
    metadata: Mapping[str, Any],
    *,
    shop_id_override: str | None,
    shop_name_override: str | None,
) -> list[tuple[str, str | None]]:
    if shop_id_override:
        return [(shop_id_override, shop_name_override or _metadata_shop_name(metadata))]

    snapshots: dict[str, str | None] = {}

    def remember(shop_id: Any, shop_name: Any = None) -> None:
        normalized_shop_id = _text(shop_id)
        if not normalized_shop_id:
            return
        normalized_shop_name = shop_name_override or _text(shop_name)
        if normalized_shop_id not in snapshots:
            snapshots[normalized_shop_id] = normalized_shop_name
        elif normalized_shop_name and not snapshots[normalized_shop_id]:
            snapshots[normalized_shop_id] = normalized_shop_name

    remember(metadata.get("shop_id"), _metadata_shop_name(metadata))
    _remember_shop_list(snapshots, metadata.get("shops"), shop_name_override)
    _remember_shop_list(snapshots, _nested(metadata.get("shop_selection"), "shops"), shop_name_override)

    for item in metadata.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        export = item.get("export") if isinstance(item.get("export"), Mapping) else {}
        remember(item.get("shop_id") or export.get("shop_id"), item.get("shop_name") or export.get("shop_name"))

    for export in metadata.get("exports") or []:
        if isinstance(export, Mapping):
            remember(export.get("shop_id"), export.get("shop_name"))

    return list(snapshots.items())


def _remember_shop_list(snapshots: dict[str, str | None], shops: Any, shop_name_override: str | None) -> None:
    if not isinstance(shops, list):
        return
    for shop in shops:
        if isinstance(shop, Mapping):
            shop_id = _text(shop.get("id") or shop.get("shop_id") or shop.get("wechatStoreId"))
            shop_name = shop_name_override or _text(shop.get("name") or shop.get("shop_name") or shop.get("switchLabel"))
        else:
            shop_id = _text(shop)
            shop_name = shop_name_override
        if not shop_id:
            continue
        if shop_id not in snapshots:
            snapshots[shop_id] = shop_name
        elif shop_name and not snapshots[shop_id]:
            snapshots[shop_id] = shop_name


def _item_shop_id(
    item: Mapping[str, Any],
    metadata: Mapping[str, Any],
    shop_id_override: str | None,
) -> str | None:
    if shop_id_override:
        return shop_id_override
    export = item.get("export") if isinstance(item.get("export"), Mapping) else {}
    return _text(item.get("shop_id") or export.get("shop_id") or metadata.get("shop_id"))


def _item_shop_name(
    item: Mapping[str, Any],
    metadata: Mapping[str, Any],
    shop_snapshots: dict[str, str | None],
    shop_id: str,
    shop_name_override: str | None,
) -> str | None:
    if shop_name_override:
        return shop_name_override
    export = item.get("export") if isinstance(item.get("export"), Mapping) else {}
    return (
        _text(item.get("shop_name"))
        or _text(export.get("shop_name"))
        or shop_snapshots.get(shop_id)
        or _metadata_shop_name(metadata)
    )


def _item_table_name(item: Mapping[str, Any]) -> str | None:
    table_name = _text(item.get("table_name"))
    if table_name:
        return table_name
    export = item.get("export") if isinstance(item.get("export"), Mapping) else {}
    export_type = _text(item.get("type") or export.get("type"))
    if not export_type:
        return None
    return table_for_export_type(export_type)


def _item_source_file(item: Mapping[str, Any]) -> str | None:
    export = item.get("export") if isinstance(item.get("export"), Mapping) else {}
    return _text(item.get("source_file") or export.get("saved_path") or export.get("source_file"))


def _metadata_source_file(metadata: Mapping[str, Any], metadata_path: str | Path | None) -> str | None:
    if metadata_path:
        return str(metadata_path)
    return _text(metadata.get("metadata_path"))


def _metadata_shop_name(metadata: Mapping[str, Any]) -> str | None:
    return _text(metadata.get("shop_name") or metadata.get("current_shop_name"))


def _task_name(metadata: Mapping[str, Any]) -> str | None:
    value = metadata.get("task_name")
    if value:
        return _text(value)
    date_range = metadata.get("date_range")
    if isinstance(date_range, Mapping):
        from_date = _text(date_range.get("from"))
        to_date = _text(date_range.get("to"))
        if from_date and to_date:
            return f"collector {from_date} to {to_date}"
    return "collector task"


def _error_message(error: Any) -> str | None:
    if error is None:
        return None
    if isinstance(error, Mapping):
        message = _text(error.get("message"))
        name = _text(error.get("name"))
        if name and message:
            return f"{name}: {message}"
        return message or name or stable_json(error)
    return _text(error)


def _nested(value: Any, key: str) -> Any:
    if not isinstance(value, Mapping):
        return None
    return value.get(key)


def _local_metadata_json(value: Any) -> str:
    return stable_json(value)


def _stable_local_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    return stable_json(value)


def _required_text(value: Any, message: str) -> str:
    text = _text(value)
    if not text:
        raise ValueError(message)
    return text


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
