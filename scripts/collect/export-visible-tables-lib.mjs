import path from 'node:path';

export const STORE_ORIGIN = 'https://store.weixin.qq.com';
export const DEFAULT_CDP_URL = 'http://127.0.0.1:9333';
export const DEFAULT_DOWNLOAD_TIMEOUT_MS = 30000;
export const DEFAULT_VERIFICATION_TIMEOUT_MS = 300000;

export const EXPORT_CONTROL_LABEL_PATTERN = /^(?:(?:全部|批量|一键)?导出(?:数据|报表|明细|列表|订单|商品|评价|售后|账单|流水|当前页|全部数据|筛选结果|\s*Excel|\s*CSV)?|(?:立即)?下载(?:数据|报表|明细|文件|账单|流水|结果|\s*Excel|\s*CSV)?|报表下载)(?:\s*[（(][^（）()]{0,20}[）)])?$/i;
export const GENERATED_DOWNLOAD_LABEL_PATTERN = /^(?:立即)?下载(?:数据|报表|明细|文件|账单|流水|结果|\s*Excel|\s*CSV)?(?:\s*[（(][^（）()]{0,20}[）)])?$/i;

const LOGIN_PAGE_PATTERN = /扫码进入我的小店|请使用微信扫码|登录超时|请先登录|重新登录|扫码登录/;
const LOGGED_IN_MARKERS = ['首页', '商品管理'];
const LOGGED_IN_SECONDARY_PATTERN = /订单(?:\/|／)?配送|店铺数据|资金结算/;
const EXPORT_DIALOG_PATTERN = /导出|下载(?:数据|报表|文件|结果)?/;
const ORDER_EXPORT_CONFIRMATION_PATTERN = /(?:共|当前)?\s*\d+\s*(?:条|个)?订单信息|订单信息.*(?:商品维度|预计需要时间)|商品维度展开.*预计需要时间/;
const EXPORT_VERIFICATION_DIALOG_PATTERN = /扫码验证|扫码确认|微信扫码|使用微信扫码|扫码.*(?:验证|确认)/;
const GENERATED_EXPORT_SUCCESS_CONTEXT_PATTERN = /导出成功|即将自动下载|如未自动下载|自动下载不生效/;
const FORBIDDEN_CONTROL_PATTERN = /^(?:立即)?(?:发货|确认收货|同意退款|拒绝退款|退款|提现|充值|投放|开始投放|暂停投放|启用|停用|删除|保存|提交|上架|下架|支付|转账|调整预算)$/;
const FORBIDDEN_EXPORT_SUFFIX_PATTERN = /(?:模板|示例|说明|规则|教程|记录|历史|中心)$/;
const SAFE_DIALOG_CONFIRM_PATTERN = /^(?:确定|确认|继续|确认导出|开始导出|立即导出|生成报表|生成并下载|导出|下载|下载数据|下载报表|立即下载)$/;
const DOWNLOAD_SUFFIXES = new Set(['.csv', '.xls', '.xlsx', '.zip']);

export const EXPORT_TARGETS = Object.freeze([
  target({
    key: 'orders',
    label: '订单明细',
    module: 'orders',
    menuPath: ['订单/配送', '订单管理'],
    route: '/shop/order/list',
    maxExportControls: 1,
    requiredExportControl: true,
    confidence: 'high'
  }),
  target({
    key: 'product_list',
    label: '商品管理 · 商品列表',
    module: 'product_list',
    menuPath: ['商品管理', '商品列表'],
    route: '/shop/goods/list',
    requiredExportControl: true,
    confidence: 'high'
  }),
  target({
    key: 'product_core_conversion',
    label: '商品数据 - 核心转化概览',
    module: 'product_core_conversion',
    menuPath: ['店铺数据', '商品数据'],
    route: '/shop/statistics/product',
    controlContextLabels: ['核心转化概览'],
    requiredExportControl: true,
    confidence: 'high'
  }),
  target({
    key: 'product_traffic_funnel',
    label: '商品数据 - 流量转化漏斗',
    module: 'product_traffic_funnel',
    menuPath: ['店铺数据', '商品数据'],
    route: '/shop/statistics/product',
    controlContextLabels: ['流量转化漏斗'],
    requiredExportControl: true,
    confidence: 'high'
  }),
  target({
    key: 'product_detail',
    label: '商品数据 - 商品明细',
    module: 'product_detail',
    menuPath: ['店铺数据', '商品数据'],
    route: '/shop/statistics/product',
    surfaceLabel: '商品明细',
    excludeControlContextLabels: ['核心转化概览', '流量转化漏斗'],
    requiredExportControl: true,
    confidence: 'high'
  }),
  target({
    key: 'fund_flows',
    label: '资金流水',
    module: 'fund_flows',
    menuPath: ['资金结算', '流水与账单'],
    route: '/shop/funds/moneyManage',
    requiredExportControl: true,
    confidence: 'high'
  }),
  target({
    key: 'transactions',
    label: '交易数据',
    module: 'transactions',
    menuPath: ['店铺数据', '交易数据'],
    route: '/shop/statistics/transaction',
    requiredExportControl: true,
    confidence: 'medium'
  }),
  target({
    key: 'audience',
    label: '人群数据',
    module: 'audience',
    menuPath: ['店铺数据', '人群数据'],
    route: '/shop/statistics/collect',
    confidence: 'medium'
  }),
  target({
    key: 'compass',
    label: '电商罗盘',
    module: 'compass',
    menuPath: ['店铺数据', '电商罗盘'],
    route: '/shop-faas/mmecnodecompasscommon/thirdParty/shop/loginCompassByShop',
    allowedRoutes: [
      { route: '/compass/home', routePrefix: true }
    ],
    confidence: 'medium'
  }),
  target({
    key: 'compass_buyer_profile',
    label: '电商罗盘 · 买家人群特征（页面数据快照）',
    module: 'compass_buyer_profile',
    menuPath: ['人群', '人群特征'],
    route: '/shop-faas/mmecnodecompasscommon/thirdParty/shop/loginCompassByShop',
    allowedRoutes: [
      { route: '/compass/persona/home', routePrefix: true }
    ],
    requiredExportControl: true,
    confidence: 'medium'
  }),
  target({
    key: 'after_sales',
    label: '售后处理',
    module: 'after_sales',
    menuPath: ['订单/配送', '售后处理'],
    route: '/shop/aftersale/home',
    confidence: 'medium'
  }),
  target({
    key: 'reviews',
    label: '订单评价',
    module: 'reviews',
    menuPath: ['订单/配送', '订单评价'],
    route: '/shop/evaluate/home',
    confidence: 'medium'
  }),
  target({
    key: 'shop_ads',
    label: '小店投放',
    module: 'shop_ads',
    menuPath: ['小店推广', '小店投放'],
    route: '/shop/promotion/',
    routePrefix: true,
    confidence: 'medium'
  }),
  target({
    key: 'shop_boost',
    label: '小店加热',
    module: 'shop_boost',
    menuPath: ['小店推广', '小店加热'],
    route: '/shop/channels-promotion',
    routePrefix: true,
    confidence: 'medium'
  }),
  target({
    key: 'repurchase',
    label: '老客复购',
    module: 'repurchase',
    menuPath: ['用户运营', '老客复购'],
    route: '/shop/useroperation/repurchaseTool',
    routePrefix: true,
    confidence: 'medium'
  }),
  target({
    key: 'alliance_data',
    label: '联盟数据',
    module: 'alliance_data',
    menuPath: ['优选联盟', '联盟数据'],
    route: '/shop/shopleague/contact-info',
    routePrefix: true,
    confidence: 'low'
  })
]);

export const REQUESTED_EXPORT_TARGET_KEYS = Object.freeze([
  'product_list',
  'orders',
  'fund_flows',
  'transactions',
  'product_core_conversion',
  'product_traffic_funnel',
  'product_detail',
  'compass_buyer_profile'
]);

export function parseExportOnlyArgs(argv, { now = new Date() } = {}) {
  const values = {};
  const allowedKeys = new Set([
    'shopId',
    'shopName',
    'from',
    'to',
    'cdpUrl',
    'downloadTimeoutMs',
    'verificationTimeoutMs',
    'targets',
    'taskId'
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (!argument.startsWith('--')) {
      throw new Error(`无法识别的参数：${argument}`);
    }

    const [rawKey, inlineValue] = argument.slice(2).split(/=(.*)/s, 2);
    const key = rawKey.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    if (!allowedKeys.has(key)) {
      throw new Error(`无法识别的参数：--${rawKey}`);
    }
    const value = inlineValue ?? argv[index + 1];
    if (inlineValue === undefined) {
      if (value === undefined || value.startsWith('--')) {
        throw new Error(`参数 --${rawKey} 缺少值`);
      }
      index += 1;
    }
    values[key] = value;
  }

  const shopId = requiredText(values.shopId, '--shop-id');
  const dateFrom = requiredIsoDate(values.from, '--from');
  const dateTo = requiredIsoDate(values.to, '--to');
  if (dateFrom > dateTo) {
    throw new Error('--from 不能晚于 --to');
  }

  const targets = resolveTargets(values.targets);
  const downloadTimeoutMs = integerInRange(
    values.downloadTimeoutMs ?? process.env.WECHAT_STORE_EXPORT_DOWNLOAD_TIMEOUT_MS ?? DEFAULT_DOWNLOAD_TIMEOUT_MS,
    '--download-timeout-ms',
    1000,
    300000
  );
  const loginTimeoutMs = integerInRange(
    process.env.WECHAT_STORE_EXPORT_LOGIN_TIMEOUT_MS ?? 8000,
    'WECHAT_STORE_EXPORT_LOGIN_TIMEOUT_MS',
    1000,
    30000
  );
  const verificationTimeoutMs = integerInRange(
    values.verificationTimeoutMs
      ?? process.env.WECHAT_STORE_EXPORT_VERIFICATION_TIMEOUT_MS
      ?? DEFAULT_VERIFICATION_TIMEOUT_MS,
    '--verification-timeout-ms',
    10000,
    600000
  );
  const taskId = values.taskId
    ? safePathSegment(values.taskId)
    : buildTaskId({ shopId, dateFrom, dateTo, now });

  if (!taskId) {
    throw new Error('无法生成有效 task_id');
  }

  return {
    taskId,
    shopId,
    shopName: cleanText(values.shopName),
    dateFrom,
    dateTo,
    cdpUrl: validateCdpUrl(values.cdpUrl ?? DEFAULT_CDP_URL),
    downloadTimeoutMs,
    verificationTimeoutMs,
    loginTimeoutMs,
    targets
  };
}

export function resolveTargets(value) {
  if (!value) {
    throw new Error('--targets 必须明确指定至少一个本轮业务目标，禁止回退导出全部页面');
  }

  const requested = [...new Set(String(value).split(',').map(cleanText).filter(Boolean))];
  if (requested.length === 0) {
    throw new Error('--targets 至少需要一个目标 key');
  }

  const allowedKeys = new Set(REQUESTED_EXPORT_TARGET_KEYS);
  const byKey = new Map(
    EXPORT_TARGETS
      .filter((item) => allowedKeys.has(item.key))
      .map((item) => [item.key, item])
  );
  const unknown = requested.filter((key) => !byKey.has(key));
  if (unknown.length > 0) {
    throw new Error(`--targets 包含非安全白名单目标：${unknown.join(', ')}`);
  }
  return requested.map((key) => byKey.get(key));
}

export function buildTaskId({ shopId, dateFrom, dateTo, now = new Date() }) {
  const timestamp = now.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
  return [
    'export_only',
    safePathSegment(shopId) || 'shop',
    dateFrom,
    dateTo,
    timestamp
  ].join('_');
}

export function isSafeExportControlLabel(value) {
  const label = cleanText(value);
  if (!label || label.length > 64) {
    return false;
  }
  if (FORBIDDEN_CONTROL_PATTERN.test(label) || FORBIDDEN_EXPORT_SUFFIX_PATTERN.test(label)) {
    return false;
  }
  return EXPORT_CONTROL_LABEL_PATTERN.test(label);
}

export function isSafePageExportCandidate(value, contextValue = '') {
  const label = cleanText(value);
  const context = cleanText(contextValue);
  if (!isSafeExportControlLabel(label)) {
    return false;
  }
  if (/模板|示例|教程|操作说明|导出记录|下载记录|历史任务/.test(context)) {
    return false;
  }
  if (GENERATED_DOWNLOAD_LABEL_PATTERN.test(label) && GENERATED_EXPORT_SUCCESS_CONTEXT_PATTERN.test(context)) {
    return false;
  }
  if (/^(?:立即)?下载$/i.test(label)) {
    return /数据|报表|表格|明细|账单|流水|统计|分析|导出/.test(context);
  }
  return true;
}

export function isExportDialogText(value) {
  const text = cleanText(value);
  return Boolean(text && (
    EXPORT_DIALOG_PATTERN.test(text)
    || ORDER_EXPORT_CONFIRMATION_PATTERN.test(text)
  ));
}

export function classifyExportDialogText(value) {
  const text = cleanText(value);
  if (!isExportDialogText(text)) {
    return 'unknown';
  }
  if (EXPORT_VERIFICATION_DIALOG_PATTERN.test(text)) {
    return 'verification';
  }
  if (GENERATED_DOWNLOAD_LABEL_PATTERN.test(text)) {
    return 'download_ready';
  }
  return 'confirmation';
}

export function isAllowedDialogConfirmLabel(value, dialogText) {
  const label = cleanText(value);
  if (!isExportDialogText(dialogText) || !label || FORBIDDEN_CONTROL_PATTERN.test(label)) {
    return false;
  }
  return SAFE_DIALOG_CONFIRM_PATTERN.test(label) || isSafeExportControlLabel(label);
}

export function classifyLoginText(value) {
  const text = cleanText(value);
  if (LOGIN_PAGE_PATTERN.test(text)) {
    return 'login_required';
  }
  if (LOGGED_IN_MARKERS.every((marker) => text.includes(marker)) && LOGGED_IN_SECONDARY_PATTERN.test(text)) {
    return 'logged_in';
  }
  return 'unknown';
}

export function isAllowedStoreRoute(urlValue, targetDefinition) {
  try {
    const url = new URL(urlValue, STORE_ORIGIN);
    if (url.protocol !== 'https:' || url.origin !== STORE_ORIGIN) {
      return false;
    }
    const currentPath = trimTrailingSlash(url.pathname);
    const routeDefinitions = [
      { route: targetDefinition.route, routePrefix: targetDefinition.routePrefix },
      ...(targetDefinition.allowedRoutes ?? [])
    ];
    return routeDefinitions.some((definition) => {
      const expectedPath = trimTrailingSlash(definition.route);
      return definition.routePrefix
        ? currentPath === expectedPath || currentPath.startsWith(`${expectedPath}/`)
        : currentPath === expectedPath;
    });
  } catch {
    return false;
  }
}

export function sanitizeDownloadFilename(value, fallback = 'download.bin') {
  const basename = path.basename(String(value ?? '')).normalize('NFKC');
  const cleaned = basename
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')
    .replace(/\s+/g, ' ')
    .replace(/^\.+/, '')
    .trim()
    .slice(0, 180);
  return cleaned || fallback;
}

export function isSupportedDownloadFilename(value) {
  return DOWNLOAD_SUFFIXES.has(path.extname(String(value ?? '')).toLowerCase());
}

export function summarizePageResults(pageResults) {
  const counts = { success: 0, skipped: 0, failed: 0 };
  for (const result of pageResults) {
    if (Object.hasOwn(counts, result.status)) {
      counts[result.status] += 1;
    }
  }
  const artifactCount = pageResults.reduce(
    (total, result) => total + Number(result.artifact_count ?? result.artifacts?.length ?? 0),
    0
  );
  const status = counts.failed === 0
    ? 'completed'
    : artifactCount > 0
      ? 'partial'
      : 'failed';

  return {
    status,
    artifact_count: artifactCount,
    success_count: counts.success,
    skipped_count: counts.skipped,
    failed_count: counts.failed
  };
}

export function buildArtifactManifest({ task, artifacts, generatedAt = new Date().toISOString() }) {
  return {
    task_id: task.taskId,
    mode: 'export_only',
    source_type: 'export_only',
    generated_at: generatedAt,
    artifact_count: artifacts.length,
    artifacts
  };
}

export function buildTaskMetadata({
  task,
  status,
  startedAt,
  completedAt,
  downloadDir,
  manifestPath,
  resultsPath,
  executionLogPath = null,
  artifacts,
  pageResults,
  bundleArtifact = null,
  bundleError = null,
  error = null
}) {
  const summary = summarizePageResults(pageResults);
  const coverageItems = artifacts.length > 0
    ? artifacts
    : pageResults.filter((result) => result.status === 'success');
  const anyDateFilterApplied = coverageItems.some((item) => item.date_filter_applied === true);
  const allDateFiltersApplied = coverageItems.length > 0
    && coverageItems.every((item) => item.date_filter_applied === true);
  return {
    task_id: task.taskId,
    status,
    mode: 'export_only',
    source_type: 'export_only',
    data_coverage: allDateFiltersApplied
      ? 'requested_date_range'
      : anyDateFilterApplied
        ? 'mixed_page_filters'
        : 'page_current_filters',
    date_filter_applied: allDateFiltersApplied,
    date_range_semantics: allDateFiltersApplied
      ? 'requested_range_applied'
      : anyDateFilterApplied
        ? 'partially_applied'
        : 'requested_only_not_applied',
    effective_date_range: allDateFiltersApplied
      ? { from: task.dateFrom, to: task.dateTo }
      : null,
    shop_id: task.shopId,
    shop_name: task.shopName || null,
    date_range: {
      from: task.dateFrom,
      to: task.dateTo
    },
    targets: task.targets.map((item) => item.key),
    download_dir: downloadDir,
    artifact_manifest_path: manifestPath,
    export_results_path: resultsPath,
    execution_log_path: executionLogPath,
    started_at: startedAt,
    completed_at: completedAt,
    fatal_error: Boolean(error),
    scan_completed: status !== 'running' && !error,
    artifact_count: artifacts.length,
    bundle_available: Boolean(bundleArtifact),
    bundle_artifact: bundleArtifact,
    bundle_error: bundleError,
    success_count: summary.success_count,
    skipped_count: summary.skipped_count,
    failed_count: summary.failed_count,
    pages: pageResults,
    artifacts,
    error
  };
}

export function cleanText(value) {
  return String(value ?? '').replace(/\s+/g, ' ').trim();
}

export function safePathSegment(value) {
  return String(value ?? '')
    .normalize('NFKC')
    .trim()
    .replace(/[<>:"/\\|?*\u0000-\u001f]+/g, '_')
    .replace(/\s+/g, '_')
    .replace(/^\.+|[._]+$/g, '')
    .slice(0, 120);
}

function target(definition) {
  return Object.freeze({
    routePrefix: false,
    requiredExportControl: false,
    surfaceLabel: null,
    surfaceOptional: false,
    controlContextLabels: Object.freeze([]),
    excludeControlContextLabels: Object.freeze([]),
    allowedRoutes: Object.freeze([]),
    maxExportControls: null,
    ...definition,
    menuPath: Object.freeze([...definition.menuPath]),
    controlContextLabels: Object.freeze([...(definition.controlContextLabels ?? [])]),
    excludeControlContextLabels: Object.freeze([...(definition.excludeControlContextLabels ?? [])]),
    allowedRoutes: Object.freeze(
      (definition.allowedRoutes ?? []).map((item) => Object.freeze({ ...item }))
    )
  });
}

function requiredText(value, label) {
  const text = cleanText(value);
  if (!text) {
    throw new Error(`缺少必填参数 ${label}`);
  }
  return text;
}

function requiredIsoDate(value, label) {
  const text = requiredText(value, label);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text) || Number.isNaN(Date.parse(`${text}T00:00:00Z`))) {
    throw new Error(`${label} 必须是 YYYY-MM-DD`);
  }
  return text;
}

function integerInRange(value, label, minimum, maximum) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${label} 必须是 ${minimum} 到 ${maximum} 之间的整数`);
  }
  return parsed;
}

function validateCdpUrl(value) {
  let url;
  try {
    url = new URL(String(value));
  } catch {
    throw new Error('--cdp-url 不是有效 URL');
  }
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('--cdp-url 仅支持 http/https');
  }
  return url.toString().replace(/\/$/, '');
}

function trimTrailingSlash(value) {
  const text = value || '/';
  return text.length > 1 ? text.replace(/\/+$/, '') : text;
}
