#!/usr/bin/env python3
import argparse
import csv
import json
import re
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert visible order-page scrape JSON to an import-friendly orders CSV.")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-csv", required=True)
    args = parser.parse_args()

    input_path = Path(args.input_json)
    output_path = Path(args.output_csv)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    rows = payload.get("rows", [])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    seen = set()
    converted = []
    for row in rows:
        order_id = str(row.get("order_id") or "").strip()
        created_at = str(row.get("created_at") or "").strip()
        if not order_id or not created_at:
            continue
        key = (order_id, created_at)
        if key in seen:
            continue
        seen.add(key)

        amounts = row.get("amount_candidates_json") or []
        payment_amount = first_amount(amounts)
        converted.append(
            {
                "订单号": order_id,
                "下单时间": created_at,
                "订单状态": row.get("status") or "",
                "实付金额": payment_amount,
                "商品名称": trim(row.get("product_text") or "", 180),
            }
        )

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["订单号", "下单时间", "订单状态", "实付金额", "商品名称"])
        writer.writeheader()
        writer.writerows(converted)

    print(json.dumps({"input": str(input_path), "output": str(output_path), "rows": len(converted)}, ensure_ascii=False, indent=2))


def first_amount(values) -> str:
    for value in values:
        match = re.search(r"[\d,]+(?:\.\d{1,2})?", str(value))
        if match:
            return match.group(0).replace(",", "")
    return ""


def trim(value: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


if __name__ == "__main__":
    main()
