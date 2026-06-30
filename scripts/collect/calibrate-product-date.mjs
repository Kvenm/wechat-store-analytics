import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const profileDir = path.join(projectRoot, 'data', 'browser-profile');
const outputDir = path.join(projectRoot, 'data', 'raw', 'calibration');

const STORE_URL = 'https://store.weixin.qq.com/';
const PRODUCT_NAV_SELECTOR = 'a[href="/shop/statistics/product"]';
const PRODUCT_FRAME_URL = '/compass/embed/channels-shop-product';
const CURRENT_SHOP_SELECTOR = '.shop-info-popover';

await fs.mkdir(outputDir, { recursive: true });

const context = await chromium.launchPersistentContext(profileDir, {
  headless: false,
  acceptDownloads: false,
  viewport: { width: 1440, height: 1000 },
  args: ['--disable-crash-reporter']
});

const page = context.pages()[0] ?? await context.newPage();

try {
  logStage('open_store_home', { url: STORE_URL });
  await page.goto(STORE_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await waitForLoggedInShell(page);

  const currentShop = await page
    .locator(CURRENT_SHOP_SELECTOR)
    .first()
    .innerText({ timeout: 5000 })
    .catch(() => '');
  logStage('current_shop', { currentShop: clean(currentShop) });

  await clickAnchorByHref(page, PRODUCT_NAV_SELECTOR);
  await page.waitForTimeout(8000);

  const frame = page.frames().find((item) => item.url().includes(PRODUCT_FRAME_URL));
  if (!frame) {
    throw new Error(`Product analytics frame not found. Frames: ${page.frames().map((item) => item.url()).join(' | ')}`);
  }
  await frame.locator('text=核心转化概览').first().waitFor({ timeout: 30000 });
  logStage('product_page_ready', { frameUrl: frame.url() });

  const before = await readDateCandidates(frame);
  const clicked = await clickDateDropdown(frame);
  await frame.page().waitForTimeout(1500);
  const after = await readDateCandidates(frame);

  const outputPath = path.join(outputDir, `product-date-candidates-${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
  await fs.writeFile(
    outputPath,
    JSON.stringify(
      {
        currentShop: clean(currentShop),
        pageUrl: page.url(),
        frameUrl: frame.url(),
        clicked,
        before,
        after
      },
      null,
      2
    ),
    'utf8'
  );

  console.log(JSON.stringify({ outputPath, currentShop: clean(currentShop), clicked, beforeCount: before.candidates.length, afterCount: after.candidates.length }, null, 2));
} finally {
  await context.close();
}

async function waitForLoggedInShell(targetPage) {
  await targetPage.waitForFunction(
    () => {
      const text = document.body?.innerText ?? '';
      return text.includes('首页')
        && text.includes('商品管理')
        && text.includes('订单/配送')
        && !text.includes('扫码进入我的小店')
        && !text.includes('登录超时');
    },
    null,
    { timeout: 600000 }
  );
}

async function clickAnchorByHref(targetPage, selector) {
  const clicked = await targetPage.evaluate((targetSelector) => {
    const entry = document.querySelector(targetSelector);
    if (!entry) {
      return false;
    }
    entry.click();
    return true;
  }, selector);
  if (!clicked) {
    throw new Error(`Navigation selector not found: ${selector}`);
  }
}

async function clickDateDropdown(frame) {
  const selectors = [
    '.time-range-dropdown',
    '.filter-dropdown:has-text("时间范围")',
    'text=近7天'
  ];
  for (const selector of selectors) {
    const locator = frame.locator(selector).first();
    if (await locator.count().catch(() => 0)) {
      await locator.click({ timeout: 5000 }).catch(async () => {
        await locator.evaluate((node) => node.click());
      });
      return selector;
    }
  }
  throw new Error('No date dropdown candidate matched.');
}

async function readDateCandidates(frame) {
  return frame.evaluate(() => {
    const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
    const attrs = (element) => Object.fromEntries([...element.attributes].map((attr) => [attr.name, attr.value]).slice(0, 40));
    const selectorOf = (element) => {
      if (element.id) {
        return `#${CSS.escape(element.id)}`;
      }
      const text = clean(element.innerText || element.textContent);
      const tag = element.tagName.toLowerCase();
      const classList = typeof element.className === 'string' ? element.className.split(/\s+/).filter(Boolean) : [];
      const escapedText = text.slice(0, 30).replace(/"/g, '\\"');
      if ((tag === 'button' || tag === 'a') && text) {
        return `${tag}:has-text("${escapedText}")`;
      }
      if (element.getAttribute('role') === 'button' && text) {
        return `[role="button"]:has-text("${escapedText}")`;
      }
      if (classList.length && text) {
        return `.${CSS.escape(classList[0])}:has-text("${escapedText}")`;
      }
      const placeholder = element.getAttribute('placeholder');
      if (placeholder) {
        return `${tag}[placeholder="${placeholder.replace(/"/g, '\\"')}"]`;
      }
      return text ? `text=${text.slice(0, 30)}` : '';
    };

    const candidates = [];
    const elements = [...document.querySelectorAll('button,a,input,[role=button],div,span,li')];
    for (const element of elements) {
      const text = clean(element.innerText || element.textContent || element.getAttribute('aria-label') || element.getAttribute('title'));
      const placeholder = clean(element.getAttribute('placeholder'));
      const cls = clean(typeof element.className === 'string' ? element.className : '');
      const html = clean(element.outerHTML).slice(0, 600);
      if (!/时间范围|近7天|近30天|昨日|今天|自定义|开始日期|结束日期|确定|取消|date|time|picker/i.test([text, placeholder, cls, html].join(' '))) {
        continue;
      }
      const rect = element.getBoundingClientRect();
      candidates.push({
        tag: element.tagName.toLowerCase(),
        text,
        placeholder,
        role: clean(element.getAttribute('role')),
        title: clean(element.getAttribute('title')),
        aria: clean(element.getAttribute('aria-label')),
        cls,
        selector: selectorOf(element),
        visible: rect.width > 0 && rect.height > 0,
        rect: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          w: Math.round(rect.width),
          h: Math.round(rect.height)
        },
        attrs: attrs(element),
        outerHTML: html
      });
    }
    return { bodyText: clean(document.body.innerText).slice(0, 1200), candidates };
  });
}

function clean(value) {
  return String(value ?? '').replace(/\s+/g, ' ').trim();
}

function logStage(stage, fields = {}) {
  console.log(JSON.stringify({ stage, ...fields }, null, 2));
}
