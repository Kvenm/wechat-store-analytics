#!/usr/bin/env node
import { chromium } from 'playwright';


const url = process.argv[2];
const clickText = process.argv.find((value) => value.startsWith('--click-text='))?.slice('--click-text='.length) || '';
if (!url || !url.startsWith('https://store.weixin.qq.com/')) {
  throw new Error('usage: node scripts/inspect/inspect-store-page.mjs https://store.weixin.qq.com/...');
}

const browser = await chromium.connectOverCDP('http://127.0.0.1:9333', { timeout: 10000 });
const context = browser.contexts()[0];
const page = await context.newPage();
try {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(1500);
  let action = null;
  if (clickText) {
    for (const frame of page.frames()) {
      const candidates = frame.getByText(clickText, { exact: true });
      const count = Math.min(await candidates.count(), 30);
      for (let index = 0; index < count; index += 1) {
        const candidate = candidates.nth(index);
        if (!await candidate.isVisible({ timeout: 300 }).catch(() => false)) continue;
        await candidate.click({ timeout: 5000 });
        action = { click_text: clickText, frame_url: frame.url(), index };
        await page.waitForTimeout(700);
        break;
      }
      if (action) break;
    }
  }
  const frames = [];
  for (const frame of page.frames()) {
    const entries = [];
    const locator = frame.locator([
      'input',
      'button',
      'a',
      '[role]',
      '[class*="date" i]',
      '[class*="time" i]',
      '[class*="picker" i]',
      '[class*="selector" i]',
      '[class*="export" i]',
      '[class*="download" i]'
    ].join(','));
    const count = Math.min(await locator.count(), 500);
    for (let index = 0; index < count; index += 1) {
      const item = locator.nth(index);
      const info = await item.evaluate((element) => {
        const normalize = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        const text = normalize(element.innerText || element.textContent);
        const placeholder = element.getAttribute('placeholder') || '';
        const className = typeof element.className === 'string'
          ? element.className
          : element.getAttribute('class') || '';
        const relevant = /\u5bfc\u51fa|\u4e0b\u8f7d|\u65e5\u671f|\u65f6\u95f4|\u8bb0\u8d26|\u52a8\u8d26|\u67e5\u8be2|\u91cd\u7f6e|\u786e\u5b9a|\u81ea\u5b9a\u4e49|\u8fd1\s*(?:7|30)\s*(?:\u65e5|\u5929)|\u5546\u54c1\u660e\u7ec6|\u4eba\u7fa4\u7279\u5f81|date|time|picker|selector|export|download/i.test([
          text,
          placeholder,
          className,
          element.getAttribute('aria-label') || '',
          element.getAttribute('title') || ''
        ].join(' '));
        if (!relevant) return null;
        const ancestors = [];
        let current = element;
        for (let depth = 0; current && depth < 4; depth += 1, current = current.parentElement) {
          ancestors.push(normalize(current.outerHTML).slice(0, 900));
        }
        return {
          tag: element.tagName.toLowerCase(),
          text: text.slice(0, 300),
          placeholder,
          value: 'value' in element ? String(element.value || '') : null,
          role: element.getAttribute('role') || '',
          aria_label: element.getAttribute('aria-label') || '',
          title: element.getAttribute('title') || '',
          class_name: className.slice(0, 300),
          visible: style.display !== 'none'
            && style.visibility !== 'hidden'
            && Number(style.opacity || 1) !== 0
            && rect.width > 0
            && rect.height > 0,
          cursor: style.cursor,
          ancestors
        };
      }).catch(() => null);
      if (info) entries.push(info);
    }
    frames.push({
      name: frame.name(),
      url: frame.url(),
      body_text: String(await frame.locator('body').innerText({ timeout: 3000 }).catch(() => '')).slice(0, 12000),
      entries
    });
  }
  process.stdout.write(`${JSON.stringify({ page_url: page.url(), action, frames }, null, 2)}\n`, () => {
    process.exit(0);
  });
} finally {
  await page.close({ runBeforeUnload: false }).catch(() => {});
}
