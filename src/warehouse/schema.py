from __future__ import annotations

import sqlite3


INSERTABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "shops": ("id", "name", "raw_json", "created_at", "updated_at"),
    "orders": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "order_id",
        "buyer_id",
        "status",
        "order_created_at",
        "paid_at",
        "payment_amount",
        "shipping_amount",
        "discount_amount",
        "refund_amount",
        "currency",
        "source_file",
        "source_sheet",
        "source_row_number",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "products": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "product_id",
        "product_name",
        "sku_id",
        "sku_name",
        "category",
        "status",
        "price",
        "stock",
        "source_file",
        "source_sheet",
        "source_row_number",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "product_skus": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "product_id",
        "product_name",
        "sku_id",
        "sku_name",
        "category",
        "status",
        "sku_price",
        "stock",
        "barcode",
        "source_file",
        "source_sheet",
        "source_row_number",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "order_items": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "order_item_id",
        "order_id",
        "product_id",
        "product_name",
        "sku_id",
        "sku_name",
        "quantity",
        "item_amount",
        "refund_amount",
        "source_file",
        "source_sheet",
        "source_row_number",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "refunds": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "refund_id",
        "order_id",
        "product_id",
        "product_name",
        "sku_id",
        "refund_status",
        "refund_amount",
        "refund_created_at",
        "refund_completed_at",
        "reason",
        "source_file",
        "source_sheet",
        "source_row_number",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "reviews": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "review_id",
        "order_id",
        "product_id",
        "product_name",
        "sku_id",
        "sku_name",
        "buyer_id",
        "rating",
        "review_content",
        "review_created_at",
        "reply_content",
        "reply_created_at",
        "is_positive",
        "source_file",
        "source_sheet",
        "source_row_number",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "shop_daily": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "stat_date",
        "visitor_count",
        "view_count",
        "exposure_user_count",
        "click_user_count",
        "click_count",
        "order_amount",
        "order_submit_count",
        "order_user_count",
        "order_count",
        "buyer_count",
        "payment_amount",
        "refund_amount",
        "sold_quantity",
        "conversion_rate",
        "click_conversion_rate",
        "refund_rate",
        "source_file",
        "source_sheet",
        "source_row_number",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "product_daily": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "stat_date",
        "product_id",
        "product_name",
        "sku_id",
        "category",
        "visitor_count",
        "view_count",
        "add_to_cart_count",
        "order_count",
        "buyer_count",
        "payment_amount",
        "refund_amount",
        "conversion_rate",
        "refund_rate",
        "source_file",
        "source_sheet",
        "source_row_number",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "traffic_sources": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "stat_date",
        "source_name",
        "source_type",
        "product_id",
        "product_name",
        "visitor_count",
        "view_count",
        "order_count",
        "payment_amount",
        "conversion_rate",
        "source_file",
        "source_sheet",
        "source_row_number",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "fund_flows": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "flow_id",
        "flow_date",
        "flow_type",
        "biz_type",
        "order_id",
        "amount",
        "currency",
        "direction",
        "balance",
        "remark",
        "source_file",
        "source_sheet",
        "source_row_number",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "ad_spend": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "stat_date",
        "platform",
        "campaign_id",
        "campaign_name",
        "ad_group_id",
        "ad_group_name",
        "product_id",
        "product_name",
        "impressions",
        "clicks",
        "spend_amount",
        "payment_amount",
        "orders",
        "roi",
        "source_file",
        "source_sheet",
        "source_row_number",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "audience_insights": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "insight_id",
        "product_id",
        "product_name",
        "sku_id",
        "category",
        "dimension",
        "segment_label",
        "visitor_count",
        "add_to_cart_count",
        "order_count",
        "payment_amount",
        "conversion_rate",
        "refund_rate",
        "active_hour",
        "region",
        "gender",
        "age_group",
        "consumption_level",
        "source_file",
        "source_sheet",
        "source_row_number",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "ai_reports": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "report_id",
        "report_type",
        "report_title",
        "report_date",
        "content",
        "metrics_json",
        "warnings_json",
        "source_file",
        "source_sheet",
        "source_row_number",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "collection_tasks": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "collection_task_id",
        "task_name",
        "status",
        "started_at",
        "finished_at",
        "source_type",
        "source_file",
        "source_sheet",
        "source_row_number",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "collection_task_items": (
        "shop_id",
        "shop_name_snapshot",
        "task_id",
        "collection_task_id",
        "item_id",
        "table_name",
        "item_status",
        "source_file",
        "source_sheet",
        "source_row_number",
        "error_message",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "api_task_runs": (
        "task_id",
        "shop_id",
        "shop_name_snapshot",
        "task_name",
        "source_type",
        "status",
        "state",
        "date_from",
        "date_to",
        "types_json",
        "headless",
        "thread_name",
        "collection_task_id",
        "source_dir",
        "metadata_path",
        "collector_status",
        "started_at",
        "completed_at",
        "result_json",
        "error_json",
        "spec_json",
        "row_fingerprint",
        "created_at",
        "updated_at",
    ),
    "api_task_run_steps": (
        "task_id",
        "step_key",
        "status",
        "started_at",
        "completed_at",
        "command_json",
        "exit_code",
        "duration_ms",
        "stdout_tail",
        "stderr_tail",
        "parsed_json",
        "error_text",
        "row_fingerprint",
        "created_at",
        "updated_at",
    ),
    "sync_runs": (
        "sync_run_id",
        "shop_id",
        "shop_name_snapshot",
        "source_kind",
        "connector",
        "status",
        "date_from",
        "date_to",
        "started_at",
        "finished_at",
        "params_json",
        "summary_json",
        "warnings_json",
        "row_fingerprint",
        "created_at",
        "updated_at",
    ),
    "sync_run_items": (
        "sync_item_id",
        "sync_run_id",
        "shop_id",
        "source_kind",
        "endpoint",
        "export_type",
        "table_hint",
        "status",
        "cursor",
        "page_number",
        "request_id",
        "rid",
        "http_status",
        "error_code",
        "error_message",
        "raw_response_id",
        "row_count",
        "started_at",
        "finished_at",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
    "raw_api_responses": (
        "raw_response_id",
        "sync_run_id",
        "sync_item_id",
        "shop_id",
        "source_kind",
        "endpoint",
        "request_id",
        "rid",
        "status",
        "storage_path",
        "sha256",
        "size_bytes",
        "record_count",
        "schema_version",
        "pulled_at",
        "row_fingerprint",
        "raw_json",
        "created_at",
        "updated_at",
    ),
}


def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS shops (
            id TEXT PRIMARY KEY,
            name TEXT,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            order_id TEXT,
            buyer_id TEXT,
            status TEXT,
            order_created_at TEXT,
            paid_at TEXT,
            payment_amount REAL,
            shipping_amount REAL,
            discount_amount REAL,
            refund_amount REAL,
            currency TEXT,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            product_id TEXT,
            product_name TEXT,
            sku_id TEXT,
            sku_name TEXT,
            category TEXT,
            status TEXT,
            price REAL,
            stock REAL,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS product_skus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            product_id TEXT,
            product_name TEXT,
            sku_id TEXT,
            sku_name TEXT,
            category TEXT,
            status TEXT,
            sku_price REAL,
            stock REAL,
            barcode TEXT,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            order_item_id TEXT,
            order_id TEXT,
            product_id TEXT,
            product_name TEXT,
            sku_id TEXT,
            sku_name TEXT,
            quantity REAL,
            item_amount REAL,
            refund_amount REAL,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS refunds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            refund_id TEXT,
            order_id TEXT,
            product_id TEXT,
            product_name TEXT,
            sku_id TEXT,
            refund_status TEXT,
            refund_amount REAL,
            refund_created_at TEXT,
            refund_completed_at TEXT,
            reason TEXT,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            review_id TEXT,
            order_id TEXT,
            product_id TEXT,
            product_name TEXT,
            sku_id TEXT,
            sku_name TEXT,
            buyer_id TEXT,
            rating REAL,
            review_content TEXT,
            review_created_at TEXT,
            reply_content TEXT,
            reply_created_at TEXT,
            is_positive TEXT,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS shop_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            stat_date TEXT,
            visitor_count REAL,
            view_count REAL,
            exposure_user_count REAL,
            click_user_count REAL,
            click_count REAL,
            order_amount REAL,
            order_submit_count REAL,
            order_user_count REAL,
            order_count REAL,
            buyer_count REAL,
            payment_amount REAL,
            refund_amount REAL,
            sold_quantity REAL,
            conversion_rate REAL,
            click_conversion_rate REAL,
            refund_rate REAL,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS product_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            stat_date TEXT,
            product_id TEXT,
            product_name TEXT,
            sku_id TEXT,
            category TEXT,
            visitor_count REAL,
            view_count REAL,
            add_to_cart_count REAL,
            order_count REAL,
            buyer_count REAL,
            payment_amount REAL,
            refund_amount REAL,
            conversion_rate REAL,
            refund_rate REAL,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS traffic_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            stat_date TEXT,
            source_name TEXT,
            source_type TEXT,
            product_id TEXT,
            product_name TEXT,
            visitor_count REAL,
            view_count REAL,
            order_count REAL,
            payment_amount REAL,
            conversion_rate REAL,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS fund_flows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            flow_id TEXT,
            flow_date TEXT,
            flow_type TEXT,
            biz_type TEXT,
            order_id TEXT,
            amount REAL,
            currency TEXT,
            direction TEXT,
            balance REAL,
            remark TEXT,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS ad_spend (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            stat_date TEXT,
            platform TEXT,
            campaign_id TEXT,
            campaign_name TEXT,
            ad_group_id TEXT,
            ad_group_name TEXT,
            product_id TEXT,
            product_name TEXT,
            impressions REAL,
            clicks REAL,
            spend_amount REAL,
            payment_amount REAL,
            orders REAL,
            roi REAL,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS audience_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            insight_id TEXT,
            product_id TEXT,
            product_name TEXT,
            sku_id TEXT,
            category TEXT,
            dimension TEXT,
            segment_label TEXT,
            visitor_count REAL,
            add_to_cart_count REAL,
            order_count REAL,
            payment_amount REAL,
            conversion_rate REAL,
            refund_rate REAL,
            active_hour TEXT,
            region TEXT,
            gender TEXT,
            age_group TEXT,
            consumption_level TEXT,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS ai_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            report_id TEXT,
            report_type TEXT,
            report_title TEXT,
            report_date TEXT,
            content TEXT,
            metrics_json TEXT,
            warnings_json TEXT,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS collection_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            collection_task_id TEXT,
            task_name TEXT,
            status TEXT,
            started_at TEXT,
            finished_at TEXT,
            source_type TEXT,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS collection_task_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_id TEXT,
            collection_task_id TEXT,
            item_id TEXT,
            table_name TEXT,
            item_status TEXT,
            source_file TEXT,
            source_sheet TEXT,
            source_row_number INTEGER,
            error_message TEXT,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS api_task_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            task_name TEXT,
            source_type TEXT,
            status TEXT,
            state TEXT,
            date_from TEXT,
            date_to TEXT,
            types_json TEXT,
            headless INTEGER,
            thread_name TEXT,
            collection_task_id TEXT,
            source_dir TEXT,
            metadata_path TEXT,
            collector_status TEXT,
            started_at TEXT,
            completed_at TEXT,
            result_json TEXT,
            error_json TEXT,
            spec_json TEXT,
            row_fingerprint TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS api_task_run_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            step_key TEXT NOT NULL,
            status TEXT,
            started_at TEXT,
            completed_at TEXT,
            command_json TEXT,
            exit_code INTEGER,
            duration_ms INTEGER,
            stdout_tail TEXT,
            stderr_tail TEXT,
            parsed_json TEXT,
            error_text TEXT,
            row_fingerprint TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS analysis_runs (
            id TEXT PRIMARY KEY,
            shop_id TEXT NOT NULL,
            date_from TEXT,
            date_to TEXT,
            params_json TEXT,
            metrics_json TEXT NOT NULL,
            warnings_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS strategy_reports (
            id TEXT PRIMARY KEY,
            analysis_run_id TEXT NOT NULL,
            shop_id TEXT NOT NULL,
            markdown_path TEXT NOT NULL,
            summary_csv_path TEXT NOT NULL,
            product_csv_path TEXT NOT NULL,
            audience_csv_path TEXT,
            warnings_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id),
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_run_id TEXT NOT NULL,
            shop_id TEXT NOT NULL,
            shop_name_snapshot TEXT,
            source_kind TEXT NOT NULL,
            connector TEXT NOT NULL,
            status TEXT NOT NULL,
            date_from TEXT,
            date_to TEXT,
            started_at TEXT,
            finished_at TEXT,
            params_json TEXT,
            summary_json TEXT,
            warnings_json TEXT,
            row_fingerprint TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS sync_run_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_item_id TEXT NOT NULL,
            sync_run_id TEXT NOT NULL,
            shop_id TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            export_type TEXT,
            table_hint TEXT,
            status TEXT NOT NULL,
            cursor TEXT,
            page_number INTEGER,
            request_id TEXT,
            rid TEXT,
            http_status INTEGER,
            error_code TEXT,
            error_message TEXT,
            raw_response_id TEXT,
            row_count INTEGER,
            started_at TEXT,
            finished_at TEXT,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        CREATE TABLE IF NOT EXISTS raw_api_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_response_id TEXT NOT NULL,
            sync_run_id TEXT NOT NULL,
            sync_item_id TEXT NOT NULL,
            shop_id TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            request_id TEXT,
            rid TEXT,
            status TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            sha256 TEXT,
            size_bytes INTEGER,
            record_count INTEGER,
            schema_version TEXT,
            pulled_at TEXT,
            row_fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );
        """
    )
    _migrate_insertable_columns(conn)
    conn.executescript(
        """

        CREATE INDEX IF NOT EXISTS idx_orders_shop_created_at ON orders(shop_id, order_created_at);
        CREATE INDEX IF NOT EXISTS idx_orders_shop_order_id ON orders(shop_id, order_id);
        CREATE INDEX IF NOT EXISTS idx_product_skus_shop_product_id ON product_skus(shop_id, product_id);
        CREATE INDEX IF NOT EXISTS idx_product_skus_shop_sku_id ON product_skus(shop_id, sku_id);
        CREATE INDEX IF NOT EXISTS idx_order_items_shop_order_id ON order_items(shop_id, order_id);
        CREATE INDEX IF NOT EXISTS idx_order_items_shop_product_id ON order_items(shop_id, product_id);
        CREATE INDEX IF NOT EXISTS idx_refunds_shop_order_id ON refunds(shop_id, order_id);
        CREATE INDEX IF NOT EXISTS idx_refunds_shop_product_id ON refunds(shop_id, product_id);
        CREATE INDEX IF NOT EXISTS idx_reviews_shop_product_id ON reviews(shop_id, product_id);
        CREATE INDEX IF NOT EXISTS idx_reviews_shop_created_at ON reviews(shop_id, review_created_at);
        CREATE INDEX IF NOT EXISTS idx_shop_daily_shop_date ON shop_daily(shop_id, stat_date);
        CREATE INDEX IF NOT EXISTS idx_product_daily_shop_date ON product_daily(shop_id, stat_date);
        CREATE INDEX IF NOT EXISTS idx_product_daily_shop_product_id ON product_daily(shop_id, product_id);
        CREATE INDEX IF NOT EXISTS idx_traffic_sources_shop_date ON traffic_sources(shop_id, stat_date);
        CREATE INDEX IF NOT EXISTS idx_fund_flows_shop_date ON fund_flows(shop_id, flow_date);
        CREATE INDEX IF NOT EXISTS idx_ad_spend_shop_date ON ad_spend(shop_id, stat_date);
        CREATE INDEX IF NOT EXISTS idx_audience_shop_product_id ON audience_insights(shop_id, product_id);
        CREATE INDEX IF NOT EXISTS idx_audience_shop_dimension ON audience_insights(shop_id, dimension);
        CREATE INDEX IF NOT EXISTS idx_ai_reports_shop_date ON ai_reports(shop_id, report_date);
        CREATE INDEX IF NOT EXISTS idx_collection_tasks_shop_task ON collection_tasks(shop_id, collection_task_id);
        CREATE INDEX IF NOT EXISTS idx_collection_task_items_shop_task ON collection_task_items(shop_id, collection_task_id);
        CREATE INDEX IF NOT EXISTS idx_api_task_runs_shop_created ON api_task_runs(shop_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_api_task_run_steps_task ON api_task_run_steps(task_id, step_key);
        CREATE INDEX IF NOT EXISTS idx_sync_runs_shop_started ON sync_runs(shop_id, started_at);
        CREATE INDEX IF NOT EXISTS idx_sync_run_items_run_endpoint ON sync_run_items(sync_run_id, endpoint);
        CREATE INDEX IF NOT EXISTS idx_raw_api_responses_run_endpoint ON raw_api_responses(sync_run_id, endpoint);
        """
    )


REAL_MIGRATION_COLUMNS = {
    "payment_amount",
    "shipping_amount",
    "discount_amount",
    "refund_amount",
    "price",
    "sku_price",
    "stock",
    "quantity",
    "item_amount",
    "rating",
    "visitor_count",
    "view_count",
    "exposure_user_count",
    "click_user_count",
    "click_count",
    "order_amount",
    "order_submit_count",
    "order_user_count",
    "add_to_cart_count",
    "order_count",
    "buyer_count",
    "sold_quantity",
    "conversion_rate",
    "click_conversion_rate",
    "refund_rate",
    "amount",
    "balance",
    "impressions",
    "clicks",
    "spend_amount",
    "orders",
    "roi",
}

INTEGER_MIGRATION_COLUMNS = {
    "source_row_number",
    "page_number",
    "http_status",
    "row_count",
    "size_bytes",
    "record_count",
}


def _migrate_insertable_columns(conn: sqlite3.Connection) -> None:
    """Add newly declared insertable columns to existing local SQLite files."""
    for table, columns in INSERTABLE_COLUMNS.items():
        existing_columns = _table_columns(conn, table)
        for column in columns:
            if column in existing_columns:
                continue
            conn.execute(
                f"ALTER TABLE {_quote_identifier(table)} "
                f"ADD COLUMN {_quote_identifier(column)} {_migration_column_type(column)}"
            )
            existing_columns.add(column)

        if "row_fingerprint" in columns and "row_fingerprint" in existing_columns:
            _ensure_row_fingerprint_unique_index(conn, table)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()}


def _migration_column_type(column: str) -> str:
    if column in REAL_MIGRATION_COLUMNS:
        return "REAL"
    if column in INTEGER_MIGRATION_COLUMNS:
        return "INTEGER"
    return "TEXT"


def _ensure_row_fingerprint_unique_index(conn: sqlite3.Connection, table: str) -> None:
    if _has_unique_single_column_index(conn, table, "row_fingerprint"):
        return
    try:
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_quote_identifier(f'idx_{table}_row_fingerprint_unique')} "
            f"ON {_quote_identifier(table)}({_quote_identifier('row_fingerprint')})"
        )
    except sqlite3.IntegrityError:
        # Keep initialization non-destructive for old local DBs with duplicate fingerprints.
        return


def _has_unique_single_column_index(conn: sqlite3.Connection, table: str, column: str) -> bool:
    for index_row in conn.execute(f"PRAGMA index_list({_quote_identifier(table)})").fetchall():
        is_unique = bool(index_row[2])
        if not is_unique:
            continue
        index_name = str(index_row[1])
        indexed_columns = [
            str(info_row[2])
            for info_row in conn.execute(f"PRAGMA index_info({_quote_identifier(index_name)})").fetchall()
        ]
        if indexed_columns == [column]:
            return True
    return False


def _quote_identifier(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum():
        raise ValueError(f"Unsafe SQLite identifier: {identifier}")
    return f'"{identifier}"'
