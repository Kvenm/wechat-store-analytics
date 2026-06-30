import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const profileDir = path.join(projectRoot, 'data', 'browser-profile');
const outputDir = path.join(projectRoot, 'data', 'raw', 'calibration');

const targets = [
  { type: 'orders', href: '/shop/order/list' },
  { type: 'products', href: '/shop/goods/list' },
  { type: 'compass', href: '/shop-faas/mmecnodecompasscommon/thirdParty/shop/loginCompassByShop' }
];

await fs.mkdir(outputDir, { recursive: true });

const context = await chromium.launchPersistentContext(profileDir, {
  headless: false,
  acceptDownloads: false,
  viewport: { width: 1440, height: 1000 },
  args: ['--disable-crash-reporter']
});

const page = context.pages()[0] ?? await context.newPage();
const snapshots = [];

try {
  await ensureHome();
  snapshots.push({ target: 'home', ...(await readPage()) });

  for (const target of targets) {
    await ensureHome();
    const clicked = await page.evaluate((href) => {
      const entry = [...document.querySelectorAll('a')].find((node) => node.getAttribute('href') === href);
      if (!entry) {
        return false;
      }
      entry.click();
      return true;
    }, target.href);
    await page.waitForTimeout(8000);
    snapshots.push({ target: target.type, clicked, ...(await readPage()) });
  }
} finally {
  const outputPath = path.join(outputDir, `pages-${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
  await fs.writeFile(outputPath, JSON.stringify(snapshots, null, 2), 'utf8');
  console.log(JSON.stringify({ outputPath, snapshots: snapshots.map((item) => ({
    target: item.target,
    clicked: item.clicked ?? null,
    url: item.url,
    bodyText: item.bodyText.slice(0, 300)
  })) }, null, 2));
  await context.close();
}

async function ensureHome() {
  await page.goto('https://store.weixin.qq.com/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3000);
  const initialText = await page.locator('body').innerText({ timeout: 5000 }).catch(() => '');
  console.log(JSON.stringify({
    stage: 'home_check',
    url: page.url(),
    text: clean(initialText).slice(0, 200)
  }));
  await page.waitForFunction(() => {
    const text = document.body?.innerText ?? '';
    return text.includes('首页')
      && text.includes('商品管理')
      && text.includes('订单/配送')
      && !text.includes('扫码进入我的小店')
      && !text.includes('登录超时');
  }, null, { timeout: 600000 });
  await page.waitForTimeout(3000);
}

async function readPage() {
  return page.evaluate(() => {
    const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
    const selectorOf = (element) => {
      if (element.id) {
        return `#${CSS.escape(element.id)}`;
      }
      const href = element.getAttribute('href');
      if (element.tagName.toLowerCase() === 'a' && href) {
        return `a[href="${href.replace(/"/g, '\\"')}"]`;
      }
      const text = clean(element.innerText || element.textContent);
      if (element.tagName.toLowerCase() === 'button' && text) {
        return `button:has-text("${text.slice(0, 30).replace(/"/g, '\\"')}")`;
      }
      const placeholder = element.getAttribute('placeholder');
      if (placeholder) {
        return `${element.tagName.toLowerCase()}[placeholder="${placeholder.replace(/"/g, '\\"')}"]`;
      }
      return '';
    };
    const pick = (element, index) => ({
      index,
      tag: element.tagName.toLowerCase(),
      text: clean(element.innerText || element.textContent).slice(0, 160),
      type: clean(element.getAttribute('type')),
      placeholder: clean(element.getAttribute('placeholder')),
      title: clean(element.getAttribute('title')),
      aria: clean(element.getAttribute('aria-label')),
      role: clean(element.getAttribute('role')),
      cls: clean(typeof element.className === 'string' ? element.className : '').slice(0, 180),
      href: clean(element.getAttribute('href')),
      selector: selectorOf(element)
    });

    const elements = [...document.querySelectorAll([
      'button',
      'a',
      'input',
      'textarea',
      'select',
      '[role="button"]',
      '[role="tab"]',
      '[class*="export"]',
      '[class*="download"]',
      '[class*="date"]',
      '[class*="picker"]',
      '[class*="weui-desktop-form"]',
      '[class*="weui-desktop-btn"]',
      '[class*="table"]',
      '[class*="pagination"]'
    ].join(','))];
    const controls = elements
      .map((element, index) => pick(element, index))
      .filter((item) => item.text || item.placeholder || item.title || item.aria || item.href);
    const likely = controls.filter((item) => /导出|下载|时间|日期|订单|商品|筛选|查询|全部|近|自定义|开始|结束|确定|确认|取消|评价|数据|报表|export|download/i.test([
      item.text,
      item.placeholder,
      item.title,
      item.aria,
      item.cls
    ].join(' '))).slice(0, 180);

    return {
      title: document.title,
      url: location.href,
      bodyText: clean(document.body.innerText).slice(0, 3000),
      likely,
      controls: controls.slice(0, 260)
    };
  });
}

function clean(value) {
  return String(value ?? '').replace(/\s+/g, ' ').trim();
}
