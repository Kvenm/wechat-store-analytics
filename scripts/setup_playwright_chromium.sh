#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi

export PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST:-https://npmmirror.com/mirrors/playwright}"
npx playwright install chromium

node - <<'NODE'
const { chromium } = await import("playwright");
const browser = await chromium.launch({ headless: true });
await browser.close();
console.log("Playwright Chromium is ready.");
NODE
