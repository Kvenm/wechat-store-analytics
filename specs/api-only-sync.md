# API-only sync loop spec

## Objective

Build the server-runnable WeChat Store API sync path for the API worktree. It must not depend on Playwright, browser profile,扫码登录, or backend Excel/CSV exports.

## Requirements

- Pull data through official WeChat Store HTTPS APIs using `access_token` or `authorizer_access_token`.
- Cover the current Playwright/export analysis surface as far as API data is available:
  - core business tables: `products`, `product_skus`, `orders`, `order_items`, `refunds`, `fund_flows`
  - analysis tables when endpoint payloads already match standard field aliases: `shop_daily`, `product_daily`, `traffic_sources`, `ad_spend`, `audience_insights`, `reviews`
- For list APIs that return IDs, call the corresponding detail API before mapping records.
- Persist `sync_runs`, `sync_run_items`, and `raw_api_responses`.
- Archive raw responses as private JSON files with sha256, size, row count, endpoint, page, cursor, request id, and rid metadata.
- Never persist secrets: `access_token`, `refresh_token`, `authorization`, `cookie`, `appsecret`, `secret`, `session`, `token`.
- Keep sync idempotent for repeated `sync_run_id`.
- Provide a CLI entrypoint suitable for server cron.
- Reuse the existing metrics/report chain after a successful sync.
- Allow custom generic standard-table endpoints such as `reviews:/channels/...` when an official path is known.
- Verification must run without calling real WeChat by using fake HTTP responses.

## Done

- `src/sync/wechat_api.py` implements real API sync and reusable fake-HTTP tests.
- `scripts/sync/wechat_api_sync.py` runs the connector from env/CLI config.
- `POST /api-sync/runs` and the admin UI can trigger API sync and generate a report.
- A regression test proves raw archive, metadata, standard tables, detail fetching, idempotency, and secret redaction.

## Loop

- Iteration budget: 2 build-review passes.
- Verification: run the new API sync regression plus existing API mapper/mock sync regressions.
- Approval gates: no production calls, no real credentials, no destructive commands.
