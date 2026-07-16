#!/usr/bin/env python3
from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "scripts" / "collector" / "local_client.py"
SPEC = importlib.util.spec_from_file_location("local_collector_client", MODULE_PATH)
assert SPEC and SPEC.loader
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)


def main() -> int:
    test_basic_auth_is_sent_without_printing_password()
    test_auth_defaults_to_environment()
    test_find_metadata_dir_matches_shop_and_dates()
    print("3 local collector client regression tests passed.")
    return 0


def test_basic_auth_is_sent_without_printing_password() -> None:
    password = "do-not-print-this-password"
    captured_request = None

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b'{"status":"ok","data":{"claimed":true}}'

    def fake_urlopen(request, timeout):
        nonlocal captured_request
        captured_request = request
        assert timeout == 60
        return Response()

    original_urlopen = client.urllib.request.urlopen
    client.urllib.request.urlopen = fake_urlopen
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = client.post_json("https://example.test", "/collector/jobs/claim", {}, "admin", password)
    finally:
        client.urllib.request.urlopen = original_urlopen

    assert result == {"claimed": True}
    expected = base64.b64encode(f"admin:{password}".encode()).decode()
    assert captured_request.get_header("Authorization") == f"Basic {expected}"
    assert password not in output.getvalue()


def test_auth_defaults_to_environment() -> None:
    original_user = os.environ.get("WECHAT_STORE_ADMIN_USER")
    original_password = os.environ.get("WECHAT_STORE_ADMIN_PASSWORD")
    os.environ["WECHAT_STORE_ADMIN_USER"] = "env-admin"
    os.environ["WECHAT_STORE_ADMIN_PASSWORD"] = "env-password"
    try:
        args = client.parse_args(["--once"])
        cli_args = client.parse_args(["--admin-user", "cli-admin", "--admin-password", "cli-password"])
    finally:
        if original_user is None:
            os.environ.pop("WECHAT_STORE_ADMIN_USER", None)
        else:
            os.environ["WECHAT_STORE_ADMIN_USER"] = original_user
        if original_password is None:
            os.environ.pop("WECHAT_STORE_ADMIN_PASSWORD", None)
        else:
            os.environ["WECHAT_STORE_ADMIN_PASSWORD"] = original_password

    assert args.admin_user == "env-admin"
    assert args.admin_password == "env-password"
    assert cli_args.admin_user == "cli-admin"
    assert cli_args.admin_password == "cli-password"


def test_find_metadata_dir_matches_shop_and_dates() -> None:
    with tempfile.TemporaryDirectory(prefix="local_collector_metadata_") as temp_dir:
        raw_dir = Path(temp_dir)
        job = {
            "shop_id": "shop-correct",
            "date_from": "2026-07-01",
            "date_to": "2026-07-10",
        }

        def write_metadata(name: str, shop_id: str) -> Path:
            task_dir = raw_dir / name
            task_dir.mkdir()
            (task_dir / "task-metadata.json").write_text(
                json.dumps(
                    {
                        "shop_id": shop_id,
                        "date_range": {"from": job["date_from"], "to": job["date_to"]},
                    }
                ),
                encoding="utf-8",
            )
            return task_dir

        correct = write_metadata("collect_correct", "shop-correct")
        wrong = write_metadata("collect_wrong_newer", "shop-other")
        os.utime(correct / "task-metadata.json", (100, 100))
        os.utime(wrong / "task-metadata.json", (200, 200))

        original_raw_dir = client.RAW_DIR
        client.RAW_DIR = raw_dir
        try:
            assert client.find_metadata_dir(job, started_at=0) == correct
        finally:
            client.RAW_DIR = original_raw_dir


if __name__ == "__main__":
    raise SystemExit(main())
