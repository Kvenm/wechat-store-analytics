import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { dismissOpenNavigationOverlays } from '../../src/collector/wechatStoreCollector.js';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const profileDir = path.join(projectRoot, 'data', 'browser-profile');
const outputDir = path.join(projectRoot, 'data', 'raw', 'calibration');

await fs.mkdir(outputDir, { recursive: true });

const context = await chromium.launchPersistentContext(profileDir, {
  headless: false,
  acceptDownloads: false,
  viewport: { width: 1440, height: 1000 },
  args: ['--disable-crash-reporter']
});

const page = context.pages()[0] ?? await context.newPage();

try {
  await enterOrdersPage(page);
  const beforeText = await bodyText(page);
  const exportButton = page.locator('text=全部导出').first();
  await exportButton.waitFor({ state: 'visible', timeout: 30000 });
  await exportButton.click({ timeout: 10000 });
  await page.waitForTimeout(3000);

  const afterText = await bodyText(page);
  const dialogs = await page.locator('[role="dialog"],.weui-desktop-dialog,.weui-desktop-dialog__wrp,.weui-desktop-popover,.weui-desktop-popover__wrp,.weui-desktop-toast,body').evaluateAll((nodes) => {
    const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
    return nodes.map((node) => {
      const rect = node.getBoundingClientRect();
      return {
        tag: node.tagName.toLowerCase(),
        role: node.getAttribute('role') ?? '',
        cls: typeof node.className === 'string' ? node.className.slice(0, 220) : '',
        text: clean(node.innerText || node.textContent).slice(0, 2000),
        rect: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          w: Math.round(rect.width),
          h: Math.round(rect.height)
        }
      };
    }).filter((item) => item.text || item.role || item.cls);
  });
  const buttons = await page.locator('button,a,[role=button]').evaluateAll((nodes) => {
    const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
    return nodes.map((node) => {
      const rect = node.getBoundingClientRect();
      return {
        tag: node.tagName.toLowerCase(),
        text: clean(node.innerText || node.textContent).slice(0, 160),
        cls: typeof node.className === 'string' ? node.className.slice(0, 180) : '',
        role: node.getAttribute('role') ?? '',
        href: node.getAttribute('href') ?? '',
        rect: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          w: Math.round(rect.width),
          h: Math.round(rect.height)
        }
      };
    }).filter((item) => item.rect.w > 0 || item.rect.h > 0 || /导出|下载|确定|确认|取消/.test(item.text));
  });

  const outputPath = path.join(outputDir, `orders-export-dialog-${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
  const screenshotPath = outputPath.replace(/\.json$/, '.png');
  await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => null);
  await fs.writeFile(
    outputPath,
    JSON.stringify({
      url: page.url(),
      beforeText: beforeText.slice(0, 3000),
      afterText: afterText.slice(0, 5000),
      newText: diffSuffix(beforeText, afterText).slice(0, 3000),
      dialogs,
      buttons,
      screenshotPath
    }, null, 2),
    'utf8'
  );

  await page.keyboard.press('Escape').catch(() => {});
  console.log(JSON.stringify({
    outputPath,
    screenshotPath,
    url: page.url(),
    dialogCount: dialogs.length,
    buttons: buttons.filter((item) => /导出|下载|确定|确认|取消|全部导出/.test(item.text)).slice(0, 40)
  }, null, 2));
} finally {
  await context.close();
}

async function enterOrdersPage(targetPage) {
  await targetPage.goto('https://store.weixin.qq.com/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await targetPage.waitForFunction(() => {
    const text = document.body?.innerText ?? '';
    return text.includes('首页')
      && text.includes('商品管理')
      && text.includes('订单/配送')
      && !text.includes('扫码进入我的小店')
      && !text.includes('登录超时')
      && !text.includes('请先登录');
  }, null, { timeout: 600000 });
  await targetPage.waitForTimeout(2000);
  await targetPage.locator('text=订单/配送').last().click({ timeout: 10000 }).catch(() => {});
  await targetPage.waitForTimeout(1000);
  const visibleOrderLink = targetPage.locator('a:has-text("订单管理")').last();
  if (await visibleOrderLink.isVisible({ timeout: 5000 }).catch(() => false)) {
    await visibleOrderLink.click({ timeout: 10000 });
  } else {
    const clicked = await targetPage.evaluate(() => {
      const entry = [...document.querySelectorAll('a')].find((node) => node.getAttribute('href') === '/shop/order/list');
      if (!entry) {
        return false;
      }
      entry.click();
      return true;
    });
    if (!clicked) {
      throw new Error('Orders navigation entry not found.');
    }
  }
  await targetPage.waitForTimeout(18000);
  await dismissOpenNavigationOverlays(targetPage);
  const exportVisible = await targetPage.locator('text=全部导出').first().isVisible({ timeout: 3000 }).catch(() => false);
  if (!exportVisible) {
    const screenshotPath = path.join(outputDir, `orders-export-dialog-not-ready-${new Date().toISOString().replace(/[:.]/g, '-')}.png`);
    await targetPage.screenshot({ path: screenshotPath, fullPage: true }).catch(() => null);
    throw new Error(`Orders page did not expose a visible 全部导出 control after fixed wait. Screenshot: ${screenshotPath}`);
  }
}

async function bodyText(targetPage) {
  return targetPage.locator('body').innerText({ timeout: 5000 }).catch(() => '');
}

function diffSuffix(before, after) {
  if (!before || !after) {
    return after || '';
  }
  const index = after.indexOf(before.slice(0, 200));
  return index >= 0 && after.length > before.length ? after.slice(before.length) : after;
}
