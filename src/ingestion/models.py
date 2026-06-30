from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shared.table_catalog import ALL_BATCH_TABLES, METADATA_TABLES, STANDARD_TABLES


Record = dict[str, Any]
WarningRecord = dict[str, Any]


@dataclass
class ImportBatch:
    shop_id: str
    task_id: str | None
    shop_name: str | None = None
    orders: list[Record] = field(default_factory=list)
    order_items: list[Record] = field(default_factory=list)
    products: list[Record] = field(default_factory=list)
    product_skus: list[Record] = field(default_factory=list)
    refunds: list[Record] = field(default_factory=list)
    reviews: list[Record] = field(default_factory=list)
    shop_daily: list[Record] = field(default_factory=list)
    product_daily: list[Record] = field(default_factory=list)
    traffic_sources: list[Record] = field(default_factory=list)
    fund_flows: list[Record] = field(default_factory=list)
    ad_spend: list[Record] = field(default_factory=list)
    audience_insights: list[Record] = field(default_factory=list)
    ai_reports: list[Record] = field(default_factory=list)
    collection_tasks: list[Record] = field(default_factory=list)
    collection_task_items: list[Record] = field(default_factory=list)
    warnings: list[WarningRecord] = field(default_factory=list)

    def extend_table(self, table: str, records: list[Record]) -> None:
        if table not in ALL_BATCH_TABLES:
            raise ValueError(f"Unsupported table: {table}")
        getattr(self, table).extend(records)

    def counts(self) -> dict[str, int]:
        counts = {table: len(getattr(self, table)) for table in ALL_BATCH_TABLES}
        counts["warnings"] = len(self.warnings)
        return counts
