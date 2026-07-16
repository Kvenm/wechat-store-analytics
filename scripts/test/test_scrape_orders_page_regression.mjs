#!/usr/bin/env node
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const source = await fs.readFile(path.join(projectRoot, 'scripts/collect/scrape-orders-page.mjs'), 'utf8');

const helperStart = source.indexOf('function cleanSingleLine');
const helperEnd = source.indexOf('function parseArgs');
assert(helperStart >= 0 && helperEnd > helperStart);
const helperContext = {};
vm.runInNewContext(
  `${source.slice(helperStart, helperEnd)}\nthis.assertExpectedShop = assertExpectedShop;`,
  helperContext
);

assert.throws(() => helperContext.assertExpectedShop('', '任务店铺'), /无法确认当前登录店铺/);
assert.throws(() => helperContext.assertExpectedShop('其他店铺', '任务店铺'), /店铺不一致/);
assert.doesNotThrow(() => helperContext.assertExpectedShop('任务店铺 管理后台', '任务店铺'));

const metadataStart = source.indexOf('async function writeRunMetadata');
const metadataEnd = source.indexOf('async function buildArtifact');
assert(metadataStart >= 0 && metadataEnd > metadataStart);
const writes = new Map();
const metadataContext = {
  fs: { writeFile: async (target, content) => writes.set(target, JSON.parse(content)) },
  TASK_METADATA: 'task-metadata.json',
  ARTIFACT_MANIFEST: 'artifacts-manifest.json',
  runStartedAt: new Date('2026-07-01T00:00:00Z'),
  buildArtifact: async () => ({ status: 'completed' })
};
vm.runInNewContext(
  `${source.slice(metadataStart, metadataEnd)}\nthis.writeRunMetadata = writeRunMetadata;`,
  metadataContext
);
await metadataContext.writeRunMetadata({
  taskId: 'task-1',
  shopId: 'shop-1',
  shopName: '任务店铺',
  dateFrom: '2026-07-01',
  dateTo: '2026-07-10',
  currentShop: '任务店铺',
  pageUrl: 'https://store.weixin.qq.com/shop/order/list',
  rowCount: 1,
  status: 'completed'
});
const metadata = writes.get('task-metadata.json');
assert.equal(metadata.data_coverage, 'visible_viewport');
assert.equal(metadata.date_filter_applied, false);
assert.equal(metadata.date_range_semantics, 'requested_only_not_applied');

console.log('4 scrape orders page regression tests passed.');
