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

from shared.paths import DEFAULT_DB_PATH
from warehouse.repository import connect, initialize_database, upsert_task_metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入 collector 生成的 task-metadata.json 到本地 SQLite")
    parser.add_argument("--metadata-path", required=True, help="collector 输出的 task-metadata.json 路径")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite 数据库路径")
    parser.add_argument("--shop-id", help="可选：覆盖 metadata 中的店铺 ID")
    parser.add_argument("--shop-name", help="可选：覆盖 metadata 中的店铺名称快照")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    metadata_path = Path(args.metadata_path)
    with metadata_path.open("r", encoding="utf-8") as file:
        metadata = json.load(file)

    with connect(args.db_path) as conn:
        initialize_database(conn)
        counts = upsert_task_metadata(
            conn,
            metadata,
            shop_id_override=args.shop_id,
            shop_name_override=args.shop_name,
            metadata_path=metadata_path,
        )

    summary = {
        "metadata_path": str(metadata_path),
        "db_path": args.db_path,
        "task_id": metadata.get("task_id"),
        "shop_id_override": args.shop_id,
        "shop_name_override": args.shop_name,
        "db_counts": counts,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
