#!/usr/bin/env node
import { chromium } from 'playwright';

import {
  applyRequestedDateRange,
  discoverExportControls,
  selectSafeSurface
} from '../collect/export-visible-tables.mjs';
import { EXPORT_TARGETS, STORE_ORIGIN } from '../collect/export-visible-tables-lib.mjs';


const dateFrom = process.argv[2] || '2026-07-01';
const dateTo = process.argv[3] || '2026-07-14';
const requestedKeys = process.argv[4]
  ? process.argv[4].split(',').map((item) => item.trim()).filter(Boolean)
  : [
      'fund_flows',
      'transactions',
      'product_core_conversion',
      'product_traffic_funnel',
      'product_detail',
      'compass_buyer_profile'
    ];
const browser = await chromium.connectOverCDP('http://127.0.0.1:9333', { timeout: 10000 });
const context = browser.contexts()[0];
const checks = [];

try {
  for (const key of requestedKeys) {
    const target = EXPORT_TARGETS.find((item) => item.key === key);
    const page = await context.newPage();
    try {
      const url = key === 'compass_buyer_profile'
        ? `${STORE_ORIGIN}/compass/persona/home`
        : new URL(target.route, STORE_ORIGIN).toString();
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
      if (target.surfaceLabel) {
        const surface = await selectSafeSurface(page, target.surfaceLabel);
        if (!surface.selected) throw new Error(`surface not selected: ${target.surfaceLabel}`);
      }
      const date = await applyRequestedDateRange({
        page,
        task: { dateFrom, dateTo },
        target
      });
      await page.waitForTimeout(1500);
      const controls = await discoverExportControls(page);
      const rawDownloadText = [];
      for (const frame of page.frames()) {
        const locator = frame.getByText(/下载数据/);
        const count = Math.min(await locator.count(), 100);
        for (let index = 0; index < count; index += 1) {
          const item = locator.nth(index);
          if (!await item.isVisible({ timeout: 100 }).catch(() => false)) continue;
          rawDownloadText.push({
            frame_url: frame.url(),
            text: await item.innerText({ timeout: 500 }).catch(() => ''),
            html: await item.evaluate((element) => element.outerHTML.slice(0, 1000)).catch(() => '')
          });
        }
      }
      checks.push({
        target: key,
        status: 'passed',
        page_url: page.url(),
        date_filter_applied: date.applied,
        effective_date_range: date.effective_date_range,
        raw_download_text: rawDownloadText,
        controls: controls.map((item) => ({
          label: item.label,
          role: item.role,
          frame_url: item.frameUrl,
          context: item.contextText
        }))
      });
      await Promise.all(controls.map((item) => item.handle.dispose().catch(() => {})));
    } catch (error) {
      checks.push({ target: key, status: 'failed', page_url: page.url(), error: String(error?.message || error) });
    } finally {
      await page.close({ runBeforeUnload: false }).catch(() => {});
    }
  }
} finally {
  process.stdout.write(`${JSON.stringify({ date_from: dateFrom, date_to: dateTo, checks }, null, 2)}\n`, () => {
    process.exit(checks.some((item) => item.status === 'failed') ? 1 : 0);
  });
}
