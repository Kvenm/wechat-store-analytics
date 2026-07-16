#!/usr/bin/env node
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { chromium } from 'playwright';

import {
  applyRequestedDateRange,
  assertFundFlowDateValuesWithinRange,
  completeExportDialog,
  createExportBundle,
  collectControlInventory,
  discoverExportControls,
  findLeftNavigationLocator,
  prioritizeTargetExportControls,
  selectSafeSurface,
  triggerDownloadFromControl,
  waitForGeneratedExportCompletion,
  waitForVerifiedExportDialog
} from '../collect/export-visible-tables.mjs';

import {
  EXPORT_TARGETS,
  REQUESTED_EXPORT_TARGET_KEYS,
  buildArtifactManifest,
  buildTaskMetadata,
  classifyExportDialogText,
  classifyLoginText,
  isAllowedDialogConfirmLabel,
  isAllowedStoreRoute,
  isSafeExportControlLabel,
  isSafePageExportCandidate,
  isSupportedDownloadFilename,
  parseExportOnlyArgs,
  sanitizeDownloadFilename,
  summarizePageResults
} from '../collect/export-visible-tables-lib.mjs';

const execFileAsync = promisify(execFile);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, '../..');
const SCRIPT_PATH = path.join(PROJECT_ROOT, 'scripts', 'collect', 'export-visible-tables.mjs');
const VERIFICATION_STATUS_PATH = path.join(PROJECT_ROOT, 'data', 'export-verification-status.json');

async function main() {
  testCliContractAndTargetSelection();
  testCliRejectsUnsafeOrInvalidInput();
  testSafeExportControlPolicy();
  testOrderControlPriority();
  testDialogConfirmationPolicy();
  testLoginClassification();
  testAllowlistedRoutes();
  testFilenameSanitization();
  testResultAndMetadataShape();
  await testImplementationUsesCdpAndNoAnalysisPipeline();
  await testRenderedOrderDateRange();
  await testRenderedFundFlowDateRange();
  await testRenderedShadowFundFlowDateRange();
  await testFundFlowQueryIgnoresGlobalHeaderSearch();
  await testRenderedCompassHiddenDateRange();
  await testRenderedPersonaCustomDateRange();
  await testPlainDivSurfaceAndExportControlDiscovery();
  await testGeneratedExportIgnoresSubmissionToastWhilePending();
  await testGeneratedExportKeepsGenerationBudgetAfterEarlySuccess();
  await testGeneratedExportClicksShortLivedSuccessLinkImmediately();
  await testGeneratedExportProgressDialogWaitsForDownload();
  await testFundFlowQrCheckDismissesStaleSuccessAndDownloadsFreshResult();
  await testFundFlowStandaloneSuccessResultIsDownloaded();
  await testStaleProductSuccessDialogIsDismissedBeforeNewExport();
  await testVerifiedExportControlDismissesPointerOverlay();
  await testTaskBundleContainsAllArtifacts();
  testFundFlowArtifactDateValidation();
  await testRenderedWeuiDialogAndVerificationFlow();
  await testControlInventoryCapturesNonSemanticControlsAndFrames();
  await testFailedRunWritesExecutionLog();
  await testFailureStdoutIsSingleJsonObject();
  console.log('31 export-visible-tables regression tests passed.');
}

function testCliContractAndTargetSelection() {
  const task = parseExportOnlyArgs([
    '--shop-id', 'shop-123',
    '--shop-name=测试小店',
    '--from', '2026-07-01',
    '--to', '2026-07-10',
    '--cdp-url', 'http://127.0.0.1:9333',
    '--download-timeout-ms', '45000',
    '--verification-timeout-ms', '240000',
    '--targets', 'orders,fund_flows'
  ], { now: new Date('2026-07-11T06:00:00.000Z') });

  assert.equal(task.taskId, 'export_only_shop-123_2026-07-01_2026-07-10_20260711T060000Z');
  assert.equal(task.shopName, '测试小店');
  assert.equal(task.downloadTimeoutMs, 45000);
  assert.equal(task.verificationTimeoutMs, 240000);
  assert.deepEqual(task.targets.map((target) => target.key), ['orders', 'fund_flows']);
  const core = EXPORT_TARGETS.find((target) => target.key === 'product_core_conversion');
  const funnel = EXPORT_TARGETS.find((target) => target.key === 'product_traffic_funnel');
  const detail = EXPORT_TARGETS.find((target) => target.key === 'product_detail');
  const buyerProfile = EXPORT_TARGETS.find((target) => target.key === 'compass_buyer_profile');
  assert.deepEqual(core.controlContextLabels, ['核心转化概览']);
  assert.deepEqual(funnel.controlContextLabels, ['流量转化漏斗']);
  assert.equal(detail.surfaceLabel, '商品明细');
  assert.deepEqual(detail.excludeControlContextLabels, ['核心转化概览', '流量转化漏斗']);
  assert.equal(buyerProfile.route, '/shop-faas/mmecnodecompasscommon/thirdParty/shop/loginCompassByShop');
  assert.deepEqual(buyerProfile.menuPath, ['人群', '人群特征']);
  assert.deepEqual(buyerProfile.allowedRoutes, [{ route: '/compass/persona/home', routePrefix: true }]);
  for (const key of [
    'product_list',
    'orders',
    'fund_flows',
    'transactions',
    'product_core_conversion',
    'product_traffic_funnel',
    'product_detail',
    'compass_buyer_profile'
  ]) {
    assert.equal(EXPORT_TARGETS.find((target) => target.key === key).requiredExportControl, true, key);
  }
  assert.equal(EXPORT_TARGETS.find((target) => target.key === 'orders').maxExportControls, 1);
  assert.deepEqual(REQUESTED_EXPORT_TARGET_KEYS, [
    'product_list',
    'orders',
    'fund_flows',
    'transactions',
    'product_core_conversion',
    'product_traffic_funnel',
    'product_detail',
    'compass_buyer_profile'
  ]);
}

function testCliRejectsUnsafeOrInvalidInput() {
  assert.throws(
    () => parseExportOnlyArgs(['--shop-id', 'x', '--from', '2026-07-10', '--to', '2026-07-01']),
    /不能晚于/
  );
  assert.throws(
    () => parseExportOnlyArgs(['--shop-id', 'x', '--from', '2026-07-01', '--to', '2026-07-10', '--targets', 'withdraw']),
    /非安全白名单/
  );
  assert.throws(
    () => parseExportOnlyArgs(['--shop-id', 'x', '--from', '2026-07-01', '--to', '2026-07-10']),
    /必须明确指定/
  );
  for (const extraTarget of [
    'audience',
    'compass',
    'after_sales',
    'reviews',
    'shop_ads',
    'shop_boost',
    'repurchase',
    'alliance_data'
  ]) {
    assert.throws(
      () => parseExportOnlyArgs([
        '--shop-id', 'x',
        '--from', '2026-07-01',
        '--to', '2026-07-10',
        '--targets', extraTarget
      ]),
      /非安全白名单/,
      extraTarget
    );
  }
  assert.throws(
    () => parseExportOnlyArgs(['--shop-id', 'x', '--from', '2026-07-01', '--to', '2026-07-10', '--deploy', 'true']),
    /无法识别的参数/
  );
}

function testSafeExportControlPolicy() {
  for (const label of ['下载数据', '导出', '全部导出', '下载报表', '导出明细', '报表下载']) {
    assert.equal(isSafeExportControlLabel(label), true, label);
  }
  for (const label of ['提现', '充值', '开始投放', '调整预算', '发货', '退款', '提交', '保存', '导出记录', '下载模板']) {
    assert.equal(isSafeExportControlLabel(label), false, label);
  }
  assert.equal(isSafePageExportCandidate('下载', '经营数据报表'), true);
  assert.equal(isSafePageExportCandidate('下载', '素材模板'), false);
  assert.equal(isSafePageExportCandidate('下载数据', '历史任务 导出记录'), false);
  assert.equal(
    isSafePageExportCandidate('下载数据', '导出成功，即将自动下载。如未自动下载，可点击下载数据'),
    false
  );
}

function testOrderControlPriority() {
  const candidates = [
    { label: '下载数据', key: 'stale-download' },
    { label: '全部导出', key: 'current-export' }
  ];
  const ordered = prioritizeTargetExportControls(candidates, { key: 'orders' });
  assert.deepEqual(ordered.map((item) => item.key), ['current-export', 'stale-download']);
  assert.deepEqual(
    prioritizeTargetExportControls(candidates, { key: 'product_list' }).map((item) => item.key),
    ['stale-download', 'current-export']
  );
}

function testDialogConfirmationPolicy() {
  assert.equal(
    classifyExportDialogText('订单导出 共导出4条订单信息，预计需要时间：2秒。 导出'),
    'confirmation'
  );
  assert.equal(
    classifyExportDialogText('温馨提示 当前196条订单信息按照商品维度展开，预计需要时间：2秒 确定'),
    'confirmation'
  );
  assert.equal(
    classifyExportDialogText('扫码验证 导出商品数据需要你微信扫码确认'),
    'verification'
  );
  assert.equal(classifyExportDialogText('确认提现吗？'), 'unknown');
  assert.equal(isAllowedDialogConfirmLabel('确定', '确认导出当前筛选数据吗？'), true);
  assert.equal(
    isAllowedDialogConfirmLabel('确定', '温馨提示 当前196条订单信息按照商品维度展开，预计需要时间：2秒'),
    true
  );
  assert.equal(isAllowedDialogConfirmLabel('生成并下载', '下载数据报表'), true);
  assert.equal(isAllowedDialogConfirmLabel('确定', '确认提现吗？'), false);
  assert.equal(isAllowedDialogConfirmLabel('提交', '确认导出当前数据吗？'), false);
  assert.equal(isAllowedDialogConfirmLabel('开始投放', '导出投放数据'), false);
}

function testLoginClassification() {
  assert.equal(classifyLoginText('扫码进入我的小店 请使用微信扫码'), 'login_required');
  assert.equal(classifyLoginText('首页 商品管理 订单/配送 店铺数据'), 'logged_in');
  assert.equal(classifyLoginText('页面加载中'), 'unknown');
}

function testAllowlistedRoutes() {
  const orders = EXPORT_TARGETS.find((target) => target.key === 'orders');
  const ads = EXPORT_TARGETS.find((target) => target.key === 'shop_ads');
  const compass = EXPORT_TARGETS.find((target) => target.key === 'compass');
  assert.equal(isAllowedStoreRoute('https://store.weixin.qq.com/shop/order/list?tab=all', orders), true);
  assert.equal(isAllowedStoreRoute('https://evil.example/shop/order/list', orders), false);
  assert.equal(isAllowedStoreRoute('https://store.weixin.qq.com/shop/funds/moneyManage', orders), false);
  assert.equal(isAllowedStoreRoute('https://store.weixin.qq.com/shop/promotion/campaign/list', ads), true);
  assert.equal(isAllowedStoreRoute('https://store.weixin.qq.com/compass/home?source=store', compass), true);
  assert.equal(isAllowedStoreRoute('https://store.weixin.qq.com/compass-other/home', compass), false);
}

function testFilenameSanitization() {
  assert.equal(sanitizeDownloadFilename('../../订单:明细?.xlsx'), '订单_明细_.xlsx');
  assert.equal(sanitizeDownloadFilename('', 'fallback.csv'), 'fallback.csv');
  assert.equal(isSupportedDownloadFilename('订单明细.XLSX'), true);
  assert.equal(isSupportedDownloadFilename('调试截图.png'), false);
  assert.equal(isSupportedDownloadFilename('download.bin'), false);
}

function testResultAndMetadataShape() {
  const pageResults = [
    { status: 'success', artifact_count: 2 },
    { status: 'skipped', artifact_count: 0 },
    { status: 'failed', artifact_count: 0 }
  ];
  assert.deepEqual(summarizePageResults(pageResults), {
    status: 'partial',
    artifact_count: 2,
    success_count: 1,
    skipped_count: 1,
    failed_count: 1
  });
  assert.deepEqual(summarizePageResults([
    { status: 'skipped', artifact_count: 0 },
    { status: 'skipped', artifact_count: 0 }
  ]), {
    status: 'completed',
    artifact_count: 0,
    success_count: 0,
    skipped_count: 2,
    failed_count: 0
  });
  assert.deepEqual(summarizePageResults([
    { status: 'failed', artifact_count: 0 },
    { status: 'skipped', artifact_count: 0 }
  ]), {
    status: 'failed',
    artifact_count: 0,
    success_count: 0,
    skipped_count: 1,
    failed_count: 1
  });

  const task = {
    taskId: 'task-1',
    shopId: 'shop-1',
    shopName: '测试店',
    dateFrom: '2026-07-01',
    dateTo: '2026-07-10',
    targets: [EXPORT_TARGETS[0]]
  };
  const artifacts = [{ saved_path: '/tmp/file.xlsx' }];
  const manifest = buildArtifactManifest({ task, artifacts, generatedAt: '2026-07-11T00:00:00.000Z' });
  assert.equal(manifest.mode, 'export_only');
  assert.equal(manifest.source_type, 'export_only');
  assert.equal(manifest.artifact_count, 1);

  const metadata = buildTaskMetadata({
    task,
    status: 'completed',
    startedAt: 'start',
    completedAt: 'end',
    downloadDir: '/tmp/task-1',
    manifestPath: '/tmp/task-1/artifacts-manifest.json',
    resultsPath: '/tmp/task-1/export-results.json',
    executionLogPath: '/tmp/task-1/execution-log.jsonl',
    artifacts,
    pageResults: [{ status: 'success', artifact_count: 1 }]
  });
  assert.equal(metadata.mode, 'export_only');
  assert.equal(metadata.source_type, 'export_only');
  assert.equal(metadata.data_coverage, 'page_current_filters');
  assert.equal(metadata.date_filter_applied, false);
  assert.equal(metadata.date_range_semantics, 'requested_only_not_applied');
  assert.equal(metadata.effective_date_range, null);
  assert.equal(metadata.scan_completed, true);
  assert.equal(metadata.success_count, 1);
  assert.equal(metadata.execution_log_path, '/tmp/task-1/execution-log.jsonl');

  const filteredArtifact = [{
    saved_path: '/tmp/orders.zip',
    date_filter_applied: true,
    effective_date_range: { from: '2026-07-01 00:00:00', to: '2026-07-10 23:59:59' }
  }];
  const filteredMetadata = buildTaskMetadata({
    task,
    status: 'completed',
    startedAt: 'start',
    completedAt: 'end',
    downloadDir: '/tmp/task-1',
    manifestPath: '/tmp/task-1/artifacts-manifest.json',
    resultsPath: '/tmp/task-1/export-results.json',
    artifacts: filteredArtifact,
    pageResults: [{ status: 'success', artifact_count: 1, date_filter_applied: true }]
  });
  assert.equal(filteredMetadata.data_coverage, 'requested_date_range');
  assert.equal(filteredMetadata.date_filter_applied, true);
  assert.equal(filteredMetadata.date_range_semantics, 'requested_range_applied');
  assert.deepEqual(filteredMetadata.effective_date_range, { from: '2026-07-01', to: '2026-07-10' });
}

async function testControlInventoryCapturesNonSemanticControlsAndFrames() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await page.setContent(`
      <div class="selector-item">自定义</div>
      <input placeholder="记账时间" value="2026-07-01 至 2026-07-14">
      <div id="shadow-date-host"></div>
      <iframe name="compass-frame" srcdoc='<div class="menu-item">人群特征</div><div class="title link">下载数据</div>'></iframe>
      <script>document.querySelector('#shadow-date-host').attachShadow({ mode: 'open' }).innerHTML = '<input placeholder="动账结束时间" value="2026-07-14 23:59:59">';</script>
    `);
    await page.waitForTimeout(50);
    const inventory = await collectControlInventory(page);
    assert(inventory.length >= 2);
    const controls = inventory.flatMap((item) => item.controls);
    assert(controls.some((item) => item.class_name.includes('selector-item') && item.text === '自定义'));
    assert(controls.some((item) => item.placeholder === '记账时间' && item.value.includes('2026-07-01')));
    assert(controls.some((item) => item.placeholder === '动账结束时间' && item.value.includes('2026-07-14')));
    assert(controls.some((item) => item.class_name.includes('menu-item') && item.text === '人群特征'));
    assert(controls.some((item) => item.class_name.includes('title link') && item.text === '下载数据'));
    assert(inventory.some((item) => item.frame_name === 'compass-frame'));
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testFailedRunWritesExecutionLog() {
  const shopId = `diagnostic-${Date.now()}`;
  let caught;
  try {
    await execFileAsync(process.execPath, [
      SCRIPT_PATH,
      '--shop-id', shopId,
      '--from', '2026-07-01',
      '--to', '2026-07-02',
      '--targets', 'orders',
      '--cdp-url', 'http://127.0.0.1:1'
    ], {
      cwd: PROJECT_ROOT,
      env: { ...process.env, NO_COLOR: '1' }
    });
  } catch (error) {
    caught = error;
  }
  assert(caught, 'unavailable CDP endpoint should fail');
  const summary = JSON.parse(String(caught.stdout ?? '').trim());
  assert(summary.execution_log_path);
  const taskDir = path.dirname(summary.execution_log_path);
  try {
    const lines = (await fs.readFile(summary.execution_log_path, 'utf8')).trim().split(/\r?\n/).filter(Boolean);
    assert(lines.length >= 3);
    const events = lines.map((line) => JSON.parse(line));
    assert(events.every((event) => event.task_id === summary.task_id));
    assert(events.some((event) => event.stage === 'export_only_started'));
    assert(events.some((event) => event.stage === 'failure_diagnostic_captured'));
    assert(events.some((event) => event.stage === 'export_only_completed'));
    const metadata = JSON.parse(await fs.readFile(summary.metadata_path, 'utf8'));
    assert.equal(metadata.execution_log_path, summary.execution_log_path);
    assert(metadata.pages[0].failures[0].debug.debug_path);
    const debug = JSON.parse(await fs.readFile(metadata.pages[0].failures[0].debug.debug_path, 'utf8'));
    assert(Array.isArray(debug.control_inventory));
  } finally {
    await fs.rm(taskDir, { recursive: true, force: true });
  }
}

async function testImplementationUsesCdpAndNoAnalysisPipeline() {
  const source = await fs.readFile(SCRIPT_PATH, 'utf8');
  assert.match(source, /chromium\.connectOverCDP/);
  assert.doesNotMatch(source, /launchPersistentContext|chromium\.launch\s*\(/);
  assert.match(source, /waitForEvent\('download'/);
  assert.match(source, /\.weui-desktop-dialog__wrp/);
  assert.match(source, /classifyExportDialogText\(dialog\.text\) === 'verification'/);
  assert.match(source, /finalSummary\.status === 'failed'/);
  assert.match(source, /locateVisibleVerifiedDialog/);
  assert.match(source, /export-verification-screenshot\.png/);
  assert.doesNotMatch(source, /locator\(EXPORT_DIALOG_SELECTOR\)[\s\S]{0,160}\.first\(\)/);
  assert.doesNotMatch(source, /scripts\/import|scripts\/analyze|scripts\/report|src\/ingestion|src\/analytics|src\/reporting/);
  assert.match(source, /Do not close the CDP Browser/);
  assert.match(source, /process\.stdout\.write[\s\S]+process\.exit\(finalExitCode\)/);
  assert.match(source, /page_name: target\.label/);
  assert.match(source, /Only the main store shell is authoritative/);
  assert.match(source, /for \(const candidate of candidates\.slice/);
  assert.match(source, /elementHandle\(\)/);
  assert.doesNotMatch(source, /attemptedKeys|discoveredKeys/);
  assert.match(source, /assertVisibleOrderRowsWithinRange/);
  assert.match(source, /assertVisibleFundFlowRowsWithinRange/);
  assert.match(source, /validateFundFlowArtifactDateCoverage/);
  assert.match(source, /23:59:59/);
  assert.match(source, /export-verification-status\.json/);
  assert.match(source, /crypto\.randomUUID\(\)/);
}

async function testRenderedOrderDateRange() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await page.setContent(`
      <dl class="my-date-picker weui-desktop-picker__date-range weui-desktop-picker__focus">
        <input placeholder="开始日期" readonly value="">
        <input placeholder="结束日期" readonly value="">
        <div class="weui-desktop-picker__panel_day">
          <div class="weui-desktop-picker__panel__hd"><span>2026年</span><span>07月</span></div>
          <a>1</a><a>13</a>
        </div>
        <div class="weui-desktop-picker__panel_day">
          <div class="weui-desktop-picker__panel__hd"><span>2026年</span><span>08月</span></div>
        </div>
        <input placeholder="请选择时间" value="00:00:00">
        <input placeholder="请选择时间" value="00:00:00">
      </dl>
      <button>查询</button>
      <span class="createTime">下单时间: 2026-07-13 15:30</span>
      <script>
        const picker = document.querySelector('.my-date-picker');
        const start = picker.querySelector('[placeholder="开始日期"]');
        const end = picker.querySelector('[placeholder="结束日期"]');
        const times = picker.querySelectorAll('[placeholder="请选择时间"]');
        let selected = 0;
        picker.querySelectorAll('a').forEach((item) => item.addEventListener('click', () => {
          selected += 1;
          if (selected === 1) start.value = '2026-07-01 ' + times[0].value;
          if (selected === 2) end.value = '2026-07-13 ' + times[1].value;
        }));
        times.forEach((input, index) => input.addEventListener('input', () => {
          if (index === 0 && start.value) start.value = '2026-07-01 ' + input.value;
          if (index === 1 && end.value) end.value = '2026-07-13 ' + input.value;
        }));
      </script>
    `);
    const result = await applyRequestedDateRange({
      page,
      task: { dateFrom: '2026-07-01', dateTo: '2026-07-13' },
      target: { key: 'orders' }
    });
    assert.equal(result.applied, true);
    assert.equal(await page.getByPlaceholder('开始日期').inputValue(), '2026-07-01 00:00:00');
    assert.equal(await page.getByPlaceholder('结束日期').inputValue(), '2026-07-13 23:59:59');
    assert.deepEqual(result.effective_date_range, {
      from: '2026-07-01 00:00:00',
      to: '2026-07-13 23:59:59'
    });
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testRenderedFundFlowDateRange() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await context.route('https://example.test/**', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '{"ok":true}'
    }));
    await page.setContent(`
      <dl class="fund-date-picker weui-desktop-picker__date-range weui-desktop-picker__focus">
        <input placeholder="开始时间" readonly value="">
        <input placeholder="结束时间" readonly value="">
        <div class="weui-desktop-picker__panel_day">
          <div class="weui-desktop-picker__panel__hd"><span>2026年</span><span>06月</span></div>
          <a>1</a><a>30</a>
        </div>
        <div class="weui-desktop-picker__panel_day">
          <div class="weui-desktop-picker__panel__hd"><span>2026年</span><span>07月</span></div>
        </div>
      </dl>
      <button>搜索</button>
      <table><tbody><tr><td>2026-06-18 12:30:00</td><td>订单支付</td></tr></tbody></table>
      <script>
        const picker = document.querySelector('.fund-date-picker');
        const start = picker.querySelector('[placeholder="开始时间"]');
        const end = picker.querySelector('[placeholder="结束时间"]');
        document.querySelector('button').addEventListener('click', () => {
          fetch('https://example.test/mmchannelstradefunds/cgi/scanFundsFlow?startTime=1780243200&endTime=1782835200');
        });
        let selected = 0;
        picker.querySelectorAll('a').forEach((item) => item.addEventListener('click', () => {
          selected += 1;
          if (selected === 1) start.value = '2026-06-01';
          if (selected === 2) end.value = '2026-06-30';
        }));
      </script>
    `);
    const result = await applyRequestedDateRange({
      page,
      task: { dateFrom: '2026-06-01', dateTo: '2026-06-30' },
      target: { key: 'fund_flows' }
    });
    assert.equal(result.applied, true);
    assert.equal(await page.getByPlaceholder('开始时间').inputValue(), '2026-06-01');
    assert.equal(await page.getByPlaceholder('结束时间').inputValue(), '2026-06-30');
    assert.deepEqual(result.effective_date_range, {
      from: '2026-06-01',
      to: '2026-06-30'
    });
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testRenderedShadowFundFlowDateRange() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await context.route('https://example.test/**', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '{"ok":true}'
    }));
    await page.setContent('<div id="host"></div>');
    await page.locator('#host').evaluate((host) => {
      const root = host.attachShadow({ mode: 'open' });
      root.innerHTML = `
        <div>资金流水数据截止至2026-06-29，请于次日查看</div>
        <dl class="weui-desktop-picker__date-range weui-desktop-picker__date-time-range">
          <dt><input placeholder="动账开始时间"><input placeholder="动账结束时间"></dt>
          <dd class="weui-desktop-picker__dd" style="display:none">
            <div class="weui-desktop-picker__panel_day">
              <div class="weui-desktop-picker__panel__hd"><span>2026年</span><span>06月</span></div>
              <a>1</a><a>29</a>
            </div>
          </dd>
        </dl>
        <div id="query" style="display:inline-block;width:80px;height:32px">查询</div>
        <table><tbody><tr><td>2026-06-18 12:30:00</td></tr></tbody></table>
      `;
      const panel = root.querySelector('dd');
      const start = root.querySelector('[placeholder="动账开始时间"]');
      const end = root.querySelector('[placeholder="动账结束时间"]');
      let selected = 0;
      start.addEventListener('click', () => { panel.style.display = 'block'; });
      root.querySelectorAll('a').forEach((item) => item.addEventListener('click', () => {
        selected += 1;
        if (selected === 1) start.value = '2026-06-01 00:00:00';
        if (selected === 2) end.value = '2026-06-29 23:59:59';
      }));
      root.querySelector('#query').addEventListener('click', () => {
        panel.style.display = 'none';
        fetch('https://example.test/mmchannelstradefunds/cgi/scanFundsFlow?startTime=1780243200&endTime=1782748800');
      });
    });
    const result = await applyRequestedDateRange({
      page,
      task: { dateFrom: '2026-06-01', dateTo: '2026-06-30' },
      target: { key: 'fund_flows', label: '流水与账单' }
    });
    assert.equal(result.applied, true);
    assert.equal(await page.getByPlaceholder('动账开始时间').inputValue(), '2026-06-01 00:00:00');
    assert.equal(await page.getByPlaceholder('动账结束时间').inputValue(), '2026-06-29 23:59:59');
    assert.deepEqual(result.effective_date_range, {
      from: '2026-06-01',
      to: '2026-06-29'
    });
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testFundFlowQueryIgnoresGlobalHeaderSearch() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await context.route('https://example.test/**', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '{"ok":true}'
    }));
    await page.setContent(`
      <style>.sub-btn{display:inline-block;width:100px;height:32px}</style>
      <button class="search_input__button" id="global-search">搜索</button>
      <div class="search_input_wrp">
        <dl class="weui-desktop-picker__date-range">
          <dt><input placeholder="动账开始时间"><input placeholder="动账结束时间"></dt>
          <dd class="weui-desktop-picker__dd" style="display:none">
            <div class="weui-desktop-picker__panel_day">
              <div class="weui-desktop-picker__panel__hd"><span>2026年</span><span>07月</span></div>
              <a>1</a><a>14</a>
            </div>
          </dd>
        </dl>
        <div class="sub-btn-wrp"><div class="sub-btn">重置</div><div class="sub-btn search-btn" id="fund-query">查询</div></div>
      </div>
      <div class="tips">共321个资金流水</div>
      <table><tbody><tr><td>2026-05-05 08:00:00</td></tr></tbody></table>
      <script>
        window.globalSearchClicks = 0;
        window.fundQueryClicks = 0;
        const picker = document.querySelector('.weui-desktop-picker__date-range');
        const panel = picker.querySelector('dd');
        const start = picker.querySelector('[placeholder="动账开始时间"]');
        const end = picker.querySelector('[placeholder="动账结束时间"]');
        let selected = 0;
        start.addEventListener('click', () => { panel.style.display = 'block'; });
        picker.querySelectorAll('a').forEach((item) => item.addEventListener('click', () => {
          selected += 1;
          if (selected === 1) start.value = '2026-07-01 00:00:00';
          if (selected === 2) end.value = '2026-07-14 23:59:59';
        }));
        document.querySelector('#global-search').addEventListener('click', () => { window.globalSearchClicks += 1; });
        document.querySelector('#fund-query').addEventListener('click', () => {
          window.fundQueryClicks += 1;
          panel.style.display = 'none';
          document.querySelector('.tips').textContent = '共37个资金流水';
          document.querySelector('tbody').innerHTML = '<tr><td>2026-07-12 12:00:00</td></tr>';
          fetch('https://example.test/mmchannelstradefunds/cgi/scanFundsFlow?startTime=1782835200&endTime=1784044800');
        });
      </script>
    `);
    const result = await applyRequestedDateRange({
      page,
      task: { dateFrom: '2026-07-01', dateTo: '2026-07-14' },
      target: { key: 'fund_flows', label: '资金流水' }
    });
    assert.equal(result.applied, true);
    assert.equal(await page.evaluate(() => window.globalSearchClicks), 0);
    assert.equal(await page.evaluate(() => window.fundQueryClicks), 1);
    assert.equal(await page.locator('.tips').innerText(), '共37个资金流水');
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testRenderedCompassHiddenDateRange() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await page.setContent(`
      <div data-eleid="product_top_filter_datetime_btn" class="time-range-dropdown">
        <div class="target"><div>时间范围</div><div class="time-wording">近7天</div></div>
        <dl class="weui-desktop-picker__date-range time-range-pciker__weui">
          <dt style="display:none"><input placeholder="开始日期"><input placeholder="结束日期"></dt>
          <dd class="weui-desktop-picker__dd" style="display:none">
            <div class="weui-desktop-picker__panel_day">
              <div class="weui-desktop-picker__panel__hd"><span>2026年</span><span>07月</span></div>
              <a>1</a><a>14</a>
            </div>
          </dd>
        </dl>
      </div>
      <script>
        const root = document.querySelector('[data-eleid="product_top_filter_datetime_btn"]');
        const panel = root.querySelector('dd');
        const start = root.querySelector('[placeholder="开始日期"]');
        const end = root.querySelector('[placeholder="结束日期"]');
        let selected = 0;
        root.querySelector('.target').addEventListener('click', () => { panel.style.display = 'block'; });
        root.querySelectorAll('a').forEach((item) => item.addEventListener('click', () => {
          selected += 1;
          if (selected === 1) start.value = '2026/07/01';
          if (selected === 2) { end.value = '2026/07/14'; panel.style.display = 'none'; }
        }));
      </script>
    `);
    const result = await applyRequestedDateRange({
      page,
      task: { dateFrom: '2026-07-01', dateTo: '2026-07-14' },
      target: { key: 'product_core_conversion', label: '商品数据 - 核心转化概览' }
    });
    assert.equal(result.applied, true);
    assert.equal(await page.getByPlaceholder('开始日期').inputValue(), '2026/07/01');
    assert.equal(await page.getByPlaceholder('结束日期').inputValue(), '2026/07/14');
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testRenderedPersonaCustomDateRange() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await page.setContent(`
      <style>.selector-item{display:inline-block;width:80px;height:32px}</style>
      <div class="selector"><div class="selector-item"><div>近7天</div></div><div class="selector-item" id="custom"><div>自定义</div></div></div>
      <dl class="weui-desktop-picker__date-range selector-item picker" style="display:none">
        <dt><input placeholder="开始日期" value="2026/07/08"><input placeholder="结束日期" value="2026/07/14"></dt>
        <dd class="weui-desktop-picker__dd" style="display:none">
          <div class="weui-desktop-picker__panel_day">
            <div class="weui-desktop-picker__panel__hd"><span>2026年</span><span>07月</span></div>
            <a>1</a><a>14</a>
          </div>
        </dd>
      </dl>
      <script>
        const picker = document.querySelector('dl');
        const panel = picker.querySelector('dd');
        const start = picker.querySelector('[placeholder="开始日期"]');
        const end = picker.querySelector('[placeholder="结束日期"]');
        let selected = 0;
        document.querySelector('#custom').addEventListener('click', () => { picker.style.display = 'block'; });
        start.addEventListener('click', () => { panel.style.display = 'block'; });
        picker.querySelectorAll('a').forEach((item) => item.addEventListener('click', () => {
          selected += 1;
          if (selected === 1) start.value = '2026/07/01';
          if (selected === 2) { end.value = '2026/07/14'; panel.style.display = 'none'; }
        }));
      </script>
    `);
    const result = await applyRequestedDateRange({
      page,
      task: { dateFrom: '2026-07-01', dateTo: '2026-07-14' },
      target: { key: 'compass_buyer_profile', label: '电商罗盘 - 买家人群特征' }
    });
    assert.equal(result.applied, true);
    assert.equal(await page.getByPlaceholder('开始日期').inputValue(), '2026/07/01');
    assert.equal(await page.getByPlaceholder('结束日期').inputValue(), '2026/07/14');
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testPlainDivSurfaceAndExportControlDiscovery() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await page.setContent(`
      <div class="tab-wrap tab-bar"><div class="tab-item-bar">商品转化</div><div class="tab-item-bar" id="detail-tab">商品明细</div></div>
      <section><h2>核心转化概览</h2><div class="plain-download">下载数据</div></section>
      <section><h2>商品明细</h2><div data-eleid="live_detail_download_btn">下载数据</div></section>
      <script>document.querySelector('#detail-tab').addEventListener('click', (event) => { event.currentTarget.dataset.clicked = 'yes'; });</script>
    `);
    const surface = await selectSafeSurface(page, '商品明细');
    assert.equal(surface.selected, true);
    assert.equal(await page.locator('#detail-tab').getAttribute('data-clicked'), 'yes');
    const controls = await discoverExportControls(page);
    try {
      const plain = controls.find((item) => item.role === 'text' && item.label === '下载数据');
      assert(plain);
      assert(plain.contextTexts.some((text) => text.includes('核心转化概览')));
      assert(controls.some((item) => item.dataEleid === 'live_detail_download_btn'));
    } finally {
      await Promise.all(controls.map((item) => item.handle.dispose().catch(() => {})));
    }
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testGeneratedExportIgnoresSubmissionToastWhilePending() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  try {
    await page.setContent(`
      <div id="toast">导出成功</div>
      <div id="state">正在导出商品数据 1800 / 3177</div>
      <script>
        setTimeout(() => {
          document.querySelector('#state').innerHTML = '商品数据已导出。如未自动下载，可点击 <a id="ready-download" href="data:text/csv,sku%0A1" download="products.csv">下载数据</a>';
        }, 150);
      </script>
    `);
    const downloadOutcome = page.waitForEvent('download', { timeout: 3000 })
      .then((download) => ({ kind: 'download', download }));
    const download = await waitForGeneratedExportCompletion({
      page,
      downloadOutcome,
      generationTimeoutMs: 2000,
      downloadTimeoutMs: 1000
    });
    assert.equal(download.suggestedFilename(), 'products.csv');
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testGeneratedExportKeepsGenerationBudgetAfterEarlySuccess() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  try {
    await page.setContent(`
      <div id="toast">商品数据已导出</div>
      <div id="state">正在导出商品数据 1800 / 3315</div>
      <script>
        setTimeout(() => {
          document.querySelector('#state').innerHTML = '商品数据已导出。如未自动下载，可点击 <a href="data:text/csv,sku%0A1" download="large-products.csv">下载数据</a>';
        }, 250);
      </script>
    `);
    const downloadOutcome = page.waitForEvent('download', { timeout: 1500 })
      .then((download) => ({ kind: 'download', download }))
      .catch((error) => ({ kind: 'download_timeout', error }));
    const download = await waitForGeneratedExportCompletion({
      page,
      downloadOutcome,
      generationTimeoutMs: 1000,
      downloadTimeoutMs: 50
    });
    assert.equal(download.suggestedFilename(), 'large-products.csv');
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testGeneratedExportClicksShortLivedSuccessLinkImmediately() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  try {
    await page.setContent(`
      <div id="state">正在导出商品数据 1800 / 3177</div>
      <script>
        setTimeout(() => {
          document.querySelector('#state').innerHTML = [
            '<p>商品数据已导出。如未自动下载，可点击下载数据</p>',
            '<a id="ready-download" href="data:text/csv,sku%0A1" download="products.csv">下载数据</a>'
          ].join('');
        }, 100);
        setTimeout(() => {
          document.querySelector('#state').textContent = '导出记录已关闭';
        }, 500);
      </script>
    `);
    const downloadOutcome = page.waitForEvent('download', { timeout: 2000 })
      .then((download) => ({ kind: 'download', download }))
      .catch((error) => ({ kind: 'download_timeout', error }));
    const download = await waitForGeneratedExportCompletion({
      page,
      downloadOutcome,
      generationTimeoutMs: 1000,
      downloadTimeoutMs: 1000
    });
    assert.equal(download.suggestedFilename(), 'products.csv');
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testGeneratedExportProgressDialogWaitsForDownload() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  try {
    await page.setContent(`
      <div class="weui-desktop-dialog__wrp" id="progress-dialog">
        <h2>商品数据导出</h2>
        <p>商品数据正在导出 69.93%</p>
        <button disabled>取消</button>
      </div>
      <script>
        setTimeout(() => {
          document.querySelector('#progress-dialog').innerHTML = [
            '<h2>商品数据导出</h2>',
            '<p>商品已导出。如未自动下载，可点击下载数据</p>',
            '<a href="data:text/csv,sku%0A1" download="product-detail.csv">下载数据</a>'
          ].join('');
        }, 150);
      </script>
    `);
    const dialog = await waitForVerifiedExportDialog(page, 1000);
    const download = await completeExportDialog({
      page,
      dialog,
      timeoutMs: 1000,
      verificationTimeoutMs: 2000
    });
    assert.equal(download.suggestedFilename(), 'product-detail.csv');
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testFundFlowQrCheckDismissesStaleSuccessAndDownloadsFreshResult() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  try {
    await page.setContent(`
      <div class="weui-desktop-msg success" id="stale-success">
        <p>资金流水已导出。如未自动下载，可点击 下载数据</p>
        <a href="data:text/csv,stale%0A1" download="stale-fund-flow.csv">下载数据</a>
        <button class="weui-desktop-msg__close" aria-label="关闭">×</button>
      </div>
      <a id="export" href="javascript:void(0)">全部导出</a>
      <script>
        document.querySelector('#stale-success button').addEventListener('click', () => {
          document.querySelector('#stale-success').remove();
        });
        document.querySelector('#export').addEventListener('click', () => {
          const verification = document.createElement('div');
          verification.className = 'weui-desktop-qrcheck';
          verification.id = 'fund-flow-verification';
          verification.innerHTML = '<h2>微信扫码</h2><p>请使用微信扫码确认导出资金流水</p>';
          document.body.appendChild(verification);
          setTimeout(() => {
            verification.remove();
            const success = document.createElement('div');
            success.className = 'weui-desktop-msg success';
            success.innerHTML = [
              '<p>资金流水已导出。如未自动下载，可点击 下载数据</p>',
              '<a href="data:text/csv,fresh%0A2" download="fresh-fund-flow.csv">下载数据</a>'
            ].join('');
            document.body.appendChild(success);
          }, 400);
        });
      </script>
    `);
    const controls = await discoverExportControls(page);
    try {
      const candidate = controls.find((item) => item.label === '全部导出');
      assert(candidate);
      candidate.frameUrl = 'https://store.weixin.qq.com/shop/funds/moneyManage';
      const download = await triggerDownloadFromControl({
        page,
        candidate,
        timeoutMs: 1500,
        verificationTimeoutMs: 1000
      });
      assert.equal(download.suggestedFilename(), 'fresh-fund-flow.csv');
      assert.equal(await page.locator('#stale-success').count(), 0);
      assert.equal(await page.locator('#fund-flow-verification').count(), 0);
    } finally {
      await Promise.all(controls.map((item) => item.handle.dispose().catch(() => {})));
    }
  } finally {
    await fs.unlink(VERIFICATION_STATUS_PATH).catch(() => {});
    await context.close();
    await browser.close();
  }
}

async function testFundFlowStandaloneSuccessResultIsDownloaded() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  try {
    await page.setContent(`
      <a id="export" href="javascript:void(0)">全部导出</a>
      <script>
        document.querySelector('#export').addEventListener('click', () => {
          setTimeout(() => {
            const success = document.createElement('div');
            success.className = 'weui-desktop-msg success';
            success.innerHTML = [
              '<p>资金流水已导出。如未自动下载，可点击 下载数据</p>',
              '<a href="data:text/csv,fresh%0A3" download="standalone-fund-flow.csv">下载数据</a>'
            ].join('');
            document.body.appendChild(success);
          }, 80);
        });
      </script>
    `);
    const controls = await discoverExportControls(page);
    try {
      const candidate = controls.find((item) => item.label === '全部导出');
      assert(candidate);
      candidate.frameUrl = 'https://store.weixin.qq.com/shop/funds/moneyManage';
      const download = await triggerDownloadFromControl({
        page,
        candidate,
        timeoutMs: 1000,
        verificationTimeoutMs: 1000
      });
      assert.equal(download.suggestedFilename(), 'standalone-fund-flow.csv');
    } finally {
      await Promise.all(controls.map((item) => item.handle.dispose().catch(() => {})));
    }
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testVerifiedExportControlDismissesPointerOverlay() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  try {
    await page.setContent(`
      <a id="export" href="javascript:void(0)">全部导出</a>
      <a id="file" href="data:text/csv,id%0A1" download="fund-flow.csv" hidden>文件</a>
      <div id="header-overlay" style="position:fixed;inset:0;z-index:10;background:rgba(0,0,0,.05)">经营成长</div>
      <script>
        document.querySelector('#export').addEventListener('click', () => document.querySelector('#file').click());
        document.addEventListener('keydown', (event) => {
          if (event.key === 'Escape') document.querySelector('#header-overlay').remove();
        });
      </script>
    `);
    const controls = await discoverExportControls(page);
    try {
      const candidate = controls.find((item) => item.label === '全部导出');
      assert(candidate);
      const download = await triggerDownloadFromControl({
        page,
        candidate,
        timeoutMs: 1000,
        verificationTimeoutMs: 1000
      });
      assert.equal(download.suggestedFilename(), 'fund-flow.csv');
      assert.equal(await page.locator('#header-overlay').count(), 0);
    } finally {
      await Promise.all(controls.map((item) => item.handle.dispose().catch(() => {})));
    }
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testStaleProductSuccessDialogIsDismissedBeforeNewExport() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  try {
    await page.setContent(`
      <div class="weui-desktop-dialog__wrp" id="stale-success">
        <p>商品已导出。如未自动下载，可点击下载数据</p>
        <a href="data:text/csv,stale%0A1" download="stale-products.csv">下载数据</a>
      </div>
      <a id="export" href="javascript:void(0)">批量导出</a>
      <a id="fresh-file" href="data:text/csv,fresh%0A1" download="fresh-products.csv" hidden>文件</a>
      <script>
        document.addEventListener('keydown', (event) => {
          if (event.key === 'Escape') document.querySelector('#stale-success').style.display = 'none';
        });
        document.querySelector('#export').addEventListener('click', () => document.querySelector('#fresh-file').click());
      </script>
    `);
    const controls = await discoverExportControls(page);
    try {
      const candidate = controls.find((item) => item.label === '批量导出');
      assert(candidate);
      const download = await triggerDownloadFromControl({
        page,
        candidate,
        timeoutMs: 1000,
        verificationTimeoutMs: 1000
      });
      assert.equal(download.suggestedFilename(), 'fresh-products.csv');
      assert.equal(await page.locator('#stale-success').isVisible(), false);
    } finally {
      await Promise.all(controls.map((item) => item.handle.dispose().catch(() => {})));
    }
  } finally {
    await context.close();
    await browser.close();
  }
}

async function testTaskBundleContainsAllArtifacts() {
  const rawDir = path.join(PROJECT_ROOT, 'data', 'raw');
  await fs.mkdir(rawDir, { recursive: true });
  const tempDir = await fs.mkdtemp(path.join(rawDir, 'bundle-regression-'));
  try {
    const ordersDir = path.join(tempDir, 'orders');
    const productDir = path.join(tempDir, 'product_detail');
    await fs.mkdir(ordersDir, { recursive: true });
    await fs.mkdir(productDir, { recursive: true });
    const ordersPath = path.join(ordersDir, 'orders.xlsx');
    const productPath = path.join(productDir, 'products.csv');
    await fs.writeFile(ordersPath, 'orders');
    await fs.writeFile(productPath, 'products');
    const bundle = await createExportBundle({
      task: {
        shopId: 'shop-bundle',
        shopName: '压缩包测试店铺',
        dateFrom: '2026-07-01',
        dateTo: '2026-07-14'
      },
      downloadDir: tempDir,
      artifacts: [
        { saved_path: ordersPath, original_filename: 'orders.xlsx', page_label: '订单明细', module: 'orders', export_type: 'orders' },
        { saved_path: productPath, original_filename: 'products.csv', page_label: '商品明细', module: 'product_detail', export_type: 'product_detail' }
      ]
    });
    assert.equal(bundle.source_kind, 'export_bundle');
    assert.equal(bundle.bundled_file_count, 2);
    assert.equal(bundle.status, 'completed');
    const { stdout } = await execFileAsync('python3', [
      '-c',
      'import json,sys,zipfile; print(json.dumps(zipfile.ZipFile(sys.argv[1]).namelist()))',
      bundle.saved_path
    ]);
    const names = JSON.parse(stdout);
    assert(names.includes('订单明细/orders.xlsx'));
    assert(names.includes('商品明细/products.csv'));
  } finally {
    await fs.rm(tempDir, { recursive: true, force: true });
  }
}

function testFundFlowArtifactDateValidation() {
  assert.deepEqual(
    assertFundFlowDateValuesWithinRange(
      ['2026-06-18', '2026-06-01', '2026-06-30', '2026-06-18'],
      '2026-06-01',
      '2026-06-30'
    ),
    {
      checked: true,
      date_count: 3,
      earliest_date: '2026-06-01',
      latest_date: '2026-06-30'
    }
  );
  assert.throws(
    () => assertFundFlowDateValuesWithinRange(
      ['2026-05-05', '2026-06-18'],
      '2026-06-01',
      '2026-06-30'
    ),
    /范围外日期 2026-05-05/
  );
  assert.throws(
    () => assertFundFlowDateValuesWithinRange([], '2026-06-01', '2026-06-30'),
    /未读取到可验证的记账日期/
  );
}

async function testFailureStdoutIsSingleJsonObject() {
  let caught;
  try {
    await execFileAsync(process.execPath, [SCRIPT_PATH], {
      cwd: PROJECT_ROOT,
      env: { ...process.env, NO_COLOR: '1' }
    });
  } catch (error) {
    caught = error;
  }
  assert(caught, 'missing required args should exit non-zero');
  const stdout = String(caught.stdout ?? '').trim();
  const lines = stdout.split(/\r?\n/).filter(Boolean);
  assert.equal(lines.length, 1, stdout);
  const payload = JSON.parse(lines[0]);
  assert.equal(payload.status, 'failed');
  for (const key of [
    'task_id',
    'status',
    'metadata_path',
    'download_dir',
    'artifact_count',
    'success_count',
    'skipped_count',
    'failed_count'
  ]) {
    assert(Object.hasOwn(payload, key), key);
  }
}

async function testRenderedWeuiDialogAndVerificationFlow() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  try {
    await page.setContent(`
      <nav><button id="orders-menu"><span>订单管理</span></button></nav>
      <div class="weui-desktop-dialog__wrp" style="display:none">隐藏导出模板</div>
      <div class="weui-desktop-dialog__wrp" style="display:none">隐藏扫码验证模板</div>
      <div id="dialog-shadow-host"></div>
      <a id="direct-file" href="data:text/csv,order_id%0A1" download="orders.csv" hidden>文件</a>
      <script>
        const root = document.querySelector('#dialog-shadow-host').attachShadow({ mode: 'open' });
        root.innerHTML = [
          '<div class="weui-desktop-dialog__wrp" id="confirm-dialog">',
          '<h2>温馨提示</h2>',
          '<p>当前196条订单信息按照商品维度展开，预计需要时间：2秒。</p>',
          '<button id="confirm-export">确定</button>',
          '</div>'
        ].join('');
        root.querySelector('#confirm-export').addEventListener('click', () => {
          document.querySelector('#direct-file').click();
        });
      </script>
    `);

    const menu = await findLeftNavigationLocator(page, '订单管理');
    assert(menu, 'visible navigation clickable should be found');
    assert.equal(await menu.evaluate((element) => element.id), 'orders-menu');

    const confirmation = await waitForVerifiedExportDialog(page, 1000);
    assert.match(confirmation.text, /订单信息/);
    assert.equal(await confirmation.locator.getAttribute('id'), 'confirm-dialog');
    const directDownload = await completeExportDialog({
      page,
      dialog: confirmation,
      timeoutMs: 1000,
      verificationTimeoutMs: 1000
    });
    assert.equal(directDownload.suggestedFilename(), 'orders.csv');

    await page.setContent(`
      <div class="weui-desktop-dialog__wrp" style="display:none">隐藏扫码验证模板</div>
      <div class="weui-desktop-dialog__wrp" id="verification-dialog">
        <h2>扫码验证</h2>
        <p>导出商品数据需要你微信扫码确认</p>
      </div>
      <script>
        setTimeout(() => {
          document.querySelector('#verification-dialog').innerHTML = '<h2>商品数据导出</h2><p>商品数据导出中，请勿刷新</p>';
        }, 50);
        setTimeout(() => {
          document.querySelector('#verification-dialog').innerHTML = [
            '<h2>商品数据导出</h2>',
            '<p>导出成功，即将自动下载。如果自动下载不生效，也可以点击下方按钮下载</p>',
            '<a href="data:text/csv,product_id%0A1" download="products.csv">下载数据</a>'
          ].join('');
        }, 100);
      </script>
    `);
    const verification = await waitForVerifiedExportDialog(page, 1000);
    const verifiedDownload = await completeExportDialog({
      page,
      dialog: verification,
      target: { key: 'product_list', label: '商品管理 · 商品列表' },
      candidate: { label: '批量导出' },
      timeoutMs: 300,
      verificationTimeoutMs: 1000
    });
    assert.equal(verifiedDownload.suggestedFilename(), 'products.csv');
    const firstChallenge = JSON.parse(await fs.readFile(VERIFICATION_STATUS_PATH, 'utf8'));
    assert.equal(firstChallenge.required, false);
    assert.match(firstChallenge.challenge_id, /^[0-9a-f-]{36}$/i);
    assert.equal(firstChallenge.timeout_ms, 1000);
    assert.equal(firstChallenge.target_key, 'product_list');
    assert.equal(firstChallenge.target_label, '商品管理 · 商品列表');
    assert.equal(firstChallenge.control_label, '批量导出');
    assert.equal(
      firstChallenge.screenshot_path,
      path.join(PROJECT_ROOT, 'data', 'export-verification-screenshot.png')
    );

    await page.setContent(`
      <div class="weui-desktop-dialog__wrp" id="verification-dialog-2">
        <h2>扫码验证</h2>
        <p>导出数据需要你微信扫码确认</p>
      </div>
      <script>
        setTimeout(() => {
          document.querySelector('#verification-dialog-2').innerHTML = [
            '<h2>导出成功</h2>',
            '<p>如未自动下载，请点击下载数据</p>',
            '<a href="data:text/csv,id%0A2" download="second.csv">下载数据</a>'
          ].join('');
        }, 50);
      </script>
    `);
    const secondVerification = await waitForVerifiedExportDialog(page, 1000);
    const secondDownload = await completeExportDialog({
      page,
      dialog: secondVerification,
      timeoutMs: 300,
      verificationTimeoutMs: 1000
    });
    assert.equal(secondDownload.suggestedFilename(), 'second.csv');
    const secondChallenge = JSON.parse(await fs.readFile(VERIFICATION_STATUS_PATH, 'utf8'));
    assert.equal(secondChallenge.required, false);
    assert.notEqual(secondChallenge.challenge_id, firstChallenge.challenge_id);
    assert.equal(secondChallenge.target_key, '');
    assert.equal(secondChallenge.target_label, '');
    assert.equal(secondChallenge.control_label, '');

    await page.setContent(`
      <div class="weui-desktop-dialog__wrp" id="verification-timeout-dialog">
        <h2>扫码验证</h2>
        <p>请使用微信扫码确认本次导出</p>
      </div>
    `);
    const timeoutVerification = await waitForVerifiedExportDialog(page, 1000);
    await assert.rejects(
      () => completeExportDialog({
        page,
        dialog: timeoutVerification,
        timeoutMs: 50,
        verificationTimeoutMs: 50
      }),
      /50ms 内未完成/
    );
    const timedOutChallenge = JSON.parse(await fs.readFile(VERIFICATION_STATUS_PATH, 'utf8'));
    assert.equal(timedOutChallenge.required, false);
    assert.notEqual(timedOutChallenge.challenge_id, secondChallenge.challenge_id);
  } finally {
    await fs.unlink(VERIFICATION_STATUS_PATH).catch(() => {});
    await context.close();
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
