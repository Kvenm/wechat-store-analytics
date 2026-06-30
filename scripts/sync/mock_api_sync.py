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

from shared.ids import new_id
from shared.paths import DEFAULT_DB_PATH
from sync.mock_wechat import run_mock_sync


DEFAULT_ARCHIVE_DIR = PROJECT_ROOT / "data" / "raw" / "api"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="离线 mock 微信小店 API 同步，不连接真实微信")
    parser.add_argument("--shop-id", required=True, help="店铺 ID 或本地稳定标识")
    parser.add_argument("--shop-name", help="店铺名称快照")
    parser.add_argument("--from", dest="date_from", help="同步开始日期，YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="同步结束日期，YYYY-MM-DD")
    parser.add_argument("--sync-run-id", help="可选：固定同步 run id，便于幂等回归")
    parser.add_argument("--endpoint", action="append", dest="endpoints", help="mock endpoint，可重复传入：products/orders/aftersale/funds")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite 数据库路径")
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR), help="raw API 响应归档目录")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sync_run_id = args.sync_run_id or new_id("sync")
    result = run_mock_sync(
        db_path=args.db_path,
        archive_dir=args.archive_dir,
        shop_id=args.shop_id,
        shop_name=args.shop_name,
        date_from=args.date_from,
        date_to=args.date_to,
        sync_run_id=sync_run_id,
        endpoints=args.endpoints,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

