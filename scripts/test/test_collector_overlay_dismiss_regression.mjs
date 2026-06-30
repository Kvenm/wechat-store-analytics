#!/usr/bin/env node
import assert from 'node:assert/strict';

import {
  WechatStoreCollector,
  dismissOpenNavigationOverlays,
  waitForOrderPageContent
} from '../../src/collector/wechatStoreCollector.js';

async function main() {
  await testDismissOpenNavigationOverlaysPressesEscapeAndClicksMainContent();
  await testWaitForOrderPageContentWaitsForOrderSurfaceText();
  await testOrdersDateRangeCanFillVisibleInputsWithoutButton();
  await testOrdersNavigationUsesVisibleMenuPath();
  await testReadonlyDateInputFallsBackToDomEvents();
  console.log('5 collector overlay/date/navigation regression tests passed.');
}

async function testDismissOpenNavigationOverlaysPressesEscapeAndClicksMainContent() {
  const calls = [];
  const page = {
    keyboard: {
      press: async (key) => {
        calls.push(['keyboard.press', key]);
      }
    },
    mouse: {
      click: async (x, y) => {
        calls.push(['mouse.click', x, y]);
      }
    },
    waitForTimeout: async (ms) => {
      calls.push(['waitForTimeout', ms]);
    }
  };

  await dismissOpenNavigationOverlays(page);

  assert.deepEqual(calls, [
    ['keyboard.press', 'Escape'],
    ['mouse.click', 520, 140],
    ['waitForTimeout', 300]
  ]);
}

async function testWaitForOrderPageContentWaitsForOrderSurfaceText() {
  const calls = [];
  const page = {
    waitForFunction: async (fn, arg, options) => {
      calls.push([fn.toString(), arg, options]);
    }
  };

  await waitForOrderPageContent(page, 1234);

  assert.equal(calls.length, 1);
  assert.match(calls[0][0], /全部导出/);
  assert.match(calls[0][0], /shadowRoot/);
  assert.deepEqual(calls[0][1], null);
  assert.deepEqual(calls[0][2], { timeout: 1234 });
}

async function testOrdersDateRangeCanFillVisibleInputsWithoutButton() {
  const calls = [];
  const collector = new WechatStoreCollector({
    task: {
      from: '2026-06-01',
      to: '2026-06-03',
      download_timeout_ms: 1000
    },
    exportConfig: {},
    projectRoot: process.cwd(),
    logger: {
      log: (...args) => calls.push(['log', ...args]),
      warn: (...args) => calls.push(['warn', ...args])
    }
  });

  const scope = {
    fill: async (selector, value, options) => calls.push(['fill', selector, value, options]),
    click: async (selector) => calls.push(['click', selector]),
    locator: (selector) => ({
      first: () => ({
        isVisible: async () => {
          calls.push(['isVisible', selector]);
          return false;
        },
        click: async () => calls.push(['buttonClick', selector])
      })
    })
  };

  collector.activeScopeForDefinition = async () => scope;
  collector.waitForExportPageReady = async (type) => calls.push(['waitForExportPageReady', type]);

  await collector.applyDateRange(
    'orders',
    {
      dateRange: {
        mode: 'custom-picker',
        dateRangeButtonRequired: false,
        selectors: {
          dateRangeButton: '.missing-date-button',
          dateStartInput: 'input[placeholder="开始日期"]',
          dateEndInput: 'input[placeholder="结束日期"]',
          dateConfirmButton: 'button:has-text("查询")'
        }
      }
    }
  );

  assert.deepEqual(calls.filter((call) => call[0] !== 'log' && call[0] !== 'warn'), [
    ['isVisible', '.missing-date-button'],
    ['fill', 'input[placeholder="开始日期"]', '2026-06-01', { timeout: 5000 }],
    ['fill', 'input[placeholder="结束日期"]', '2026-06-03', { timeout: 5000 }],
    ['click', 'button:has-text("查询")'],
    ['waitForExportPageReady', 'orders']
  ]);
  assert(calls.some((call) => call[0] === 'warn' && /not visible/.test(call[1])));
}

async function testOrdersNavigationUsesVisibleMenuPath() {
  const calls = [];
  const collector = new WechatStoreCollector({
    task: {
      download_timeout_ms: 1000
    },
    exportConfig: {},
    projectRoot: process.cwd(),
    logger: {
      log() {},
      warn: (...args) => calls.push(['warn', ...args])
    }
  });

  collector.page = {
    url: () => 'https://store.weixin.qq.com/shop/home',
    waitForTimeout: async (ms) => calls.push(['waitForTimeout', ms]),
    goto: async (url) => calls.push(['goto', url]),
    waitForLoadState: async () => {},
    keyboard: {
      press: async () => {}
    },
    mouse: {
      click: async () => {}
    }
  };

  collector.openStoreHome = async () => calls.push(['openStoreHome']);
  collector.waitForLoggedInShell = async () => calls.push(['waitForLoggedInShell']);
  collector.visibleNavigationEntry = async () => {
    throw new Error('orders navigation should not use generic hidden href path');
  };
  collector.openOrdersPageFromVisibleNavigation = async () => calls.push(['openOrdersPageFromVisibleNavigation']);
  collector.waitForExportPageContent = async (type) => calls.push(['waitForExportPageContent', type]);

  await collector.openExportPageFromNavigation('orders', {
    configKey: 'navigationSelector',
    selector: 'a[href="/shop/order/list"]'
  });

  assert.deepEqual(calls, [
    ['openStoreHome'],
    ['waitForLoggedInShell'],
    ['openOrdersPageFromVisibleNavigation'],
    ['waitForTimeout', 300],
    ['waitForExportPageContent', 'orders']
  ]);
}

async function testReadonlyDateInputFallsBackToDomEvents() {
  const calls = [];
  const collector = new WechatStoreCollector({
    task: {},
    exportConfig: {},
    projectRoot: process.cwd(),
    logger: {
      warn: (...args) => calls.push(['warn', ...args])
    }
  });
  const scope = {
    fill: async (selector, value, options) => {
      calls.push(['fill', selector, value, options]);
      throw new Error('element is not editable');
    },
    locator: (selector) => ({
      first: () => ({
        evaluate: async (fn, value) => {
          calls.push(['evaluate', selector, fn.toString(), value]);
        }
      })
    })
  };

  await collector.fillDateInput(scope, 'input[placeholder="开始日期"]', '2026-06-01');

  assert.equal(calls[0][0], 'fill');
  assert.equal(calls[1][0], 'warn');
  assert.equal(calls[2][0], 'evaluate');
  assert.match(calls[2][2], /removeAttribute\('readonly'\)/);
  assert.match(calls[2][2], /dispatchEvent\(new Event\('change'/);
  assert.equal(calls[2][3], '2026-06-01');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
