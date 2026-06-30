import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const profileDir = path.join(projectRoot, 'data', 'browser-profile');
const outputDir = path.join(projectRoot, 'data', 'raw', 'calibration');

const STORE_URL = 'https://store.weixin.qq.com/';
const ORDER_NAV_SELECTOR = 'a[href="/shop/order/list"]';
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
  await page.waitForTimeout(2000);
  logStage('logged_in_shell', { url: page.url() });

  const currentShop = await page
    .locator(CURRENT_SHOP_SELECTOR)
    .first()
    .innerText({ timeout: 5000 })
    .catch(() => '');
  logStage('current_shop', { currentShop: clean(currentShop) });

  logStage('click_orders_navigation', { selector: ORDER_NAV_SELECTOR });
  const clicked = await clickOrdersNavigation(page);

  if (!clicked) {
    throw new Error(`Orders navigation selector not found: ${ORDER_NAV_SELECTOR}`);
  }

  await waitForPageStability(page);
  await waitForOrdersContent(page, 30000);

  const result = await readPageAndFrameCandidates(page);
  const outputPath = path.join(outputDir, `orders-page-candidates-${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
  const screenshotPath = outputPath.replace(/\.json$/, '.png');
  await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => null);

  await fs.writeFile(
    outputPath,
    JSON.stringify(
      {
        currentShop: clean(currentShop),
        pageUrl: page.url(),
        screenshotPath,
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
        screenshotPath,
        currentShop: clean(currentShop),
        pageUrl: page.url(),
        frameCount: result.frames.length,
        candidateCount: result.candidates.length,
        tableCount: result.tables.length,
        candidates: result.candidates.slice(0, 80).map((candidate, index) => ({
          index,
          frameName: candidate.frameName,
          frameUrl: candidate.frameUrl,
          tag: candidate.tag,
          text: candidate.text,
          placeholder: candidate.placeholder,
          role: candidate.role,
          selector: candidate.selector,
          rect: candidate.rect
        })),
        tables: result.tables.slice(0, 10).map((table, index) => ({
          index,
          frameName: table.frameName,
          frameUrl: table.frameUrl,
          headers: table.headers,
          sampleRows: table.sampleRows
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
        !text.includes('登录超时') &&
        !text.includes('请先登录')
      );
    },
    null,
    { timeout: 600000 }
  );
}

async function waitForPageStability(targetPage) {
  await targetPage.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => {});
  await targetPage.waitForLoadState('load', { timeout: 15000 }).catch(() => {});
  await targetPage.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
}

async function clickOrdersNavigation(targetPage) {
  const visibleMenu = targetPage.locator('text=订单/配送').last();
  if (await visibleMenu.isVisible({ timeout: 5000 }).catch(() => false)) {
    await visibleMenu.click({ timeout: 10000 }).catch(() => {});
    await targetPage.waitForTimeout(1000);
  }

  const visibleOrderLink = targetPage.locator('a:has-text("订单管理")').filter({ hasText: '订单管理' }).first();
  if (await visibleOrderLink.isVisible({ timeout: 3000 }).catch(() => false)) {
    await visibleOrderLink.click({ timeout: 10000 });
    return true;
  }

  return targetPage.evaluate((selector) => {
    const entry = document.querySelector(selector);
    if (!entry) {
      return false;
    }
    entry.click();
    return true;
  }, ORDER_NAV_SELECTOR);
}

async function waitForOrdersContent(targetPage, timeoutMs) {
  const startedAt = Date.now();
  let lastText = '';
  while (Date.now() - startedAt < timeoutMs) {
    lastText = await targetPage.locator('body').innerText({ timeout: 2000 }).catch(() => '');
    const frameTexts = await Promise.all(targetPage.frames().map((frame) => frame.locator('body').innerText({ timeout: 1000 }).catch(() => '')));
    const haystack = [lastText, ...frameTexts].join('\n');
    if (/订单编号|订单号|全部导出|导出|订单状态|下单时间|付款时间|订单金额|暂无订单|订单列表|收货人/i.test(haystack)) {
      return;
    }
    await targetPage.waitForTimeout(1000);
  }
  logStage('orders_content_wait_timeout', { bodyText: clean(lastText).slice(0, 500), url: targetPage.url() });
}

async function readPageAndFrameCandidates(targetPage) {
  const frameResults = [];
  for (const frame of targetPage.frames()) {
    try {
      const frameResult = await frame.evaluate(readCandidatesInDocument);
      frameResults.push({
        frameName: frame.name(),
        frameUrl: frame.url(),
        ...frameResult
      });
    } catch (error) {
      frameResults.push({
        frameName: frame.name(),
        frameUrl: frame.url(),
        error: error.message,
        bodyText: '',
        candidates: [],
        tables: []
      });
    }
  }

  const candidates = frameResults.flatMap((frameResult) => frameResult.candidates.map((candidate) => ({
    ...candidate,
    frameName: frameResult.frameName,
    frameUrl: frameResult.frameUrl
  })));
  const tables = frameResults.flatMap((frameResult) => frameResult.tables.map((table) => ({
    ...table,
    frameName: frameResult.frameName,
    frameUrl: frameResult.frameUrl
  })));

  return {
    title: await targetPage.title().catch(() => ''),
    bodyText: frameResults.map((item) => item.bodyText).filter(Boolean).join('\n\n--- frame ---\n\n').slice(0, 6000),
    frames: frameResults.map((item) => ({
      frameName: item.frameName,
      frameUrl: item.frameUrl,
      error: item.error ?? null,
      bodyText: item.bodyText.slice(0, 1200),
      candidateCount: item.candidates.length,
      tableCount: item.tables.length
    })),
    candidates,
    tables
  };
}

function readCandidatesInDocument() {
  const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
  const attrs = (element) => Object.fromEntries([...element.attributes].map((attr) => [attr.name, attr.value]).slice(0, 50));
  const selectorOf = (element) => {
    if (element.id) {
      return `#${CSS.escape(element.id)}`;
    }

    const text = clean(element.innerText || element.textContent);
    const tag = element.tagName.toLowerCase();
    const classList = typeof element.className === 'string' ? element.className.split(/\s+/).filter(Boolean) : [];
    const placeholder = clean(element.getAttribute('placeholder'));
    const escapedText = text.slice(0, 30).replace(/"/g, '\\"');
    const escapedPlaceholder = placeholder.slice(0, 30).replace(/"/g, '\\"');

    if (placeholder) {
      return `${tag}[placeholder="${escapedPlaceholder}"]`;
    }
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

  const shouldKeep = (element) => {
    const text = clean(element.innerText || element.textContent);
    const placeholder = clean(element.getAttribute('placeholder'));
    const title = clean(element.getAttribute('title'));
    const aria = clean(element.getAttribute('aria-label'));
    const cls = clean(typeof element.className === 'string' ? element.className : '');
    const html = clean(element.outerHTML).slice(0, 800);
    return /导出|下载|全部导出|订单|时间|日期|筛选|查询|搜索|状态|售后|退款|发货|收货|金额|商品|买家|开始|结束|确定|确认|取消|近|自定义|export|download|order|refund|date|search/i.test([
      text,
      placeholder,
      title,
      aria,
      cls,
      html
    ].join(' '));
  };

  const elements = [...document.querySelectorAll([
    'button',
    'a',
    'input',
    'textarea',
    'select',
    '[role="button"]',
    '[role="tab"]',
    '[role="menuitem"]',
    '[class*="export"]',
    '[class*="download"]',
    '[class*="date"]',
    '[class*="picker"]',
    '[class*="search"]',
    '[class*="filter"]',
    '[class*="order"]',
    '[class*="table"]',
    '[class*="pagination"]'
  ].join(','))];

  const candidates = elements
    .filter(shouldKeep)
    .map((element) => {
      const rect = element.getBoundingClientRect();
      return {
        tag: element.tagName.toLowerCase(),
        text: clean(element.innerText || element.textContent).slice(0, 220),
        placeholder: clean(element.getAttribute('placeholder')),
        role: clean(element.getAttribute('role')),
        title: clean(element.getAttribute('title')),
        aria: clean(element.getAttribute('aria-label')),
        type: clean(element.getAttribute('type')),
        cls: clean(typeof element.className === 'string' ? element.className : '').slice(0, 220),
        href: clean(element.getAttribute('href')),
        selector: selectorOf(element),
        rect: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          w: Math.round(rect.width),
          h: Math.round(rect.height)
        },
        attrs: attrs(element),
        outerHTML: clean(element.outerHTML).slice(0, 1000)
      };
    })
    .filter((candidate) => candidate.text || candidate.placeholder || candidate.title || candidate.aria || candidate.href)
    .slice(0, 300);

  const tables = [...document.querySelectorAll('table,[role="table"],[class*="table"]')]
    .map((table) => {
      const headers = [...table.querySelectorAll('th,[role="columnheader"],.table-header,.thead')]
        .map((cell) => clean(cell.innerText || cell.textContent))
        .filter(Boolean)
        .slice(0, 40);
      const rows = [...table.querySelectorAll('tr,[role="row"]')]
        .slice(0, 5)
        .map((row) => [...row.querySelectorAll('td,[role="cell"],th,[role="columnheader"]')]
          .map((cell) => clean(cell.innerText || cell.textContent))
          .filter(Boolean)
          .slice(0, 20))
        .filter((row) => row.length > 0);

      return {
        tag: table.tagName.toLowerCase(),
        cls: clean(typeof table.className === 'string' ? table.className : '').slice(0, 220),
        headers,
        sampleRows: rows,
        text: clean(table.innerText || table.textContent).slice(0, 1500)
      };
    })
    .filter((table) => table.headers.length || table.sampleRows.length || table.text)
    .slice(0, 30);

  return {
    url: location.href,
    title: document.title,
    bodyText: clean(document.body.innerText).slice(0, 3000),
    candidates,
    tables
  };
}

function clean(value) {
  return String(value ?? '').replace(/\s+/g, ' ').trim();
}

function logStage(stage, fields = {}) {
  console.log(JSON.stringify({ stage, ...fields }, null, 2));
}
