from __future__ import annotations

import sqlite3
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from shared.paths import DEFAULT_DB_PATH, DEFAULT_REPORTS_DIR, DEFAULT_STANDARD_DIR, PROJECT_ROOT


SHOP_OVERVIEW_TABLES = (
    "shops",
    "orders",
    "products",
    "product_skus",
    "order_items",
    "refunds",
    "reviews",
    "shop_daily",
    "product_daily",
    "traffic_sources",
    "fund_flows",
    "ad_spend",
    "audience_insights",
    "ai_reports",
    "collection_tasks",
    "collection_task_items",
    "api_task_runs",
    "analysis_runs",
    "strategy_reports",
)

TASK_ITEM_STATUS_KEYS = ("completed", "failed", "no_permission", "skipped")
TASK_RECENT_ERROR_LIMIT = 5
SYNC_RUN_LIMIT_DEFAULT = 50
RAW_API_RESPONSE_LIMIT_DEFAULT = 100
SYNC_METADATA_LIMIT_MAX = 500
SENSITIVE_KEY_PARTS = ("token", "cookie", "authorization", "access_token", "secret", "session")
BEARER_VALUE_PATTERN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")


class LocalRepository:
    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        reports_dir: str | Path = DEFAULT_REPORTS_DIR,
        standard_dir: str | Path = DEFAULT_STANDARD_DIR,
    ) -> None:
        self.db_path = Path(db_path)
        self.reports_dir = Path(reports_dir)
        self.standard_dir = Path(standard_dir)

    def health(self) -> dict[str, Any]:
        return {
            "db_path": str(self.db_path),
            "db_exists": self.db_path.exists(),
            "reports_dir": str(self.reports_dir),
            "reports_dir_exists": self.reports_dir.exists(),
        }

    def list_shops(self) -> list[dict[str, Any]]:
        shops = self._fetch_all(
            """
            SELECT id, name, created_at, updated_at
            FROM shops
            ORDER BY updated_at DESC, created_at DESC
            """
        )
        return [self._with_shop_overview(shop) for shop in shops]

    def list_tasks(self) -> list[dict[str, Any]]:
        api_tasks = self.list_api_task_runs()
        tasks = self._fetch_all(
            """
            SELECT
                collection_task_id AS id,
                task_id,
                shop_id,
                shop_name_snapshot,
                task_name,
                status,
                source_type,
                started_at,
                finished_at,
                created_at,
                updated_at
            FROM collection_tasks
            ORDER BY created_at DESC
            """
        )
        summaries = self._task_item_summaries()
        recent_errors = self._task_recent_errors()
        collection_tasks = [
            self._with_task_item_overview(task, summaries=summaries, recent_errors=recent_errors)
            for task in tasks
        ]
        return [*api_tasks, *collection_tasks]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        api_task = self.get_api_task_run(task_id)
        if api_task is not None:
            return api_task

        task = self._fetch_one(
            """
            SELECT
                collection_task_id AS id,
                task_id,
                shop_id,
                shop_name_snapshot,
                task_name,
                status,
                source_type,
                started_at,
                finished_at,
                created_at,
                updated_at
            FROM collection_tasks
            WHERE collection_task_id = ? OR task_id = ?
            ORDER BY created_at DESC
            """,
            (task_id, task_id),
        )
        if task is None:
            return None
        collection_task_id = task.get("id") or task.get("task_id")
        items = self._task_items(str(collection_task_id)) if collection_task_id else []
        task_with_summary = self._with_task_item_overview(
            task,
            summaries={str(collection_task_id): _summarize_task_items(items)} if collection_task_id else {},
            recent_errors={str(collection_task_id): _recent_errors_from_items(items)} if collection_task_id else {},
        )
        task_with_summary["items"] = items
        return task_with_summary

    def list_api_task_runs(self) -> list[dict[str, Any]]:
        rows = self._fetch_all(
            """
            SELECT
                task_id,
                shop_id,
                shop_name_snapshot,
                task_name,
                source_type,
                status,
                state,
                date_from,
                date_to,
                types_json,
                headless,
                thread_name,
                collection_task_id,
                source_dir,
                metadata_path,
                collector_status,
                started_at,
                completed_at,
                result_json,
                error_json,
                spec_json,
                created_at,
                updated_at
            FROM api_task_runs
            ORDER BY COALESCE(started_at, created_at) DESC, updated_at DESC
            """
        )
        return [_normalize_api_task_run(row) for row in rows]

    def get_api_task_run(self, task_id: str) -> dict[str, Any] | None:
        row = self._fetch_one(
            """
            SELECT
                task_id,
                shop_id,
                shop_name_snapshot,
                task_name,
                source_type,
                status,
                state,
                date_from,
                date_to,
                types_json,
                headless,
                thread_name,
                collection_task_id,
                source_dir,
                metadata_path,
                collector_status,
                started_at,
                completed_at,
                result_json,
                error_json,
                spec_json,
                created_at,
                updated_at
            FROM api_task_runs
            WHERE task_id = ? OR collection_task_id = ?
            ORDER BY
                CASE WHEN task_id = ? THEN 0 ELSE 1 END,
                COALESCE(started_at, created_at) DESC,
                updated_at DESC
            LIMIT 1
            """,
            (task_id, task_id, task_id),
        )
        if row is None:
            return None
        task = _normalize_api_task_run(row)
        task["steps"] = {
            step["step_key"]: _api_task_step_public(step)
            for step in self.list_api_task_run_steps(str(row.get("task_id")))
        }
        return task

    def get_api_task_run_download_record(self, task_id: str) -> dict[str, Any] | None:
        """Return the unredacted path fields used only by the authenticated download route."""
        row = self._fetch_one(
            """
            SELECT task_id, state, status, source_dir, metadata_path, result_json
            FROM api_task_runs
            WHERE task_id = ? OR collection_task_id = ?
            ORDER BY
                CASE WHEN task_id = ? THEN 0 ELSE 1 END,
                COALESCE(started_at, created_at) DESC,
                updated_at DESC
            LIMIT 1
            """,
            (task_id, task_id, task_id),
        )
        if row is None:
            return None
        return {
            "id": row.get("task_id"),
            "task_id": row.get("task_id"),
            "state": row.get("state"),
            "status": row.get("status"),
            "source_dir": row.get("source_dir"),
            "metadata_path": row.get("metadata_path"),
            "result": _parse_json_field(row.get("result_json")),
        }

    def list_api_task_run_steps(self, task_id: str) -> list[dict[str, Any]]:
        return self._fetch_all(
            """
            SELECT
                task_id,
                step_key,
                status,
                started_at,
                completed_at,
                command_json,
                exit_code,
                duration_ms,
                stdout_tail,
                stderr_tail,
                parsed_json,
                error_text,
                created_at,
                updated_at
            FROM api_task_run_steps
            WHERE task_id = ?
            ORDER BY
                CASE step_key
                    WHEN 'collect' THEN 1
                    WHEN 'import_metadata' THEN 2
                    WHEN 'import_files' THEN 3
                    WHEN 'analyze' THEN 4
                    WHEN 'report' THEN 5
                    ELSE 99
                END,
                created_at ASC
            """,
            (task_id,),
        )

    def list_reports(self) -> list[dict[str, Any]]:
        strategy_reports = self._fetch_all(
            """
            SELECT
                id,
                analysis_run_id,
                shop_id,
                markdown_path,
                summary_csv_path,
                product_csv_path,
                audience_csv_path,
                warnings_json,
                created_at
            FROM strategy_reports
            ORDER BY created_at DESC
            """
        )
        ai_reports = self._fetch_all(
            """
            SELECT
                report_id AS id,
                shop_id,
                shop_name_snapshot,
                task_id,
                report_type,
                report_title,
                report_date,
                content,
                metrics_json,
                warnings_json,
                source_file,
                created_at,
                updated_at
            FROM ai_reports
            ORDER BY created_at DESC
            """
        )
        filesystem_reports = self._list_report_files()
        return [
            *[_normalize_strategy_report(row, include_content=False) for row in strategy_reports],
            *[_normalize_ai_report(row, include_content=True) for row in ai_reports],
            *[_normalize_filesystem_report(row) for row in filesystem_reports],
        ]

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        strategy_report = self._fetch_one(
            """
            SELECT
                id,
                analysis_run_id,
                shop_id,
                markdown_path,
                summary_csv_path,
                product_csv_path,
                audience_csv_path,
                warnings_json,
                created_at
            FROM strategy_reports
            WHERE id = ?
            """,
            (report_id,),
        )
        if strategy_report:
            return _normalize_strategy_report(strategy_report, include_content=True)

        ai_report = self._fetch_one(
            """
            SELECT
                report_id AS id,
                shop_id,
                shop_name_snapshot,
                task_id,
                report_type,
                report_title,
                report_date,
                content,
                metrics_json,
                warnings_json,
                source_file,
                created_at,
                updated_at
            FROM ai_reports
            WHERE report_id = ?
            """,
            (report_id,),
        )
        if ai_report:
            return _normalize_ai_report(ai_report, include_content=True)

        report_file = self._find_report_file(report_id)
        if report_file is None:
            return None
        return _normalize_filesystem_report(report_file, include_content=True)

    def list_exports(self) -> list[dict[str, Any]]:
        exports: list[dict[str, Any]] = []
        exports.extend(self._list_files(self.reports_dir, ("*.csv", "*.md"), "reports"))
        exports.extend(self._list_files(self.standard_dir, ("*.csv",), "standard_tables"))
        return sorted(exports, key=lambda item: item["modified_at"], reverse=True)

    def list_sync_runs(self, limit: int = SYNC_RUN_LIMIT_DEFAULT, shop_id: str | None = None) -> list[dict[str, Any]]:
        conditions: list[str] = []
        params: list[Any] = []
        if shop_id:
            conditions.append("shop_id = ?")
            params.append(shop_id)
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._fetch_all(
            f"""
            SELECT
                sync_run_id,
                shop_id,
                shop_name_snapshot,
                source_kind,
                connector,
                status,
                date_from,
                date_to,
                started_at,
                finished_at,
                params_json,
                summary_json,
                warnings_json,
                created_at,
                updated_at
            FROM sync_runs
            {where_sql}
            ORDER BY COALESCE(started_at, created_at) DESC, updated_at DESC, sync_run_id DESC
            LIMIT ?
            """,
            (*params, _clamp_limit(limit, default=SYNC_RUN_LIMIT_DEFAULT, maximum=SYNC_METADATA_LIMIT_MAX)),
        )
        counts = self._sync_run_index_counts([str(row["sync_run_id"]) for row in rows if row.get("sync_run_id")])
        return [
            _normalize_sync_run(
                row,
                item_count=counts.get(str(row.get("sync_run_id")), {}).get("item_count", 0),
                raw_response_count=counts.get(str(row.get("sync_run_id")), {}).get("raw_response_count", 0),
            )
            for row in rows
        ]

    def get_sync_run(self, sync_run_id: str) -> dict[str, Any] | None:
        row = self._fetch_one(
            """
            SELECT
                sync_run_id,
                shop_id,
                shop_name_snapshot,
                source_kind,
                connector,
                status,
                date_from,
                date_to,
                started_at,
                finished_at,
                params_json,
                summary_json,
                warnings_json,
                created_at,
                updated_at
            FROM sync_runs
            WHERE sync_run_id = ?
            ORDER BY COALESCE(started_at, created_at) DESC, updated_at DESC
            """,
            (sync_run_id,),
        )
        if row is None:
            return None
        counts = self._sync_run_index_counts([sync_run_id])
        run_counts = counts.get(sync_run_id, {})
        run = _normalize_sync_run(
            row,
            item_count=run_counts.get("item_count", 0),
            raw_response_count=run_counts.get("raw_response_count", 0),
        )
        run["items"] = self.list_sync_run_items(sync_run_id)
        run["raw_responses"] = self.list_raw_api_responses(sync_run_id=sync_run_id)
        return run

    def list_sync_run_items(self, sync_run_id: str) -> list[dict[str, Any]]:
        rows = self._fetch_all(
            """
            SELECT
                sync_item_id,
                sync_run_id,
                shop_id,
                source_kind,
                endpoint,
                export_type,
                table_hint,
                status,
                cursor,
                page_number,
                request_id,
                rid,
                http_status,
                error_code,
                error_message,
                raw_response_id,
                row_count,
                started_at,
                finished_at,
                raw_json,
                created_at,
                updated_at
            FROM sync_run_items
            WHERE sync_run_id = ?
            ORDER BY COALESCE(page_number, 0) ASC, endpoint ASC, sync_item_id ASC
            """,
            (sync_run_id,),
        )
        return [_normalize_sync_run_item(row) for row in rows]

    def list_raw_api_responses(
        self,
        sync_run_id: str | None = None,
        sync_item_id: str | None = None,
        shop_id: str | None = None,
        limit: int = RAW_API_RESPONSE_LIMIT_DEFAULT,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        params: list[Any] = []
        if sync_run_id:
            conditions.append("sync_run_id = ?")
            params.append(sync_run_id)
        if sync_item_id:
            conditions.append("sync_item_id = ?")
            params.append(sync_item_id)
        if shop_id:
            conditions.append("shop_id = ?")
            params.append(shop_id)
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._fetch_all(
            f"""
            SELECT
                raw_response_id,
                sync_run_id,
                sync_item_id,
                shop_id,
                source_kind,
                endpoint,
                request_id,
                rid,
                status,
                storage_path,
                sha256,
                size_bytes,
                record_count,
                schema_version,
                pulled_at,
                raw_json,
                created_at,
                updated_at
            FROM raw_api_responses
            {where_sql}
            ORDER BY COALESCE(pulled_at, created_at) DESC, endpoint ASC, raw_response_id ASC
            LIMIT ?
            """,
            (*params, _clamp_limit(limit, default=RAW_API_RESPONSE_LIMIT_DEFAULT, maximum=SYNC_METADATA_LIMIT_MAX)),
        )
        return [_normalize_raw_api_response(row) for row in rows]

    def _with_shop_overview(self, shop: dict[str, Any]) -> dict[str, Any]:
        shop_id = shop.get("id")
        return {
            **shop,
            "data_overview": {
                "table_counts": self._shop_table_counts(str(shop_id)) if shop_id else _empty_table_counts(),
                "last_task_at": self._shop_last_task_at(str(shop_id)) if shop_id else None,
                "last_report_at": self._shop_last_report_at(str(shop_id)) if shop_id else None,
            },
        }

    def _shop_table_counts(self, shop_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in SHOP_OVERVIEW_TABLES:
            try:
                if table == "shops":
                    row = self._fetch_one("SELECT COUNT(*) AS count FROM shops WHERE id = ?", (shop_id,))
                else:
                    row = self._fetch_one(
                        f"SELECT COUNT(*) AS count FROM {table} WHERE shop_id = ?",
                        (shop_id,),
                    )
            except sqlite3.OperationalError as exc:
                if _is_missing_table(exc) or _is_missing_column(exc):
                    row = None
                else:
                    raise
            counts[table] = int(row["count"]) if row else 0
        return counts

    def _shop_last_task_at(self, shop_id: str) -> str | None:
        row = self._fetch_one(
            """
            SELECT MAX(COALESCE(finished_at, started_at, updated_at, created_at)) AS last_at
            FROM collection_tasks
            WHERE shop_id = ?
            """,
            (shop_id,),
        )
        return row.get("last_at") if row else None

    def _shop_last_report_at(self, shop_id: str) -> str | None:
        ai_row = self._fetch_one(
            """
            SELECT MAX(COALESCE(updated_at, created_at, report_date)) AS last_at
            FROM ai_reports
            WHERE shop_id = ?
            """,
            (shop_id,),
        )
        strategy_row = self._fetch_one(
            """
            SELECT MAX(created_at) AS last_at
            FROM strategy_reports
            WHERE shop_id = ?
            """,
            (shop_id,),
        )
        return _latest_timestamp(
            ai_row.get("last_at") if ai_row else None,
            strategy_row.get("last_at") if strategy_row else None,
        )

    def _task_item_summaries(self) -> dict[str, dict[str, Any]]:
        rows = self._fetch_all(
            """
            SELECT
                collection_task_id,
                COUNT(*) AS total,
                SUM(CASE WHEN lower(replace(replace(trim(COALESCE(item_status, '')), '-', '_'), ' ', '_')) = 'completed' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN lower(replace(replace(trim(COALESCE(item_status, '')), '-', '_'), ' ', '_')) = 'failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN lower(replace(replace(trim(COALESCE(item_status, '')), '-', '_'), ' ', '_')) = 'no_permission' THEN 1 ELSE 0 END) AS no_permission,
                SUM(CASE WHEN lower(replace(replace(trim(COALESCE(item_status, '')), '-', '_'), ' ', '_')) = 'skipped' THEN 1 ELSE 0 END) AS skipped
            FROM collection_task_items
            GROUP BY collection_task_id
            """
        )
        summaries: dict[str, dict[str, Any]] = {}
        for row in rows:
            task_id = row.get("collection_task_id")
            if task_id is None:
                continue
            summary = _empty_task_item_summary()
            summary.update({key: int(row.get(key) or 0) for key in ("total", *TASK_ITEM_STATUS_KEYS)})
            summaries[str(task_id)] = summary
        return summaries

    def _task_recent_errors(self) -> dict[str, list[dict[str, Any]]]:
        rows = self._fetch_all(
            """
            SELECT
                collection_task_id,
                item_id,
                table_name,
                item_status,
                error_message,
                updated_at,
                created_at
            FROM collection_task_items
            WHERE error_message IS NOT NULL AND trim(error_message) != ''
            ORDER BY COALESCE(updated_at, created_at) DESC
            """
        )
        errors_by_task: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            task_id = row.get("collection_task_id")
            if task_id is None:
                continue
            task_errors = errors_by_task.setdefault(str(task_id), [])
            if len(task_errors) >= TASK_RECENT_ERROR_LIMIT:
                continue
            task_errors.append(_task_error_summary(row))
        return errors_by_task

    def _task_items(self, collection_task_id: str) -> list[dict[str, Any]]:
        return self._fetch_all(
            """
            SELECT
                item_id,
                shop_id,
                shop_name_snapshot,
                task_id,
                collection_task_id,
                table_name,
                item_status,
                source_file,
                source_sheet,
                source_row_number,
                error_message,
                created_at,
                updated_at
            FROM collection_task_items
            WHERE collection_task_id = ?
            ORDER BY created_at ASC, item_id ASC
            """,
            (collection_task_id,),
        )

    def _with_task_item_overview(
        self,
        task: dict[str, Any],
        *,
        summaries: dict[str, dict[str, Any]],
        recent_errors: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        collection_task_id = str(task.get("id") or task.get("task_id") or "")
        return {
            **task,
            "item_summary": summaries.get(collection_task_id, _empty_task_item_summary()),
            "recent_errors": recent_errors.get(collection_task_id, []),
        }

    def _sync_run_index_counts(self, sync_run_ids: list[str]) -> dict[str, dict[str, int]]:
        if not sync_run_ids:
            return {}
        placeholders = ", ".join(["?"] * len(sync_run_ids))
        counts = {
            sync_run_id: {"item_count": 0, "raw_response_count": 0}
            for sync_run_id in sync_run_ids
        }
        item_rows = self._fetch_all(
            f"""
            SELECT sync_run_id, COUNT(*) AS item_count
            FROM sync_run_items
            WHERE sync_run_id IN ({placeholders})
            GROUP BY sync_run_id
            """,
            tuple(sync_run_ids),
        )
        for row in item_rows:
            sync_run_id = str(row.get("sync_run_id"))
            counts.setdefault(sync_run_id, {"item_count": 0, "raw_response_count": 0})
            counts[sync_run_id]["item_count"] = int(row.get("item_count") or 0)

        raw_rows = self._fetch_all(
            f"""
            SELECT sync_run_id, COUNT(*) AS raw_response_count
            FROM raw_api_responses
            WHERE sync_run_id IN ({placeholders})
            GROUP BY sync_run_id
            """,
            tuple(sync_run_ids),
        )
        for row in raw_rows:
            sync_run_id = str(row.get("sync_run_id"))
            counts.setdefault(sync_run_id, {"item_count": 0, "raw_response_count": 0})
            counts[sync_run_id]["raw_response_count"] = int(row.get("raw_response_count") or 0)
        return counts

    def _connect(self) -> sqlite3.Connection | None:
        if not self.db_path.exists():
            return None
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        conn = self._connect()
        if conn is None:
            return []
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.OperationalError as exc:
            if _is_missing_table(exc):
                return []
            raise
        finally:
            conn.close()

    def _fetch_one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        rows = self._fetch_all(sql, params)
        return rows[0] if rows else None

    def _list_report_files(self) -> list[dict[str, Any]]:
        return self._list_files(self.reports_dir, ("*.md",), "reports")

    def _find_report_file(self, report_id: str) -> dict[str, Any] | None:
        for report in self._list_report_files():
            if report["id"] == report_id or report["name"] == report_id:
                return report
        return None

    def _list_files(self, directory: Path, patterns: tuple[str, ...], source: str) -> list[dict[str, Any]]:
        if not directory.exists():
            return []
        files: list[dict[str, Any]] = []
        for pattern in patterns:
            for path in directory.rglob(pattern):
                if not path.is_file():
                    continue
                stat = path.stat()
                files.append(
                    {
                        "id": path.stem,
                        "source": source,
                        "name": path.name,
                        "path": str(path),
                        "size_bytes": stat.st_size,
                        "modified_at": stat.st_mtime,
                    }
                )
        return files


def _is_missing_table(exc: sqlite3.OperationalError) -> bool:
    return "no such table" in str(exc).lower()


def _is_missing_column(exc: sqlite3.OperationalError) -> bool:
    return "no such column" in str(exc).lower()


def _empty_table_counts() -> dict[str, int]:
    return {table: 0 for table in SHOP_OVERVIEW_TABLES}


def _empty_task_item_summary() -> dict[str, int]:
    return {"total": 0, **{status: 0 for status in TASK_ITEM_STATUS_KEYS}}


def _summarize_task_items(items: list[dict[str, Any]]) -> dict[str, int]:
    summary = _empty_task_item_summary()
    summary["total"] = len(items)
    for item in items:
        status = _normalize_status(item.get("item_status"))
        if status in TASK_ITEM_STATUS_KEYS:
            summary[status] += 1
    return summary


def _recent_errors_from_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors = [_task_error_summary(item) for item in items if item.get("error_message")]
    return sorted(errors, key=lambda item: item.get("occurred_at") or "", reverse=True)[:TASK_RECENT_ERROR_LIMIT]


def _task_error_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": row.get("item_id"),
        "table_name": row.get("table_name"),
        "item_status": row.get("item_status"),
        "message": row.get("error_message"),
        "occurred_at": row.get("updated_at") or row.get("created_at"),
    }


def _normalize_status(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _normalize_api_task_run(row: dict[str, Any]) -> dict[str, Any]:
    task_id = row.get("task_id")
    interrupted = row.get("state") in ("queued", "running") or row.get("status") in ("queued", "running")
    error = (
        {
            "type": "TaskInterrupted",
            "message": "任务因服务重启或进程中断而未完成，请重新创建任务。",
        }
        if interrupted
        else _parse_public_json_field(row.get("error_json"))
    )
    spec = _parse_public_json_field(row.get("spec_json"))
    return _without_none(
        {
            "id": task_id,
            "task_id": task_id,
            "collection_task_id": row.get("collection_task_id"),
            "source": "api_task_runs",
            "state": "failed" if interrupted else row.get("state"),
            "status": "failed" if interrupted else row.get("status"),
            "shop_id": row.get("shop_id"),
            "shop_name_snapshot": _sanitize_public_text(row.get("shop_name_snapshot")),
            "task_name": _sanitize_public_text(row.get("task_name")),
            "source_type": row.get("source_type"),
            "mode": spec.get("mode") if isinstance(spec, dict) else None,
            "date_range": {
                "from": row.get("date_from"),
                "to": row.get("date_to"),
            },
            "types": _parse_public_json_field(row.get("types_json")) or [],
            "headless": bool(row.get("headless")) if row.get("headless") is not None else None,
            "thread_name": _sanitize_public_text(row.get("thread_name")),
            "source_dir": _sanitize_public_text(row.get("source_dir")),
            "metadata_path": _sanitize_public_text(row.get("metadata_path")),
            "collector_status": row.get("collector_status"),
            "created_at": row.get("created_at"),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "updated_at": row.get("updated_at"),
            "result": _parse_public_json_field(row.get("result_json")),
            "error": error,
        }
    )


def _api_task_step_public(row: dict[str, Any]) -> dict[str, Any]:
    return _without_none(
        {
            "status": row.get("status"),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "command": _parse_public_json_field(row.get("command_json")),
            "exit_code": row.get("exit_code"),
            "duration_ms": row.get("duration_ms"),
            "stdout_tail": _sanitize_public_text(row.get("stdout_tail")),
            "stderr_tail": _sanitize_public_text(row.get("stderr_tail")),
            "parsed": _parse_public_json_field(row.get("parsed_json")),
            "error": _sanitize_public_text(row.get("error_text")),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
    )


def _normalize_sync_run(row: dict[str, Any], *, item_count: int, raw_response_count: int) -> dict[str, Any]:
    return _without_none(
        {
            "sync_run_id": row.get("sync_run_id"),
            "shop_id": row.get("shop_id"),
            "shop_name_snapshot": _sanitize_public_text(row.get("shop_name_snapshot")),
            "source_kind": row.get("source_kind"),
            "connector": _sanitize_public_text(row.get("connector")),
            "status": row.get("status"),
            "date_from": row.get("date_from"),
            "date_to": row.get("date_to"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
            "params": _parse_public_json_field(row.get("params_json")),
            "summary": _parse_public_json_field(row.get("summary_json")),
            "warnings": _parse_public_json_field(row.get("warnings_json")),
            "item_count": int(item_count),
            "raw_response_count": int(raw_response_count),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
    )


def _normalize_sync_run_item(row: dict[str, Any]) -> dict[str, Any]:
    return _without_none(
        {
            "sync_item_id": row.get("sync_item_id"),
            "sync_run_id": row.get("sync_run_id"),
            "shop_id": row.get("shop_id"),
            "source_kind": row.get("source_kind"),
            "endpoint": _sanitize_public_text(row.get("endpoint")),
            "export_type": row.get("export_type"),
            "table_hint": row.get("table_hint"),
            "status": row.get("status"),
            "cursor": _sanitize_public_text(row.get("cursor")),
            "page_number": row.get("page_number"),
            "request_id": row.get("request_id"),
            "rid": row.get("rid"),
            "http_status": row.get("http_status"),
            "error_code": _sanitize_public_text(row.get("error_code")),
            "error_message": _sanitize_public_text(row.get("error_message")),
            "raw_response_id": row.get("raw_response_id"),
            "row_count": row.get("row_count"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
            "raw": _parse_public_json_field(row.get("raw_json")),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
    )


def _normalize_raw_api_response(row: dict[str, Any]) -> dict[str, Any]:
    return _without_none(
        {
            "raw_response_id": row.get("raw_response_id"),
            "sync_run_id": row.get("sync_run_id"),
            "sync_item_id": row.get("sync_item_id"),
            "shop_id": row.get("shop_id"),
            "source_kind": row.get("source_kind"),
            "endpoint": _sanitize_public_text(row.get("endpoint")),
            "request_id": row.get("request_id"),
            "rid": row.get("rid"),
            "status": row.get("status"),
            "storage_path": _sanitize_public_text(row.get("storage_path")),
            "sha256": row.get("sha256"),
            "size_bytes": row.get("size_bytes"),
            "record_count": row.get("record_count"),
            "schema_version": row.get("schema_version"),
            "pulled_at": row.get("pulled_at"),
            "metadata": _parse_public_json_field(row.get("raw_json")),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
    )


def _normalize_strategy_report(row: dict[str, Any], *, include_content: bool) -> dict[str, Any]:
    markdown_path = row.get("markdown_path")
    content = _read_text_file(markdown_path) if include_content else None
    title = _report_title_from_content(content) if content else None
    return _without_none(
        {
            "id": row.get("id"),
            "source": "strategy_reports",
            "report_type": "strategy",
            "title": title or "微信小店运营分析报告",
            "date": _date_from_timestamp(row.get("created_at")),
            "content": content,
            "path": markdown_path,
            "paths": {
                "markdown": markdown_path,
                "summary_csv": row.get("summary_csv_path"),
                "product_csv": row.get("product_csv_path"),
                "audience_csv": row.get("audience_csv_path"),
            },
            "analysis_run_id": row.get("analysis_run_id"),
            "shop_id": row.get("shop_id"),
            "warnings": _parse_json_field(row.get("warnings_json")),
            "created_at": row.get("created_at"),
        }
    )


def _normalize_ai_report(row: dict[str, Any], *, include_content: bool) -> dict[str, Any]:
    source_file = row.get("source_file")
    content = row.get("content") if include_content else None
    return _without_none(
        {
            "id": row.get("id"),
            "source": "ai_reports",
            "report_type": row.get("report_type"),
            "title": row.get("report_title"),
            "date": row.get("report_date") or _date_from_timestamp(row.get("created_at")),
            "content": content,
            "path": source_file,
            "paths": {"source_file": source_file} if source_file else {},
            "shop_id": row.get("shop_id"),
            "shop_name_snapshot": row.get("shop_name_snapshot"),
            "task_id": row.get("task_id"),
            "metrics": _parse_json_field(row.get("metrics_json")),
            "warnings": _parse_json_field(row.get("warnings_json")),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
    )


def _normalize_filesystem_report(row: dict[str, Any], *, include_content: bool = False) -> dict[str, Any]:
    path = row.get("path")
    content = _read_text_file(path) if include_content else None
    title = _report_title_from_content(content) if content else None
    return _without_none(
        {
            "id": row.get("id"),
            "source": row.get("source"),
            "report_type": "markdown",
            "title": title or row.get("name"),
            "date": _date_from_timestamp(row.get("modified_at")),
            "content": content,
            "path": path,
            "paths": {"markdown": path} if path else {},
            "name": row.get("name"),
            "size_bytes": row.get("size_bytes"),
            "modified_at": row.get("modified_at"),
        }
    )


def _parse_json_field(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return {"parse_error": "invalid_json", "value": value}


def _parse_public_json_field(value: Any) -> Any:
    return _sanitize_public_value(_parse_json_field(value))


def _sanitize_public_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_public_key(key_text):
                continue
            redacted[key_text] = _sanitize_public_value(item)
        return redacted
    if isinstance(value, list):
        return [_sanitize_public_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_public_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_public_text(value)
    return value


def _sanitize_public_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    text = BEARER_VALUE_PATTERN.sub("[REDACTED]", text)
    for part in SENSITIVE_KEY_PARTS:
        text = re.sub(re.escape(part), "[REDACTED]", text, flags=re.IGNORECASE)
    return text


def _is_sensitive_public_key(key: str) -> bool:
    normalized = key.replace("-", "_").casefold()
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _clamp_limit(value: Any, *, default: int, maximum: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    if limit < 1:
        return default
    return min(limit, maximum)


def _read_text_file(path_value: Any) -> str | None:
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        resolved_path = path.resolve()
        project_root = PROJECT_ROOT.resolve()
        if project_root not in (resolved_path, *resolved_path.parents):
            return None
        if not resolved_path.is_file():
            return None
        return resolved_path.read_text(encoding="utf-8")
    except OSError:
        return None


def _report_title_from_content(content: str | None) -> str | None:
    if not content:
        return None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
    return None


def _date_from_timestamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value).date().isoformat()
    text = str(value)
    return text[:10] if len(text) >= 10 else text


def _latest_timestamp(*values: str | None) -> str | None:
    valid_values = [value for value in values if value]
    return max(valid_values) if valid_values else None


def _without_none(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}
