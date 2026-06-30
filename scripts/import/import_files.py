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

from ingestion.pipeline import build_import_batch, write_standard_tables
from shared.paths import DEFAULT_DB_PATH, DEFAULT_STANDARD_DIR
from warehouse.repository import connect, import_batch, initialize_database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入微信小店本地 Excel/CSV 导出文件")
    parser.add_argument("--source-dir", required=True, help="包含 xlsx/xls/csv 导出文件的目录")
    parser.add_argument("--shop-id", required=True, help="店铺 ID 或本地稳定标识")
    parser.add_argument("--shop-name", help="店铺名称快照，会写入支持 shop_name_snapshot 的标准表")
    parser.add_argument("--task-id", required=True, help="采集/导入任务 ID")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite 数据库路径")
    parser.add_argument("--field-map", help="可选字段映射 JSON 文件")
    parser.add_argument("--standard-dir", help="可选：输出标准 CSV 表目录")
    parser.add_argument(
        "--standard-only",
        action="store_true",
        help="只输出标准 CSV，不写入 SQLite",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch = build_import_batch(
        source_dir=args.source_dir,
        shop_id=args.shop_id,
        shop_name=args.shop_name,
        task_id=args.task_id,
        field_map_path=args.field_map,
    )

    db_counts = {}
    if not args.standard_only:
        with connect(args.db_path) as conn:
            initialize_database(conn)
            db_counts = import_batch(conn, batch)

    standard_paths = {}
    if args.standard_dir:
        standard_paths = write_standard_tables(batch, args.standard_dir)
    elif args.standard_only:
        standard_paths = write_standard_tables(batch, DEFAULT_STANDARD_DIR)

    summary = {
        "shop_id": args.shop_id,
        "shop_name": args.shop_name,
        "task_id": args.task_id,
        "source_dir": args.source_dir,
        "db_path": None if args.standard_only else args.db_path,
        "batch_counts": batch.counts(),
        "db_counts": db_counts,
        "standard_paths": standard_paths,
        "warnings": batch.warnings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
