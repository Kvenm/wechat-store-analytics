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

from analytics.metrics import calculate_metrics
from shared.paths import DEFAULT_DB_PATH
from warehouse.repository import connect, create_analysis_run, initialize_database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="计算微信小店本地分析指标")
    parser.add_argument("--shop-id", required=True, help="店铺 ID 或本地稳定标识")
    parser.add_argument("--from", dest="date_from", help="分析开始日期，YYYY-MM-DD，按订单创建时间筛选")
    parser.add_argument("--to", dest="date_to", help="分析结束日期，YYYY-MM-DD，按订单创建时间筛选")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite 数据库路径")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with connect(args.db_path) as conn:
        initialize_database(conn)
        metrics, warnings = calculate_metrics(
            conn,
            shop_id=args.shop_id,
            date_from=args.date_from,
            date_to=args.date_to,
        )
        analysis_run_id = create_analysis_run(
            conn,
            shop_id=args.shop_id,
            date_from=args.date_from,
            date_to=args.date_to,
            params={"shop_id": args.shop_id, "date_from": args.date_from, "date_to": args.date_to},
            metrics=metrics,
            warnings=warnings,
        )

    print(
        json.dumps(
            {
                "analysis_run_id": analysis_run_id,
                "metrics": metrics,
                "warnings": warnings,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
