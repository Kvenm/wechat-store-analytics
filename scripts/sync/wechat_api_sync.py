#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shared.ids import new_id
from shared.paths import DEFAULT_DB_PATH, DEFAULT_REPORTS_DIR
from sync.wechat_api import DEFAULT_API_BASE_URL, create_api_sync_report, run_wechat_api_sync


DEFAULT_ARCHIVE_DIR = PROJECT_ROOT / "data" / "raw" / "api"
ENV_LOCAL_PATH = PROJECT_ROOT / ".env.local"


def parse_args() -> argparse.Namespace:
    env = {**parse_env_file(ENV_LOCAL_PATH), **os.environ}
    parser = argparse.ArgumentParser(description="微信小店官方 API 同步，不依赖 Playwright 或后台导出文件")
    parser.add_argument("--shop-id", default=env.get("WECHAT_STORE_SHOP_ID"), help="店铺 ID 或本地稳定标识")
    parser.add_argument("--shop-name", default=env.get("WECHAT_STORE_SHOP_NAME"), help="店铺名称快照")
    parser.add_argument("--from", dest="date_from", help="同步开始日期，YYYY-MM-DD 或秒级时间戳")
    parser.add_argument("--to", dest="date_to", help="同步结束日期，YYYY-MM-DD 或秒级时间戳")
    parser.add_argument("--sync-run-id", help="可选：固定同步 run id，便于幂等回归")
    parser.add_argument("--endpoint", action="append", dest="endpoints", help="endpoint，可重复传入：products/orders/aftersale/funds/compass_shop/compass_product/compass_audience")
    parser.add_argument("--access-token", default=env.get("WECHAT_STORE_ACCESS_TOKEN"), help="微信 access_token 或服务商 authorizer_access_token")
    parser.add_argument("--api-base-url", default=env.get("WECHAT_STORE_API_BASE_URL") or DEFAULT_API_BASE_URL, help="微信 API base URL")
    parser.add_argument("--page-size", type=int, default=30, help="每页数量；商品接口官方上限为 30")
    parser.add_argument("--max-pages", type=int, default=20, help="每个 endpoint 最多翻页数")
    parser.add_argument("--timeout", type=int, default=20, help="单次 HTTP 超时秒数")
    parser.add_argument("--endpoint-params-json", default="{}", help="按 endpoint 合并请求体的 JSON，例如 {\"orders\":{\"status\":20}}")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite 数据库路径")
    parser.add_argument("--archive-dir", default=resolve_project_path(env.get("WECHAT_STORE_RAW_ARCHIVE_DIR") or DEFAULT_ARCHIVE_DIR), help="raw API 响应归档目录")
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR), help="报告输出目录")
    parser.add_argument("--skip-report", action="store_true", help="只同步入库，不生成分析报告")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.shop_id:
        raise SystemExit("--shop-id or WECHAT_STORE_SHOP_ID is required")
    if not args.access_token:
        raise SystemExit("--access-token or WECHAT_STORE_ACCESS_TOKEN is required")

    result = run_wechat_api_sync(
        db_path=args.db_path,
        archive_dir=args.archive_dir,
        shop_id=args.shop_id,
        shop_name=args.shop_name,
        date_from=args.date_from,
        date_to=args.date_to,
        sync_run_id=args.sync_run_id or new_id("sync"),
        access_token=args.access_token,
        api_base_url=args.api_base_url,
        endpoints=args.endpoints,
        page_size=args.page_size,
        max_pages=args.max_pages,
        timeout=args.timeout,
        endpoint_params=parse_json_mapping(args.endpoint_params_json),
    )
    if result.get("status") == "completed" and not args.skip_report:
        result.update(
            create_api_sync_report(
                db_path=args.db_path,
                reports_dir=args.reports_dir,
                shop_id=args.shop_id,
                date_from=args.date_from,
                date_to=args.date_to,
                sync_run_id=result["sync_run_id"],
            )
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") == "completed" else 1


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped.removeprefix("export ").lstrip()
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def parse_json_mapping(value: str) -> dict[str, dict[str, Any]]:
    parsed = json.loads(value or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("--endpoint-params-json must be a JSON object")
    return {
        str(key): dict(item)
        for key, item in parsed.items()
        if isinstance(item, dict)
    }


def resolve_project_path(value: Any) -> str:
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return str(path)
    return str(PROJECT_ROOT / path)


if __name__ == "__main__":
    raise SystemExit(main())
