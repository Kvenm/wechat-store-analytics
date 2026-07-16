#!/usr/bin/env node
import { chromium } from 'playwright';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDir, '..', '..');
const userDataDir = path.join(projectRoot, 'data', 'browser-profile');
const screenshotPath = path.join(projectRoot, 'data', 'web-login-screenshot.png');
const exportVerificationScreenshotPath = path.join(
  projectRoot,
  'data',
  'export-verification-screenshot.png'
);
const loginUrl = 'https://store.weixin.qq.com/';
const exportVerificationSelector = [
  '.my-qrcode',
  '.weui-desktop-qrcheck',
  '.weui-desktop-dialog__wrp',
  '.weui-desktop-dialog',
  '[role="dialog"]',
  '[aria-modal="true"]'
].join(',');
const exportVerificationTextPattern = /扫码验证|扫码确认|微信扫码|使用微信扫码|扫码.*(?:验证|确认)/;
const remoteDebugPort = process.env.WECHAT_STORE_REMOTE_DEBUG_PORT || '9333';
const headless = process.env.WECHAT_STORE_WEB_LOGIN_HEADLESS
  ? process.env.WECHAT_STORE_WEB_LOGIN_HEADLESS !== 'false'
  : process.platform === 'linux' && !process.env.DISPLAY;

const context = await chromium.launchPersistentContext(userDataDir, {
  headless,
  acceptDownloads: true,
  viewport: { width: 1440, height: 960 },
  args: [`--remote-debugging-port=${remoteDebugPort}`],
});

let closed = false;

async function closeContext() {
  if (closed) {
    return;
  }
  closed = true;
  await context.close().catch(() => {});
}

process.on('SIGINT', () => {
  void closeContext().then(() => process.exit(0));
});

process.on('SIGTERM', () => {
  void closeContext().then(() => process.exit(0));
});

context.on('close', () => {
  closed = true;
});

const page = context.pages()[0] || await context.newPage();
await page.goto(loginUrl, { waitUntil: 'domcontentloaded' });
await fs.mkdir(path.dirname(screenshotPath), { recursive: true });

async function screenshotPage() {
  const pages = context.pages();
  for (let index = pages.length - 1; index >= 0; index -= 1) {
    const candidate = pages[index];
    if (candidate.isClosed()) {
      continue;
    }
    const dialogs = candidate.locator(exportVerificationSelector);
    const count = Math.min(await dialogs.count().catch(() => 0), 20);
    for (let dialogIndex = 0; dialogIndex < count; dialogIndex += 1) {
      const dialog = dialogs.nth(dialogIndex);
      if (!await dialog.isVisible({ timeout: 100 }).catch(() => false)) {
        continue;
      }
      const text = String(await dialog.innerText({ timeout: 300 }).catch(() => ''))
        .replace(/\s+/g, ' ')
        .trim();
      if (exportVerificationTextPattern.test(text)) {
        return candidate;
      }
    }
  }
  return page;
}

while (!closed) {
  const exportScreenshotIsFresh = await fs.stat(exportVerificationScreenshotPath)
    .then((stats) => Date.now() - stats.mtimeMs <= 3000)
    .catch(() => false);
  if (exportScreenshotIsFresh) {
    await fs.copyFile(exportVerificationScreenshotPath, screenshotPath).catch(() => {});
  } else {
    const currentScreenshotPage = await screenshotPage().catch(() => page);
    await currentScreenshotPage.screenshot({ path: screenshotPath }).catch(() => {});
  }
  await new Promise((resolve) => setTimeout(resolve, 1000));
}
