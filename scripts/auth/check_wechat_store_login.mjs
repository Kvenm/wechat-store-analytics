#!/usr/bin/env node
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright';

import {
  DEFAULT_CDP_URL,
  STORE_ORIGIN,
  classifyLoginText,
  cleanText
} from '../collect/export-visible-tables-lib.mjs';

const STORE_URL = `${STORE_ORIGIN}/`;
const CDP_CONNECT_TIMEOUT_MS = 3000;
const PAGE_NAVIGATION_TIMEOUT_MS = 8000;
const LOGIN_MARKER_TIMEOUT_MS = 2000;
const LOGIN_MARKER_PATTERN = /扫码进入我的小店|请使用微信扫码|登录超时|请先登录|重新登录|扫码登录/;

const STATE_MESSAGES = Object.freeze({
  logged_in: '微信小店后台已登录，可以开始页面表格导出',
  login_required: '微信小店后台需要扫码登录，请先完成扫码后再导出',
  unknown: '已打开微信小店后台，但暂时无法确认登录状态',
  cdp_unavailable: '无法连接扫码浏览器，请先启动微信小店登录浏览器'
});

export function parseArgs(argv) {
  let cdpUrl = DEFAULT_CDP_URL;

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--cdp-url') {
      index += 1;
      if (index >= argv.length) {
        throw new Error('missing_cdp_url');
      }
      cdpUrl = argv[index];
      continue;
    }
    if (argument.startsWith('--cdp-url=')) {
      cdpUrl = argument.slice('--cdp-url='.length);
      continue;
    }
    throw new Error('unknown_argument');
  }

  const parsed = new URL(String(cdpUrl));
  if (!['http:', 'https:', 'ws:', 'wss:'].includes(parsed.protocol)) {
    throw new Error('invalid_cdp_url');
  }

  return { cdpUrl: parsed.toString() };
}

export function sanitizePageUrl(value) {
  try {
    const parsed = new URL(String(value));
    parsed.username = '';
    parsed.password = '';
    parsed.search = '';
    parsed.hash = '';
    return parsed.toString();
  } catch {
    return null;
  }
}

export function buildResult(state, { url = null, title = null } = {}) {
  const normalizedState = Object.hasOwn(STATE_MESSAGES, state) ? state : 'unknown';
  return {
    state: normalizedState,
    authenticated: normalizedState === 'logged_in',
    url: sanitizePageUrl(url),
    title: cleanText(title).slice(0, 200) || null,
    message: STATE_MESSAGES[normalizedState]
  };
}

export function classifySnapshot({ bodyText, url, title }) {
  const storePage = isWechatStorePage(url);
  const state = storePage ? classifyLoginText(bodyText) : 'unknown';
  return buildResult(state, { url, title });
}

export async function checkWechatStoreLogin({ cdpUrl }) {
  let browser;
  let probePage;

  try {
    browser = await chromium.connectOverCDP(cdpUrl, { timeout: CDP_CONNECT_TIMEOUT_MS });
  } catch {
    return buildResult('cdp_unavailable');
  }

  try {
    const context = browser.contexts()[0];
    if (!context) {
      return buildResult('cdp_unavailable');
    }

    probePage = await context.newPage();
    probePage.setDefaultTimeout(LOGIN_MARKER_TIMEOUT_MS);
    probePage.setDefaultNavigationTimeout(PAGE_NAVIGATION_TIMEOUT_MS);

    await probePage.goto(STORE_URL, {
      waitUntil: 'domcontentloaded',
      timeout: PAGE_NAVIGATION_TIMEOUT_MS
    }).catch(() => null);

    let snapshot = await readMainFrameSnapshot(probePage);
    if (isWechatStorePage(snapshot.url) && classifyLoginText(snapshot.bodyText) === 'unknown') {
      await waitForLoginMarker(probePage);
      snapshot = await readMainFrameSnapshot(probePage);
    }

    return classifySnapshot(snapshot);
  } catch {
    const fallback = probePage ? await readMainFrameSnapshot(probePage).catch(() => null) : null;
    return buildResult('unknown', fallback ?? {});
  } finally {
    // This probe owns only the temporary tab. The shared CDP browser must stay alive.
    if (probePage) {
      await probePage.close({ runBeforeUnload: false }).catch(() => {});
    }
  }
}

async function readMainFrameSnapshot(page) {
  const [bodyText, title] = await Promise.all([
    page.mainFrame().evaluate(() => String(document.body?.innerText ?? '')),
    page.title().catch(() => '')
  ]);
  return {
    bodyText,
    url: page.mainFrame().url(),
    title
  };
}

async function waitForLoginMarker(page) {
  await page.mainFrame().waitForFunction(
    ({ loginPatternSource }) => {
      const text = String(document.body?.innerText ?? '').replace(/\s+/g, ' ');
      const loginRequired = new RegExp(loginPatternSource).test(text);
      const loggedIn = text.includes('首页')
        && text.includes('商品管理')
        && /订单(?:\/|／)?配送|店铺数据|资金结算/.test(text);
      return loginRequired || loggedIn;
    },
    { loginPatternSource: LOGIN_MARKER_PATTERN.source },
    { timeout: LOGIN_MARKER_TIMEOUT_MS }
  ).catch(() => null);
}

function isWechatStorePage(value) {
  try {
    return new URL(String(value)).origin === STORE_ORIGIN;
  } catch {
    return false;
  }
}

async function main() {
  let result;
  let exitCode = 0;

  try {
    result = await checkWechatStoreLogin(parseArgs(process.argv.slice(2)));
  } catch {
    result = buildResult('cdp_unavailable');
    exitCode = 2;
  }

  process.stdout.write(`${JSON.stringify(result)}\n`, () => {
    // A CDP websocket can keep Node alive. Exit only after stdout is flushed.
    process.exit(exitCode);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
