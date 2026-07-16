#!/usr/bin/env node
import crypto from 'node:crypto';
import { execFile } from 'node:child_process';
import { appendFileSync, createReadStream } from 'node:fs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { chromium } from 'playwright';

import {
  EXPORT_CONTROL_LABEL_PATTERN,
  GENERATED_DOWNLOAD_LABEL_PATTERN,
  STORE_ORIGIN,
  buildArtifactManifest,
  buildTaskMetadata,
  classifyExportDialogText,
  classifyLoginText,
  cleanText,
  isAllowedDialogConfirmLabel,
  isAllowedStoreRoute,
  isExportDialogText,
  isSafeExportControlLabel,
  isSafePageExportCandidate,
  isSupportedDownloadFilename,
  parseExportOnlyArgs,
  safePathSegment,
  sanitizeDownloadFilename,
  summarizePageResults
} from './export-visible-tables-lib.mjs';

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const STORE_URL = `${STORE_ORIGIN}/`;
const EXPORT_VERIFICATION_SCREENSHOT_PATH = path.join(
  PROJECT_ROOT,
  'data',
  'export-verification-screenshot.png'
);
const EXPORT_VERIFICATION_STATUS_PATH = path.join(
  PROJECT_ROOT,
  'data',
  'export-verification-status.json'
);
const MAX_EXPORT_CONTROLS_PER_TARGET = 12;
const CONTROL_ROLES = ['button', 'link', 'menuitem'];
const EXPORT_DIALOG_SELECTOR = [
  '[role="dialog"]',
  '[aria-modal="true"]',
  '.my-qrcode',
  '.weui-desktop-qrcheck',
  '.weui-desktop-dialog__wrp',
  '.weui-desktop-dialog'
].join(',');
const EXPORT_DIALOG_TEXT_PATTERN = /导出|下载|扫码验证|扫码确认|微信扫码|(?:共|当前)?\s*\d+\s*(?:条|个)?订单信息|商品维度展开.*预计需要时间/;
const EXPORT_SUCCESS_TEXT_PATTERN = /(?:订单|商品|资金流水|数据|报表)[^。\n]{0,40}已导出|即将自动下载|如未自动下载|自动下载不生效/;
const EXPORT_FAILURE_TEXT_PATTERN = /用户取消扫码|验证未通过|二维码加载失败|订单导出失败|导出失败|导出数据为空|没有符合条件的订单/;
const FUND_FLOW_ROUTE_PATTERN = /\/shop\/funds\/moneyManage(?:[/?#]|$)/;
const FUND_FLOW_SUCCESS_SELECTOR = '.weui-desktop-msg.success';
const FUND_FLOW_GENERATION_TEXT_PATTERN = /资金流水[^。\n]{0,60}(?:正在导出|导出中|正在生成|生成中|导出进度)|(?:正在导出|导出中|正在生成|生成中|导出进度)[^。\n]{0,60}资金流水/;
const NO_PERMISSION_PATTERN = /暂无权限|无权限|没有权限|未开通|功能未开放|无权访问/;
const UNAVAILABLE_PAGE_PATTERN = /页面不存在|页面已下线|功能已下线|访问的页面不存在|404\s*(?:Not Found)?/i;
const ORDER_DATE_PICKER_SELECTOR = '.weui-desktop-picker__date-range.weui-desktop-picker__focus';
const DATE_PANEL_SELECTOR = '.weui-desktop-picker__panel_day';
const DATE_FILTER_TARGET_KEYS = new Set([
  'orders',
  'fund_flows',
  'transactions',
  'product_core_conversion',
  'product_traffic_funnel',
  'product_detail',
  'compass_buyer_profile'
]);
const DATE_INPUT_PLACEHOLDER_PAIRS = Object.freeze([
  Object.freeze(['开始日期', '结束日期']),
  Object.freeze(['开始时间', '结束时间']),
  Object.freeze(['起始日期', '截止日期']),
  Object.freeze(['起始时间', '截止时间']),
  Object.freeze(['动账开始时间', '动账结束时间'])
]);
const COMPASS_EMBED_DATE_TRIGGER_IDS = Object.freeze({
  transactions: 'trade_top_filter_datetime_btn',
  product_core_conversion: 'product_top_filter_datetime_btn',
  product_traffic_funnel: 'product_top_filter_datetime_btn',
  product_detail: 'product_top_filter_datetime_btn'
});
const execFileAsync = promisify(execFile);
let executionLogPath = null;
let executionLogTaskId = null;

async function main() {
  let finalSummary;
  try {
    const task = parseExportOnlyArgs(process.argv.slice(2));
    finalSummary = await runExportOnly(task);
    if (finalSummary.fatal_error || finalSummary.status === 'failed') {
      process.exitCode = 1;
    }
  } catch (error) {
    process.exitCode = 1;
    log('argument_or_startup_failed', { error: errorMessage(error) });
    finalSummary = {
      task_id: null,
      status: 'failed',
      metadata_path: null,
      download_dir: null,
      artifact_count: 0,
      success_count: 0,
      skipped_count: 0,
      failed_count: 1,
      fatal_error: true,
      scan_completed: false,
      error: serializeError(error)
    };
  }

  const finalExitCode = Number(process.exitCode ?? 0);
  process.stdout.write(`${JSON.stringify(finalSummary)}\n`, () => {
    // connectOverCDP keeps a websocket handle alive. Exit only after stdout is
    // flushed; never call browser.close(), which would terminate login Chrome.
    process.exit(finalExitCode);
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}

async function runExportOnly(task) {
  const startedAt = new Date().toISOString();
  const downloadDir = path.join(PROJECT_ROOT, 'data', 'raw', task.taskId);
  const metadataPath = path.join(downloadDir, 'task-metadata.json');
  const manifestPath = path.join(downloadDir, 'artifacts-manifest.json');
  const resultsPath = path.join(downloadDir, 'export-results.json');
  const taskExecutionLogPath = path.join(downloadDir, 'execution-log.jsonl');
  const pageResults = [];
  const artifacts = [];
  let bundleArtifact = null;
  let bundleError = null;
  let browser;
  let page;
  let fatalError = null;

  await fs.mkdir(downloadDir, { recursive: true });
  executionLogPath = taskExecutionLogPath;
  executionLogTaskId = task.taskId;
  log('export_only_started', {
    task_id: task.taskId,
    target_count: task.targets.length,
    download_dir: downloadDir
  });

  try {
    browser = await chromium.connectOverCDP(task.cdpUrl, { timeout: 10000 });
    const context = browser.contexts()[0];
    if (!context) {
      throw fatal('CDP_CONTEXT_MISSING', '已连接 CDP，但没有可复用的浏览器上下文');
    }

    page = await context.newPage();
    page.setDefaultTimeout(Math.min(task.downloadTimeoutMs, 10000));
    page.setDefaultNavigationTimeout(30000);

    log('open_store_home', { url: STORE_URL });
    await page.goto(STORE_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await assertLoggedInFast(page, task.loginTimeoutMs);
    await assertExpectedShop(page, task.shopName);

    for (const target of task.targets) {
      log('target_started', { target: target.key, label: target.label });
      let result;
      try {
        result = await exportTarget({ page, task, target, downloadDir, artifacts });
      } catch (error) {
        if (error?.fatal) {
          throw error;
        }
        const debug = await captureFailure(page, downloadDir, target, 1, error, {
          stage: 'target_unhandled_failure'
        });
        result = failedPageResult(target, error, debug);
      }
      pageResults.push(result);
      log('target_completed', {
        target: target.key,
        status: result.status,
        artifact_count: result.artifact_count,
        failure_count: result.failures?.length ?? 0
      });
      await persistRunFiles({
        task,
        status: 'running',
        startedAt,
        completedAt: null,
        downloadDir,
        metadataPath,
        manifestPath,
        resultsPath,
        executionLogPath: taskExecutionLogPath,
        artifacts,
        pageResults,
        bundleArtifact,
        bundleError
      });
    }
  } catch (error) {
    fatalError = error;
    const debug = await captureFailure(page, downloadDir, { key: '_run', module: '_run', label: '运行前置检查' }, 1, error, {
      stage: 'fatal_run_failure'
    });
    pageResults.push(failedPageResult(
      { key: '_run', module: '_run', label: '运行前置检查', menuPath: [], route: null, confidence: 'high' },
      error,
      debug
    ));
    log('export_only_failed', { error: errorMessage(error), code: error?.code ?? null });
  } finally {
    await page?.close({ runBeforeUnload: false }).catch(() => {});
    // Do not close the CDP Browser: it is the user's persistent login browser.
  }

  if (artifacts.length > 0) {
    try {
      bundleArtifact = await createExportBundle({ task, downloadDir, artifacts });
      log('export_bundle_created', {
        filename: bundleArtifact.original_filename,
        saved_path: bundleArtifact.saved_path,
        size_bytes: bundleArtifact.size_bytes,
        bundled_file_count: bundleArtifact.bundled_file_count
      });
    } catch (error) {
      bundleError = serializeError(error);
      log('export_bundle_failed', { error: errorMessage(error) });
    }
  }

  const completedAt = new Date().toISOString();
  const counts = summarizePageResults(pageResults);
  const status = fatalError ? 'failed' : counts.status;
  await persistRunFiles({
    task,
    status,
    startedAt,
    completedAt,
    downloadDir,
    metadataPath,
    manifestPath,
    resultsPath,
    executionLogPath: taskExecutionLogPath,
    artifacts,
    pageResults,
    bundleArtifact,
    bundleError,
    error: fatalError ? serializeError(fatalError) : null
  });

  const summary = {
    task_id: task.taskId,
    status,
    metadata_path: metadataPath,
    execution_log_path: taskExecutionLogPath,
    download_dir: downloadDir,
    artifact_count: artifacts.length,
    success_count: counts.success_count,
    skipped_count: counts.skipped_count,
    failed_count: counts.failed_count,
    bundle_available: Boolean(bundleArtifact),
    bundle_path: bundleArtifact?.saved_path ?? null,
    fatal_error: Boolean(fatalError),
    scan_completed: !fatalError
  };
  if (fatalError) {
    summary.error = serializeError(fatalError);
  }
  log('export_only_completed', summary);
  return summary;
}

async function exportTarget({ page, task, target, downloadDir, artifacts }) {
  const startedAt = new Date().toISOString();
  const result = basePageResult(target, startedAt);
  const navigation = await navigateToTarget(page, target);
  result.navigation = navigation;
  result.page_url = page.url();
  result.page_title = await page.title().catch(() => '');

  await assertSessionStillLoggedIn(page);
  const pageSnapshot = await collectFrameSnapshots(page, 1800);
  const surfaceText = pageSnapshot.map((item) => item.body_text).join(' ');
  if (NO_PERMISSION_PATTERN.test(surfaceText)) {
    return finishSkipped(result, 'no_permission', '当前账号在该页面没有导出权限');
  }
  if (UNAVAILABLE_PAGE_PATTERN.test(surfaceText)) {
    return finishSkipped(result, 'page_unavailable', '白名单页面当前不可用或已下线');
  }

  if (target.surfaceLabel) {
    const surface = await selectSafeSurface(page, target.surfaceLabel);
    result.surface = surface;
    if (!surface.selected && !target.surfaceOptional) {
      if (target.requiredExportControl) {
        throw new Error(`未找到目标数据页签“${target.surfaceLabel}”，本项没有导出，已按失败处理`);
      }
      return finishSkipped(result, 'surface_not_found', `未找到安全页签“${target.surfaceLabel}”`);
    }
  }

  const dateFilter = await applyRequestedDateRange({ page, task, target });
  result.data_coverage = dateFilter.data_coverage;
  result.date_filter_applied = dateFilter.applied;
  result.date_range_semantics = dateFilter.date_range_semantics;
  result.effective_date_range = dateFilter.effective_date_range;

  const targetArtifacts = [];
  const failures = [];
  const candidates = prioritizeTargetExportControls(
    await discoverScopedExportControls(page, target, result.surface),
    target
  );

  if (target.key === 'compass_buyer_profile' && candidates.length === 0) {
    const artifact = await saveBuyerProfilePageSnapshot({
      page,
      task,
      target,
      downloadDir,
      dateFilter
    });
    artifacts.push(artifact);
    result.controls_found = 0;
    result.controls_attempted = 0;
    result.artifacts = [artifact.saved_path];
    result.exported_filenames = [artifact.original_filename];
    result.artifact_count = 1;
    result.status = 'success';
    result.completed_at = new Date().toISOString();
    result.message = '该页面没有微信原生下载按钮，已保存买家人群特征页面可见数据快照';
    log('page_snapshot_saved', {
      target: target.key,
      filename: artifact.original_filename,
      saved_path: artifact.saved_path,
      size_bytes: artifact.size_bytes
    });
    return result;
  }

  const maximumControls = target.maxExportControls ?? MAX_EXPORT_CONTROLS_PER_TARGET;
  if (candidates.length === 0 && target.requiredExportControl) {
    throw new Error(`“${target.label}”页面未发现明确导出/下载控件，本项没有生成文件，已按失败处理`);
  }
  for (const candidate of candidates.slice(0, maximumControls)) {
    log('export_control_clicked', {
      target: target.key,
      label: candidate.label,
      frame_url: candidate.frameUrl
    });
    try {
      const download = await triggerDownloadFromControl({
        page,
        target,
        candidate,
        timeoutMs: task.downloadTimeoutMs,
        verificationTimeoutMs: task.verificationTimeoutMs
      });
      const artifact = await saveDownloadArtifact({
        download,
        task,
        target,
        candidate,
        downloadDir,
        pageUrl: page.url(),
        dateFilter
      });
      artifacts.push(artifact);
      targetArtifacts.push(artifact);
      await closeVerifiedExportDialog(page);
      log('download_saved', {
        target: target.key,
        filename: artifact.original_filename,
        saved_path: artifact.saved_path,
        size_bytes: artifact.size_bytes
      });
    } catch (error) {
      if (error?.fatal) {
        throw error;
      }
      const debug = await captureFailure(page, downloadDir, target, failures.length + 1, error, {
        stage: 'export_control_failed',
        control: candidateDescriptor(candidate)
      });
      failures.push({
        control: candidateDescriptor(candidate),
        error: serializeError(error),
        debug
      });
      log('export_control_failed', {
        target: target.key,
        label: candidate.label,
        error: errorMessage(error)
      });
      await closeVerifiedExportDialog(page);
    }
  }

  result.controls_found = candidates.length;
  result.controls_attempted = Math.min(candidates.length, maximumControls);
  result.artifacts = targetArtifacts.map((artifact) => artifact.saved_path);
  result.exported_filenames = targetArtifacts.map((artifact) => artifact.original_filename);
  result.artifact_count = targetArtifacts.length;
  result.failures = failures;
  result.completed_at = new Date().toISOString();

  if (failures.length > 0) {
    result.status = 'failed';
    result.failure_reason = targetArtifacts.length > 0
      ? '部分明确导出控件失败；已保留成功下载文件'
      : '明确导出控件未产生可保存的下载文件';
  } else if (targetArtifacts.length > 0) {
    result.status = 'success';
  } else {
    result.status = 'skipped';
    result.skipped_reason = candidates.length === 0
      ? 'no_visible_export_control'
      : 'no_new_export_control';
    result.message = '页面及 iframe 中未发现新的明确导出/下载控件';
  }

  return result;
}

async function applyRequestedDateRange({ page, task, target }) {
  if (!DATE_FILTER_TARGET_KEYS.has(target.key)) {
    return unappliedDateFilter();
  }

  // A previous export can leave its success dialog visible in this browser
  // profile. Dismiss it before interacting with the real page filters.
  await closeVerifiedExportDialog(page);

  const targetLabel = target.label || ({
    orders: '订单',
    fund_flows: '资金流水',
    transactions: '交易数据',
    product_core_conversion: '商品数据核心转化概览',
    product_traffic_funnel: '商品数据流量转化漏斗',
    product_detail: '商品数据商品明细',
    compass_buyer_profile: '电商罗盘买家人群特征'
  }[target.key] ?? target.key);
  let controls = await locateVisibleDateRangeControls(page);
  if (!controls) {
    await openTargetDateRangePicker(page, target);
    controls = await locateVisibleDateRangeControls(page);
  }
  if (!controls) {
    throw new Error(`${targetLabel}页未找到可验证的开始/结束日期控件，已停止本次导出以避免导出全店数据`);
  }
  const { frame, startInput, endInput, picker } = controls;

  if (await startInput.isVisible({ timeout: 500 }).catch(() => false)) {
    await startInput.click({ timeout: 5000 });
  }
  await Promise.any([
    picker.locator('.weui-desktop-picker__dd').waitFor({ state: 'visible', timeout: 5000 }),
    picker.locator(DATE_PANEL_SELECTOR).first().waitFor({ state: 'visible', timeout: 5000 })
  ]);

  const effectiveRange = await resolveEffectiveDateRange(frame, task, target);
  const from = parseIsoDateParts(effectiveRange.from);
  const to = parseIsoDateParts(effectiveRange.to);
  await navigatePickerUntilMonthVisible(picker, from, targetLabel);
  await selectPickerDay(picker, from, targetLabel);
  await navigatePickerUntilMonthVisible(picker, to, targetLabel);
  await selectPickerDay(picker, to, targetLabel);

  const timeInputs = picker.getByPlaceholder('请选择时间');
  const timeInputCount = await timeInputs.count();
  if (target.key === 'orders' && timeInputCount < 2) {
    throw new Error('订单日期控件未找到完整的起止时间输入框');
  }
  if (timeInputCount >= 2) {
    await timeInputs.nth(0).fill('00:00:00');
    await timeInputs.nth(1).fill('23:59:59');
  }

  await assertDateInputValue(startInput, effectiveRange.from, timeInputCount >= 2 ? '00:00:00' : null, targetLabel, '开始日期');
  await assertDateInputValue(endInput, effectiveRange.to, timeInputCount >= 2 ? '23:59:59' : null, targetLabel, '结束日期');

  if (Object.hasOwn(COMPASS_EMBED_DATE_TRIGGER_IDS, target.key) || target.key === 'compass_buyer_profile') {
    await waitForNetworkSettle(page);
  } else {
    const queryButton = await locateVisibleQueryButton(frame, picker);
    if (!queryButton) {
      throw new Error(`${targetLabel}页未找到可验证的“查询/搜索”按钮，已停止本次导出`);
    }
    const fundFlowResponse = target.key === 'fund_flows'
      ? page.waitForResponse((response) => {
        try {
          return new URL(response.url()).pathname.includes('/mmchannelstradefunds/cgi/scanFundsFlow');
        } catch {
          return false;
        }
      }, { timeout: 10000 })
      : null;
    await queryButton.click({ timeout: 5000 });
    if (fundFlowResponse) {
      const response = await fundFlowResponse.catch(() => {
        throw new Error('资金流水日期查询未发起列表请求，已停止导出以避免导出全店数据');
      });
      const queryRange = assertFundFlowQueryRequestRange(
        response.url(),
        effectiveRange.from,
        effectiveRange.to
      );
      log('fund_flow_filter_query_verified', {
        target: target.key,
        start_time: queryRange.start_time,
        end_time: queryRange.end_time
      });
    }
    await waitForNetworkSettle(page);
  }
  await assertDateInputValue(startInput, effectiveRange.from, timeInputCount >= 2 ? '00:00:00' : null, targetLabel, '开始日期');
  await assertDateInputValue(endInput, effectiveRange.to, timeInputCount >= 2 ? '23:59:59' : null, targetLabel, '结束日期');
  if (target.key === 'orders') {
    await assertVisibleOrderRowsWithinRange(page, effectiveRange.from, effectiveRange.to);
  } else if (target.key === 'fund_flows') {
    await assertVisibleFundFlowRowsWithinRange(frame, effectiveRange.from, effectiveRange.to);
  }

  const expectedFrom = timeInputCount >= 2 ? `${effectiveRange.from} 00:00:00` : effectiveRange.from;
  const expectedTo = timeInputCount >= 2 ? `${effectiveRange.to} 23:59:59` : effectiveRange.to;

  log('date_filter_applied', {
    target: target.key,
    from: expectedFrom,
    to: expectedTo
  });
  return {
    applied: true,
    data_coverage: 'requested_date_range',
    date_range_semantics: 'requested_range_applied',
    effective_date_range: { from: expectedFrom, to: expectedTo }
  };
}

async function resolveEffectiveDateRange(frame, task, target) {
  if (target.key !== 'fund_flows') {
    return { from: task.dateFrom, to: task.dateTo };
  }
  const notices = frame.getByText(/资金流水数据截止至\s*\d{4}-\d{2}-\d{2}/);
  const count = Math.min(await notices.count(), 20);
  for (let index = 0; index < count; index += 1) {
    const notice = notices.nth(index);
    if (!await notice.isVisible({ timeout: 300 }).catch(() => false)) continue;
    const text = cleanText(await notice.innerText({ timeout: 1000 }).catch(() => ''));
    const cutoff = text.match(/资金流水数据截止至\s*(\d{4}-\d{2}-\d{2})/)?.[1];
    if (!cutoff || cutoff >= task.dateTo) break;
    if (cutoff < task.dateFrom) {
      throw new Error(`资金流水当前仅更新至 ${cutoff}，早于请求开始日期 ${task.dateFrom}`);
    }
    log('date_range_adjusted_to_available_data', {
      target: target.key,
      requested_to: task.dateTo,
      effective_to: cutoff,
      reason: 'fund_flow_data_cutoff'
    });
    return { from: task.dateFrom, to: cutoff };
  }
  return { from: task.dateFrom, to: task.dateTo };
}

async function locateVisibleDateRangeControls(page) {
  for (const frame of page.frames()) {
    for (const [startPlaceholder, endPlaceholder] of DATE_INPUT_PLACEHOLDER_PAIRS) {
      const startInputs = frame.getByPlaceholder(startPlaceholder, { exact: true });
      const endInputs = frame.getByPlaceholder(endPlaceholder, { exact: true });
      const pairCount = Math.min(await startInputs.count(), await endInputs.count());
      for (let index = 0; index < pairCount; index += 1) {
        const startInput = startInputs.nth(index);
        const endInput = endInputs.nth(index);
        const bothVisible = await Promise.all([
          startInput.isVisible({ timeout: 600 }).catch(() => false),
          endInput.isVisible({ timeout: 600 }).catch(() => false)
        ]);
        const picker = startInput.locator('xpath=ancestor::dl[contains(@class, "weui-desktop-picker__date-range")][1]');
        const panelVisible = await picker.locator('.weui-desktop-picker__dd')
          .isVisible({ timeout: 300 })
          .catch(() => false);
        if (bothVisible.every(Boolean) || panelVisible) {
          return { frame, startInput, endInput, picker, startPlaceholder, endPlaceholder };
        }
      }
    }
  }
  return null;
}

async function openTargetDateRangePicker(page, target) {
  const triggerId = COMPASS_EMBED_DATE_TRIGGER_IDS[target.key];
  if (triggerId) {
    for (const frame of page.frames()) {
      const trigger = frame.locator(`[data-eleid="${triggerId}"] .target`).first();
      if (!await trigger.isVisible({ timeout: 500 }).catch(() => false)) continue;
      await trigger.click({ timeout: 5000 });
      await frame.locator(`[data-eleid="${triggerId}"] .weui-desktop-picker__dd`)
        .waitFor({ state: 'visible', timeout: 5000 });
      return;
    }
  }
  if (target.key === 'compass_buyer_profile') {
    for (const frame of page.frames()) {
      const candidates = frame.getByText('自定义', { exact: true });
      const count = Math.min(await candidates.count(), 20);
      for (let index = 0; index < count; index += 1) {
        const candidate = candidates.nth(index);
        if (!await candidate.isVisible({ timeout: 300 }).catch(() => false)) continue;
        const safeSelector = await candidate.evaluate((element) => {
          const clickable = element.closest('.selector-item') ?? element;
          const rect = clickable.getBoundingClientRect();
          const style = window.getComputedStyle(clickable);
          return style.display !== 'none'
            && style.visibility !== 'hidden'
            && rect.width > 0
            && rect.height > 0
            && rect.width < 420
            && rect.height < 120;
        }).catch(() => false);
        if (!safeSelector) continue;
        await candidate.click({ timeout: 5000 });
        await frame.getByPlaceholder('开始日期', { exact: true })
          .waitFor({ state: 'visible', timeout: 5000 });
        return;
      }
    }
  }
}

async function locateVisibleQueryButton(frame, picker = null) {
  if (picker) {
    const filterScope = picker.locator(
      'xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " search_input_wrp ")][1]'
    );
    if (await filterScope.count().catch(() => 0)) {
      const scoped = await locateVisibleExactQueryAction(filterScope, ['查询', '搜索']);
      if (scoped) return scoped;
    }
  }

  const query = await locateVisibleExactQueryAction(frame, ['查询']);
  if (query) return query;
  return locateVisibleExactQueryAction(frame, ['搜索'], { rejectGlobalHeaderSearch: true });
}

async function locateVisibleExactQueryAction(scope, labels, options = {}) {
  for (const label of labels) {
    const roleCandidates = scope.getByRole('button', { name: label, exact: true });
    const roleCount = Math.min(await roleCandidates.count(), 10);
    for (let index = 0; index < roleCount; index += 1) {
      const candidate = roleCandidates.nth(index);
      if (await isSafeVisibleQueryAction(candidate, options)) {
        return candidate;
      }
    }

    const textCandidates = scope.getByText(label, { exact: true });
    const textCount = Math.min(await textCandidates.count(), 20);
    for (let index = 0; index < textCount; index += 1) {
      const candidate = textCandidates.nth(index);
      if (await isSafeVisibleQueryAction(candidate, options)) {
        return candidate;
      }
    }
  }
  return null;
}

async function isSafeVisibleQueryAction(candidate, { rejectGlobalHeaderSearch = false } = {}) {
  return candidate.evaluate((element, rejectHeaderSearch) => {
    const clickable = element.closest('button,a,[role="button"],.weui-desktop-btn,.sub-btn') ?? element;
    const rect = clickable.getBoundingClientRect();
    const style = window.getComputedStyle(clickable);
    if (
      style.display === 'none'
      || style.visibility === 'hidden'
      || Number(style.opacity || 1) === 0
      || rect.width <= 0
      || rect.height <= 0
      || rect.width >= 360
      || rect.height >= 140
    ) {
      return false;
    }
    if (
      rejectHeaderSearch
      && (clickable.matches('.search_input__button') || Boolean(clickable.closest('.search_bar')))
    ) {
      return false;
    }
    return true;
  }, rejectGlobalHeaderSearch).catch(() => false);
}

function assertFundFlowQueryRequestRange(responseUrl, dateFrom, dateTo) {
  const url = new URL(responseUrl);
  const startTime = Number(url.searchParams.get('startTime'));
  const endTime = Number(url.searchParams.get('endTime'));
  if (!Number.isFinite(startTime) || startTime <= 0 || !Number.isFinite(endTime) || endTime <= 0) {
    throw new Error('资金流水日期查询请求未带有有效的 startTime/endTime，已停止导出全店数据');
  }
  const expectedStart = chinaDateEpochSeconds(dateFrom);
  const expectedEndStart = chinaDateEpochSeconds(dateTo);
  const expectedEndExclusive = expectedEndStart + 86400;
  if (Math.abs(startTime - expectedStart) > 1) {
    throw new Error(`资金流水查询开始时间未生效：期望 ${dateFrom}，请求值 ${startTime}`);
  }
  if (endTime < expectedEndStart || endTime > expectedEndExclusive + 1) {
    throw new Error(`资金流水查询结束时间未生效：期望 ${dateTo}，请求值 ${endTime}`);
  }
  return { start_time: startTime, end_time: endTime };
}

function chinaDateEpochSeconds(value) {
  return Math.floor(Date.parse(`${value}T00:00:00+08:00`) / 1000);
}

function prioritizeTargetExportControls(candidates, target) {
  if (target.key !== 'orders') {
    return candidates;
  }
  // The order page can retain a previous success-state “下载数据” action.
  // Always prefer the current explicit export action before enforcing the
  // single-artifact order export contract.
  return [...candidates].sort((left, right) =>
    orderExportControlPriority(left.label) - orderExportControlPriority(right.label)
  );
}

function orderExportControlPriority(labelValue) {
  const label = cleanText(labelValue);
  if (/导出/.test(label)) {
    return 0;
  }
  if (GENERATED_DOWNLOAD_LABEL_PATTERN.test(label)) {
    return 2;
  }
  return 1;
}

function unappliedDateFilter() {
  return {
    applied: false,
    data_coverage: 'page_current_filters',
    date_range_semantics: 'requested_only_not_applied',
    effective_date_range: null
  };
}

function parseIsoDateParts(value) {
  const [year, month, day] = String(value).split('-').map(Number);
  return { year, month, day, monthIndex: year * 12 + month - 1 };
}

async function navigatePickerUntilMonthVisible(picker, date, targetLabel = '订单') {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    const panels = picker.locator(DATE_PANEL_SELECTOR);
    const months = [];
    for (let index = 0; index < await panels.count(); index += 1) {
      months.push(await readPickerPanelMonth(panels.nth(index)));
    }
    const matchedIndex = months.findIndex((month) => month?.monthIndex === date.monthIndex);
    if (matchedIndex >= 0) {
      return panels.nth(matchedIndex);
    }

    const validMonths = months.filter(Boolean);
    if (validMonths.length === 0) {
      break;
    }
    const earliest = Math.min(...validMonths.map((month) => month.monthIndex));
    const latest = Math.max(...validMonths.map((month) => month.monthIndex));
    const direction = date.monthIndex < earliest ? 'left' : date.monthIndex > latest ? 'right' : null;
    if (!direction) {
      break;
    }
    const arrow = picker.locator(`.weui-desktop-btn__icon__${direction}`).first();
    if (!await arrow.isVisible({ timeout: 500 }).catch(() => false)) {
      throw new Error(`${targetLabel}日期控件无法切换到 ${date.year}-${String(date.month).padStart(2, '0')}`);
    }
    await arrow.click({ timeout: 3000 });
  }
  throw new Error(`${targetLabel}日期控件未找到 ${date.year}-${String(date.month).padStart(2, '0')} 月份`);
}

async function readPickerPanelMonth(panel) {
  const text = cleanText(await panel.locator('.weui-desktop-picker__panel__hd').innerText({ timeout: 1000 }));
  const match = text.match(/(\d{4})年\s*(\d{1,2})月/);
  if (!match) {
    return null;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  return { year, month, monthIndex: year * 12 + month - 1 };
}

async function selectPickerDay(picker, date, targetLabel = '订单') {
  const panel = await navigatePickerUntilMonthVisible(picker, date, targetLabel);
  const day = panel
    .locator('a:not(.weui-desktop-picker__faded):not(.weui-desktop-picker__disabled)')
    .filter({ hasText: new RegExp(`^${date.day}$`) });
  if (await day.count() !== 1) {
    throw new Error(`${targetLabel}日期控件未找到可选日期 ${date.year}-${String(date.month).padStart(2, '0')}-${String(date.day).padStart(2, '0')}`);
  }
  await day.click({ timeout: 3000 });
}

async function assertDateInputValue(input, expectedDate, expectedTime, targetLabel, label) {
  const actual = await input.inputValue();
  const expected = expectedTime ? `${expectedDate} ${expectedTime}` : expectedDate;
  const actualMatch = actual.match(/(\d{4})[-/](\d{2})[-/](\d{2})(?:\s+(\d{2}:\d{2}:\d{2}))?/);
  const actualDate = actualMatch ? `${actualMatch[1]}-${actualMatch[2]}-${actualMatch[3]}` : null;
  const dateMatches = actualDate === expectedDate;
  const timeMatches = !expectedTime || !actualMatch?.[4] || actualMatch[4] === expectedTime;
  if (!dateMatches || !timeMatches) {
    throw new Error(`${targetLabel}${label}未生效：期望 ${expected}，实际 ${actual || '空'}`);
  }
}

async function assertVisibleOrderRowsWithinRange(page, dateFrom, dateTo) {
  const visibleRowTimes = await page.locator('.createTime').evaluateAll((elements) => elements
    .filter((element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    })
    .map((element) => String(element.textContent ?? '')));
  const outsideRange = visibleRowTimes.find((text) => {
    const match = text.match(/(\d{4}-\d{2}-\d{2})/);
    return match && (match[1] < dateFrom || match[1] > dateTo);
  });
  if (outsideRange) {
    throw new Error(`订单查询结果仍包含请求范围外的数据：${cleanText(outsideRange)}`);
  }
}

async function assertVisibleFundFlowRowsWithinRange(frame, dateFrom, dateTo) {
  const visibleRows = frame.locator('tbody tr:visible');
  const rowTexts = [];
  const count = Math.min(await visibleRows.count(), 200);
  for (let index = 0; index < count; index += 1) {
    rowTexts.push(cleanText(await visibleRows.nth(index).innerText({ timeout: 1000 }).catch(() => '')));
  }
  const visibleDates = rowTexts.flatMap((text) => text.match(/\d{4}-\d{2}-\d{2}/g) ?? []);
  const outsideRange = visibleDates.find((date) => date < dateFrom || date > dateTo);
  if (outsideRange) {
    throw new Error(`资金流水查询结果仍包含请求范围外的数据：${outsideRange}`);
  }
  if (visibleDates.length === 0) {
    const bodyText = cleanText(await frame.locator('body').innerText({ timeout: 1500 }).catch(() => ''));
    if (!/暂无数据|暂无相关数据|无数据|暂无记录/.test(bodyText)) {
      throw new Error('资金流水查询后无法从可见表格回读记账日期，已停止导出以避免导出全店数据');
    }
  }
}

async function navigateToTarget(page, target) {
  if (target.key === 'compass_buyer_profile') {
    return navigateToCompassBuyerProfile(page, target);
  }
  if (isAllowedStoreRoute(page.url(), target)) {
    return { method: 'already_on_allowlisted_route', url: page.url() };
  }

  const allowlistedUrl = new URL(target.route, STORE_ORIGIN).toString();
  if (!isAllowedStoreRoute(allowlistedUrl, target)) {
    throw new Error(`目标 ${target.key} 的路由未通过安全白名单校验`);
  }

  let routeError = null;
  try {
    await page.goto(allowlistedUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await waitForNetworkSettle(page);
    if (isAllowedStoreRoute(page.url(), target)) {
      return { method: 'allowlisted_route', url: page.url() };
    }
  } catch (error) {
    routeError = error;
    log('allowlisted_route_navigation_fallback', {
      target: target.key,
      error: errorMessage(error)
    });
  }

  if (isAllowedStoreRoute(page.url(), target)) {
    await waitForNetworkSettle(page);
    return {
      method: 'allowlisted_route_after_navigation_error',
      url: page.url(),
      route_error: routeError ? errorMessage(routeError) : null
    };
  }

  const state = await currentLoginState(page);
  if (state === 'login_required') {
    throw fatal('LOGIN_REQUIRED', '登录状态已失效，已停止导出');
  }

  let menuError = null;
  try {
    const navigated = await navigateByVisibleMenu(page, target);
    if (navigated && isAllowedStoreRoute(page.url(), target)) {
      await waitForNetworkSettle(page);
      return {
        method: 'visible_left_menu_after_route_failure',
        url: page.url(),
        route_error: routeError ? errorMessage(routeError) : null
      };
    }
  } catch (error) {
    menuError = error;
  }

  throw new Error(
    `白名单页面跳转后落在非预期地址：${page.url()}`
    + `${routeError ? `；直接导航错误：${errorMessage(routeError)}` : ''}`
    + `${menuError ? `；菜单导航错误：${errorMessage(menuError)}` : ''}`
  );
}

async function navigateToCompassBuyerProfile(page, target) {
  const gatewayUrl = new URL(target.route, STORE_ORIGIN).toString();
  await page.goto(gatewayUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await waitForNetworkSettle(page);
  if (isAllowedStoreRoute(page.url(), target)) {
    return { method: 'compass_gateway_direct_persona', url: page.url() };
  }

  if (new URL(page.url()).pathname.startsWith('/compass/home')) {
    const candidates = page.getByText('买家人群特征', { exact: true });
    const count = Math.min(await candidates.count(), 20);
    for (let index = 0; index < count; index += 1) {
      const candidate = candidates.nth(index);
      if (!await candidate.isVisible({ timeout: 300 }).catch(() => false)) continue;
      const safeCardLink = await candidate.evaluate((element) => {
        const clickable = element.closest('.title.link,a,button,[role="button"],[role="link"]') ?? element;
        const rect = clickable.getBoundingClientRect();
        const style = window.getComputedStyle(clickable);
        return style.display !== 'none'
          && style.visibility !== 'hidden'
          && rect.width > 0
          && rect.height > 0
          && /买家人群特征/.test(String(clickable.innerText || clickable.textContent || ''));
      }).catch(() => false);
      if (!safeCardLink) continue;
      const navigated = page.waitForURL(
        (url) => isAllowedStoreRoute(url.toString(), target),
        { timeout: 12000 }
      ).then(() => true).catch(() => false);
      await candidate.click({ timeout: 5000 });
      if (await navigated || isAllowedStoreRoute(page.url(), target)) {
        await waitForNetworkSettle(page);
        return { method: 'compass_home_buyer_profile_card', url: page.url() };
      }
    }
  }

  const currentUrl = new URL(page.url());
  const directUrl = new URL('/compass/persona/home', STORE_ORIGIN);
  for (const key of ['source', 'appid']) {
    const value = currentUrl.searchParams.get(key);
    if (value) directUrl.searchParams.set(key, value);
  }
  await page.goto(directUrl.toString(), { waitUntil: 'domcontentloaded', timeout: 30000 });
  await waitForNetworkSettle(page);
  if (!isAllowedStoreRoute(page.url(), target)) {
    throw new Error(`买家人群特征页面导航失败，当前地址：${page.url()}`);
  }
  return { method: 'compass_initialized_direct_persona', url: page.url() };
}

async function navigateByVisibleMenu(page, target) {
  const [groupLabel, leafLabel] = target.menuPath;
  let leaf = await findLeftNavigationLocator(page, leafLabel);
  if (!leaf && groupLabel) {
    const group = await findLeftNavigationLocator(page, groupLabel);
    if (!group) {
      throw new Error(`未找到左侧菜单分组“${groupLabel}”`);
    }
    await group.click({ timeout: 5000 });
    await page.getByText(leafLabel, { exact: true }).first().waitFor({ state: 'visible', timeout: 3000 }).catch(() => {});
    leaf = await findLeftNavigationLocator(page, leafLabel);
  }
  if (!leaf) {
    throw new Error(`未找到左侧菜单“${leafLabel}”`);
  }

  const expectedUrl = page.waitForURL(
    (url) => isAllowedStoreRoute(url.toString(), target),
    { timeout: 12000 }
  ).then(() => true).catch(() => false);
  await leaf.click({ timeout: 5000 });
  const matched = await expectedUrl;
  return matched || isAllowedStoreRoute(page.url(), target);
}

async function findLeftNavigationLocator(page, label) {
  const locator = page.getByText(label, { exact: true });
  const count = Math.min(await locator.count(), 30);
  for (let index = 0; index < count; index += 1) {
    const candidate = locator.nth(index);
    const candidateHandle = await candidate.elementHandle().catch(() => null);
    if (!candidateHandle) {
      continue;
    }
    const clickableHandle = await candidateHandle.evaluateHandle((element) =>
      element.closest('a,button,[role="menuitem"],[role="button"]') ?? element
    ).catch(() => null);
    await candidateHandle.dispose().catch(() => {});
    const clickable = clickableHandle?.asElement?.() ?? null;
    if (!clickable) {
      await clickableHandle?.dispose?.().catch(() => {});
      continue;
    }
    const allowed = await clickable.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      const navigationRoot = element.closest('aside,nav,[role="navigation"]');
      const visible = style.display !== 'none'
        && style.visibility !== 'hidden'
        && Number(style.opacity || 1) !== 0
        && rect.width > 0
        && rect.height > 0;
      const inLeftRail = rect.left < Math.min(520, window.innerWidth * 0.46)
        && rect.width <= Math.max(480, window.innerWidth * 0.5);
      return visible && Boolean(navigationRoot || inLeftRail);
    }).catch(() => false);
    if (allowed) {
      return clickable;
    }
    await clickable.dispose().catch(() => {});
  }
  return null;
}

async function selectSafeSurface(page, label) {
  for (const frame of page.frames()) {
    const roleLocators = [
      frame.getByRole('tab', { name: label, exact: true }),
      frame.getByRole('button', { name: label, exact: true }),
      frame.getByRole('link', { name: label, exact: true })
    ];
    for (const locator of roleLocators) {
      const count = Math.min(await locator.count(), 10);
      for (let index = 0; index < count; index += 1) {
        const candidate = locator.nth(index);
        if (!await candidate.isVisible({ timeout: 300 }).catch(() => false)) {
          continue;
        }
        const alreadySelected = await candidate.getAttribute('aria-selected').catch(() => null);
        if (alreadySelected !== 'true') {
          await candidate.click({ timeout: 5000 });
          await waitForNetworkSettle(page);
        }
        return {
          selected: true,
          label,
          frame_url: frame.url(),
          frame_name: frame.name(),
          already_selected: alreadySelected === 'true'
        };
      }
    }

    const textLocator = frame.getByText(label, { exact: true });
    const count = Math.min(await textLocator.count(), 20);
    for (let index = 0; index < count; index += 1) {
      const candidate = textLocator.nth(index);
      const safeTab = await candidate.evaluate((element) => {
        const clickable = element.closest('[role="tab"],button,a,[role="button"],.tab-item-bar,[class*="tab-item"]') ?? element;
        const tablist = clickable.closest('[role="tablist"]');
        const visualTablist = clickable.closest('.tab-bar,.tab-wrap,[class*="tab-bar"],[class*="tab-wrap"]');
        const rect = clickable.getBoundingClientRect();
        const style = window.getComputedStyle(clickable);
        return Boolean(tablist || visualTablist || clickable.matches('[role="tab"],button,a,[role="button"]'))
          && style.display !== 'none'
          && style.visibility !== 'hidden'
          && rect.width > 0
          && rect.height > 0;
      }).catch(() => false);
      if (safeTab) {
        await candidate.click({ timeout: 5000 });
        await waitForNetworkSettle(page);
        return {
          selected: true,
          label,
          frame_url: frame.url(),
          frame_name: frame.name(),
          already_selected: false
        };
      }
    }
  }
  return { selected: false, label, frame_url: null, frame_name: null, already_selected: false };
}

async function discoverExportControls(page) {
  const controls = [];
  const controlKeys = new Set();
  const frames = page.frames();
  for (let frameIndex = 0; frameIndex < frames.length; frameIndex += 1) {
    const frame = frames[frameIndex];
    const occurrenceCounts = new Map();
    for (const role of CONTROL_ROLES) {
      const roleLocator = frame.getByRole(role);
      const count = Math.min(await roleLocator.count(), 300);
      for (let index = 0; index < count; index += 1) {
        const locator = roleLocator.nth(index);
        if (!await locator.isVisible({ timeout: 200 }).catch(() => false)) {
          continue;
        }
        const info = await locator.evaluate(readControlInfo).catch(() => null);
        if (!info || info.disabled) {
          continue;
        }
        if (info.insideNavigationRoot) {
          continue;
        }
        const label = cleanText(info.label);
        if (!isSafeDiscoveredExportControl(label, info)) {
          continue;
        }
        const occurrenceKey = `${role}\u0000${label}`;
        const ordinal = occurrenceCounts.get(occurrenceKey) ?? 0;
        occurrenceCounts.set(occurrenceKey, ordinal + 1);
        const frameUrl = frame.url();
        const handle = await locator.elementHandle().catch(() => null);
        if (!handle) {
          continue;
        }
        const control = {
          key: `${frameIndex}|${frameUrl}|${role}|${label}|${ordinal}`,
          handle,
          frame,
          frameIndex,
          frameUrl,
          frameName: frame.name(),
          role,
          label,
          dataEleid: info.dataEleid,
          ordinal,
          contextText: cleanText(info.contextText).slice(0, 300),
          contextTexts: info.contextTexts.map((text) => cleanText(text).slice(0, 900))
        };
        controls.push(control);
        controlKeys.add(control.key);
      }
    }

    const textLocator = frame.getByText(EXPORT_CONTROL_LABEL_PATTERN);
    const textCount = Math.min(await textLocator.count(), 300);
    for (let index = 0; index < textCount; index += 1) {
      const locator = textLocator.nth(index);
      if (!await locator.isVisible({ timeout: 200 }).catch(() => false)) continue;
      const coveredBySemanticControl = await locator.evaluate((element) => Boolean(
        element.closest('a,button,[role="button"],[role="link"],[role="menuitem"]')
      )).catch(() => false);
      if (coveredBySemanticControl) continue;
      const info = await locator.evaluate(readControlInfo).catch(() => null);
      if (!info || info.disabled || info.insideNavigationRoot) continue;
      const label = cleanText(info.label);
      if (!isSafeDiscoveredExportControl(label, info)) continue;
      const role = 'text';
      const occurrenceKey = `${role}\u0000${label}`;
      const ordinal = occurrenceCounts.get(occurrenceKey) ?? 0;
      occurrenceCounts.set(occurrenceKey, ordinal + 1);
      const frameUrl = frame.url();
      const key = `${frameIndex}|${frameUrl}|${role}|${label}|${ordinal}`;
      if (controlKeys.has(key)) continue;
      const handle = await locator.elementHandle().catch(() => null);
      if (!handle) continue;
      controls.push({
        key,
        handle,
        frame,
        frameIndex,
        frameUrl,
        frameName: frame.name(),
        role,
        label,
        dataEleid: info.dataEleid,
        ordinal,
        contextText: cleanText(info.contextText).slice(0, 300),
        contextTexts: info.contextTexts.map((text) => cleanText(text).slice(0, 900))
      });
      controlKeys.add(key);
    }

    const stableDownloadLocator = frame.locator('[data-eleid$="_download_btn"]');
    const stableDownloadCount = Math.min(await stableDownloadLocator.count(), 100);
    for (let index = 0; index < stableDownloadCount; index += 1) {
      const locator = stableDownloadLocator.nth(index);
      if (!await locator.isVisible({ timeout: 200 }).catch(() => false)) continue;
      const info = await locator.evaluate(readControlInfo).catch(() => null);
      if (!info || info.disabled || info.insideNavigationRoot) continue;
      const label = cleanText(info.label);
      if (!isSafeDiscoveredExportControl(label, info)) continue;
      if (controls.some((item) => item.frameIndex === frameIndex && item.dataEleid === info.dataEleid)) continue;
      const handle = await locator.elementHandle().catch(() => null);
      if (!handle) continue;
      const key = `${frameIndex}|${frame.url()}|data-eleid|${info.dataEleid}`;
      controls.push({
        key,
        handle,
        frame,
        frameIndex,
        frameUrl: frame.url(),
        frameName: frame.name(),
        role: 'data-eleid',
        label,
        dataEleid: info.dataEleid,
        ordinal: index,
        contextText: cleanText(info.contextText).slice(0, 300),
        contextTexts: info.contextTexts.map((text) => cleanText(text).slice(0, 900))
      });
      controlKeys.add(key);
    }
  }
  return controls;
}

async function discoverScopedExportControls(page, target, surface) {
  const deadline = Date.now() + (target.key === 'compass_buyer_profile' ? 2500 : 15000);
  while (Date.now() < deadline) {
    const discovered = await discoverExportControls(page);
    const matched = discovered.filter((candidate) => candidateMatchesTargetScope(candidate, target, surface));
    const matchedHandles = new Set(matched.map((candidate) => candidate.handle));
    await Promise.all(discovered
      .filter((candidate) => !matchedHandles.has(candidate.handle))
      .map((candidate) => candidate.handle.dispose().catch(() => {})));
    if (matched.length > 0) return matched;
    await Promise.all(matched.map((candidate) => candidate.handle.dispose().catch(() => {})));
    await delay(Math.min(400, Math.max(1, deadline - Date.now())));
  }
  return [];
}

function readControlInfo(element) {
  const normalize = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
  const text = normalize(element.innerText || element.textContent);
  const aria = normalize(element.getAttribute('aria-label'));
  const title = normalize(element.getAttribute('title'));
  const rect = element.getBoundingClientRect();
  const contextTexts = [];
  let contextRoot = element.closest('[role="dialog"],tr,section,article,form,header') ?? element.parentElement;
  for (let depth = 0; contextRoot && depth < 8; depth += 1, contextRoot = contextRoot.parentElement) {
    const candidateText = normalize(contextRoot.innerText || contextRoot.textContent);
    if (candidateText && candidateText.length <= 1800 && !contextTexts.includes(candidateText)) {
      contextTexts.push(candidateText);
    }
  }
  const contextText = contextTexts.find((value) => value.length > text.length + 2) ?? contextTexts[0] ?? '';
  const navigationRoot = element.closest('aside,nav,[role="navigation"]');
  const inLeftRail = rect.left < Math.min(520, window.innerWidth * 0.46)
    && rect.width <= Math.max(480, window.innerWidth * 0.5);
  return {
    label: aria || text || title,
    contextText,
    contextTexts,
    insideNavigationRoot: Boolean(navigationRoot),
    inLeftRail,
    insideMain: Boolean(element.closest('main,[role="main"]')),
    dataEleid: normalize(element.getAttribute('data-eleid')),
    disabled: Boolean(element.disabled)
      || element.getAttribute('aria-disabled') === 'true'
      || element.hasAttribute('disabled')
  };
}

function isSafeDiscoveredExportControl(label, info) {
  if (/^(?:live|trade|product|shop|fund)[-_].*[-_]download[-_]btn$/i.test(info.dataEleid || '')) {
    return isSafeExportControlLabel(label);
  }
  return isSafePageExportCandidate(label, info.contextText);
}

async function triggerDownloadFromControl({
  page,
  target = null,
  candidate,
  timeoutMs,
  verificationTimeoutMs = timeoutMs
}) {
  await dismissTransientUiBeforeExport(page);
  const fundFlowExport = isFundFlowExportControl(page, candidate);
  if (fundFlowExport) {
    await dismissStaleFundFlowSuccess(page);
  }
  const downloadOutcome = page.waitForEvent('download', { timeout: timeoutMs })
    .then((download) => ({ kind: 'download', download }))
    .catch((error) => ({ kind: 'timeout', error }));
  const dialogOutcome = ignoreRejection(
    waitForVerifiedExportDialog(page, Math.min(timeoutMs, 8000))
      .then((dialog) => ({ kind: 'dialog', dialog }))
  );
  const popupOutcome = ignoreRejection(
    page.waitForEvent('popup', { timeout: Math.min(timeoutMs, 8000) })
      .then((popup) => ({ kind: 'popup', popup }))
  );
  const fundFlowPageStateOutcome = fundFlowExport
    ? ignoreRejection(
      waitForFundFlowPageExportState(page, timeoutMs)
        .then((state) => ({ kind: 'fund_flow_page_state', state }))
    )
    : new Promise(() => {});

  // The download listener above must remain registered before every click.
  await clickVerifiedExportControl(page, candidate);
  const outcome = await Promise.race([
    downloadOutcome,
    dialogOutcome,
    popupOutcome,
    fundFlowPageStateOutcome
  ]);

  if (outcome.kind === 'download') {
    return outcome.download;
  }
  if (outcome.kind === 'fund_flow_page_state') {
    log('fund_flow_export_page_state_detected', {
      state: outcome.state.kind,
      text: outcome.state.text.slice(0, 500)
    });
    return waitForGeneratedExportCompletion({
      page,
      downloadOutcome,
      generationTimeoutMs: timeoutMs,
      downloadTimeoutMs: timeoutMs
    });
  }
  if (outcome.kind === 'popup') {
    return downloadFromVerifiedPopup(outcome.popup, timeoutMs, { target });
  }
  if (outcome.kind === 'dialog') {
    return completeExportDialog({
      page,
      dialog: outcome.dialog,
      target,
      candidate,
      timeoutMs,
      verificationTimeoutMs
    });
  }
  throw new Error(`点击“${candidate.label}”后 ${timeoutMs}ms 内未捕获下载事件或导出弹窗`);
}

function isFundFlowExportControl(page, candidate) {
  return FUND_FLOW_ROUTE_PATTERN.test(String(candidate?.frameUrl || page.url()));
}

async function dismissStaleFundFlowSuccess(page) {
  for (const frame of page.frames()) {
    const messages = frame.locator(FUND_FLOW_SUCCESS_SELECTOR);
    const count = Math.min(await messages.count().catch(() => 0), 20);
    for (let index = 0; index < count; index += 1) {
      const message = messages.nth(index);
      if (!await message.isVisible({ timeout: 100 }).catch(() => false)) {
        continue;
      }
      const text = cleanText(await message.innerText({ timeout: 500 }).catch(() => ''));
      if (
        !/资金流水/.test(text)
        || !EXPORT_SUCCESS_TEXT_PATTERN.test(text)
        || !/下载数据/.test(text)
      ) {
        continue;
      }
      const closeAction = await findSafeMessageCloseAction(message);
      if (closeAction) {
        await closeAction.click({ timeout: 3000 });
      } else {
        await page.keyboard.press('Escape').catch(() => {});
      }
      const dismissed = await message.waitFor({ state: 'hidden', timeout: 2000 })
        .then(() => true)
        .catch(() => false);
      if (!dismissed) {
        throw new Error('资金流水页面残留上次导出成功提示，且无法安全关闭；已停止以避免下载上次任务的旧文件');
      }
      log('stale_fund_flow_export_success_dismissed', {
        text: text.slice(0, 300)
      });
    }
  }
}

async function waitForFundFlowPageExportState(page, timeoutMs) {
  const outcomes = [
    [EXPORT_SUCCESS_TEXT_PATTERN, 'success'],
    [EXPORT_FAILURE_TEXT_PATTERN, 'failure'],
    [FUND_FLOW_GENERATION_TEXT_PATTERN, 'generation']
  ].map(([pattern, kind]) => ignoreRejection(
    waitForVisibleExportText(page, pattern, timeoutMs)
      .then((state) => ({ kind, text: state.text }))
  ));
  return Promise.race(outcomes);
}

async function findSafeMessageCloseAction(message) {
  const actions = message.locator('button,[role="button"],a,[class*="close" i]');
  const count = Math.min(await actions.count().catch(() => 0), 40);
  for (let index = 0; index < count; index += 1) {
    const action = actions.nth(index);
    if (!await action.isVisible({ timeout: 100 }).catch(() => false)) {
      continue;
    }
    const info = await action.evaluate((element) => ({
      label: String(
        element.getAttribute('aria-label')
        || element.getAttribute('title')
        || element.innerText
        || element.textContent
        || ''
      ).replace(/\s+/g, ' ').trim(),
      html: element.outerHTML.slice(0, 600)
    })).catch(() => null);
    if (!info || GENERATED_DOWNLOAD_LABEL_PATTERN.test(info.label)) {
      continue;
    }
    if (/关闭|close|icon[_-]*(?:close|cancel)|(?:msg|message).*__close/i.test(`${info.label} ${info.html}`)) {
      return action;
    }
  }
  return null;
}

async function dismissTransientUiBeforeExport(page) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const visibleDialog = await firstVisibleVerifiedExportDialog(page);
    if (!visibleDialog) break;
    await page.keyboard.press('Escape').catch(() => {});
    await delay(120);
  }
  // Header dropdowns such as “经营成长” are not export dialogs but can cover
  // the verified export link. Escape is a safe way to dismiss those overlays.
  await page.keyboard.press('Escape').catch(() => {});
  await delay(80);
}

async function firstVisibleVerifiedExportDialog(page) {
  for (const frame of page.frames()) {
    const dialog = await locateVisibleVerifiedDialog(frame).catch(() => null);
    if (dialog && isExportDialogText(dialog.text)) return dialog;
  }
  return null;
}

async function clickVerifiedExportControl(page, candidate) {
  try {
    await candidate.handle.click({ timeout: 8000 });
    return;
  } catch (error) {
    if (!/intercepts pointer events|Timeout/i.test(errorMessage(error))) throw error;
  }

  await page.keyboard.press('Escape').catch(() => {});
  await delay(120);
  try {
    await candidate.handle.click({ timeout: 3000 });
    return;
  } catch (error) {
    if (!/intercepts pointer events|Timeout/i.test(errorMessage(error))) throw error;
  }

  // The candidate has already passed the route, visibility, context and label
  // safety checks. Force only this verified element when a floating header is
  // still intercepting pointer events.
  await candidate.handle.click({ timeout: 3000, force: true });
}

async function waitForVerifiedExportDialog(page, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (const frame of page.frames()) {
      const dialog = await locateVisibleVerifiedDialog(frame).catch(() => null);
      if (dialog) {
        return dialog;
      }
    }
    await delay(Math.min(200, Math.max(1, deadline - Date.now())));
  }
  throw new Error(`在 ${timeoutMs}ms 内未找到当前可见的安全导出弹窗`);
}

async function locateVisibleVerifiedDialog(frame) {
  const dialogs = frame.locator(EXPORT_DIALOG_SELECTOR);
  const count = Math.min(await dialogs.count(), 100);
  for (let index = 0; index < count; index += 1) {
    const locator = dialogs.nth(index);
    if (!await locator.isVisible({ timeout: 200 }).catch(() => false)) {
      continue;
    }
    const box = await locator.boundingBox().catch(() => null);
    if (!box || box.width <= 0 || box.height <= 0) {
      continue;
    }
    const text = cleanText(await locator.innerText({ timeout: 1000 }).catch(() => ''));
    if (EXPORT_DIALOG_TEXT_PATTERN.test(text) && isExportDialogText(text)) {
      return verifiedDialog(frame, locator);
    }
  }
  throw new Error('检测到导出弹窗状态，但未找到当前可见的安全弹窗容器');
}

async function verifiedDialog(frame, locator) {
  const text = cleanText(await locator.innerText({ timeout: 1000 }));
  if (!isExportDialogText(text)) {
    throw new Error('检测到弹窗，但弹窗内容不是明确导出/下载流程');
  }
  return { frame, locator, text };
}

async function completeExportDialog({
  page,
  dialog,
  target = null,
  candidate = null,
  timeoutMs,
  verificationTimeoutMs
}) {
  if (classifyExportDialogText(dialog.text) === 'verification') {
    return completeExportVerification({
      page,
      dialog,
      target,
      candidate,
      downloadTimeoutMs: timeoutMs,
      verificationTimeoutMs,
      generationTimeoutMs: verificationTimeoutMs
    });
  }

  if (isExportGenerationInProgress(dialog.text)) {
    const generationDownloadOutcome = page.waitForEvent('download', {
      timeout: Math.max(timeoutMs, verificationTimeoutMs)
    })
      .then((download) => ({ kind: 'download', download }))
      .catch((error) => ({ kind: 'download_timeout', error }));
    return waitForGeneratedExportCompletion({
      page,
      downloadOutcome: generationDownloadOutcome,
      generationTimeoutMs: Math.max(timeoutMs, verificationTimeoutMs),
      downloadTimeoutMs: timeoutMs
    });
  }

  const action = await findDialogAction(dialog);
  if (!action) {
    throw new Error('导出弹窗内没有安全的确认/下载按钮，未点击弹窗外任何确认控件');
  }

  // The confirmation dialog can consume most of the initial click timeout.
  // Register a fresh listener before the confirmed export action and grant it
  // the full download timeout.
  const confirmedDownloadOutcome = page.waitForEvent('download', {
    timeout: Math.max(timeoutMs, verificationTimeoutMs)
  })
    .then((download) => ({ kind: 'download', download }))
    .catch((error) => ({ kind: 'timeout', error }));
  await action.locator.click({ timeout: 8000 });
  if (GENERATED_DOWNLOAD_LABEL_PATTERN.test(action.label)) {
    const outcome = await confirmedDownloadOutcome;
    if (outcome.kind === 'download') {
      return outcome.download;
    }
    throw new Error(`点击导出弹窗内“${action.label}”后未捕获下载事件`);
  }

  return waitForGeneratedExportCompletion({
    page,
    downloadOutcome: confirmedDownloadOutcome,
    generationTimeoutMs: Math.max(timeoutMs, verificationTimeoutMs),
    downloadTimeoutMs: timeoutMs
  });
}

function isExportGenerationInProgress(value) {
  const text = cleanText(value);
  return /正在(?:导出|生成)|导出中|生成中|导出进度|(?:^|\s)\d{1,3}(?:\.\d+)?%/.test(text)
    && !EXPORT_SUCCESS_TEXT_PATTERN.test(text)
    && !EXPORT_FAILURE_TEXT_PATTERN.test(text);
}

async function completeExportVerification({
  page,
  dialog,
  target,
  candidate,
  downloadTimeoutMs,
  verificationTimeoutMs,
  generationTimeoutMs
}) {
  const challenge = await beginVerificationChallenge(verificationTimeoutMs, { target, candidate });
  const stopScreenshotPump = startVerificationScreenshotPump(page);
  try {
    log('export_verification_required', {
      challenge_id: challenge.challenge_id,
      target_key: challenge.target_key,
      target_label: challenge.target_label,
      control_label: challenge.control_label,
      message: '微信要求扫码确认导出；任务正在等待用户完成验证',
      timeout_ms: verificationTimeoutMs,
      screenshot_path: EXPORT_VERIFICATION_SCREENSHOT_PATH
    });

    // Register before the user scans: some WeChat export pages start the
    // download at the same moment the verification dialog disappears.
    const downloadOutcome = page.waitForEvent('download', {
      timeout: verificationTimeoutMs + generationTimeoutMs + downloadTimeoutMs
    }).then((download) => ({ kind: 'download', download }))
      .catch((error) => ({ kind: 'download_timeout', error }));
    const verificationOutcome = waitForVerificationTransition(dialog, verificationTimeoutMs)
      .then((state) => ({ kind: state.kind, text: state.text }))
      .catch((error) => ({ kind: 'verification_timeout', error }));

    const outcome = await Promise.race([downloadOutcome, verificationOutcome]);
    if (outcome.kind === 'download') {
      log('export_verification_completed', {
        challenge_id: challenge.challenge_id,
        method: 'download_started'
      });
      return outcome.download;
    }
    if (outcome.kind === 'verification_timeout') {
      throw fatal(
        'VERIFICATION_REQUIRED',
        `微信要求扫码确认本次导出，但 ${verificationTimeoutMs}ms 内未完成；请扫描本次导出验证二维码后重试`
      );
    }
    if (outcome.kind === 'verification_failed') {
      throw fatal(
        'VERIFICATION_FAILED',
        outcome.text || '微信扫码验证未通过；请刷新导出二维码后重试'
      );
    }
    if (outcome.kind === 'download_timeout') {
      throw new Error(
        `等待扫码确认、生成与下载超过 ${verificationTimeoutMs + generationTimeoutMs + downloadTimeoutMs}ms`
      );
    }

    log('export_verification_completed', {
      challenge_id: challenge.challenge_id,
      method: outcome.kind
    });
    return waitForGeneratedExportCompletion({
      page,
      downloadOutcome,
      generationTimeoutMs,
      downloadTimeoutMs
    });
  } finally {
    await stopScreenshotPump();
    await finishVerificationChallenge(challenge.challenge_id);
  }
}

async function beginVerificationChallenge(timeoutMs, { target = null, candidate = null } = {}) {
  const challenge = {
    challenge_id: crypto.randomUUID(),
    required: true,
    target_key: cleanText(target?.key),
    target_label: cleanText(target?.label),
    control_label: cleanText(candidate?.label),
    started_at: new Date().toISOString(),
    timeout_ms: timeoutMs,
    screenshot_path: EXPORT_VERIFICATION_SCREENSHOT_PATH
  };
  await writeJsonAtomic(EXPORT_VERIFICATION_STATUS_PATH, challenge);
  return challenge;
}

async function finishVerificationChallenge(challengeId) {
  const current = await fs.readFile(EXPORT_VERIFICATION_STATUS_PATH, 'utf8')
    .then((value) => JSON.parse(value))
    .catch(() => null);
  // A newer export may already be showing a fresh QR code. Never let an older
  // finally block clear that newer challenge.
  if (!current || current.challenge_id !== challengeId) {
    return;
  }
  await writeJsonAtomic(EXPORT_VERIFICATION_STATUS_PATH, {
    ...current,
    required: false,
    completed_at: new Date().toISOString()
  });
}

function startVerificationScreenshotPump(page) {
  let active = true;
  const temporaryPath = `${EXPORT_VERIFICATION_SCREENSHOT_PATH}.${process.pid}.tmp.png`;
  const pump = (async () => {
    await fs.mkdir(path.dirname(EXPORT_VERIFICATION_SCREENSHOT_PATH), { recursive: true });
    while (active) {
      await page.screenshot({ path: temporaryPath, timeout: 10000 })
        .then(() => fs.rename(temporaryPath, EXPORT_VERIFICATION_SCREENSHOT_PATH))
        .catch(() => {});
      if (active) {
        await delay(500);
      }
    }
  })();
  return async () => {
    active = false;
    await pump.catch(() => {});
    await fs.unlink(temporaryPath).catch(() => {});
    await fs.unlink(EXPORT_VERIFICATION_SCREENSHOT_PATH).catch(() => {});
  };
}

async function waitForVerificationTransition(dialog, timeoutMs) {
  const handle = await dialog.locator.elementHandle();
  if (!handle) {
    return { kind: 'verified', text: '' };
  }
  const transition = await dialog.frame.waitForFunction(
    (element) => {
      if (!element?.isConnected) {
        return { kind: 'dialog_closed', text: '' };
      }
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      const visible = style.display !== 'none'
        && style.visibility !== 'hidden'
        && Number(style.opacity || 1) !== 0
        && rect.width > 0
        && rect.height > 0;
      if (!visible) {
        return { kind: 'dialog_closed', text: '' };
      }
      const text = String(element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim();
      if (/用户取消扫码|验证未通过|二维码加载失败/.test(text)) {
        return { kind: 'verification_failed', text: text.slice(0, 500) };
      }
      if (!/扫码验证|扫码确认|微信扫码|使用微信扫码|已扫码|扫码.*(?:验证|确认)/.test(text)) {
        return { kind: 'verification_completed', text: text.slice(0, 500) };
      }
      return false;
    },
    handle,
    { timeout: timeoutMs }
  );
  return transition.jsonValue();
}

async function waitForGeneratedExportCompletion({
  page,
  downloadOutcome,
  generationTimeoutMs,
  downloadTimeoutMs
}) {
  const successOutcome = ignoreRejection(
    waitForVisibleExportText(page, EXPORT_SUCCESS_TEXT_PATTERN, generationTimeoutMs)
      .then((state) => ({ kind: 'success', state }))
  );
  const failureOutcome = ignoreRejection(
    waitForVisibleExportText(page, EXPORT_FAILURE_TEXT_PATTERN, generationTimeoutMs)
      .then((state) => ({ kind: 'failure', state }))
  );
  const outcome = await Promise.race([
    downloadOutcome,
    successOutcome,
    failureOutcome,
    timeoutOutcome(generationTimeoutMs, 'generation_timeout')
  ]);

  if (outcome.kind === 'download') {
    return outcome.download;
  }
  if (outcome.kind === 'failure') {
    throw new Error(`微信小店页面提示：${outcome.state.text}`);
  }
  if (outcome.kind === 'download_timeout' || outcome.kind === 'generation_timeout' || outcome.kind === 'timeout') {
    throw new Error(`导出生成超过 ${generationTimeoutMs}ms，未出现成功下载或可用结果`);
  }

  // Some WeChat success dialogs expose “下载数据” only briefly. Search for the
  // verified fallback action while the automatic download listener remains
  // active, and click the action as soon as it appears instead of waiting a
  // fixed grace period that can outlive the success dialog.
  // WeChat's product list shows an early success acknowledgement after the
  // QR check even though the server is still generating thousands of rows.
  // Keep the original generation budget here; limiting this fallback to the
  // download timeout caused large product lists to fail while their visible
  // progress banner was still advancing.
  const fallbackActionOutcome = waitForSafeGeneratedDownloadAction(
    outcome.state.frame,
    Math.max(downloadTimeoutMs, generationTimeoutMs)
  ).then((action) => ({ kind: action ? 'fallback_action' : 'fallback_action_timeout', action }));
  const nextOutcome = await Promise.race([downloadOutcome, fallbackActionOutcome]);
  if (nextOutcome.kind === 'download') {
    return nextOutcome.download;
  }
  if (nextOutcome.kind !== 'fallback_action' || !nextOutcome.action) {
    throw new Error('页面提示导出成功，但未找到成功提示区域内的安全“下载数据”按钮');
  }
  const fallbackAction = nextOutcome.action;
  const fallbackDownloadOutcome = page.waitForEvent('download', { timeout: downloadTimeoutMs })
    .then((download) => ({ kind: 'download', download }))
    .catch((error) => ({ kind: 'download_timeout', error }));
  await fallbackAction.locator.click({ timeout: 8000 });
  const fallbackDownload = await Promise.race([downloadOutcome, fallbackDownloadOutcome]);
  if (fallbackDownload.kind === 'download') {
    return fallbackDownload.download;
  }
  throw new Error(`点击成功提示内“${fallbackAction.label}”后未捕获下载事件`);
}

async function waitForVisibleExportText(page, pattern, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (const frame of page.frames()) {
      const matches = frame.getByText(pattern);
      const count = Math.min(await matches.count().catch(() => 0), 100);
      for (let index = 0; index < count; index += 1) {
        const item = matches.nth(index);
        if (!await item.isVisible({ timeout: 100 }).catch(() => false)) {
          continue;
        }
        const text = cleanText(await item.innerText({ timeout: 500 }).catch(() => ''));
        if (pattern.test(text)) {
          return { frame, text: text.slice(0, 800) };
        }
      }
    }
    await delay(Math.min(200, Math.max(1, deadline - Date.now())));
  }
  throw new Error(`在 ${timeoutMs}ms 内未出现预期导出状态：${pattern.source}`);
}

async function waitForSafeGeneratedDownloadAction(frame, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const locators = [
      frame.getByRole('button'),
      frame.getByRole('link'),
      frame.locator('a,button,[role="button"],[role="link"],[data-eleid*="download" i]')
    ];
    const visited = new Set();
    for (const locator of locators) {
      const count = Math.min(await locator.count(), 160);
      for (let index = 0; index < count; index += 1) {
        const item = locator.nth(index);
        if (!await item.isVisible({ timeout: 100 }).catch(() => false)) {
          continue;
        }
        const info = await item.evaluate((element) => {
          const label = String(element.getAttribute('aria-label') || element.innerText || element.textContent || '')
            .replace(/\s+/g, ' ')
            .trim();
          const contexts = [];
          let context = element;
          for (let depth = 0; context && depth < 8; depth += 1, context = context.parentElement) {
            contexts.push(String(context.innerText || context.textContent || '').replace(/\s+/g, ' ').trim());
          }
          const key = [element.tagName, label, element.getAttribute('data-eleid') || '', element.outerHTML.slice(0, 240)].join('|');
          return { key, label, contexts };
        }).catch(() => null);
        if (!info || visited.has(info.key) || !GENERATED_DOWNLOAD_LABEL_PATTERN.test(info.label)) {
          continue;
        }
        visited.add(info.key);
        if (!info.contexts.some((text) => EXPORT_SUCCESS_TEXT_PATTERN.test(text))) {
          continue;
        }
        return { locator: item, label: cleanText(info.label) };
      }
    }
    await delay(Math.min(200, Math.max(1, deadline - Date.now())));
  }
  return null;
}

async function createExportBundle({ task, downloadDir, artifacts }) {
  if (!Array.isArray(artifacts) || artifacts.length === 0) return null;

  const stagingDir = path.join(downloadDir, '.bundle-staging');
  await fs.rm(stagingDir, { recursive: true, force: true });
  await fs.mkdir(stagingDir, { recursive: true });
  try {
    for (const artifact of artifacts) {
      const sourcePath = String(artifact.saved_path || '');
      if (!sourcePath) continue;
      const moduleName = safePathSegment(artifact.page_label || artifact.module || artifact.export_type) || '其他数据';
      const moduleDir = path.join(stagingDir, moduleName);
      await fs.mkdir(moduleDir, { recursive: true });
      const filename = sanitizeDownloadFilename(
        artifact.original_filename || path.basename(sourcePath),
        `${safePathSegment(artifact.export_type) || 'data'}.bin`
      );
      const destination = await uniquePath(moduleDir, filename);
      await fs.copyFile(sourcePath, destination);
    }

    const entries = (await fs.readdir(stagingDir)).sort();
    if (entries.length === 0) {
      throw new Error('没有可加入压缩包的导出文件');
    }
    const bundleFilename = sanitizeDownloadFilename(
      `微信小店数据导出_${task.shopId}_${task.dateFrom}_${task.dateTo}.zip`,
      `wechat_store_export_${task.dateFrom}_${task.dateTo}.zip`
    );
    const bundlePath = await uniquePath(downloadDir, bundleFilename);
    await execFileAsync('python3', ['-m', 'zipfile', '-c', bundlePath, ...entries], {
      cwd: stagingDir,
      maxBuffer: 8 * 1024 * 1024
    });
    const stats = await fs.stat(bundlePath);
    return {
      source_kind: 'export_bundle',
      source_type: 'export_bundle',
      export_type: 'export_bundle',
      module: 'export_bundle',
      page_label: '本次导出全部文件',
      page_name: '本次导出全部文件',
      shop_id: task.shopId,
      shop_name: task.shopName || null,
      date_range: { from: task.dateFrom, to: task.dateTo },
      original_filename: path.basename(bundlePath),
      saved_path: bundlePath,
      relative_path: path.relative(downloadDir, bundlePath),
      sha256: await sha256File(bundlePath),
      size_bytes: stats.size,
      bundled_file_count: artifacts.length,
      status: 'completed',
      created_at: new Date().toISOString()
    };
  } finally {
    await fs.rm(stagingDir, { recursive: true, force: true }).catch(() => {});
  }
}

async function findDialogAction(dialog) {
  const candidates = [];
  for (const role of ['button', 'link']) {
    const locator = dialog.locator.getByRole(role);
    const count = Math.min(await locator.count(), 50);
    for (let index = 0; index < count; index += 1) {
      const item = locator.nth(index);
      if (!await item.isVisible({ timeout: 200 }).catch(() => false)) {
        continue;
      }
      const info = await item.evaluate((element) => ({
        label: String(element.getAttribute('aria-label') || element.innerText || element.textContent || element.getAttribute('title') || '')
          .replace(/\s+/g, ' ')
          .trim(),
        disabled: Boolean(element.disabled) || element.getAttribute('aria-disabled') === 'true' || element.hasAttribute('disabled')
      })).catch(() => null);
      if (!info || info.disabled || !isAllowedDialogConfirmLabel(info.label, dialog.text)) {
        continue;
      }
      candidates.push({
        locator: item,
        label: cleanText(info.label),
        priority: dialogActionPriority(info.label)
      });
    }
  }
  candidates.sort((left, right) => left.priority - right.priority);
  return candidates[0] ?? null;
}

async function waitForGeneratedDownloadAction(dialog, timeoutMs) {
  const waiters = [];
  for (const role of ['button', 'link']) {
    const locator = dialog.locator.getByRole(role, { name: GENERATED_DOWNLOAD_LABEL_PATTERN }).first();
    waiters.push(
      locator.waitFor({ state: 'visible', timeout: timeoutMs })
        .then(() => ({ locator, label: '下载' }))
    );
  }
  return Promise.any(waiters);
}

async function downloadFromVerifiedPopup(popup, timeoutMs, { target = null } = {}) {
  const popupDownloadOutcome = popup.waitForEvent('download', { timeout: timeoutMs })
    .then((download) => ({ kind: 'download', download }))
    .catch((error) => ({ kind: 'timeout', error }));
  const initialOutcome = await Promise.race([
    popupDownloadOutcome,
    popup.waitForLoadState('domcontentloaded', { timeout: 15000 })
      .then(() => ({ kind: 'loaded' }))
      .catch(() => ({ kind: 'loaded' }))
  ]);
  if (initialOutcome.kind === 'download') {
    await popup.close().catch(() => {});
    return initialOutcome.download;
  }
  const popupText = cleanText(await popup.locator('body').innerText({ timeout: 3000 }).catch(() => ''));
  const popupTitle = cleanText(await popup.title().catch(() => ''));
  if (!isExportDialogText(`${popupTitle} ${popupText}`)) {
    await popup.close().catch(() => {});
    throw new Error('导出控件打开了新窗口，但新窗口不是明确导出/下载页面');
  }

  const candidates = await discoverExportControls(popup);
  const candidate = candidates[0];
  if (!candidate) {
    const outcome = await popupDownloadOutcome;
    await popup.close().catch(() => {});
    if (outcome.kind === 'download') {
      return outcome.download;
    }
    throw new Error('明确导出/下载新窗口中未找到可安全点击的下载控件或自动下载');
  }

  try {
    const explicitOutcome = triggerDownloadFromControl({
      page: popup,
      target,
      candidate,
      timeoutMs
    })
      .then((download) => ({ kind: 'download', download }))
      .catch((error) => ({ kind: 'error', error }));
    const outcome = await Promise.race([popupDownloadOutcome, explicitOutcome]);
    if (outcome.kind === 'download') {
      return outcome.download;
    }
    const automaticOutcome = await popupDownloadOutcome;
    if (automaticOutcome.kind === 'download') {
      return automaticOutcome.download;
    }
    throw outcome.error;
  } finally {
    await popup.close().catch(() => {});
  }
}

async function saveDownloadArtifact({ download, task, target, candidate, downloadDir, pageUrl, dateFilter }) {
  const failure = await download.failure();
  if (failure) {
    throw new Error(`浏览器下载失败：${failure}`);
  }

  const moduleDir = path.join(downloadDir, safePathSegment(target.module) || 'unknown_module');
  await fs.mkdir(moduleDir, { recursive: true });
  const originalFilename = download.suggestedFilename();
  const fallback = `${safePathSegment(target.key) || 'download'}_${Date.now()}.bin`;
  const safeFilename = sanitizeDownloadFilename(originalFilename, fallback);
  if (!isSupportedDownloadFilename(safeFilename)) {
    throw new Error(`不支持下载文件类型：${safeFilename}`);
  }
  const savedPath = await uniquePath(moduleDir, safeFilename);
  await download.saveAs(savedPath);
  let dateContentValidation = null;
  try {
    if (target.key === 'fund_flows') {
      const validationFrom = String(dateFilter.effective_date_range?.from ?? task.dateFrom).slice(0, 10);
      const validationTo = String(dateFilter.effective_date_range?.to ?? task.dateTo).slice(0, 10);
      dateContentValidation = await validateFundFlowArtifactDateCoverage(
        savedPath,
        validationFrom,
        validationTo
      );
    }
  } catch (error) {
    await fs.rm(savedPath, { force: true }).catch(() => {});
    throw error;
  }
  const stats = await fs.stat(savedPath);
  const createdAt = new Date().toISOString();

  return {
    source_kind: 'export_file',
    source_type: 'export_file',
    export_type: target.key,
    module: target.module,
    page_label: target.label,
    page_name: target.label,
    menu_path: target.menuPath,
    surface_label: target.surfaceLabel,
    confidence: target.confidence,
    shop_id: task.shopId,
    shop_name: task.shopName || null,
    date_range: {
      from: task.dateFrom,
      to: task.dateTo
    },
    data_coverage: dateFilter.data_coverage,
    date_filter_applied: dateFilter.applied,
    date_range_semantics: dateFilter.date_range_semantics,
    effective_date_range: dateFilter.effective_date_range,
    date_content_validation: dateContentValidation,
    page_url: pageUrl,
    frame_url: candidate.frameUrl,
    trigger_label: candidate.label,
    original_filename: originalFilename,
    saved_path: savedPath,
    relative_path: path.relative(downloadDir, savedPath),
    sha256: await sha256File(savedPath),
    size_bytes: stats.size,
    status: 'completed',
    created_at: createdAt
  };
}

async function saveBuyerProfilePageSnapshot({ page, task, target, downloadDir, dateFilter }) {
  const frame = page.frames().find((item) => {
    try {
      return new URL(item.url()).pathname.startsWith('/compass/persona/home');
    } catch {
      return false;
    }
  }) ?? page.mainFrame();
  const bodyText = await frame.locator('body').innerText({ timeout: 5000 }).catch(() => '');
  const visibleLines = bodyText
    .split(/\r?\n/)
    .map((line) => cleanText(line))
    .filter(Boolean)
    .slice(0, 2000);
  if (visibleLines.length === 0) {
    throw new Error('买家人群特征页面没有可保存的可见数据');
  }

  const moduleDir = path.join(downloadDir, safePathSegment(target.module) || target.key);
  await fs.mkdir(moduleDir, { recursive: true });
  const filename = `买家人群特征_${task.dateFrom}_${task.dateTo}_页面快照.csv`;
  const savedPath = await uniquePath(moduleDir, filename);
  const sampleInsufficient = visibleLines.some((line) => /购买人群数量不足\s*10\s*人|人群数量不足/.test(line));
  const rows = [
    ['数据项', '内容'],
    ['店铺', task.shopName || task.shopId],
    ['请求日期范围', `${task.dateFrom} 至 ${task.dateTo}`],
    ['页面地址', frame.url()],
    ['页面状态', sampleInsufficient ? '购买人群数量不足，微信页面未展示画像明细' : '已保存页面可见买家人群特征'],
    ...visibleLines.map((line, index) => [`页面内容 ${index + 1}`, line])
  ];
  const csv = `\uFEFF${rows.map((row) => row.map(csvCell).join(',')).join('\r\n')}\r\n`;
  await fs.writeFile(savedPath, csv, 'utf8');
  const stats = await fs.stat(savedPath);
  const createdAt = new Date().toISOString();
  return {
    source_kind: 'page_snapshot',
    source_type: 'page_snapshot',
    export_type: target.key,
    module: target.module,
    page_label: target.label,
    page_name: target.label,
    menu_path: target.menuPath,
    surface_label: target.surfaceLabel,
    confidence: target.confidence,
    shop_id: task.shopId,
    shop_name: task.shopName || null,
    date_range: { from: task.dateFrom, to: task.dateTo },
    data_coverage: dateFilter.data_coverage,
    date_filter_applied: dateFilter.applied,
    date_range_semantics: dateFilter.date_range_semantics,
    effective_date_range: dateFilter.effective_date_range,
    page_url: page.url(),
    frame_url: frame.url(),
    trigger_label: '页面可见数据快照',
    original_filename: filename,
    saved_path: savedPath,
    relative_path: path.relative(downloadDir, savedPath),
    sha256: await sha256File(savedPath),
    size_bytes: stats.size,
    status: 'completed',
    page_snapshot_status: sampleInsufficient ? 'insufficient_sample' : 'visible_profile_saved',
    created_at: createdAt
  };
}

function csvCell(value) {
  const text = String(value ?? '');
  return `"${text.replace(/"/g, '""')}"`;
}

async function validateFundFlowArtifactDateCoverage(filePath, dateFrom, dateTo) {
  const sourceTexts = await readTabularArtifactTexts(filePath);
  const dateValues = sourceTexts.flatMap((text) => String(text).match(/\d{4}-\d{2}-\d{2}/g) ?? []);
  return assertFundFlowDateValuesWithinRange(dateValues, dateFrom, dateTo);
}

function assertFundFlowDateValuesWithinRange(dateValues, dateFrom, dateTo) {
  const normalizedDates = [...new Set(
    dateValues
      .map((value) => String(value).match(/\d{4}-\d{2}-\d{2}/)?.[0] ?? null)
      .filter(Boolean)
  )].sort();
  if (normalizedDates.length === 0) {
    throw new Error('资金流水下载文件中未读取到可验证的记账日期，将拒绝保留该文件以避免误用全店数据');
  }
  const outsideRange = normalizedDates.find((date) => date < dateFrom || date > dateTo);
  if (outsideRange) {
    throw new Error(`资金流水下载文件仍包含范围外日期 ${outsideRange}（请求 ${dateFrom} 至 ${dateTo}），将拒绝保留该文件以避免误用全店数据`);
  }
  return {
    checked: true,
    date_count: normalizedDates.length,
    earliest_date: normalizedDates[0],
    latest_date: normalizedDates.at(-1)
  };
}

async function readTabularArtifactTexts(filePath) {
  const extension = path.extname(filePath).toLowerCase();
  if (extension === '.xlsx') {
    return readXlsxWorksheetXml(filePath);
  }
  if (extension === '.csv') {
    return [await fs.readFile(filePath, 'utf8')];
  }
  if (extension === '.xls') {
    const { stdout } = await execFileAsync('strings', [filePath], {
      encoding: 'utf8',
      maxBuffer: 64 * 1024 * 1024
    });
    return [stdout];
  }
  if (extension !== '.zip') {
    throw new Error(`资金流水文件类型无法验证日期范围：${extension || '无扩展名'}`);
  }

  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'wechat-fund-flow-'));
  try {
    await execFileAsync('unzip', ['-qq', filePath, '-d', tempDir], {
      encoding: 'utf8',
      maxBuffer: 64 * 1024 * 1024
    });
    const extractedFiles = await listFilesRecursively(tempDir);
    const tabularFiles = extractedFiles.filter((item) => ['.xlsx', '.xls', '.csv'].includes(path.extname(item).toLowerCase()));
    if (tabularFiles.length === 0) {
      throw new Error('资金流水压缩包中未找到可验证的 XLSX/XLS/CSV 表格');
    }
    const texts = [];
    for (const tableFile of tabularFiles) {
      texts.push(...await readTabularArtifactTexts(tableFile));
    }
    return texts;
  } finally {
    await fs.rm(tempDir, { recursive: true, force: true }).catch(() => {});
  }
}

async function readXlsxWorksheetXml(filePath) {
  const { stdout: entryList } = await execFileAsync('unzip', ['-Z1', filePath], {
    encoding: 'utf8',
    maxBuffer: 8 * 1024 * 1024
  });
  const worksheetEntries = entryList
    .split(/\r?\n/)
    .filter((entry) => /^xl\/worksheets\/[^/]+\.xml$/i.test(entry));
  if (worksheetEntries.length === 0) {
    throw new Error('资金流水 XLSX 中未找到可读取的工作表');
  }
  const texts = [];
  for (const entry of worksheetEntries) {
    const { stdout } = await execFileAsync('unzip', ['-p', filePath, entry], {
      encoding: 'utf8',
      maxBuffer: 64 * 1024 * 1024
    });
    texts.push(stdout);
  }
  return texts;
}

async function listFilesRecursively(directory) {
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...await listFilesRecursively(entryPath));
    } else if (entry.isFile()) {
      files.push(entryPath);
    }
  }
  return files;
}

async function assertLoggedInFast(page, timeoutMs) {
  let state = await currentLoginState(page);
  if (state === 'login_required') {
    throw fatal('LOGIN_REQUIRED', '微信小店后台仍停留在扫码/登录页；只导出任务已快速停止，请先在 9333 浏览器完成登录');
  }
  if (state === 'logged_in') {
    log('login_preflight_passed', { url: page.url() });
    return;
  }

  const waiters = page.frames().map((frame) => frame.waitForFunction(
    () => {
      const text = String(document.body?.innerText ?? '').replace(/\s+/g, ' ');
      const loginRequired = /扫码进入我的小店|请使用微信扫码|登录超时|请先登录|重新登录|扫码登录/.test(text);
      const loggedIn = text.includes('首页')
        && text.includes('商品管理')
        && /订单(?:\/|／)?配送|店铺数据|资金结算/.test(text);
      return loginRequired || loggedIn;
    },
    null,
    { timeout: timeoutMs }
  ));
  await Promise.any(waiters).catch(() => {});
  state = await currentLoginState(page);
  if (state !== 'logged_in') {
    const message = state === 'login_required'
      ? '微信小店后台仍停留在扫码/登录页'
      : `在 ${timeoutMs}ms 内未确认微信小店登录壳层`;
    throw fatal('LOGIN_REQUIRED', `${message}；只导出任务已停止，请先在 9333 浏览器完成登录`);
  }
  log('login_preflight_passed', { url: page.url() });
}

async function assertSessionStillLoggedIn(page) {
  const state = await currentLoginState(page);
  if (state === 'login_required') {
    throw fatal('LOGIN_REQUIRED', '导出过程中登录状态失效，已停止后续页面');
  }
}

async function currentLoginState(page) {
  const snapshots = await collectFrameSnapshots(page, 1200);
  const mainSnapshot = snapshots.find((item) => item.is_main_frame);
  const mainState = classifyLoginText(mainSnapshot?.body_text ?? '');
  if (mainState === 'login_required' || mainState === 'logged_in') {
    return mainState;
  }
  // Third-party feature iframes can legitimately show their own “请先登录”.
  // Only the main store shell is authoritative for a fatal login decision.
  const states = snapshots
    .filter((item) => !item.is_main_frame)
    .map((item) => classifyLoginText(item.body_text));
  if (states.includes('logged_in')) {
    return 'logged_in';
  }
  return 'unknown';
}

async function assertExpectedShop(page, expectedShopName) {
  if (!expectedShopName) {
    return;
  }

  const knownCurrentShop = cleanText(await page.locator('.shop-info-popover').first()
    .innerText({ timeout: 1500 })
    .catch(() => ''));
  if (knownCurrentShop) {
    if (!knownCurrentShop.includes(expectedShopName)) {
      throw fatal(
        'SHOP_MISMATCH',
        `当前登录店铺“${knownCurrentShop}”与期望店铺“${expectedShopName}”不一致，已停止导出`
      );
    }
    return;
  }

  for (const frame of page.frames()) {
    const locator = frame.getByText(expectedShopName, { exact: false });
    const count = Math.min(await locator.count(), 20);
    for (let index = 0; index < count; index += 1) {
      const visibleInHeader = await locator.nth(index).evaluate((element) => {
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        return style.display !== 'none'
          && style.visibility !== 'hidden'
          && rect.width > 0
          && rect.height > 0
          && rect.top < 260;
      }).catch(() => false);
      if (visibleInHeader) {
        return;
      }
    }
  }
  throw fatal('SHOP_UNCONFIRMED', `无法在店铺页头确认期望店铺“${expectedShopName}”，为避免导错店铺已停止`);
}

async function collectFrameSnapshots(page, previewLength = 2000) {
  const frames = page?.frames?.() ?? [];
  const mainFrame = page?.mainFrame?.() ?? null;
  return Promise.all(frames.map(async (frame) => ({
    is_main_frame: frame === mainFrame,
    frame_name: frame.name(),
    frame_url: frame.url(),
    body_text: cleanText(await frame.locator('body').innerText({ timeout: 1500 }).catch(() => '')).slice(0, previewLength)
  })));
}

async function collectControlInventory(page, maxPerFrame = 160) {
  const frames = page?.frames?.() ?? [];
  const mainFrame = page?.mainFrame?.() ?? null;
  const inventories = [];
  for (const frame of frames) {
    const controls = await frame.evaluate(({ limit }) => {
      const selectors = [
        'button',
        'a',
        'input',
        'select',
        'textarea',
        '[role]',
        '[aria-label]',
        '[title]',
        '.selector-item',
        '.menu-item',
        '.title.link',
        '[class*="download" i]',
        '[class*="export" i]',
        '[class*="picker" i]',
        '[class*="date" i]'
      ];
      const seen = new Set();
      const candidates = [];
      const selector = selectors.join(',');
      const roots = [document];
      const matchedElements = [];
      for (let rootIndex = 0; rootIndex < roots.length; rootIndex += 1) {
        for (const element of roots[rootIndex].querySelectorAll('*')) {
          if (element.shadowRoot) roots.push(element.shadowRoot);
          if (element.matches(selector)) matchedElements.push(element);
        }
      }
      for (const element of matchedElements) {
        if (seen.has(element)) continue;
        seen.add(element);
        const tag = element.tagName.toLowerCase();
        const className = typeof element.className === 'string'
          ? element.className
          : element.getAttribute('class') || '';
        const text = String(element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim();
        const placeholder = element.getAttribute('placeholder') || '';
        const role = element.getAttribute('role') || '';
        const ariaLabel = element.getAttribute('aria-label') || '';
        const title = element.getAttribute('title') || '';
        const type = element.getAttribute('type') || '';
        const name = element.getAttribute('name') || '';
        const joined = [className, text, placeholder, role, ariaLabel, title, type, name].join(' ');
        const nativelyInteractive = ['button', 'a', 'input', 'select', 'textarea'].includes(tag);
        const relevant = nativelyInteractive
          || Boolean(role)
          || /selector-item|menu-item|title\s+link|download|export|picker|date|time|\u5bfc\u51fa|\u4e0b\u8f7d|\u65e5\u671f|\u65f6\u95f4|\u4ea4\u6613|\u8bb0\u8d26|\u4eba\u7fa4|\u5546\u54c1\u660e\u7ec6/i.test(joined);
        if (!relevant) continue;
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        const visible = style.display !== 'none'
          && style.visibility !== 'hidden'
          && Number(style.opacity || 1) !== 0
          && rect.width > 0
          && rect.height > 0;
        const labels = 'labels' in element && element.labels
          ? Array.from(element.labels).map((label) => label.innerText || label.textContent || '').join(' ')
          : '';
        const dateLike = ['date', 'datetime-local', 'time', 'month'].includes(type.toLowerCase())
          || /date|time|\u65e5\u671f|\u65f6\u95f4|\u8bb0\u8d26|\u52a8\u8d26|\u4ea4\u6613/i.test([placeholder, ariaLabel, title, name, className].join(' '));
        const id = element.getAttribute('id') || '';
        const classTokens = className.split(/\s+/).filter(Boolean).slice(0, 6);
        candidates.push({
          tag,
          id: id.slice(0, 120),
          type: type.slice(0, 80),
          text: text.slice(0, 240),
          label: String(labels).replace(/\s+/g, ' ').trim().slice(0, 160),
          placeholder: placeholder.slice(0, 160),
          role: role.slice(0, 80),
          aria_label: ariaLabel.slice(0, 160),
          title: title.slice(0, 160),
          class_name: classTokens.join(' ').slice(0, 240),
          visible,
          value: dateLike && 'value' in element ? String(element.value || '').slice(0, 160) : null
        });
        if (candidates.length >= limit) break;
      }
      return candidates;
    }, { limit: maxPerFrame }).catch(() => []);
    inventories.push({
      is_main_frame: frame === mainFrame,
      frame_name: frame.name(),
      frame_url: frame.url(),
      control_count: controls.length,
      controls
    });
  }
  return inventories;
}

async function captureFailure(page, downloadDir, target, index, error, extra = {}) {
  const moduleDir = path.join(downloadDir, safePathSegment(target.module || target.key) || '_run');
  await fs.mkdir(moduleDir, { recursive: true });
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
  const stem = `failure-${String(index).padStart(2, '0')}-${timestamp}`;
  const screenshotPath = path.join(moduleDir, `${stem}.png`);
  const debugPath = path.join(moduleDir, `${stem}.json`);
  let screenshotSaved = false;

  if (page) {
    screenshotSaved = await page.screenshot({ path: screenshotPath, fullPage: true, timeout: 10000 })
      .then(() => true)
      .catch(() => false);
  }
  const debug = {
    task_mode: 'export_only',
    target: target.key,
    target_label: target.label,
    captured_at: new Date().toISOString(),
    page_url: page?.url?.() ?? null,
    page_title: page ? await page.title().catch(() => '') : '',
    error: serializeError(error),
    frames: page ? await collectFrameSnapshots(page, 2500) : [],
    control_inventory: page ? await collectControlInventory(page) : [],
    screenshot_path: screenshotSaved ? screenshotPath : null,
    ...extra
  };
  await writeJsonAtomic(debugPath, debug);
  log('failure_diagnostic_captured', {
    target: target.key,
    failure_stage: extra.stage ?? null,
    debug_path: debugPath,
    screenshot_path: screenshotSaved ? screenshotPath : null,
    frame_count: debug.frames.length,
    control_count: debug.control_inventory.reduce((total, item) => total + item.control_count, 0)
  });
  return {
    screenshot_path: screenshotSaved ? screenshotPath : null,
    debug_path: debugPath
  };
}

async function closeVerifiedExportDialog(page) {
  for (const frame of page.frames()) {
    const dialog = await locateVisibleVerifiedDialog(frame).catch(() => null);
    if (dialog && isExportDialogText(dialog.text)) {
      await page.keyboard.press('Escape').catch(() => {});
      return;
    }
  }
}

async function persistRunFiles({
  task,
  status,
  startedAt,
  completedAt,
  downloadDir,
  metadataPath,
  manifestPath,
  resultsPath,
  executionLogPath,
  artifacts,
  pageResults,
  bundleArtifact = null,
  bundleError = null,
  error = null
}) {
  const generatedAt = new Date().toISOString();
  const manifest = {
    ...buildArtifactManifest({ task, artifacts, generatedAt }),
    bundle_available: Boolean(bundleArtifact),
    bundle_artifact: bundleArtifact,
    bundle_error: bundleError
  };
  const counts = summarizePageResults(pageResults);
  const results = {
    task_id: task.taskId,
    mode: 'export_only',
    source_type: 'export_only',
    generated_at: generatedAt,
    ...counts,
    status,
    fatal_error: Boolean(error),
    scan_completed: status !== 'running' && !error,
    artifact_count: artifacts.length,
    bundle_available: Boolean(bundleArtifact),
    bundle_artifact: bundleArtifact,
    bundle_error: bundleError,
    execution_log_path: executionLogPath,
    pages: pageResults
  };
  const metadata = buildTaskMetadata({
    task,
    status,
    startedAt,
    completedAt,
    downloadDir,
    manifestPath,
    resultsPath,
    executionLogPath,
    artifacts,
    pageResults,
    bundleArtifact,
    bundleError,
    error
  });
  await writeJsonAtomic(manifestPath, manifest);
  await writeJsonAtomic(resultsPath, results);
  await writeJsonAtomic(metadataPath, metadata);
}

async function writeJsonAtomic(filePath, value) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const tempPath = `${filePath}.${process.pid}.tmp`;
  await fs.writeFile(tempPath, JSON.stringify(value, null, 2), 'utf8');
  await fs.rename(tempPath, filePath);
}

async function uniquePath(directory, filename) {
  const extension = path.extname(filename);
  const stem = path.basename(filename, extension);
  for (let index = 0; index < 1000; index += 1) {
    const candidate = path.join(directory, index === 0 ? filename : `${stem}__${index + 1}${extension}`);
    const exists = await fs.access(candidate).then(() => true).catch(() => false);
    if (!exists) {
      return candidate;
    }
  }
  throw new Error(`无法为下载文件生成唯一名称：${filename}`);
}

async function sha256File(filePath) {
  const hash = crypto.createHash('sha256');
  for await (const chunk of createReadStream(filePath)) {
    hash.update(chunk);
  }
  return hash.digest('hex');
}

async function waitForNetworkSettle(page) {
  await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => {});
  await page.waitForLoadState('networkidle', { timeout: 6000 }).catch(() => {});
}

function basePageResult(target, startedAt) {
  return {
    target: target.key,
    module: target.module,
    label: target.label,
    page_name: target.label,
    menu_path: target.menuPath,
    route: target.route,
    surface_label: target.surfaceLabel,
    data_coverage: 'page_current_filters',
    date_filter_applied: false,
    date_range_semantics: 'requested_only_not_applied',
    effective_date_range: null,
    confidence: target.confidence,
    status: 'skipped',
    started_at: startedAt,
    completed_at: null,
    page_url: null,
    page_title: '',
    navigation: null,
    surface: null,
    controls_found: 0,
    controls_attempted: 0,
    artifact_count: 0,
    artifacts: [],
    exported_filenames: [],
    failures: [],
    skipped_reason: null,
    failure_reason: null,
    message: null
  };
}

function finishSkipped(result, reason, message) {
  result.status = 'skipped';
  result.skipped_reason = reason;
  result.message = message;
  result.completed_at = new Date().toISOString();
  return result;
}

function failedPageResult(target, error, debug) {
  const result = basePageResult(target, new Date().toISOString());
  result.status = 'failed';
  result.completed_at = new Date().toISOString();
  result.failure_reason = errorMessage(error);
  result.failures = [{ error: serializeError(error), debug }];
  return result;
}

function candidateDescriptor(candidate) {
  return {
    key: candidate.key,
    role: candidate.role,
    label: candidate.label,
    ordinal: candidate.ordinal,
    frame_name: candidate.frameName,
    frame_url: candidate.frameUrl,
    context_text: candidate.contextText,
    context_texts: candidate.contextTexts
  };
}

function candidateMatchesTargetScope(candidate, target, surface) {
  const contexts = candidate.contextTexts ?? [candidate.contextText ?? ''];
  if (target.controlContextLabels?.length > 0) {
    const includesRequiredContext = target.controlContextLabels.some((label) =>
      contexts.some((context) => context.includes(label))
    );
    if (!includesRequiredContext) {
      return false;
    }
  }
  if (target.excludeControlContextLabels?.length > 0) {
    const includesExcludedContext = target.excludeControlContextLabels.some((label) =>
      contexts.some((context) => context.includes(label))
    );
    if (includesExcludedContext) {
      return false;
    }
  }
  if (target.surfaceLabel && surface?.selected && surface.frame_url) {
    return candidate.frameUrl === surface.frame_url
      && (!surface.frame_name || candidate.frameName === surface.frame_name);
  }
  return true;
}

function dialogActionPriority(labelValue) {
  const label = cleanText(labelValue);
  if (GENERATED_DOWNLOAD_LABEL_PATTERN.test(label)) {
    return 0;
  }
  if (/确认导出|开始导出|立即导出|生成并下载|生成报表/.test(label)) {
    return 1;
  }
  if (label === '导出') {
    return 2;
  }
  return 3;
}

function ignoreRejection(promise) {
  return promise.catch(() => new Promise(() => {}));
}

function timeoutOutcome(timeoutMs, kind) {
  return new Promise((resolve) => {
    setTimeout(() => resolve({ kind }), timeoutMs);
  });
}

function delay(timeoutMs) {
  return new Promise((resolve) => {
    setTimeout(resolve, timeoutMs);
  });
}

function fatal(code, message) {
  const error = new Error(message);
  error.name = 'ExportOnlyFatalError';
  error.code = code;
  error.fatal = true;
  return error;
}

function serializeError(error) {
  if (!error) {
    return null;
  }
  return {
    name: error.name ?? 'Error',
    code: error.code ?? null,
    message: errorMessage(error)
  };
}

function errorMessage(error) {
  return String(error?.message ?? error ?? 'unknown error');
}

function log(stage, fields = {}) {
  const line = `${JSON.stringify({
    time: new Date().toISOString(),
    stage,
    ...(executionLogTaskId ? { task_id: executionLogTaskId } : {}),
    ...fields
  })}\n`;
  process.stderr.write(line);
  if (!executionLogPath) {
    return;
  }
  try {
    appendFileSync(executionLogPath, line, 'utf8');
  } catch {
    // Diagnostics must never make the export task fail.
  }
}

export {
  applyRequestedDateRange,
  assertFundFlowDateValuesWithinRange,
  collectControlInventory,
  completeExportDialog,
  createExportBundle,
  discoverExportControls,
  findLeftNavigationLocator,
  triggerDownloadFromControl,
  prioritizeTargetExportControls,
  selectSafeSurface,
  validateFundFlowArtifactDateCoverage,
  waitForGeneratedExportCompletion,
  waitForVerifiedExportDialog
};
