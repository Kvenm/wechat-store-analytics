#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
ALLOWED_SUFFIXES = {".csv", ".xlsx", ".xls", ".zip", ".json", ".png"}


def main() -> int:
    args = parse_args()
    collector_id = args.collector_id or socket.gethostname()
    while True:
        post_json(
            args.server,
            "/collector/heartbeat",
            {"collector_id": collector_id, "status": "online"},
            args.admin_user,
            args.admin_password,
        )
        job = post_json(
            args.server,
            "/collector/jobs/claim",
            {"collector_id": collector_id},
            args.admin_user,
            args.admin_password,
        )
        if not job:
            print("no pending collector job")
            if args.once:
                return 0
            time.sleep(args.poll_interval)
            continue
        run_job(args.server, collector_id, job, args.admin_user, args.admin_password)
        if args.once:
            return 0


def run_job(
    server: str,
    collector_id: str,
    job: dict[str, Any],
    admin_user: str = "",
    admin_password: str = "",
) -> None:
    started_at = time.time()
    try:
        command = build_collect_command(job)
        print("running:", " ".join(command))
        subprocess.run(command, cwd=PROJECT_ROOT, check=True)
        source_dir = find_metadata_dir(job, started_at)
        post_json(
            server,
            f"/collector/jobs/{job['job_id']}/complete",
            {
                "collector_id": collector_id,
                "status": "completed",
                "message": f"uploaded {source_dir.name}",
                "files": build_upload_files(source_dir),
            },
            admin_user,
            admin_password,
        )
        print("uploaded:", source_dir)
    except Exception as exc:
        post_json(
            server,
            f"/collector/jobs/{job['job_id']}/complete",
            {"collector_id": collector_id, "status": "failed", "message": str(exc), "files": []},
            admin_user,
            admin_password,
        )
        raise


def find_metadata_dir(job: dict[str, Any], started_at: float) -> Path:
    candidates: list[tuple[float, Path]] = []
    for metadata_path in RAW_DIR.glob("collect_*/task-metadata.json"):
        stat = metadata_path.stat()
        if stat.st_mtime < started_at - 5:
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        date_range = metadata.get("date_range") or {}
        if (
            metadata.get("shop_id") == job["shop_id"]
            and date_range.get("from") == job["date_from"]
            and date_range.get("to") == job["date_to"]
        ):
            candidates.append((stat.st_mtime, metadata_path.parent))
    if not candidates:
        raise RuntimeError("collector finished but task-metadata.json was not found")
    return sorted(candidates, reverse=True)[0][1]


def build_collect_command(job: dict[str, Any]) -> list[str]:
    types = job.get("types") or ["orders"]
    normalized_types = [str(item).strip() for item in types if str(item).strip()]
    if set(normalized_types or ["orders"]) <= {"orders"}:
        command = [
            "node",
            "scripts/collect/scrape-orders-page.mjs",
            "--shop-id",
            job["shop_id"],
            "--shop-name",
            job.get("shop_name") or job["shop_id"],
            "--from",
            job["date_from"],
            "--to",
            job["date_to"],
            "--types",
            "orders",
            "--headless",
            "true" if job.get("headless") else "false",
        ]
        return command

    return [
        "node",
        "scripts/collect/collect.mjs",
        "--shop-id",
        job["shop_id"],
        "--from",
        job["date_from"],
        "--to",
        job["date_to"],
        "--types",
        ",".join(normalized_types),
        "--headless",
        "true" if job.get("headless") else "false",
    ]


def build_upload_files(source_dir: Path) -> list[dict[str, str]]:
    files = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        files.append(
            {
                "path": str(path.relative_to(source_dir)),
                "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        )
    if not files:
        raise RuntimeError(f"no uploadable files found in {source_dir}")
    return files


def post_json(
    server: str,
    path: str,
    payload: dict[str, Any],
    admin_user: str = "",
    admin_password: str = "",
) -> Any:
    url = server.rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    if admin_user or admin_password:
        credentials = base64.b64encode(f"{admin_user}:{admin_password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {credentials}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            wrapper = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{path} failed: HTTP {exc.code} {exc.read().decode('utf-8', 'ignore')}") from exc
    if wrapper.get("status") == "error":
        raise RuntimeError(wrapper.get("message") or f"{path} failed")
    return wrapper.get("data")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="微信小店本地采集助手")
    parser.add_argument("--server", default="http://127.0.0.1:8001", help="服务端地址")
    parser.add_argument("--admin-user", default=os.getenv("WECHAT_STORE_ADMIN_USER", ""), help="服务端 Basic Auth 用户名")
    parser.add_argument(
        "--admin-password",
        default=os.getenv("WECHAT_STORE_ADMIN_PASSWORD", ""),
        help="服务端 Basic Auth 密码（也可用 WECHAT_STORE_ADMIN_PASSWORD）",
    )
    parser.add_argument("--collector-id", help="本机采集器标识，默认使用主机名")
    parser.add_argument("--once", action="store_true", help="只领取并执行一个任务")
    parser.add_argument("--poll-interval", type=int, default=10, help="无任务时轮询间隔秒数")
    args = parser.parse_args(argv)
    if bool(args.admin_user) != bool(args.admin_password):
        parser.error("--admin-user 和 --admin-password 必须同时提供")
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
