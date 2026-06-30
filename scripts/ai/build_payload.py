#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ai_analysis.openai_client import OpenAIAnalysisClient
from ai_analysis.payloads import build_multi_shop_payload, build_shop_payload
from ai_analysis.reports import build_ai_report_record
from ai_analysis.schema import empty_report

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "processed" / "wechat_store.sqlite"


@dataclass(frozen=True)
class MetricSource:
    metrics: dict[str, Any]
    analysis_run_id: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建脱敏 AI 分析 payload，默认只干跑")
    parser.add_argument("--metrics-json", action="append", help="包含 metrics dict 的 JSON 文件，可重复传入")
    parser.add_argument("--analysis-run-id", action="append", help="从 SQLite analysis_runs.metrics_json 读取指标，可重复传入")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite 数据库路径")
    parser.add_argument("--multi-shop", action="store_true", help="按多店对比 payload 输出")
    parser.add_argument("--generate-report", action="store_true", help="输出 OpenAI dry-run 请求结构，不会真实调用 API")
    parser.add_argument("--top-n", type=int, default=20, help="商品/人群截断数量")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        sources = _load_sources(
            metrics_paths=args.metrics_json or [],
            analysis_run_ids=args.analysis_run_id or [],
            db_path=args.db_path,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        _print_error("load_metrics_failed", str(exc), analysis_run_ids=args.analysis_run_id or [])
        return 2

    if not sources:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "missing_metrics_source",
                        "message": "Pass at least one --metrics-json file or --analysis-run-id.",
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    metrics_rows = [source.metrics for source in sources]
    run_ids = [source.analysis_run_id for source in sources]
    analysis_run_ids = [run_id for run_id in run_ids if run_id]

    if args.multi_shop or len(metrics_rows) > 1:
        report_type = "multi_shop_comparison"
        payload = build_multi_shop_payload(metrics_rows, analysis_run_ids=run_ids, top_n=args.top_n)
        dry_run = OpenAIAnalysisClient().generate_multi_shop_report(payload) if args.generate_report else None
    else:
        report_type = "shop_analysis"
        payload = build_shop_payload(
            metrics_rows[0],
            analysis_run_id=run_ids[0],
            top_n=args.top_n,
        )
        dry_run = OpenAIAnalysisClient().generate_shop_report(payload) if args.generate_report else None

    result = (
        _build_generate_report_output(
            report_type=report_type,
            analysis_run_ids=analysis_run_ids,
            payload=payload,
            dry_run=dry_run,
        )
        if args.generate_report
        else payload
    )

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _load_sources(
    *,
    metrics_paths: list[str],
    analysis_run_ids: list[str],
    db_path: str,
) -> list[MetricSource]:
    sources = [MetricSource(_load_metrics(path), None) for path in metrics_paths]
    sources.extend(_load_analysis_runs(db_path, analysis_run_ids))
    return sources


def _load_metrics(path_text: str) -> dict:
    with Path(path_text).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return _metrics_from_json(data, source_label=path_text)


def _load_analysis_runs(db_path_text: str, analysis_run_ids: list[str]) -> list[MetricSource]:
    if not analysis_run_ids:
        return []

    db_path = Path(db_path_text)
    if not db_path.exists():
        raise ValueError("SQLite database not found for --analysis-run-id lookup")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [_load_analysis_run(conn, analysis_run_id) for analysis_run_id in analysis_run_ids]
    finally:
        conn.close()


def _load_analysis_run(conn: sqlite3.Connection, analysis_run_id: str) -> MetricSource:
    try:
        row = conn.execute(
            """
            SELECT id, shop_id, date_from, date_to, metrics_json
            FROM analysis_runs
            WHERE id = ?
            """,
            (analysis_run_id,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise ValueError("analysis_runs table is not available in the SQLite database") from exc

    if row is None:
        raise ValueError(f"analysis_run_id not found: {analysis_run_id}")

    try:
        metrics_data = json.loads(row["metrics_json"])
    except json.JSONDecodeError as exc:
        raise ValueError(f"analysis_run_id has invalid metrics_json: {analysis_run_id}") from exc

    metrics = _metrics_from_json(metrics_data, source_label=f"analysis_run_id:{analysis_run_id}")
    metrics.setdefault("shop_id", row["shop_id"])
    metrics.setdefault("date_from", row["date_from"])
    metrics.setdefault("date_to", row["date_to"])
    return MetricSource(metrics=metrics, analysis_run_id=row["id"])


def _metrics_from_json(data: Any, *, source_label: str) -> dict[str, Any]:
    if isinstance(data, dict) and isinstance(data.get("metrics"), dict):
        return data["metrics"]
    if isinstance(data, dict):
        return data
    raise ValueError(f"{source_label} must contain a metrics object")


def _build_generate_report_output(
    *,
    report_type: str,
    analysis_run_ids: list[str],
    payload: dict[str, Any],
    dry_run: dict[str, Any] | None,
) -> dict[str, Any]:
    report = empty_report()
    return {
        "report_type": report_type,
        "analysis_run_ids": analysis_run_ids,
        "payload": payload,
        "report": report,
        "dry_run": dry_run,
        "ai_report_record": build_ai_report_record(
            report,
            report_type=report_type,
            analysis_run_ids=analysis_run_ids,
            payload=payload,
            dry_run=True,
        ),
    }


def _print_error(code: str, message: str, *, analysis_run_ids: list[str] | None = None) -> None:
    print(
        json.dumps(
            {
                "ok": False,
                "error": {
                    "code": code,
                    "message": message,
                },
                "analysis_run_ids": analysis_run_ids or [],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
