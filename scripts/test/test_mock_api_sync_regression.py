#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import inspect
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SRC_DIR))

SHOP_ID = "mock-sync-shop"
SHOP_NAME = "Mock Sync Shop"
TASK_ID = "mock-api-sync-task-001"
DATE_FROM = "2026-06-01"
DATE_TO = "2026-06-07"
SENSITIVE_TERMS = ("token", "cookie", "authorization", "access_token", "secret", "session")
SENSITIVE_SENTINELS = {
    "token": "slice1-token-value-must-not-persist-7f3f719e",
    "cookie": "slice1-cookie-value-must-not-persist-6c0e5c02",
    "authorization": "Bearer slice1-authorization-value-must-not-persist-07e2dcb2",
    "access_token": "slice1-access-token-value-must-not-persist-c91aa3f1",
    "secret": "slice1-secret-value-must-not-persist-bb5814c8",
    "session": "slice1-session-value-must-not-persist-4e74bdf0",
}

PYTHON_ENTRYPOINTS = (
    "sync.mock_wechat:run_mock_sync",
    "sync.mock_connector:run_mock_sync",
    "sync.runner:run_mock_sync",
    "sync.runner:run_api_sync",
    "sync.runner:run_sync",
    "sync.api_sync:run_mock_sync",
    "sync.api_sync:run_api_sync",
    "sync.cli:run_mock_sync",
)

CLI_ENTRYPOINTS = (
    PROJECT_ROOT / "scripts" / "sync" / "mock_api_sync.py",
    PROJECT_ROOT / "scripts" / "sync" / "sync_api.py",
    PROJECT_ROOT / "scripts" / "sync" / "run_api_sync.py",
)

REQUIRED_TABLES = ("sync_runs", "sync_run_items", "raw_api_responses")
BUSINESS_TABLES = ("products", "product_skus", "orders", "order_items", "refunds", "fund_flows")

MOCK_RESPONSES = (
    {
        "endpoint": "/mock/orders",
        "request_id": "mock-req-orders-001",
        "rid": "mock-rid-orders-001",
        "records": [
            {"order_id": "order-001", "payment_amount": 120.5},
            {"order_id": "order-002", "payment_amount": 88.0},
        ],
    },
    {
        "endpoint": "/mock/products",
        "request_id": "mock-req-products-001",
        "rid": "mock-rid-products-001",
        "records": [
            {"product_id": "product-001", "product_name": "Mock Mug"},
        ],
    },
)


class NotReady(RuntimeError):
    pass


@dataclass(frozen=True)
class Entrypoint:
    label: str
    runner: Callable[["RunContext"], Any]


@dataclass(frozen=True)
class RunContext:
    db_path: Path
    raw_dir: Path
    task_id: str


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regression contract for Slice 1 mock API sync persistence.",
    )
    parser.add_argument(
        "--entrypoint",
        help=(
            "Optional override. Use module:function for Python or cli:/absolute/path.py "
            "for a CLI script."
        ),
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the temporary database and raw archive directory after the run.",
    )
    args = parser.parse_args()

    try:
        entrypoint = discover_entrypoint(args.entrypoint or os.environ.get("MOCK_API_SYNC_ENTRYPOINT"))
    except Exception:
        print("FAIL discover_entrypoint")
        traceback.print_exc()
        return 1

    if entrypoint is None:
        print("NOT_READY mock API sync entrypoint was not found.")
        print_contract()
        return 2

    temp_dir_context = tempfile.TemporaryDirectory(prefix="wechat_mock_api_sync_regression_")
    temp_dir = Path(temp_dir_context.name)
    try:
        context = RunContext(
            db_path=temp_dir / "wechat_store.sqlite",
            raw_dir=temp_dir / "raw-api",
            task_id=TASK_ID,
        )
        print(f"Using entrypoint: {entrypoint.label}")
        print(f"Temporary database: {context.db_path}")
        print(f"Temporary raw archive: {context.raw_dir}")

        run_once(entrypoint, context)
        assert_sync_records_created(context)
        assert_product_business_records_created(context)
        assert_transaction_business_records_created(context)
        assert_raw_archives_have_hash_and_size(context)
        assert_sensitive_data_not_persisted(context)
        first_snapshot = snapshot_state(context)

        run_once(entrypoint, context)
        assert_sync_records_created(context)
        assert_product_business_records_created(context)
        assert_transaction_business_records_created(context)
        assert_raw_archives_have_hash_and_size(context)
        assert_sensitive_data_not_persisted(context)
        second_snapshot = snapshot_state(context)
        assert first_snapshot == second_snapshot, (
            "mock API sync is not idempotent for the same task/item; "
            f"first={json.dumps(first_snapshot, ensure_ascii=False, sort_keys=True)}, "
            f"second={json.dumps(second_snapshot, ensure_ascii=False, sort_keys=True)}"
        )
    except NotReady as exc:
        print(f"NOT_READY {exc}")
        print_contract()
        return 2
    except Exception:
        print("FAIL mock API sync regression")
        traceback.print_exc()
        return 1
    finally:
        if args.keep_temp:
            print(f"Kept temporary directory: {temp_dir}")
        else:
            temp_dir_context.cleanup()

    print("PASS mock API sync regression")
    return 0


def discover_entrypoint(override: str | None) -> Entrypoint | None:
    if override:
        if override.startswith("cli:"):
            return cli_entrypoint(Path(override.removeprefix("cli:")).expanduser())
        return python_entrypoint(override)

    for path in CLI_ENTRYPOINTS:
        if path.exists():
            return cli_entrypoint(path)

    for spec in PYTHON_ENTRYPOINTS:
        try:
            return python_entrypoint(spec)
        except ModuleNotFoundError as exc:
            if not _missing_requested_module(spec, exc):
                raise
        except AttributeError:
            continue

    return None


def python_entrypoint(spec: str) -> Entrypoint:
    if ":" not in spec:
        raise ValueError(f"Python entrypoint must be module:function, got {spec!r}")
    module_name, function_name = spec.split(":", 1)
    module = importlib.import_module(module_name)
    function = getattr(module, function_name)
    if not callable(function):
        raise TypeError(f"{spec} is not callable")

    return Entrypoint(label=spec, runner=lambda context: call_python_function(function, context))


def cli_entrypoint(path: Path) -> Entrypoint:
    if not path.exists():
        raise NotReady(f"CLI entrypoint does not exist: {path}")
    return Entrypoint(label=f"cli:{path}", runner=lambda context: call_cli(path, context))


def call_python_function(function: Callable[..., Any], context: RunContext) -> Any:
    signature = inspect.signature(function)
    with sensitive_environment():
        if accepts_argv(signature):
            result = function(cli_args(context))
            return consume_result(result)

        kwargs = kwargs_for_signature(signature, context)
        result = function(**kwargs)
        return consume_result(result)


def accepts_argv(signature: inspect.Signature) -> bool:
    parameters = signature.parameters
    return "argv" in parameters and len(parameters) <= 2


def kwargs_for_signature(signature: inspect.Signature, context: RunContext) -> dict[str, Any]:
    base_kwargs = contract_kwargs(context)
    kwargs: dict[str, Any] = {}
    accepts_var_kwargs = False

    for name, parameter in signature.parameters.items():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            accepts_var_kwargs = True
            continue
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.POSITIONAL_ONLY):
            if parameter.default is inspect.Parameter.empty:
                raise NotReady(f"cannot call positional-only parameter {name!r}")
            continue
        if name in base_kwargs:
            kwargs[name] = base_kwargs[name]
            continue
        if name in {"config", "settings", "options", "request"}:
            kwargs[name] = contract_config(context)
            continue
        if parameter.default is inspect.Parameter.empty:
            raise NotReady(f"entrypoint has unsupported required parameter {name!r}")

    if accepts_var_kwargs:
        kwargs.update(base_kwargs)

    return kwargs


def contract_kwargs(context: RunContext) -> dict[str, Any]:
    config = contract_config(context)
    return {
        "db_path": str(context.db_path),
        "database_path": str(context.db_path),
        "warehouse_db_path": str(context.db_path),
        "raw_dir": str(context.raw_dir),
        "raw_root": str(context.raw_dir),
        "archive_dir": str(context.raw_dir),
        "shop_id": SHOP_ID,
        "shop_name": SHOP_NAME,
        "task_id": context.task_id,
        "sync_run_id": context.task_id,
        "idempotency_key": context.task_id,
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
        "source_kind": "api",
        "connector": "mock",
        "connector_name": "mock",
        "mode": "mock",
        "dry_run": False,
        "token": SENSITIVE_SENTINELS["token"],
        "cookie": SENSITIVE_SENTINELS["cookie"],
        "authorization": SENSITIVE_SENTINELS["authorization"],
        "access_token": SENSITIVE_SENTINELS["access_token"],
        "secret": SENSITIVE_SENTINELS["secret"],
        "session": SENSITIVE_SENTINELS["session"],
        "credentials": config["credentials"],
        "params": config["params"],
        "mock_responses": list(MOCK_RESPONSES),
    }


def contract_config(context: RunContext) -> dict[str, Any]:
    return {
        "db_path": str(context.db_path),
        "raw_dir": str(context.raw_dir),
        "shop_id": SHOP_ID,
        "shop_name": SHOP_NAME,
        "task_id": context.task_id,
        "sync_run_id": context.task_id,
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
        "source_kind": "api",
        "connector": "mock",
        "mode": "mock",
        "credentials": {
            "token": SENSITIVE_SENTINELS["token"],
            "cookie": SENSITIVE_SENTINELS["cookie"],
            "authorization": SENSITIVE_SENTINELS["authorization"],
            "access_token": SENSITIVE_SENTINELS["access_token"],
            "secret": SENSITIVE_SENTINELS["secret"],
            "session": SENSITIVE_SENTINELS["session"],
        },
        "params": {
            "date_from": DATE_FROM,
            "date_to": DATE_TO,
            "requested_by": "mock_api_sync_regression",
        },
        "mock_responses": list(MOCK_RESPONSES),
    }


def call_cli(path: Path, context: RunContext) -> None:
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH")
    pythonpath_parts = [str(SRC_DIR), str(PROJECT_ROOT)]
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    env.update(sensitive_env_vars())

    completed = subprocess.run(
        [sys.executable, str(path), *cli_args(context)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"CLI entrypoint failed with code {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )


def cli_args(context: RunContext) -> list[str]:
    return [
        "--db-path",
        str(context.db_path),
        "--archive-dir",
        str(context.raw_dir),
        "--shop-id",
        SHOP_ID,
        "--shop-name",
        SHOP_NAME,
        "--sync-run-id",
        context.task_id,
        "--from",
        DATE_FROM,
        "--to",
        DATE_TO,
    ]


def consume_result(result: Any) -> Any:
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def run_once(entrypoint: Entrypoint, context: RunContext) -> None:
    context.raw_dir.mkdir(parents=True, exist_ok=True)
    entrypoint.runner(context)
    if not context.db_path.exists():
        raise AssertionError(f"sync did not create database at {context.db_path}")


def assert_sync_records_created(context: RunContext) -> None:
    with connect(context.db_path) as conn:
        existing_tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing_tables = [table for table in REQUIRED_TABLES if table not in existing_tables]
        assert not missing_tables, f"missing sync tables: {missing_tables}"

        counts = {table: table_count(conn, table) for table in REQUIRED_TABLES}
        assert counts["sync_runs"] == 1, f"expected one idempotent sync run, got counts={counts}"
        assert counts["sync_run_items"] >= 1, f"expected sync run items, got counts={counts}"
        assert counts["raw_api_responses"] >= 1, f"expected raw API responses, got counts={counts}"

        linked_items = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM sync_run_items item
            JOIN sync_runs run ON run.sync_run_id = item.sync_run_id
            """
        ).fetchone()["count"]
        assert linked_items == counts["sync_run_items"], "some sync_run_items do not link to sync_runs"

        linked_raw = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM raw_api_responses raw
            JOIN sync_run_items item ON item.sync_item_id = raw.sync_item_id
            """
        ).fetchone()["count"]
        assert linked_raw == counts["raw_api_responses"], "some raw_api_responses do not link to sync_run_items"


def assert_product_business_records_created(context: RunContext) -> None:
    with connect(context.db_path) as conn:
        counts = {table: table_count(conn, table) for table in BUSINESS_TABLES}
        assert counts["products"] == 2, f"expected 2 products from mock product endpoint, got counts={counts}"
        assert counts["product_skus"] == 1, f"expected 1 product_skus row from mock product endpoint, got counts={counts}"

        product = conn.execute(
            """
            SELECT shop_id, shop_name_snapshot, task_id, product_id, product_name, price, stock, source_sheet
            FROM products
            WHERE product_id = ?
            """,
            ("mock-p001",),
        ).fetchone()
        assert product is not None, "mock-p001 was not persisted to products"
        assert product["shop_id"] == SHOP_ID
        assert product["shop_name_snapshot"] == SHOP_NAME
        assert product["task_id"] == context.task_id
        assert product["product_name"] == "Mock 连衣裙"
        assert product["price"] == 199.0
        assert product["stock"] == 20.0
        assert product["source_sheet"] == "/channels-shop-product/shop/getproductlist"

        sku = conn.execute(
            """
            SELECT shop_id, product_id, product_name, sku_id, sku_name, sku_price, stock, barcode
            FROM product_skus
            WHERE sku_id = ?
            """,
            ("mock-sku-p001-red-m",),
        ).fetchone()
        assert sku is not None, "mock SKU was not persisted to product_skus"
        assert sku["shop_id"] == SHOP_ID
        assert sku["product_id"] == "mock-p001"
        assert sku["product_name"] == "Mock 连衣裙"
        assert sku["sku_name"] == "红色 / M"
        assert sku["sku_price"] == 199.0
        assert sku["stock"] == 8.0
        assert sku["barcode"] == "690mock001"


def assert_transaction_business_records_created(context: RunContext) -> None:
    with connect(context.db_path) as conn:
        expected_counts = {
            "orders": 2,
            "order_items": 2,
            "refunds": 1,
            "fund_flows": 1,
        }
        counts = {table: table_count(conn, table) for table in expected_counts}
        assert counts == expected_counts, f"unexpected transaction business counts: {counts}"

        order = conn.execute(
            """
            SELECT shop_id, shop_name_snapshot, task_id, order_id, buyer_id, status, payment_amount, source_sheet
            FROM orders
            WHERE order_id = ?
            """,
            ("mock-o001",),
        ).fetchone()
        assert order is not None, "mock-o001 was not persisted to orders"
        assert order["shop_id"] == SHOP_ID
        assert order["shop_name_snapshot"] == SHOP_NAME
        assert order["task_id"] == context.task_id
        assert order["buyer_id"] == "mock-buyer-001"
        assert order["status"] == "paid"
        assert order["payment_amount"] == 199.0
        assert order["source_sheet"] == "/channels-shop-order/getorderlist"

        item = conn.execute(
            """
            SELECT shop_id, order_item_id, order_id, product_id, sku_id, quantity, item_amount
            FROM order_items
            WHERE order_item_id = ?
            """,
            ("mock-oi001",),
        ).fetchone()
        assert item is not None, "mock-oi001 was not persisted to order_items"
        assert item["shop_id"] == SHOP_ID
        assert item["order_id"] == "mock-o001"
        assert item["product_id"] == "mock-p001"
        assert item["sku_id"] == "mock-sku-p001-red-m"
        assert item["quantity"] == 1.0
        assert item["item_amount"] == 199.0

        refund = conn.execute(
            """
            SELECT shop_id, refund_id, order_id, product_id, sku_id, refund_status, refund_amount
            FROM refunds
            WHERE refund_id = ?
            """,
            ("mock-r001",),
        ).fetchone()
        assert refund is not None, "mock-r001 was not persisted to refunds"
        assert refund["shop_id"] == SHOP_ID
        assert refund["order_id"] == "mock-o001"
        assert refund["product_id"] == "mock-p001"
        assert refund["sku_id"] == "mock-sku-p001-red-m"
        assert refund["refund_status"] == "completed"
        assert refund["refund_amount"] == 20.0

        flow = conn.execute(
            """
            SELECT shop_id, flow_id, flow_date, flow_type, biz_type, order_id, amount, direction
            FROM fund_flows
            WHERE flow_id = ?
            """,
            ("mock-f001",),
        ).fetchone()
        assert flow is not None, "mock-f001 was not persisted to fund_flows"
        assert flow["shop_id"] == SHOP_ID
        assert flow["flow_date"] == "2026-06-02"
        assert flow["flow_type"] == "payment"
        assert flow["biz_type"] == "order"
        assert flow["order_id"] == "mock-o001"
        assert flow["amount"] == 199.0
        assert flow["direction"] == "in"


def assert_raw_archives_have_hash_and_size(context: RunContext) -> None:
    with connect(context.db_path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT raw_response_id, storage_path, sha256, size_bytes
                FROM raw_api_responses
                ORDER BY raw_response_id
                """
            ).fetchall()
        ]

    assert rows, "raw_api_responses has no rows"
    for row in rows:
        storage_path = str(row["storage_path"])
        archive_path = resolve_archive_path(storage_path, context)
        assert archive_path.exists(), (
            f"raw archive file missing for raw_response_id={row['raw_response_id']}: {storage_path}"
        )
        payload = archive_path.read_bytes()
        expected_sha = hashlib.sha256(payload).hexdigest()
        expected_size = len(payload)
        assert row["sha256"] == expected_sha, (
            f"sha256 mismatch for {archive_path}: expected {expected_sha}, got {row['sha256']}"
        )
        assert row["size_bytes"] == expected_size, (
            f"size_bytes mismatch for {archive_path}: expected {expected_size}, got {row['size_bytes']}"
        )


def assert_sensitive_data_not_persisted(context: RunContext) -> None:
    if context.db_path.exists():
        assert_no_sensitive_bytes(context.db_path.read_bytes(), f"SQLite file {context.db_path}")

    if context.raw_dir.exists():
        for path in context.raw_dir.rglob("*"):
            if path.is_file():
                assert_no_sensitive_bytes(path.read_bytes(), f"raw archive {path}")

    with connect(context.db_path) as conn:
        for table in REQUIRED_TABLES + BUSINESS_TABLES:
            for row in select_all(conn, table):
                serialized = json.dumps(row, ensure_ascii=False, default=str).encode("utf-8")
                assert_no_sensitive_bytes(serialized, f"{table} row {row}")


def assert_no_sensitive_bytes(payload: bytes, label: str) -> None:
    lowered = payload.lower()
    for term in SENSITIVE_TERMS:
        assert term.encode("utf-8") not in lowered, f"sensitive term {term!r} was persisted in {label}"
    for name, value in SENSITIVE_SENTINELS.items():
        assert value.encode("utf-8") not in payload, (
            f"sensitive {name!r} sentinel value was persisted in {label}"
        )


@contextmanager
def sensitive_environment() -> Iterator[None]:
    updates = sensitive_env_vars()
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def sensitive_env_vars() -> dict[str, str]:
    return {
        "WECHAT_MOCK_TOKEN": SENSITIVE_SENTINELS["token"],
        "WECHAT_MOCK_COOKIE": SENSITIVE_SENTINELS["cookie"],
        "WECHAT_MOCK_AUTHORIZATION": SENSITIVE_SENTINELS["authorization"],
        "WECHAT_MOCK_ACCESS_TOKEN": SENSITIVE_SENTINELS["access_token"],
        "WECHAT_MOCK_SECRET": SENSITIVE_SENTINELS["secret"],
        "WECHAT_MOCK_SESSION": SENSITIVE_SENTINELS["session"],
    }


def snapshot_state(context: RunContext) -> dict[str, Any]:
    with connect(context.db_path) as conn:
        table_snapshots = {table: table_snapshot(conn, table) for table in REQUIRED_TABLES + BUSINESS_TABLES}

    archive_snapshot = []
    if context.raw_dir.exists():
        archive_snapshot = [
            {
                "path": str(path.relative_to(context.raw_dir)),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(context.raw_dir.rglob("*"))
            if path.is_file()
        ]

    return {
        "tables": table_snapshots,
        "archives": archive_snapshot,
    }


def table_snapshot(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    identity_columns = {
        "sync_runs": ("sync_run_id", "row_fingerprint"),
        "sync_run_items": ("sync_item_id", "sync_run_id", "row_fingerprint"),
        "raw_api_responses": (
            "raw_response_id",
            "sync_run_id",
            "sync_item_id",
            "storage_path",
            "sha256",
            "size_bytes",
            "row_fingerprint",
        ),
        "products": ("shop_id", "product_id", "sku_id", "row_fingerprint"),
        "product_skus": ("shop_id", "product_id", "sku_id", "row_fingerprint"),
        "orders": ("shop_id", "order_id", "row_fingerprint"),
        "order_items": ("shop_id", "order_item_id", "order_id", "row_fingerprint"),
        "refunds": ("shop_id", "refund_id", "order_id", "row_fingerprint"),
        "fund_flows": ("shop_id", "flow_id", "row_fingerprint"),
    }[table]
    column_sql = ", ".join(identity_columns)
    rows = [
        dict(row)
        for row in conn.execute(
            f"SELECT {column_sql} FROM {table} ORDER BY row_fingerprint"
        ).fetchall()
    ]
    fingerprints = [row["row_fingerprint"] for row in rows]
    assert len(fingerprints) == len(set(fingerprints)), f"duplicate row_fingerprint in {table}"
    return rows


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])


def select_all(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]


def resolve_archive_path(storage_path: str, context: RunContext) -> Path:
    path = Path(storage_path)
    candidates = []
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.extend(
            [
                context.raw_dir / path,
                context.db_path.parent / path,
                PROJECT_ROOT / path,
            ]
        )
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.resolve().is_relative_to(context.raw_dir.resolve()):
                return candidate
        except FileNotFoundError:
            continue
    raise AssertionError(
        f"storage_path must resolve to a file under the requested raw_dir; got {storage_path!r}"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _missing_requested_module(spec: str, exc: ModuleNotFoundError) -> bool:
    module_name = spec.split(":", 1)[0]
    missing_name = exc.name or ""
    return module_name == missing_name or module_name.startswith(f"{missing_name}.")


def print_contract() -> None:
    print("Expected one of these Python entrypoints:")
    for spec in PYTHON_ENTRYPOINTS:
        print(f"  - {spec}")
    print("Or set MOCK_API_SYNC_ENTRYPOINT=module:function / cli:/absolute/path.py.")
    print("The entrypoint should run connector=mock without network/browser access and persist:")
    print("  - one idempotent sync_runs row for the supplied task_id/sync_run_id")
    print("  - one or more idempotent sync_run_items rows")
    print("  - one or more raw_api_responses rows with storage_path, sha256, and size_bytes")
    print("It must not persist token/access_token/credentials into SQLite rows or raw archive files.")


if __name__ == "__main__":
    raise SystemExit(main())
