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
  logStage('logged_in_shell', { url: page.url() });

  const currentShop = await page
    .locator(CURRENT_SHOP_SELECTOR)
    .first()
    .innerText({ timeout: 5000 })
    .catch(() => '');
  logStage('current_shop', { currentShop: clean(currentShop) });

  logStage('click_product_navigation', { selector: PRODUCT_NAV_SELECTOR });
  const clicked = await page.evaluate((selector) => {
    const entry = document.querySelector(selector);
    if (!entry) {
      return false;
    }
    entry.click();
    return true;
  }, PRODUCT_NAV_SELECTOR);
  if (!clicked) {
    throw new Error(`Product navigation selector not found: ${PRODUCT_NAV_SELECTOR}`);
  }
  await page.waitForTimeout(8000);

  let frame = page.frames().find((item) => item.url().includes(PRODUCT_FRAME_URL));
  if (!frame) {
    logStage('product_frame_not_found_after_click', { pageUrl: page.url(), frameUrls: page.frames().map((item) => item.url()) });
    await page.goto(new URL('/shop/statistics/product', STORE_URL).toString(), { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(8000);
    frame = page.frames().find((item) => item.url().includes(PRODUCT_FRAME_URL));
  }
  if (!frame) {
    throw new Error(`Product analytics frame not found. Frames: ${page.frames().map((item) => item.url()).join(' | ')}`);
  }

  logStage('product_frame_found', { pageUrl: page.url(), frameUrl: frame.url() });
  await frame.locator('text=核心转化概览').first().waitFor({ timeout: 30000 });
  logStage('product_page_ready', { frameUrl: frame.url() });
  const result = await readDownloadCandidates(frame);
  const outputPath = path.join(outputDir, `product-download-candidates-${new Date().toISOString().replace(/[:.]/g, '-')}.json`);

  await fs.writeFile(
    outputPath,
    JSON.stringify(
      {
        currentShop: clean(currentShop),
        pageUrl: page.url(),
        frameUrl: frame.url(),
        ...result
      },
      null,
      2
    ),
    'utf8'
  );

  console.log(
    JSON.stringify(
      {
        outputPath,
        currentShop: clean(currentShop),
        pageUrl: page.url(),
        frameUrl: frame.url(),
        candidateCount: result.candidates.length,
        candidates: result.candidates.map((candidate, index) => ({
          index,
          tag: candidate.tag,
          text: candidate.text,
          role: candidate.role,
          cls: candidate.cls,
          selector: candidate.selector,
          rect: candidate.rect
        }))
      },
      null,
      2
    )
  );
} finally {
  await context.close();
}

async function waitForLoggedInShell(targetPage) {
  await targetPage.waitForFunction(
    () => {
      const text = document.body?.innerText ?? '';
      return (
        text.includes('首页') &&
        text.includes('商品管理') &&
        text.includes('订单/配送') &&
        !text.includes('扫码进入我的小店') &&
        !text.includes('登录超时')
      );
    },
    null,
    { timeout: 600000 }
  );
}

async function readDownloadCandidates(frame) {
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
      return text ? `text=${text.slice(0, 30)}` : '';
    };

    const candidates = [];
    const elements = [...document.querySelectorAll('button,a,[role=button],div,span,i')];
    for (const element of elements) {
      const text = clean(element.innerText || element.textContent || element.getAttribute('aria-label') || element.getAttribute('title'));
      const cls = clean(typeof element.className === 'string' ? element.className : '');
      const html = clean(element.outerHTML).slice(0, 600);

      if (!/下载数据|导出|download|export/i.test([text, cls, html].join(' '))) {
        continue;
      }

      const rect = element.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) {
        continue;
      }

      candidates.push({
        tag: element.tagName.toLowerCase(),
        text,
        role: clean(element.getAttribute('role')),
        title: clean(element.getAttribute('title')),
        aria: clean(element.getAttribute('aria-label')),
        cls,
        href: clean(element.getAttribute('href')),
        selector: selectorOf(element),
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

    return {
      url: location.href,
      title: document.title,
      bodyText: clean(document.body.innerText).slice(0, 1000),
      candidates
    };
  });
}

function clean(value) {
  return String(value ?? '').replace(/\s+/g, ' ').trim();
}

function logStage(stage, fields = {}) {
  console.log(JSON.stringify({ stage, ...fields }, null, 2));
}
