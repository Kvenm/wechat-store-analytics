import fs from 'node:fs/promises';
import path from 'node:path';
import {
  buildCollectedArtifact,
  writeArtifactManifest
} from './artifactManifest.js';
import { safePathSegment } from '../shared/nodePaths.js';

export const STORE_URL = 'https://store.weixin.qq.com/';
export const DEFAULT_EXPORT_TYPES = [
  'transactions',
  'product_list',
  'products',
  'audience',
  'compass',
  'orders',
  'reviews',
  'repurchase',
  'funds',
  'ads'
];

const COMMON_SELECTORS = {
  loggedInShell: 'TODO_SELECTOR_STORE_HOME_READY',
  shopSwitcherButton: 'TODO_SELECTOR_SHOP_SWITCHER_BUTTON',
  shopSearchInput: 'TODO_SELECTOR_SHOP_SEARCH_INPUT',
  shopResult: 'TODO_SELECTOR_SHOP_RESULT_{{shopLabel}}',
  currentShopName: 'TODO_SELECTOR_CURRENT_SHOP_NAME',
  shopDiscoveryItems: 'TODO_SELECTOR_SHOP_DISCOVERY_ITEMS',
  shopDiscoveryItemName: 'TODO_SELECTOR_SHOP_DISCOVERY_ITEM_NAME',
  dateRangeButton: 'TODO_SELECTOR_DATE_RANGE_BUTTON',
  dateStartInput: 'TODO_SELECTOR_DATE_START_INPUT',
  dateEndInput: 'TODO_SELECTOR_DATE_END_INPUT',
  dateConfirmButton: 'TODO_SELECTOR_DATE_CONFIRM_BUTTON',
  noPermission: 'TODO_SELECTOR_NO_PERMISSION_EMPTY_STATE'
};

const NAVIGATION_DENY_TEXT_PATTERN = /(发货|退款|退货|提交|保存|删除|上架|下架|发布|启用|关闭|提现|充值|投放|处理|同意|拒绝|ship|refund|return|submit|save|delete|publish|enable|disable)/i;
const EXPORT_ALLOW_TEXT_PATTERN = /(导出|下载|export|download)/i;
const EXPORT_DENY_TEXT_PATTERN = /(发货|退款|退货|提交|保存|删除|上架|下架|发布|启用|关闭|提现|充值|投放|处理|同意|拒绝|ship|refund|return|submit|save|delete|publish|enable|disable)/i;
const DEFAULT_TABLE_HINT_BY_EXPORT_TYPE = {
  ads: 'ad_spend',
  audience: 'audience_insights',
  compass: 'audience_insights',
  funds: 'fund_flows',
  orders: 'orders',
  product_list: 'products',
  products: 'products',
  repurchase: 'shop_daily',
  reviews: 'reviews',
  transactions: 'orders'
};

const DEFAULT_EXPORT_DEFINITIONS = {
  transactions: {
    label: 'Transactions export',
    path: 'TODO_PATH_TRANSACTIONS_EXPORT_PAGE',
    expectedFormats: ['xlsx', 'csv'],
    selectors: {
      pageReady: 'TODO_SELECTOR_TRANSACTIONS_PAGE_READY',
      exportButton: 'TODO_SELECTOR_TRANSACTIONS_EXPORT_BUTTON'
    }
  },
  orders: {
    label: 'Orders export',
    path: 'TODO_PATH_ORDERS_EXPORT_PAGE',
    expectedFormats: ['xlsx', 'csv'],
    selectors: {
      pageReady: 'TODO_SELECTOR_ORDERS_PAGE_READY',
      exportButton: 'TODO_SELECTOR_ORDERS_EXPORT_BUTTON'
    }
  },
  product_list: {
    label: 'Product list export',
    tableHint: 'products',
    path: 'TODO_PATH_PRODUCT_LIST_EXPORT_PAGE',
    expectedFormats: ['xlsx', 'csv'],
    selectors: {
      pageReady: 'TODO_SELECTOR_PRODUCT_LIST_PAGE_READY',
      exportButton: 'TODO_SELECTOR_PRODUCT_LIST_EXPORT_BUTTON'
    }
  },
  products: {
    label: 'Products export',
    path: 'TODO_PATH_PRODUCTS_EXPORT_PAGE',
    expectedFormats: ['xlsx', 'csv'],
    selectors: {
      pageReady: 'TODO_SELECTOR_PRODUCTS_PAGE_READY',
      exportButton: 'TODO_SELECTOR_PRODUCTS_EXPORT_BUTTON'
    }
  },
  compass: {
    label: 'Compass export',
    path: 'TODO_PATH_COMPASS_EXPORT_PAGE',
    expectedFormats: ['xlsx', 'csv'],
    selectors: {
      pageReady: 'TODO_SELECTOR_COMPASS_PAGE_READY',
      exportButton: 'TODO_SELECTOR_COMPASS_EXPORT_BUTTON'
    }
  },
  audience: {
    label: 'Audience export',
    path: 'TODO_PATH_AUDIENCE_EXPORT_PAGE',
    expectedFormats: ['xlsx', 'csv'],
    selectors: {
      pageReady: 'TODO_SELECTOR_AUDIENCE_PAGE_READY',
      exportButton: 'TODO_SELECTOR_AUDIENCE_EXPORT_BUTTON'
    }
  },
  reviews: {
    label: 'Reviews export',
    path: 'TODO_PATH_REVIEWS_EXPORT_PAGE',
    expectedFormats: ['xlsx', 'csv'],
    selectors: {
      pageReady: 'TODO_SELECTOR_REVIEWS_PAGE_READY',
      exportButton: 'TODO_SELECTOR_REVIEWS_EXPORT_BUTTON'
    }
  },
  repurchase: {
    label: 'Repurchase export',
    path: 'TODO_PATH_REPURCHASE_EXPORT_PAGE',
    expectedFormats: ['xlsx', 'csv'],
    selectors: {
      pageReady: 'TODO_SELECTOR_REPURCHASE_PAGE_READY',
      exportButton: 'TODO_SELECTOR_REPURCHASE_EXPORT_BUTTON'
    }
  },
  funds: {
    label: 'Funds export',
    path: 'TODO_PATH_FUNDS_EXPORT_PAGE',
    expectedFormats: ['xlsx', 'csv'],
    selectors: {
      pageReady: 'TODO_SELECTOR_FUNDS_PAGE_READY',
      exportButton: 'TODO_SELECTOR_FUNDS_EXPORT_BUTTON'
    }
  },
  ads: {
    label: 'Ads export',
    path: 'TODO_PATH_ADS_EXPORT_PAGE',
    expectedFormats: ['xlsx', 'csv'],
    selectors: {
      pageReady: 'TODO_SELECTOR_ADS_PAGE_READY',
      exportButton: 'TODO_SELECTOR_ADS_EXPORT_BUTTON'
    }
  }
};

export class WechatStoreCollector {
  constructor({ task, exportConfig = {}, projectRoot, logger = console }) {
    this.task = task;
    this.exportConfig = exportConfig;
    this.projectRoot = projectRoot;
    this.logger = logger;
    this.context = null;
    this.browser = null;
    this.attachedToExistingBrowser = false;
    this.page = null;
    this.exports = [];
    this.artifacts = [];
    this.items = [];
    this.discoveredShops = [];
    this.currentShopName = null;
  }

  async run() {
    const startedAt = new Date().toISOString();
    const { chromium } = await import('playwright');

    if (this.task.cdp_url) {
      try {
        this.browser = await chromium.connectOverCDP(this.task.cdp_url);
        this.context = this.browser.contexts()[0];
        this.attachedToExistingBrowser = Boolean(this.context);
        this.logger.log(`Connected to existing login browser at ${this.task.cdp_url}`);
      } catch (error) {
        this.logger.warn(`Could not connect to existing login browser at ${this.task.cdp_url}: ${error?.message ?? error}`);
      }
    }

    if (!this.context) {
      this.context = await chromium.launchPersistentContext(this.task.profile_dir, {
        headless: this.task.headless,
        acceptDownloads: true,
        downloadsPath: this.task.download_dir,
        viewport: { width: 1440, height: 1000 }
      });
    }

    this.page = this.context.pages()[0] ?? await this.context.newPage();

    try {
      await this.openStoreHome();
      await this.waitForManualLogin();
      this.currentShopName = await this.getCurrentShopName();
      this.discoveredShops = await this.discoverShops();

      const shops = this.resolveRunShops();
      if (shops.length === 0) {
        throw new Error('No shops were selected for collection. Configure config/shops.json or calibrate shop discovery selectors for --all-shops.');
      }

      for (const shop of shops) {
        await this.collectShop(shop);
      }

      return this.writeTaskMetadata({
        status: this.overallStatus(),
        started_at: startedAt,
        completed_at: new Date().toISOString(),
        artifacts: this.artifacts,
        exports: this.exports,
        items: this.items
      });
    } catch (error) {
      await this.writeTaskMetadata({
        status: 'failed',
        started_at: startedAt,
        completed_at: new Date().toISOString(),
        artifacts: this.artifacts,
        exports: this.exports,
        items: this.items,
        error: serializeError(error)
      });
      throw error;
    } finally {
      if (!this.attachedToExistingBrowser) {
        await this.context?.close();
      }
    }
  }

  async openStoreHome() {
    this.logger.log(`Opening ${STORE_URL}`);
    await this.page.goto(STORE_URL, { waitUntil: 'domcontentloaded' });
  }

  async waitForManualLogin() {
    this.logger.log('Waiting for manual login. Scan QR code or pass platform checks in the opened browser if prompted.');
    await this.waitForLoggedInShell(this.task.login_timeout_ms);
  }

  async discoverShops() {
    const discovery = this.task.shop_selection?.discovery ?? {};
    if (discovery.enabled === false) {
      return [];
    }

    const itemSelector = this.optionalCommonSelector('shopDiscoveryItems');
    const nameSelector = this.optionalCommonSelector('shopDiscoveryItemName');

    if (!itemSelector || !nameSelector) {
      this.logger.warn('Shop discovery selectors are not calibrated; using configured shops only.');
      return [];
    }

    this.logger.log('Discovering shops available to the current account.');
    try {
      await this.page.click(this.commonSelector('shopSwitcherButton'));
      const shops = await this.page.locator(itemSelector).evaluateAll((nodes, selector) => nodes.map((node, index) => {
        const labelNode = node.querySelector(selector);
        const name = (labelNode?.textContent ?? node.textContent ?? '').trim();
        return name ? { id: name, name, switchLabel: name, discovered: true, discoveryIndex: index } : null;
      }).filter(Boolean), nameSelector);

      return shops.map((shop) => ({
        ...shop,
        id: safePathSegment(shop.id) || `discovered_shop_${shop.discoveryIndex + 1}`
      }));
    } catch (error) {
      this.logger.warn(`Shop discovery failed; using configured shops only. ${error.message}`);
      return [];
    } finally {
      await this.page.keyboard.press('Escape').catch(() => {});
    }
  }

  async getCurrentShopName() {
    const selector = this.optionalCommonSelector('currentShopName');
    if (!selector) {
      return null;
    }

    try {
      return await this.page.locator(selector).first().innerText({ timeout: 5000 });
    } catch (error) {
      this.logger.warn(`Unable to read current shop name: ${error.message}`);
      return null;
    }
  }

  async switchShopAndConfirm(shop) {
    const shopLabel = shop?.switchLabel ?? shop?.name ?? shop?.wechatStoreId ?? shop?.id;

    if (shop?.useCurrentShop === true) {
      this.logger.log(`Using current logged-in shop${shopLabel ? ` for ${shopLabel}` : ''}; shop switching is skipped by config.`);
      const currentShopName = await this.getCurrentShopName();
      if (!currentShopName) {
        throw new Error('Current-shop mode requires a calibrated currentShopName selector so data is not collected from the wrong shop.');
      }
      this.logger.log(`Current shop appears to be ${currentShopName}`);
      if (shopLabel && !currentShopName.includes(shopLabel)) {
        throw new Error(`Current-shop confirmation failed. Expected "${shopLabel}", current shop appears to be "${currentShopName}".`);
      }
      return;
    }

    if (!shopLabel) {
      this.logger.warn('No shop switch target configured; assuming the current logged-in shop is correct.');
      return;
    }

    this.logger.log(`Switching shop to ${shopLabel}`);
    await this.page.click(this.commonSelector('shopSwitcherButton'));
    await this.page.fill(this.commonSelector('shopSearchInput'), shopLabel);
    await this.page.click(this.commonSelector('shopResult').replace('{{shopLabel}}', shopLabel));
    await this.page.waitForLoadState('networkidle');
    const currentShopName = await this.getCurrentShopName();
    if (currentShopName && !currentShopName.includes(shopLabel)) {
      throw new Error(`Shop switch confirmation failed. Expected "${shopLabel}", current shop appears to be "${currentShopName}".`);
    }
  }

  async collectShop(shop) {
    const shopMeta = shopMetadata(shop);
    if (shop?.enabled === false) {
      for (const type of this.task.types) {
        this.items.push({
          ...shopMeta,
          type,
          label: this.exportDefinition(type).label,
          status: 'skipped',
          started_at: new Date().toISOString(),
          completed_at: new Date().toISOString(),
          export: null,
          error: {
            name: 'ShopSkipped',
            message: `Shop "${shop.id}" is disabled in config/shops.json.`
          }
        });
      }
      return;
    }

    try {
      await this.switchShopAndConfirm(shop);
    } catch (error) {
      for (const type of this.task.types) {
        this.items.push(this.itemRecord({
          shop,
          type,
          status: classifyErrorStatus(error),
          error
        }));
      }
      return;
    }

    for (const type of this.task.types) {
      const itemStartedAt = new Date().toISOString();
      try {
        const exportRecord = await this.exportType(type, shop);
        this.exports.push(exportRecord);
        this.items.push({
          ...shopMeta,
          type,
          label: exportRecord.label,
          status: 'completed',
          started_at: itemStartedAt,
          completed_at: new Date().toISOString(),
          export: exportRecord,
          error: null
        });
      } catch (error) {
        this.items.push(this.itemRecord({
          shop,
          type,
          status: classifyErrorStatus(error),
          startedAt: itemStartedAt,
          error
        }));
      }
    }
  }

  async exportType(type, shop) {
    const definition = this.exportDefinition(type);
    const dateStrategy = this.dateRangeStrategy(type, definition);
    this.logger.log(`Starting ${type} export for ${shop?.name ?? shop?.id ?? 'current shop'}`);

    await this.openExportPage(type, definition);
    await this.applyDateRange(type, definition, dateStrategy);
    const download = await this.triggerExportDownload(type, definition);
    const createdAt = new Date().toISOString();
    const saved = await this.saveDownload(type, download, shop, definition, createdAt);
    const artifact = await buildCollectedArtifact({
      task: this.task,
      shop,
      exportType: type,
      tableHint: definition.tableHint ?? definition.table_hint ?? DEFAULT_TABLE_HINT_BY_EXPORT_TYPE[type] ?? type,
      savedPath: saved.saved_path,
      originalFilename: saved.original_filename,
      status: 'completed',
      createdAt,
      metadata: this.exportArtifactMetadata(type, definition, dateStrategy)
    });
    this.artifacts.push(artifact);

    return {
      shop_id: shop?.id ?? null,
      shop_name: shop?.name ?? null,
      type,
      export_type: type,
      label: definition.label,
      table_hint: artifact.table_hint,
      expected_formats: definition.expectedFormats ?? [],
      saved_path: saved.saved_path,
      original_filename: saved.original_filename,
      suggested_filename: saved.suggested_filename,
      date_range_mode: dateStrategy.mode,
      data_coverage: definition.dataCoverage ?? (type === 'orders' ? 'full_export_requested' : 'export_file'),
      sha256: artifact.sha256,
      size_bytes: artifact.size_bytes,
      artifact,
      saved_at: createdAt
    };
  }

  async openExportPage(type, definition) {
    const navigation = this.exportNavigationSelector(type, definition);
    if (navigation) {
      await this.openExportPageFromNavigation(type, navigation);
    } else {
      await this.openExportPageFromDirectUrl(type, definition);
    }

    await this.assertPermission(type);
    await this.waitForExportPageReady(type, definition);
  }

  async openExportPageFromDirectUrl(type, definition) {
    const target = definition.url ?? definition.path;
    if (!target || isTodoValue(target)) {
      throw new Error(selectorError(type, 'export page URL/path', 'path or url'));
    }

    const targetUrl = target.startsWith('http') ? target : new URL(target, STORE_URL).toString();
    await this.page.goto(targetUrl, { waitUntil: 'domcontentloaded' });
  }

  async openExportPageFromNavigation(type, navigation) {
    this.logger.log(`Opening store home before navigating to ${type} via ${navigation.configKey}`);
    await this.openStoreHome();
    await this.waitForLoggedInShell(this.task.download_timeout_ms);
    await this.waitForPageLoadStability();

    if (type === 'orders') {
      await this.openOrdersPageFromVisibleNavigation();
      await this.waitForPageLoadStability();
      await this.dismissOpenNavigationOverlays();
      await this.waitForExportPageContent(type);
      return;
    }

    const entry = await this.visibleNavigationEntry(type, navigation);
    await this.assertSafeNavigationEntry(type, navigation, entry);
    await this.clickNavigationEntry(type, navigation, entry);
    await this.waitForPageLoadStability();
    await this.dismissOpenNavigationOverlays();
    await this.waitForExportPageContent(type);
  }

  async openOrdersPageFromVisibleNavigation() {
    if (/\/shop\/order\/list(?:[/?#]|$)/.test(this.page.url()) && await this.hasOrderListText()) {
      return;
    }

    const clickedOrderDelivery = await clickAllowedNavigationEntry(this.page, '订单/配送');
    if (clickedOrderDelivery) {
      await this.page.waitForTimeout(800);
    }

    const clickedOrderManagement = await clickAllowedNavigationEntry(this.page, '订单管理');
    if (clickedOrderManagement) {
      await this.page.waitForTimeout(2500);
      if (/\/shop\/order\/list(?:[/?#]|$)/.test(this.page.url()) || await this.hasOrderListText()) {
        return;
      }
      this.logger.warn(`Order management visible navigation click did not expose the order list; current URL: ${this.page.url()}`);
    }

    const clickedHref = await clickAllowedOrderHref(this.page);
    if (clickedHref) {
      this.logger.warn('Used visible order href fallback for orders navigation.');
      await this.page.waitForTimeout(2500);
      if (/\/shop\/order\/list(?:[/?#]|$)/.test(this.page.url()) || await this.hasOrderListText()) {
        return;
      }
    }

    await this.page.goto(new URL('/shop/order/list', STORE_URL).toString(), {
      waitUntil: 'domcontentloaded',
      timeout: this.task.download_timeout_ms
    });
    await this.page.waitForTimeout(8000);
  }

  async hasOrderListText() {
    return hasOrderListText(this.page);
  }

  async waitForLoggedInShell(timeout) {
    await this.page.waitForFunction(() => {
      const text = document.body?.innerText ?? '';
      return text.includes('首页')
        && text.includes('商品管理')
        && text.includes('订单/配送')
        && !text.includes('扫码进入我的小店')
        && !text.includes('登录超时')
        && !text.includes('请先登录');
    }, null, { timeout });
  }

  async waitForPageLoadStability() {
    const timeout = Math.min(this.task.download_timeout_ms ?? 30000, 15000);
    await this.page.waitForLoadState('domcontentloaded', { timeout }).catch(() => {});
    await this.page.waitForLoadState('load', { timeout }).catch(() => {});
    await this.page.waitForLoadState('networkidle', { timeout: Math.min(timeout, 5000) }).catch(() => {});
  }

  async dismissOpenNavigationOverlays() {
    await dismissOpenNavigationOverlays(this.page);
  }

  async visibleNavigationEntry(type, navigation) {
    const entry = this.page.locator(navigation.selector).first();
    try {
      await entry.waitFor({ state: 'visible', timeout: this.task.download_timeout_ms });
    } catch (error) {
      const href = hrefFromAnchorSelector(navigation.selector);
      if (!href) {
        throw new Error(`Navigation selector "${navigation.configKey}" for export type "${type}" did not match a visible entry after opening store home. Selector: ${navigation.selector}. Calibrate it to a sidebar or menu entry, not an export/action button. Original error: ${error.message}`);
      }

      const hiddenEntry = this.page.locator(`a[href="${cssString(href)}"]`).first();
      try {
        await hiddenEntry.waitFor({ state: 'attached', timeout: this.task.download_timeout_ms });
        this.logger.warn(`Navigation selector "${navigation.configKey}" for export type "${type}" is attached but not visible; using DOM click fallback for href ${href}.`);
        return hiddenEntry;
      } catch {
        throw new Error(`Navigation selector "${navigation.configKey}" for export type "${type}" did not match an attached entry after opening store home. Selector: ${navigation.selector}. Original error: ${error.message}`);
      }
    }
    return entry;
  }

  async clickNavigationEntry(type, navigation, entry) {
    try {
      await entry.click({ timeout: this.task.download_timeout_ms });
    } catch (error) {
      const href = hrefFromAnchorSelector(navigation.selector);
      if (!href || !/not visible|visible|stable/i.test(error.message)) {
        throw error;
      }
      const clicked = await this.page.evaluate((targetHref) => {
        const escapedHref = String(targetHref).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
        const element = document.querySelector(`a[href="${escapedHref}"]`);
        if (!element) {
          return false;
        }
        element.click();
        return true;
      }, href);
      if (!clicked) {
        throw error;
      }
      this.logger.warn(`Used DOM click fallback for hidden navigation entry ${navigation.configKey} on export type "${type}".`);
    }
  }

  async assertSafeNavigationEntry(type, navigation, entry) {
    const summary = await entry.evaluate(clickableSummary);

    const label = [summary.text, summary.ariaLabel, summary.title].filter(Boolean).join(' ').trim();
    const looksLikeBusinessCommand = NAVIGATION_DENY_TEXT_PATTERN.test([
      label,
      summary.href,
      summary.datasetAction,
      summary.className
    ].join(' '));

    if (looksLikeBusinessCommand) {
      throw new Error(`Refusing to click navigation selector "${navigation.configKey}" for export type "${type}" because the matched element looks like a business action: "${label}". Calibrate the selector to a sidebar or menu entry, not export, shipping, refund, or submit controls.`);
    }
  }

  async applyDateRange(type, definition, dateStrategy = this.dateRangeStrategy(type, definition)) {
    if (dateStrategy.mode === 'skip-default') {
      this.logger.warn(`Skipping custom date range for ${type}; using page default range${dateStrategy.expectedLabel ? ` (${dateStrategy.expectedLabel})` : ''}.`);
      return;
    }

    this.logger.log(`Applying date range ${this.task.from} to ${this.task.to} for ${type}`);
    const scope = await this.activeScopeForDefinition(type, definition);
    const dateRangeButton = this.optionalDateRangeSelector(type, definition, dateStrategy, 'dateRangeButton');
    if (dateRangeButton) {
      const dateRangeButtonEntry = scope.locator(dateRangeButton).first();
      const visible = await dateRangeButtonEntry.isVisible({ timeout: 3000 }).catch(() => false);
      if (visible) {
        await dateRangeButtonEntry.click();
      } else if (dateStrategy.dateRangeButtonRequired) {
        throw new Error(`Date range button for "${type}" was required but not visible: ${dateRangeButton}`);
      } else {
        this.logger.warn(`Date range button for ${type} was not visible; filling visible date inputs directly.`);
      }
    }
    await this.fillDateInput(scope, this.dateRangeSelector(type, definition, dateStrategy, 'dateStartInput'), this.task.from);
    await this.fillDateInput(scope, this.dateRangeSelector(type, definition, dateStrategy, 'dateEndInput'), this.task.to);
    await scope.click(this.dateRangeSelector(type, definition, dateStrategy, 'dateConfirmButton'));
    await this.waitForExportPageReady(type, definition);
  }

  async fillDateInput(scope, selector, value) {
    try {
      await scope.fill(selector, value, { timeout: 5000 });
      return;
    } catch (error) {
      if (!/not editable|readonly|Timeout/i.test(error.message)) {
        throw error;
      }
      this.logger.warn(`Date input ${selector} was not directly editable; setting value through DOM events.`);
    }

    const locator = scope.locator(selector).first();
    await locator.evaluate((element, nextValue) => {
      const input = element;
      input.removeAttribute('readonly');
      input.value = nextValue;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      input.dispatchEvent(new Event('blur', { bubbles: true }));
    }, value);
  }

  async triggerExportDownload(type, definition) {
    const exportButton = this.exportSelector(type, definition, 'exportButton');
    const scope = await this.activeScopeForDefinition(type, definition);
    const exportEntry = scope.locator(exportButton).first();
    await exportEntry.waitFor({ state: 'visible', timeout: this.task.download_timeout_ms });
    await this.assertSafeExportButton(type, exportButton, exportEntry);
    return this.clickAndWaitForExportDownload(type, definition, exportEntry);
  }

  async clickAndWaitForExportDownload(type, definition, exportEntry) {
    const workflow = this.exportWorkflow(definition);
    const downloadPromise = this.page.waitForEvent('download', { timeout: workflow.directDownloadTimeoutMs });
    await exportEntry.click();
    await this.handleExportConfirmation(type, definition, workflow);

    try {
      return await downloadPromise;
    } catch (error) {
      if (workflow.downloadReadySelector || workflow.generatedDownloadSelector) {
        this.logger.warn(`Direct download event was not emitted for ${type}; waiting for generated download control.`);
        return this.waitForGeneratedDownload(type, definition, workflow);
      }
      throw error;
    }
  }

  async handleExportConfirmation(type, definition, workflow) {
    const confirmSelector = workflow.confirmSelector ?? definition.selectors?.exportConfirmButton;
    if (!confirmSelector) {
      return;
    }
    const scope = await this.activeScopeForDefinition(type, definition);
    const confirmEntry = scope.locator(confirmSelector).first();
    const visible = await confirmEntry.isVisible({ timeout: workflow.confirmTimeoutMs }).catch(() => false);
    if (!visible) {
      if (workflow.confirmRequired) {
        throw new Error(`Export confirmation selector for "${type}" was required but not visible: ${confirmSelector}`);
      }
      return;
    }
    await this.assertSafeExportButton(type, confirmSelector, confirmEntry);
    this.logger.log(`Confirming export dialog for ${type}.`);
    await confirmEntry.click();
  }

  async waitForGeneratedDownload(type, definition, workflow) {
    const scope = await this.activeScopeForDefinition(type, definition);
    const readySelector = workflow.downloadReadySelector;
    if (readySelector) {
      await scope.locator(readySelector).first().waitFor({
        state: 'visible',
        timeout: workflow.generatedDownloadTimeoutMs
      });
    } else {
      await this.page.waitForTimeout(workflow.generatedDownloadPollMs);
    }

    const selector = workflow.generatedDownloadSelector ?? readySelector;
    if (!selector) {
      throw new Error(`Export workflow for "${type}" needs generatedDownloadSelector or downloadReadySelector.`);
    }
    const generatedEntry = scope.locator(selector).first();
    await generatedEntry.waitFor({ state: 'visible', timeout: workflow.generatedDownloadTimeoutMs });
    await this.assertSafeExportButton(type, selector, generatedEntry);
    const [download] = await Promise.all([
      this.page.waitForEvent('download', { timeout: this.task.download_timeout_ms }),
      generatedEntry.click()
    ]);
    return download;
  }

  async waitForExportPageReady(type, definition) {
    const scope = await this.activeScopeForDefinition(type, definition);
    await scope.waitForSelector(this.exportSelector(type, definition, 'pageReady'), {
      timeout: this.task.download_timeout_ms
    });
  }

  async waitForExportPageContent(type) {
    if (type !== 'orders') {
      return;
    }

    await waitForOrderPageContent(this.page, this.task.download_timeout_ms ?? 30000);
  }

  async activeScopeForDefinition(type, definition) {
    const frameUrlIncludes = this.frameUrlIncludesForDefinition(type, definition);
    if (frameUrlIncludes.length === 0) {
      return this.page;
    }

    const timeout = this.task.download_timeout_ms ?? 30000;
    const startedAt = Date.now();
    let observedFrameUrls = [];

    while (Date.now() - startedAt <= timeout) {
      const matchingFrame = this.page.frames().find((frame) => {
        if (frame === this.page.mainFrame()) {
          return false;
        }
        const url = frame.url();
        return frameUrlIncludes.every((urlPart) => url.includes(urlPart));
      });

      if (matchingFrame) {
        return matchingFrame;
      }

      observedFrameUrls = this.page.frames()
        .filter((frame) => frame !== this.page.mainFrame())
        .map((frame) => frame.url())
        .filter(Boolean);

      const remaining = timeout - (Date.now() - startedAt);
      if (remaining <= 0) {
        break;
      }
      await this.page.waitForTimeout(Math.min(250, remaining));
    }

    const observed = observedFrameUrls.length > 0
      ? observedFrameUrls.slice(0, 10).join(', ')
      : 'none';
    throw new Error(`Timed out waiting for frameUrlIncludes ${JSON.stringify(frameUrlIncludes)} for export type "${type}". Observed frame URLs: ${observed}`);
  }

  frameUrlIncludesForDefinition(type, definition) {
    const configured = definition.frameUrlIncludes ?? definition.selectors?.frameUrlIncludes;
    if (configured === undefined) {
      return [];
    }

    const values = Array.isArray(configured) ? configured : [configured];
    const urlParts = values.map((value) => String(value).trim()).filter(Boolean);
    if (urlParts.length === 0 || urlParts.some((value) => isTodoValue(value))) {
      throw new Error(selectorError(type, 'frame URL include pattern', 'frameUrlIncludes'));
    }
    return urlParts;
  }

  dateRangeStrategy(type, definition) {
    const configured = definition.dateRange ?? definition.selectors?.dateRange ?? {};
    const mode = configured.mode ?? 'custom-picker';
    if (!['custom-picker', 'skip-default'].includes(mode)) {
      throw new Error(`Unsupported dateRange.mode "${mode}" for export type "${type}".`);
    }
    return {
      mode,
      expectedLabel: configured.expectedLabel ?? null,
      dateRangeButtonRequired: configured.dateRangeButtonRequired === true
    };
  }

  dateRangeSelector(type, definition, dateStrategy, name) {
    const configured = this.optionalDateRangeSelector(type, definition, dateStrategy, name);
    if (configured && !isTodoValue(configured)) {
      return configured;
    }
    return this.commonSelector(name);
  }

  optionalDateRangeSelector(type, definition, dateStrategy, name) {
    const configured = definition.dateRange?.selectors?.[name]
      ?? definition.selectors?.dateRange?.selectors?.[name]
      ?? definition.selectors?.[name];
    return configured && !isTodoValue(configured) ? configured : null;
  }

  exportWorkflow(definition) {
    const workflow = definition.exportWorkflow ?? definition.selectors?.exportWorkflow ?? {};
    return {
      confirmSelector: workflow.confirmSelector ?? workflow.confirmButtonSelector ?? null,
      confirmRequired: workflow.confirmRequired === true,
      confirmTimeoutMs: Number(workflow.confirmTimeoutMs ?? 3000),
      directDownloadTimeoutMs: Number(workflow.directDownloadTimeoutMs ?? this.task.download_timeout_ms ?? 120000),
      downloadReadySelector: workflow.downloadReadySelector ?? null,
      generatedDownloadSelector: workflow.generatedDownloadSelector ?? workflow.downloadButtonSelector ?? null,
      generatedDownloadTimeoutMs: Number(workflow.generatedDownloadTimeoutMs ?? this.task.download_timeout_ms ?? 120000),
      generatedDownloadPollMs: Number(workflow.generatedDownloadPollMs ?? 2000)
    };
  }

  exportArtifactMetadata(type, definition, dateStrategy) {
    return {
      requested_date_range: {
        from: this.task.from,
        to: this.task.to
      },
      date_range_mode: dateStrategy.mode,
      date_range_expected_label: dateStrategy.expectedLabel,
      data_coverage: definition.dataCoverage ?? (type === 'orders' ? 'full_export_requested' : 'export_file'),
      export_workflow: {
        has_confirm_selector: Boolean(definition.exportWorkflow?.confirmSelector ?? definition.selectors?.exportWorkflow?.confirmSelector),
        has_generated_download_selector: Boolean(definition.exportWorkflow?.generatedDownloadSelector ?? definition.selectors?.exportWorkflow?.generatedDownloadSelector)
      }
    };
  }

  async assertSafeExportButton(type, selector, entry) {
    const summary = await entry.evaluate(clickableSummary);
    const label = [summary.text, summary.ariaLabel, summary.title].filter(Boolean).join(' ').trim();
    const haystack = [
      label,
      summary.href,
      summary.datasetAction,
      summary.className
    ].join(' ');

    if (!EXPORT_ALLOW_TEXT_PATTERN.test(haystack) || EXPORT_DENY_TEXT_PATTERN.test(haystack)) {
      throw new Error(`Refusing to click exportButton for "${type}" because selector "${selector}" matched a non-export action: "${label || summary.href || summary.className}". Calibrate exportButton to a visible export/download control only.`);
    }
  }

  async saveDownload(type, download, shop, definition = {}, exportTime = new Date().toISOString()) {
    const shopDir = safePathSegment(shop?.id ?? 'current_shop') || 'current_shop';
    const typeDir = safePathSegment(type) || 'unknown_export';
    const archiveDir = path.join(this.task.download_dir, shopDir, typeDir);
    await fs.mkdir(archiveDir, { recursive: true });

    const suggestedFilename = download.suggestedFilename();
    const savedFilename = this.archiveFilename({
      shop,
      type,
      suggestedFilename,
      expectedFormats: definition.expectedFormats,
      exportTime
    });
    const savedPath = path.join(archiveDir, savedFilename);

    await download.saveAs(savedPath);

    return {
      original_filename: suggestedFilename,
      suggested_filename: suggestedFilename,
      saved_path: savedPath
    };
  }

  archiveFilename({ shop, type, suggestedFilename, expectedFormats = [], exportTime = new Date().toISOString() }) {
    const shopId = safePathSegment(shop?.id ?? 'current_shop') || 'current_shop';
    const exportType = safePathSegment(type) || 'unknown_export';
    const dateRange = safePathSegment(`${this.task.from}_to_${this.task.to}`);
    const exportTimeSegment = safePathSegment(exportTime.replace(/[:.]/g, '-'));
    const originalStem = safePathSegment(path.basename(suggestedFilename ?? '', path.extname(suggestedFilename ?? '')));
    const extension = safeDownloadExtension(suggestedFilename, expectedFormats);

    return [
      shopId,
      exportType,
      dateRange,
      exportTimeSegment,
      originalStem
    ].filter(Boolean).join('__') + extension;
  }

  async writeTaskMetadata(fields) {
    await fs.mkdir(this.task.download_dir, { recursive: true });
    const artifacts = fields.artifacts ?? [];
    const artifactManifest = await writeArtifactManifest({ task: this.task, artifacts });
    const metadata = {
      task_id: this.task.task_id,
      status: fields.status,
      shop_id: this.task.shop_id,
      shop_selection: this.task.shop_selection,
      shops: this.task.shops ?? [],
      discovered_shops: this.discoveredShops,
      current_shop_name: this.currentShopName,
      date_range: {
        from: this.task.from,
        to: this.task.to
      },
      types: this.task.types,
      headless: this.task.headless,
      store_url: STORE_URL,
      profile_dir: this.task.profile_dir,
      download_dir: this.task.download_dir,
      created_at: this.task.created_at,
      started_at: fields.started_at,
      completed_at: fields.completed_at,
      artifact_manifest_path: artifactManifest.manifest_path,
      artifacts,
      items: fields.items ?? [],
      exports: fields.exports ?? [],
      error: fields.error ?? null
    };

    const metadataPath = path.join(this.task.download_dir, 'task-metadata.json');
    await fs.writeFile(metadataPath, JSON.stringify(metadata, null, 2), 'utf8');
    return { ...metadata, metadata_path: metadataPath };
  }

  async assertPermission(type) {
    const selector = this.optionalCommonSelector('noPermission');
    if (!selector) {
      return;
    }

    const noPermission = await this.page.locator(selector).first().isVisible({ timeout: 1000 }).catch(() => false);
    if (noPermission) {
      throw new NoPermissionError(`No permission to export "${type}" for the selected shop.`);
    }
  }

  commonSelector(name) {
    const selector = this.exportConfig?.selectors?.[name] ?? COMMON_SELECTORS[name];
    return requireCalibratedSelector(selector, `common selector "${name}"`);
  }

  optionalCommonSelector(name) {
    const selector = this.exportConfig?.selectors?.[name] ?? COMMON_SELECTORS[name];
    return selector && !isTodoValue(selector) ? selector : null;
  }

  exportSelector(type, definition, name) {
    const selector = definition.selectors?.[name];
    return requireCalibratedSelector(selector, `${type} selector "${name}"`);
  }

  exportNavigationSelector(type, definition) {
    const candidates = [
      ['navigationSelector', definition.navigationSelector],
      ['entrySelector', definition.entrySelector],
      ['selectors.navigationSelector', definition.selectors?.navigationSelector],
      ['selectors.entrySelector', definition.selectors?.entrySelector]
    ];
    const configured = candidates.find(([, selector]) => selector !== undefined);

    if (!configured) {
      return null;
    }

    const [configKey, selector] = configured;
    if (!selector || isTodoValue(selector)) {
      throw new Error(selectorError(type, `navigation selector "${configKey}"`, configKey));
    }

    return { configKey, selector };
  }

  exportDefinition(type) {
    const defaultDefinition = DEFAULT_EXPORT_DEFINITIONS[type];
    const configuredDefinition = this.exportConfig?.types?.[type];

    if (!defaultDefinition && !configuredDefinition) {
      throw new Error(`Unsupported export type "${type}".`);
    }

    return {
      ...defaultDefinition,
      ...configuredDefinition,
      selectors: {
        ...(defaultDefinition?.selectors ?? {}),
        ...(configuredDefinition?.selectors ?? {})
      }
    };
  }

  resolveRunShops() {
    if (this.task.shop_selection?.mode === 'all' && this.discoveredShops.length > 0) {
      return this.discoveredShops;
    }
    return this.task.shops ?? [];
  }

  overallStatus() {
    if (this.items.length === 0) {
      return 'skipped';
    }
    return this.items.every((item) => item.status === 'completed') ? 'completed' : 'completed_with_item_errors';
  }

  itemRecord({ shop, type, status, startedAt = new Date().toISOString(), error }) {
    return {
      ...shopMetadata(shop),
      type,
      label: this.exportDefinition(type).label,
      status,
      started_at: startedAt,
      completed_at: new Date().toISOString(),
      export: null,
      error: serializeError(error)
    };
  }
}

class NoPermissionError extends Error {
  constructor(message) {
    super(message);
    this.name = 'NoPermissionError';
  }
}

function requireCalibratedSelector(selector, label) {
  if (!selector || isTodoValue(selector)) {
    throw new Error(`Missing calibrated selector for ${label}. Replace the TODO selector in config/export-tasks.json or src/collector/wechatStoreCollector.js after inspecting the real WeChat Store page.`);
  }
  return selector;
}

function selectorError(type, label, configKey) {
  return `Missing calibrated ${label} for export type "${type}". Set "${configKey}" in config/export-tasks.json after inspecting the real WeChat Store page.`;
}

export async function dismissOpenNavigationOverlays(page) {
  if (!page) {
    return;
  }

  await page.keyboard?.press?.('Escape').catch(() => {});
  await page.mouse?.click?.(520, 140).catch(() => {});
  await page.waitForTimeout?.(300).catch(() => {});
}

export async function waitForOrderPageContent(page, timeoutMs = 30000) {
  if (!page) {
    return;
  }

  await page.waitForFunction(
    () => {
      const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
      const roots = [];
      const visit = (root) => {
        if (!root) {
          return;
        }
        roots.push(root);
        for (const element of [...(root.children ?? [])]) {
          if (element.shadowRoot) {
            visit(element.shadowRoot);
          }
          visit(element);
        }
      };
      visit(document.body);
      const text = roots
        .map((root) => clean(root.innerText || root.textContent || ''))
        .filter(Boolean)
        .join('\n');
      return /全部导出|订单号|订单编号|下单时间|订单状态|暂无订单|暂无数据/.test(text);
    },
    null,
    { timeout: timeoutMs }
  );
}

export async function clickAllowedNavigationEntry(page, label) {
  return page.evaluate((targetLabel) => {
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
}

export async function clickAllowedOrderHref(page) {
  return page.evaluate(() => {
    const entry = [...document.querySelectorAll('a[href="/shop/order/list"]')].find((element) => {
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
  });
}

export async function hasOrderListText(page) {
  return page.locator('body').innerText({ timeout: 2000 })
    .then((text) => /全部导出|订单号|订单编号|下单时间|订单状态/.test(text))
    .catch(() => false);
}

function isTodoValue(value) {
  return String(value).startsWith('TODO_');
}

function hrefFromAnchorSelector(selector) {
  const match = String(selector).match(/^a\[href=(["'])(.+)\1\]$/);
  return match?.[2] ?? null;
}

function cssString(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function clickableSummary(node) {
  const clickable = node.closest('button,a,[role="button"],[role="menuitem"],[tabindex]') ?? node;
  const dataset = clickable.dataset ?? {};
  return {
    text: (clickable.textContent ?? '').replace(/\s+/g, ' ').trim(),
    ariaLabel: clickable.getAttribute('aria-label') ?? '',
    title: clickable.getAttribute('title') ?? '',
    role: clickable.getAttribute('role') ?? '',
    tagName: clickable.tagName.toLowerCase(),
    inputType: clickable.getAttribute('type') ?? '',
    href: clickable.getAttribute('href') ?? '',
    className: typeof clickable.className === 'string' ? clickable.className : '',
    datasetAction: [dataset.action, dataset.type, dataset.event, dataset.name].filter(Boolean).join(' ')
  };
}

function classifyErrorStatus(error) {
  if (error?.name === 'NoPermissionError' || /permission|unauthorized|forbidden|无权限|未授权/i.test(error?.message ?? '')) {
    return 'no_permission';
  }
  return 'failed';
}

function shopMetadata(shop) {
  return {
    shop_id: shop?.id ?? null,
    shop_name: shop?.name ?? null,
    shop_switch_label: shop?.switchLabel ?? null
  };
}

function serializeError(error) {
  if (!error) {
    return null;
  }
  return {
    name: error.name,
    message: error.message,
    stack: error.stack
  };
}

function safeDownloadExtension(filename, expectedFormats = []) {
  const ext = path.extname(String(filename ?? '')).toLowerCase();
  if (ext && /^[.][a-z0-9]{1,12}$/.test(ext)) {
    return ext;
  }

  const firstExpectedFormat = expectedFormats
    .map((format) => safePathSegment(format).toLowerCase())
    .find(Boolean);

  return firstExpectedFormat ? `.${firstExpectedFormat}` : '.download';
}
