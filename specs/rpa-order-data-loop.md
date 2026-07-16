# RPA Order Data Loop

## Objective

Use the local RPA collector path to obtain real WeChat Store order data when the official API returns zero orders, and verify the result all the way into the standard SQLite `orders` table.

## Requirements

- The local collector must have a runnable path that does not depend on uncalibrated `config/export-tasks.json` selectors.
- The runnable path must use the logged-in WeChat Store browser profile and collect the visible order list from the real backend.
- Generated order artifacts must be importable by the existing local import pipeline as standard `orders`.
- The server-side collector job client must be able to run this practical order scraping mode, upload artifacts, and trigger the existing import task.
- Verification must distinguish three outcomes:
  - real orders imported into SQLite;
  - RPA ran but the backend page shows no orders for the visible/default view;
  - RPA could not run because of login, permission, browser profile, or page structure blockers.

## Edge Cases

- Existing Playwright/Chrome profile is already locked by a stale collector process.
- The backend is logged out or asks for QR login again.
- The order page loads but shows an empty state.
- The order page uses iframe or shadow DOM list rendering.
- Visible rows contain amount text in different formats, including `¥12.34`, `12.34元`, or only status/time/order number.

## Definition Of Done

- A local RPA command runs against the real backend without relying on TODO export selectors.
- If rows are visible, the run produces CSV/JSON/debug/screenshot artifacts and imports at least one `orders` row into `data/processed/wechat_store.sqlite`.
- If no rows are visible, debug JSON and screenshot prove the backend page state.
- Regression tests and compile checks pass for touched Python files.

## Loop Budget

- Maximum build-review iterations: 3.
- Stop and ask for human input only if WeChat requires fresh QR login, account permission is missing, or the page has no visible orders after successful RPA navigation.

## Verification Commands

- `node scripts/collect/scrape-orders-page.mjs`
- `.venv/bin/python scripts/import/import_files.py --source-dir <captured-dir> --shop-id <shop-id> --shop-name <shop-name> --task-id <task-id> --expected-types orders --manifest-policy ignore`
- SQLite count checks for `orders`, `order_items`, and latest imported rows.
- `.venv/bin/python -m py_compile src/api/collector_jobs.py scripts/collector/local_client.py`
- `.venv/bin/python scripts/test/test_collector_jobs_regression.py`
- `.venv/bin/python scripts/test/test_admin_ui_config_regression.py`

## Approval Gates

- Do not print secrets from `.env.local`.
- Do not delete user data.
- It is acceptable to close a stale local Playwright Chrome process that is locking this project profile, because the user asked to run the RPA collector end to end.
