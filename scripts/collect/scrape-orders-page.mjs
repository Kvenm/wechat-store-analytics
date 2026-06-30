#!/usr/bin/env node
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const profileDir = path.join(projectRoot, 'data', 'browser-profile');
const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
const outputDir = path.join(projectRoot, 'data', 'raw', 'manual-orders', timestamp);

const STORE_URL = 'https://store.weixin.qq.com/';
const ORDER_NAV_SELECTOR = 'a[href="/shop/order/list"]';
const CURRENT_SHOP_SELECTOR = '.shop-info-popover';
const OUTPUT_JSON = path.join(outputDir, 'orders_visible_page.json');
const OUTPUT_CSV = path.join(outputDir, 'orders_visible_page.csv');
const DEBUG_JSON = path.join(outputDir, 'debug.json');
const SCREENSHOT = path.join(outputDir, 'screenshot.png');

const context = await chromium.launchPersistentContext(profileDir, {
  headless: false,
  acceptDownloads: false,
  viewport: { width: 1440, height: 1000 },
  args: ['--disable-crash-reporter']
});

const page = context.pages()[0] ?? await context.newPage();
let lastDebug = {};
let currentShop = '';

try {
  await fs.mkdir(outputDir, { recursive: true });

  logStage('open_store_home', { url: STORE_URL, outputDir });
  await page.goto(STORE_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });

  logStage('wait_for_logged_in_shell');
  await waitForLoggedInShell(page, Number(process.env.WECHAT_STORE_LOGIN_TIMEOUT_MS ?? 30000));
  await waitForPageStability(page);
  logStage('logged_in_shell', { url: page.url() });

  currentShop = await page
    .locator(CURRENT_SHOP_SELECTOR)
    .first()
    .innerText({ timeout: 5000 })
    .then(cleanSingleLine)
    .catch(() => '');
  logStage('current_shop', { currentShop });

  logStage('enter_orders_page');
  await enterOrdersPage(page);
  await waitForPageStability(page);
  logStage('orders_page_navigation_done', { url: page.url() });

  logStage('wait_for_visible_order_list');
  await waitForVisibleOrderList(page, 60000);

  logStage('read_visible_rows');
  const snapshot = await readVisibleOrdersSnapshot(page);
  lastDebug = snapshot.debug;

  const rows = normalizeRows(snapshot.rows);
  await writeOrdersArtifacts(rows, {
    currentShop,
    pageUrl: page.url(),
    title: await page.title().catch(() => ''),
    capturedAt: new Date().toISOString(),
    outputDir,
    rowCount: rows.length
  });

  await saveDebugAndScreenshot(page, {
    ok: true,
    currentShop,
    pageUrl: page.url(),
    rowCount: rows.length,
    debug: lastDebug
  });

  logStage('saved_orders_visible_page', {
    rowCount: rows.length,
    csv: OUTPUT_CSV,
    json: OUTPUT_JSON,
    debug: DEBUG_JSON,
    screenshot: SCREENSHOT
  });
} catch (error) {
  const isTimeout = /Timeout|timed out|timeout/i.test(String(error?.message ?? error));
  logStage('capture_failed', {
    error: String(error?.message ?? error),
    timeout: isTimeout
  });

  await saveDebugAndScreenshot(page, {
    ok: false,
    currentShop,
    pageUrl: page.url(),
    error: String(error?.message ?? error),
    stack: process.env.DEBUG ? error?.stack : undefined,
    timeout: isTimeout,
    debug: lastDebug || {}
  });

  process.exitCode = 1;
} finally {
  await context.close().catch(() => {});
}

async function waitForLoggedInShell(targetPage, timeoutMs) {
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
    { timeout: timeoutMs }
  );
}

async function waitForPageStability(targetPage) {
  await targetPage.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => {});
  await targetPage.waitForLoadState('load', { timeout: 15000 }).catch(() => {});
  await targetPage.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
}

async function enterOrdersPage(targetPage) {
  if (/\/shop\/order\/list(?:[/?#]|$)/.test(targetPage.url())) {
    logStage('already_on_orders_page', { url: targetPage.url() });
    return;
  }

  const clickedOrderDelivery = await clickAllowedNavigationEntry(targetPage, '订单/配送');
  if (clickedOrderDelivery) {
    await targetPage.waitForTimeout(800);
  }

  const clickedOrderManagement = await clickAllowedNavigationEntry(targetPage, '订单管理');
  if (clickedOrderManagement) {
    await targetPage.waitForTimeout(2500);
    if (/\/shop\/order\/list(?:[/?#]|$)/.test(targetPage.url())) {
      return;
    }
    const hasOrderList = await hasOrderListText(targetPage);
    if (hasOrderList) {
      return;
    }
    logStage('order_management_click_no_navigation', { url: targetPage.url() });
  }

  const clickedHref = await clickAllowedOrderHref(targetPage);

  if (clickedHref) {
    logStage('click_allowed_left_nav', { selector: ORDER_NAV_SELECTOR });
    await targetPage.waitForTimeout(2500);
    if (/\/shop\/order\/list(?:[/?#]|$)/.test(targetPage.url()) || await hasOrderListText(targetPage)) {
      return;
    }
    logStage('order_href_click_no_navigation', { url: targetPage.url() });
  }

  const directNavigated = await directNavigateToOrdersFromShell(targetPage);
  if (directNavigated) {
    return;
  }

  throw new Error('未找到左侧订单管理入口，未执行其他页面按钮点击');
}

async function hasOrderListText(targetPage) {
  return targetPage.locator('body').innerText({ timeout: 2000 })
    .then((text) => /全部导出|订单号|订单编号|下单时间|订单状态/.test(text))
    .catch(() => false);
}

async function directNavigateToOrdersFromShell(targetPage) {
  logStage('direct_navigate_orders_page', { url: '/shop/order/list' });
  await targetPage.goto(new URL('/shop/order/list', STORE_URL).toString(), { waitUntil: 'domcontentloaded', timeout: 60000 });
  await targetPage.waitForTimeout(8000);
  return /\/shop\/order\/list(?:[/?#]|$)/.test(targetPage.url()) || await hasOrderListText(targetPage);
}

async function clickAllowedNavigationEntry(targetPage, label) {
  const clicked = await targetPage.evaluate((targetLabel) => {
    const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
    const isVisible = (element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };
    const isAllowedNavigationElement = (element) => {
      const href = element.closest('a')?.getAttribute('href') ?? '';
      if (href === '/shop/order/list' || href.includes('/shop/order/list')) {
        return true;
      }

      const navRoot = element.closest('aside, nav, [role="navigation"], [class*="menu"], [class*="Menu"], [class*="side"], [class*="Side"], [class*="sider"], [class*="Sider"], [class*="sidebar"], [class*="Sidebar"], [class*="left"], [class*="Left"]');
      if (!navRoot) {
        return false;
      }

      const rect = navRoot.getBoundingClientRect();
      return rect.width > 0 && rect.width <= Math.max(420, window.innerWidth * 0.45) && rect.left < window.innerWidth * 0.55;
    };

    const candidates = [...document.querySelectorAll('a,button,[role="menuitem"],[role="button"],span,div')]
      .filter((element) => clean(element.innerText || element.textContent) === targetLabel)
      .filter((element) => isVisible(element) && isAllowedNavigationElement(element));

    const candidate = candidates.at(-1);
    if (!candidate) {
      return false;
    }

    candidate.click();
    return true;
  }, label);

  if (clicked) {
    logStage('click_allowed_left_nav', { label });
  }

  return clicked;
}

async function clickAllowedOrderHref(targetPage) {
  return targetPage.evaluate((selector) => {
    const entry = [...document.querySelectorAll(selector)].find((element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const navRoot = element.closest('aside, nav, [role="navigation"], [class*="menu"], [class*="Menu"], [class*="side"], [class*="Side"], [class*="sider"], [class*="Sider"], [class*="sidebar"], [class*="Sidebar"], [class*="left"], [class*="Left"]');
      const navRect = navRoot?.getBoundingClientRect();
      const isLeftNavigation = navRect && navRect.width > 0 && navRect.width <= Math.max(420, window.innerWidth * 0.45) && navRect.left < window.innerWidth * 0.55;

      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0 && isLeftNavigation;
    });
    if (!entry) {
      return false;
    }
    entry.click();
    return true;
  }, ORDER_NAV_SELECTOR);
}

async function waitForVisibleOrderList(targetPage, timeoutMs) {
  const startedAt = Date.now();
  let lastSnapshot = null;

  while (Date.now() - startedAt < timeoutMs) {
    lastSnapshot = await readVisibleOrdersSnapshot(targetPage);
    lastDebug = lastSnapshot.debug;

    if (lastSnapshot.rows.length > 0 || lastSnapshot.debug.hasEmptyOrderState || lastSnapshot.debug.hasOrderListSurface) {
      return;
    }

    await targetPage.waitForTimeout(1000);
  }

  const bodyPreview = lastSnapshot?.debug?.frames?.map((frame) => frame.bodyText).filter(Boolean).join('\n--- frame ---\n').slice(0, 1500);
  throw new Error(`等待可见订单列表超时；页面预览：${bodyPreview || '<empty>'}`);
}

async function readVisibleOrdersSnapshot(targetPage) {
  const frameResults = [];

  for (const frame of targetPage.frames()) {
    try {
      const result = await frame.evaluate(collectVisibleRowsInDocument);
      frameResults.push({
        frameName: frame.name(),
        frameUrl: frame.url(),
        ...result
      });
    } catch (error) {
      frameResults.push({
        frameName: frame.name(),
        frameUrl: frame.url(),
        title: '',
        bodyText: '',
        rows: [],
        hasOrderListSurface: false,
        hasEmptyOrderState: false,
        error: String(error?.message ?? error)
      });
    }
  }

  const rows = frameResults.flatMap((frameResult) =>
    frameResult.rows.map((row) => ({
      ...row,
      frameName: frameResult.frameName,
      frameUrl: frameResult.frameUrl
    }))
  );

  const debug = {
    pageUrl: targetPage.url(),
    title: await targetPage.title().catch(() => ''),
    frameCount: frameResults.length,
    hasOrderListSurface: frameResults.some((frame) => frame.hasOrderListSurface),
    hasEmptyOrderState: frameResults.some((frame) => frame.hasEmptyOrderState),
    frames: frameResults.map((frame) => ({
      frameName: frame.frameName,
      frameUrl: frame.frameUrl,
      title: frame.title,
      rowCandidateCount: frame.rows.length,
      hasOrderListSurface: frame.hasOrderListSurface,
      hasEmptyOrderState: frame.hasEmptyOrderState,
      error: frame.error ?? null,
      bodyText: cleanSingleLine(frame.bodyText).slice(0, 1200)
    }))
  };

  return { rows, debug };
}

function collectVisibleRowsInDocument() {
  const cleanLine = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
  const cleanLines = (value) =>
    String(value ?? '')
      .split(/\n+/)
      .map(cleanLine)
      .filter(Boolean);
  const cleanText = (value) => cleanLines(value).join('\n').trim();
  const rectOf = (element) => {
    const rect = element.getBoundingClientRect();
    return {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      w: Math.round(rect.width),
      h: Math.round(rect.height)
    };
  };
  const collectRoots = (root) => {
    const roots = [root];
    const visit = (currentRoot) => {
      for (const element of [...(currentRoot.children ?? [])]) {
        if (element.shadowRoot) {
          roots.push(element.shadowRoot);
          visit(element.shadowRoot);
        }
        visit(element);
      }
    };
    visit(root);
    return roots;
  };
  const allRoots = collectRoots(document.body);
  const queryAllAcrossRoots = (selectorValue) => {
    const results = [];
    const seen = new Set();
    for (const root of allRoots) {
      for (const element of [...root.querySelectorAll(selectorValue)]) {
        if (!seen.has(element)) {
          seen.add(element);
          results.push(element);
        }
      }
    }
    return results;
  };
  const readTextAcrossRoots = () => allRoots
    .map((root) => cleanText(root.innerText || root.textContent || ''))
    .filter(Boolean)
    .join('\n');
  const isVisible = (element) => {
    if (!element || !(element instanceof Element)) {
      return false;
    }
    const style = window.getComputedStyle(element);
    if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0) {
      return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && rect.bottom >= 0 && rect.top <= window.innerHeight;
  };
  const orderIdCount = (text) => (text.match(/订单(?:编号|号)\s*[:：#]?\s*\d{8,}/g) ?? []).length;
  const scoreText = (text, element) => {
    let score = 0;
    const compact = cleanLine(text);
    if (/订单(?:编号|号)\s*[:：#]?\s*\d{8,}/.test(compact)) score += 5;
    if (/20\d{2}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?/.test(compact)) score += 3;
    if (/[¥￥]\s*\d|(?:实付|应付|订单金额|金额|合计|总计|支付金额|付款金额)/.test(compact)) score += 2;
    if (/待付款|待支付|待发货|待收货|待评价|已发货|已完成|已关闭|已取消|售后中|退款中|退款成功|已退款|交易成功|交易关闭/.test(compact)) score += 2;
    if (/商品|买家|收货人|收件人|客户|顾客|下单时间|付款时间/.test(compact)) score += 1;
    if (/^(TR|tr)$/i.test(element.tagName) || element.getAttribute('role') === 'row') score += 1;
    return score;
  };
  const selector = [
    'tbody tr',
    '[role="row"]',
    '[data-row-key]',
    '[class*="table-row"]',
    '[class*="TableRow"]',
    '[class*="order"]',
    '[class*="Order"]',
    '[class*="list-item"]',
    '[class*="ListItem"]',
    '[class*="row"]',
    '[class*="card"]'
  ].join(',');

  const elements = queryAllAcrossRoots(selector).filter(isVisible);
  const candidates = elements
    .map((element) => {
      const rawText = cleanText(element.innerText || element.textContent);
      const text = cleanLine(rawText);
      const score = scoreText(rawText, element);
      return {
        rawText,
        lines: cleanLines(rawText),
        score,
        orderIdCount: orderIdCount(text),
        rect: rectOf(element),
        tag: element.tagName.toLowerCase(),
        role: element.getAttribute('role') || '',
        className: typeof element.className === 'string' ? cleanLine(element.className).slice(0, 200) : ''
      };
    })
    .filter((candidate) => {
      if (candidate.rawText.length < 20 || candidate.rawText.length > 3000) {
        return false;
      }
      if (/首页\s+商品管理\s+订单\/配送/.test(cleanLine(candidate.rawText))) {
        return false;
      }
      return candidate.score >= 5 || (candidate.score >= 4 && candidate.orderIdCount <= 2);
    })
    .sort((a, b) => {
      const aMultiPenalty = a.orderIdCount > 1 ? 5000 : 0;
      const bMultiPenalty = b.orderIdCount > 1 ? 5000 : 0;
      return a.rawText.length + aMultiPenalty - (b.rawText.length + bMultiPenalty);
    });

  const selected = [];
  const seenTexts = new Set();
  for (const candidate of candidates) {
    const normalized = cleanLine(candidate.rawText);
    if (seenTexts.has(normalized)) {
      continue;
    }
    if (selected.some((item) => cleanLine(item.rawText).includes(normalized))) {
      continue;
    }
    if (candidate.orderIdCount > 1 && selected.some((item) => normalized.includes(cleanLine(item.rawText)))) {
      continue;
    }
    selected.push(candidate);
    seenTexts.add(normalized);
  }

  let rows = selected;
  if (rows.length === 0) {
    rows = splitBodyIntoOrderSegments(readTextAcrossRoots()).map((rawText) => ({
      rawText,
      lines: cleanLines(rawText),
      score: scoreText(rawText, document.body),
      orderIdCount: orderIdCount(rawText),
      rect: { x: 0, y: 0, w: 0, h: 0 },
      tag: 'body-segment',
      role: '',
      className: ''
    }));
  }

  const bodyText = readTextAcrossRoots();
  const bodySingleLine = cleanLine(bodyText);
  return {
    title: document.title,
    bodyText: bodyText.slice(0, 3000),
    hasOrderListSurface: /订单编号|订单号|订单列表|订单状态|下单时间|付款时间|订单金额|收货人/.test(bodySingleLine),
    hasEmptyOrderState: /暂无订单|暂无数据|没有订单/.test(bodySingleLine),
    rows: rows.slice(0, 200)
  };

  function splitBodyIntoOrderSegments(bodyTextValue) {
    const text = cleanLine(bodyTextValue);
    const starts = [...text.matchAll(/订单(?:编号|号)\s*[:：#]?\s*\d{8,}/g)].map((match) => match.index).filter((index) => index !== undefined);
    if (starts.length === 0) {
      return [];
    }

    return starts
      .map((start, index) => text.slice(start, starts[index + 1] ?? text.length).trim())
      .map((segment) => segment.slice(0, 2500))
      .filter((segment) => scoreText(segment, document.body) >= 5);
  }
}

function normalizeRows(rawRows) {
  const rows = [];
  const seen = new Map();

  for (const rawRow of rawRows) {
    const rawText = cleanSingleLine(rawRow.rawText);
    if (!rawText) {
      continue;
    }

    const orderId = extractOrderId(rawText);
    const createdAt = extractCreatedAt(rawText);
    const key = orderId ? `${orderId}|${createdAt}` : rawText;
    const normalized = {
      raw_text: rawText,
      order_id: orderId,
      created_at: createdAt,
      status: extractStatus(rawText),
      amount_candidates_json: extractAmountCandidates(rawText),
      product_text: extractProductText(rawText, rawRow.lines ?? []),
      buyer_text: extractBuyerText(rawText, rawRow.lines ?? []),
      frame_name: rawRow.frameName ?? '',
      frame_url: rawRow.frameUrl ?? '',
      source_tag: rawRow.tag ?? '',
      source_rect: rawRow.rect ?? null
    };

    if (orderId && !createdAt && normalized.amount_candidates_json.length === 0 && !normalized.status) {
      continue;
    }
    if (!orderId && rows.some((row) => row.product_text && normalized.product_text && row.product_text.includes(normalized.product_text.slice(0, 16)))) {
      continue;
    }

    if (seen.has(key)) {
      const existingIndex = seen.get(key);
      if (normalized.raw_text.length > rows[existingIndex].raw_text.length) {
        rows[existingIndex] = { ...normalized, row_index: existingIndex + 1 };
      }
      continue;
    }

    seen.set(key, rows.length);
    rows.push({ ...normalized, row_index: rows.length + 1 });
  }

  return rows.map((row, index) => ({ ...row, row_index: index + 1 }));
}

function extractOrderId(text) {
  const match = text.match(/订单(?:编号|号)\s*[:：#]?\s*([0-9]{8,})/);
  return match?.[1] ?? '';
}

function extractCreatedAt(text) {
  const match = text.match(/20\d{2}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?/);
  return match?.[0] ?? '';
}

function extractStatus(text) {
  const statuses = [
    '售后中',
    '退款成功',
    '退款中',
    '退货退款',
    '已退款',
    '部分退款',
    '待付款',
    '待支付',
    '待发货',
    '已发货',
    '待收货',
    '待评价',
    '已完成',
    '已关闭',
    '已取消',
    '交易成功',
    '交易关闭',
    '异常订单',
    '待核销',
    '已核销'
  ];

  const matches = statuses
    .map((status) => ({ status, index: text.indexOf(status) }))
    .filter((item) => item.index >= 0)
    .sort((a, b) => a.index - b.index);

  return matches[0]?.status ?? '';
}

function extractAmountCandidates(text) {
  const candidates = [];
  const push = (value) => {
    const normalized = cleanSingleLine(value).replace(/\s+/g, '');
    if (normalized && !candidates.includes(normalized)) {
      candidates.push(normalized);
    }
  };

  for (const match of text.matchAll(/[¥￥]\s*\d{1,7}(?:,\d{3})*(?:\.\d{1,2})?/g)) {
    push(match[0]);
  }

  for (const match of text.matchAll(/(?:实付|应付|订单金额|金额|合计|总计|支付金额|付款金额|商品金额|运费)[^\d¥￥]{0,8}([¥￥]?\s*\d{1,7}(?:,\d{3})*(?:\.\d{1,2})?\s*元?)/g)) {
    push(match[1]);
  }

  for (const match of text.matchAll(/\b\d{1,7}(?:,\d{3})*(?:\.\d{1,2})\s*元\b/g)) {
    push(match[0]);
  }

  return candidates.slice(0, 20);
}

function extractProductText(text, lines) {
  const labelMatch = text.match(/(?:商品信息|商品名称|商品)\s*[:：]?\s*(.{2,160}?)(?=\s*(?:买家|买家昵称|客户|顾客|收货人|收件人|订单|下单时间|付款时间|实付|应付|金额|合计|总计|状态|待付款|待支付|待发货|待收货|已发货|已完成|已关闭|已取消|售后中|退款中|退款成功|$))/);
  if (labelMatch?.[1]) {
    return cleanSingleLine(labelMatch[1]);
  }

  const ignored = /订单(?:编号|号)|下单时间|付款时间|创建时间|订单状态|实付|应付|金额|合计|总计|买家|客户|顾客|收货人|收件人|手机号|电话|待付款|待支付|待发货|待收货|已发货|已完成|已关闭|已取消|售后中|退款中|退款成功|详情|发货|退款|售后|复制|备注|联系/;
  return lines
    .map(cleanSingleLine)
    .filter((line) => line.length >= 3 && line.length <= 120)
    .filter((line) => !ignored.test(line))
    .filter((line) => !/^[\d\s.,¥￥元xX*#:-]+$/.test(line))
    .slice(0, 3)
    .join(' | ');
}

function extractBuyerText(text, lines) {
  const labelMatch = text.match(/(?:买家昵称|买家|客户|顾客|收货人|收件人|用户)\s*[:：]?\s*(.{1,120}?)(?=\s*(?:商品|订单|下单时间|付款时间|实付|应付|金额|合计|总计|状态|待付款|待支付|待发货|待收货|已发货|已完成|已关闭|已取消|售后中|退款中|退款成功|详情|发货|退款|售后|$))/);
  if (labelMatch?.[1]) {
    return cleanSingleLine(labelMatch[1]);
  }

  return lines
    .map(cleanSingleLine)
    .filter((line) => /买家|客户|顾客|收货人|收件人|用户/.test(line))
    .slice(0, 3)
    .join(' | ');
}

async function writeOrdersArtifacts(rows, metadata) {
  const jsonRows = rows.map((row) => ({
    row_index: row.row_index,
    raw_text: row.raw_text,
    order_id: row.order_id,
    created_at: row.created_at,
    status: row.status,
    amount_candidates_json: row.amount_candidates_json,
    product_text: row.product_text,
    buyer_text: row.buyer_text,
    frame_name: row.frame_name,
    frame_url: row.frame_url,
    source_tag: row.source_tag,
    source_rect: row.source_rect
  }));

  await fs.writeFile(
    OUTPUT_JSON,
    JSON.stringify(
      {
        metadata,
        rows: jsonRows
      },
      null,
      2
    ),
    'utf8'
  );

  const csvRows = rows.map((row) => ({
    row_index: row.row_index,
    raw_text: row.raw_text,
    order_id: row.order_id,
    created_at: row.created_at,
    status: row.status,
    amount_candidates_json: JSON.stringify(row.amount_candidates_json),
    product_text: row.product_text,
    buyer_text: row.buyer_text
  }));
  await fs.writeFile(OUTPUT_CSV, toCsv(csvRows), 'utf8');
}

async function saveDebugAndScreenshot(targetPage, payload) {
  await fs.mkdir(outputDir, { recursive: true });
  const debugPayload = {
    capturedAt: new Date().toISOString(),
    screenshot: SCREENSHOT,
    outputs: {
      csv: OUTPUT_CSV,
      json: OUTPUT_JSON,
      debug: DEBUG_JSON
    },
    ...payload
  };

  await fs.writeFile(DEBUG_JSON, JSON.stringify(debugPayload, null, 2), 'utf8').catch(() => {});
  await targetPage.screenshot({ path: SCREENSHOT, fullPage: true }).catch(() => {});
}

function toCsv(rows) {
  const headers = [
    'row_index',
    'raw_text',
    'order_id',
    'created_at',
    'status',
    'amount_candidates_json',
    'product_text',
    'buyer_text'
  ];
  return [
    headers.join(','),
    ...rows.map((row) => headers.map((header) => csvCell(row[header])).join(','))
  ].join('\n') + '\n';
}

function csvCell(value) {
  const text = String(value ?? '');
  if (/[",\n\r]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

function cleanSingleLine(value) {
  return String(value ?? '').replace(/\s+/g, ' ').trim();
}

function logStage(stage, fields = {}) {
  console.log(JSON.stringify({ ts: new Date().toISOString(), stage, ...fields }, null, 2));
}
