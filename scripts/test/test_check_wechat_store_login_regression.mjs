#!/usr/bin/env node
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import {
  buildResult,
  classifySnapshot,
  parseArgs,
  sanitizePageUrl
} from '../auth/check_wechat_store_login.mjs';

const execFileAsync = promisify(execFile);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, '../..');
const SCRIPT_PATH = path.join(PROJECT_ROOT, 'scripts', 'auth', 'check_wechat_store_login.mjs');

async function main() {
  testArgumentParsing();
  testSnapshotClassificationAndSafeShape();
  testSensitiveUrlPartsAreRemoved();
  await testImplementationSafetyContract();
  await testUnavailableCdpProducesOneJsonObject();
  console.log('5 check-wechat-store-login regression tests passed.');
}

function testArgumentParsing() {
  assert.equal(parseArgs([]).cdpUrl, 'http://127.0.0.1:9333/');
  assert.equal(
    parseArgs(['--cdp-url=http://localhost:9444']).cdpUrl,
    'http://localhost:9444/'
  );
  assert.throws(() => parseArgs(['--cdp-url']), /missing_cdp_url/);
  assert.throws(() => parseArgs(['--unknown']), /unknown_argument/);
  assert.throws(() => parseArgs(['--cdp-url', 'file:///tmp/browser']), /invalid_cdp_url/);
}

function testSnapshotClassificationAndSafeShape() {
  const loggedIn = classifySnapshot({
    bodyText: '首页 商品管理 订单/配送 店铺数据',
    url: 'https://store.weixin.qq.com/shop/home',
    title: '微信小店'
  });
  assert.deepEqual(loggedIn, {
    state: 'logged_in',
    authenticated: true,
    url: 'https://store.weixin.qq.com/shop/home',
    title: '微信小店',
    message: '微信小店后台已登录，可以开始页面表格导出'
  });

  const loginRequired = classifySnapshot({
    bodyText: '扫码进入我的小店 请使用微信扫码',
    url: 'https://store.weixin.qq.com/',
    title: '扫码登录'
  });
  assert.equal(loginRequired.state, 'login_required');
  assert.equal(loginRequired.authenticated, false);

  const wrongOrigin = classifySnapshot({
    bodyText: '首页 商品管理 订单/配送 店铺数据',
    url: 'https://example.com/',
    title: '其他页面'
  });
  assert.equal(wrongOrigin.state, 'unknown');
  assert.equal(wrongOrigin.authenticated, false);

  assert.deepEqual(Object.keys(buildResult('unknown')), [
    'state',
    'authenticated',
    'url',
    'title',
    'message'
  ]);
  assert.equal(Object.hasOwn(loggedIn, 'bodyText'), false);
}

function testSensitiveUrlPartsAreRemoved() {
  assert.equal(
    sanitizePageUrl('https://user:password@store.weixin.qq.com/shop/home?token=secret#access_token=secret'),
    'https://store.weixin.qq.com/shop/home'
  );
  assert.equal(sanitizePageUrl('not a url'), null);
}

async function testImplementationSafetyContract() {
  const source = await fs.readFile(SCRIPT_PATH, 'utf8');
  assert.match(source, /chromium\.connectOverCDP/);
  assert.doesNotMatch(source, /chromium\.launch|launchPersistentContext/);
  assert.doesNotMatch(source, /\bbrowser\.close\s*\(/);
  assert.match(source, /page\.mainFrame\(\)\.evaluate/);
  assert.match(source, /classifyLoginText/);
  assert.match(source, /process\.stdout\.write[\s\S]+process\.exit\(exitCode\)/);
  assert.doesNotMatch(source, /\.cookies\s*\(|storageState\s*\(/);
  assert.doesNotMatch(source, /console\.(?:log|error|warn)/);
}

async function testUnavailableCdpProducesOneJsonObject() {
  const { stdout, stderr } = await execFileAsync(
    process.execPath,
    [SCRIPT_PATH, '--cdp-url', 'http://127.0.0.1:1'],
    {
      cwd: PROJECT_ROOT,
      env: { ...process.env, NO_COLOR: '1' },
      timeout: 10000
    }
  );

  assert.equal(stderr, '');
  const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
  assert.equal(lines.length, 1, stdout);
  const result = JSON.parse(lines[0]);
  assert.deepEqual(Object.keys(result), [
    'state',
    'authenticated',
    'url',
    'title',
    'message'
  ]);
  assert.equal(result.state, 'cdp_unavailable');
  assert.equal(result.authenticated, false);
  assert.equal(result.url, null);
  assert.equal(result.title, null);
  assert.doesNotMatch(stdout, /127\.0\.0\.1|token|cookie|websocket/i);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
