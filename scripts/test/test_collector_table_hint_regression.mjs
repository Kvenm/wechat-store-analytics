#!/usr/bin/env node
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { WechatStoreCollector } from '../../src/collector/wechatStoreCollector.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, '../..');

async function main() {
  await testConfiguredProductsAnalyticsUsesShopDailyTableHint();
  await testDefaultProductsTableHintStaysProducts();
  await testExampleConfigDocumentsProductsAnalyticsHint();
  console.log('3 collector table hint regression tests passed.');
}

async function testConfiguredProductsAnalyticsUsesShopDailyTableHint() {
  const exportConfig = { types: { products: { tableHint: 'shop_daily' } } };
  assert.equal(exportConfig.types.products.tableHint, 'shop_daily');

  const { record } = await runExportTypeWithConfig(exportConfig);

  assert.equal(record.export_type, 'products');
  assert.equal(record.table_hint, 'shop_daily');
  assert.equal(record.artifact.export_type, 'products');
  assert.equal(record.artifact.table_hint, 'shop_daily');
}

async function testDefaultProductsTableHintStaysProducts() {
  const { record } = await runExportTypeWithConfig({});

  assert.equal(record.export_type, 'products');
  assert.equal(record.table_hint, 'products');
  assert.equal(record.artifact.table_hint, 'products');
}

async function testExampleConfigDocumentsProductsAnalyticsHint() {
  const exampleConfig = await readJson(path.join(PROJECT_ROOT, 'config/export-tasks.example.json'));
  const products = exampleConfig.types.products;

  assert.equal(products.label, 'Product analytics export');
  assert.equal(products.tableHint, 'shop_daily');
  assert.match(products.notes, /product master\/list exports/);
}

async function runExportTypeWithConfig(exportConfig) {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'wechat_collector_table_hint_'));
  const savedPath = path.join(tempDir, 'core-conversion-overview.xlsx');
  await fs.writeFile(savedPath, 'fixture');

  const collector = new WechatStoreCollector({
    task: {
      task_id: 'test-task',
      from: '2026-06-21',
      to: '2026-06-27',
      download_dir: tempDir,
      download_timeout_ms: 1000,
      types: ['products']
    },
    exportConfig,
    projectRoot: PROJECT_ROOT,
    logger: silentLogger()
  });

  collector.openExportPage = async () => {};
  collector.applyDateRange = async () => {};
  collector.triggerExportDownload = async () => ({});
  collector.saveDownload = async () => ({
    saved_path: savedPath,
    original_filename: '核心转化概览.xlsx',
    suggested_filename: '核心转化概览.xlsx'
  });

  try {
    const record = await collector.exportType('products', { id: 'aoshang-baihuo', name: '奥尚百货' });
    return { record, tempDir };
  } finally {
    await fs.rm(tempDir, { recursive: true, force: true });
  }
}

async function readJson(filePath) {
  return JSON.parse(await fs.readFile(filePath, 'utf8'));
}

function silentLogger() {
  return {
    log() {},
    warn() {},
    error() {}
  };
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
