#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from reporting.render import generate_report
from shared.paths import DEFAULT_DB_PATH, DEFAULT_REPORTS_DIR
from warehouse.repository import connect, initialize_database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="根据 analysis_run_id 生成 Markdown/CSV 报告")
    parser.add_argument("--analysis-run-id", required=True, help="scripts/analyze/analyze.py 输出的分析 Run ID")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite 数据库路径")
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR), help="报告输出目录")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with connect(args.db_path) as conn:
        initialize_database(conn)
        result = generate_report(
            conn,
            analysis_run_id=args.analysis_run_id,
            reports_dir=args.reports_dir,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
