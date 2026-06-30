#!/usr/bin/env node
import { chromium } from 'playwright';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDir, '..', '..');
const userDataDir = path.join(projectRoot, 'data', 'browser-profile');
const loginUrl = 'https://store.weixin.qq.com/';

const context = await chromium.launchPersistentContext(userDataDir, {
  headless: false,
  acceptDownloads: true,
  viewport: { width: 1440, height: 960 },
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

while (!closed) {
  await new Promise((resolve) => setTimeout(resolve, 1000));
}
