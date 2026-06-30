import fs from 'node:fs/promises';
import path from 'node:path';
import { WechatStoreCollector, DEFAULT_EXPORT_TYPES } from './wechatStoreCollector.js';
import { compactUtcTimestamp, projectRootFrom, safePathSegment } from '../shared/nodePaths.js';

const PROJECT_ROOT = projectRootFrom(import.meta.url);
const DEFAULT_HEADLESS = false;
const SAFE_DEFAULT_EXPORT_TYPES = ['orders', 'products', 'compass'];
const REQUIRED_COMMON_SELECTORS = [
  'loggedInShell'
];
const DATE_RANGE_COMMON_SELECTORS = [
  'dateRangeButton',
  'dateStartInput',
  'dateEndInput',
  'dateConfirmButton'
];
const SHOP_SWITCH_COMMON_SELECTORS = [
  'shopSwitcherButton',
  'shopSearchInput',
  'shopResult'
];
const OPTIONAL_COMMON_SELECTORS = [
  'currentShopName',
  'shopDiscoveryItems',
  'shopDiscoveryItemName',
  'noPermission'
];
const REQUIRED_EXPORT_SELECTORS = ['pageReady', 'exportButton'];
const EXPORT_NAVIGATION_SELECTOR_NAMES = [
  'navigationSelector',
  'entrySelector',
  'selectors.navigationSelector',
  'selectors.entrySelector'
];
const EXPORT_FRAME_URL_INCLUDE_NAMES = [
  'frameUrlIncludes',
  'selectors.frameUrlIncludes'
];
const EXPORT_DATE_RANGE_SELECTOR_NAMES = [
  'dateRange.selectors.dateRangeButton',
  'dateRange.selectors.dateStartInput',
  'dateRange.selectors.dateEndInput',
  'dateRange.selectors.dateConfirmButton',
  'selectors.dateRange.selectors.dateRangeButton',
  'selectors.dateRange.selectors.dateStartInput',
  'selectors.dateRange.selectors.dateEndInput',
  'selectors.dateRange.selectors.dateConfirmButton',
  'selectors.dateRangeButton',
  'selectors.dateStartInput',
  'selectors.dateEndInput',
  'selectors.dateConfirmButton'
];

export async function runCollection(argv = process.argv.slice(2), options = {}) {
  const logger = options.logger ?? console;
  const projectRoot = options.projectRoot ?? PROJECT_ROOT;
  const wantsConfigCheck = hasCheckConfigMode(argv);
  let parsedArgs;

  try {
    parsedArgs = parseCollectArgs(argv);
  } catch (error) {
    if (wantsConfigCheck) {
      const configState = await loadCollectionConfigState(projectRoot);
      const result = buildCollectionConfigCheck({
        args: { allShops: false },
        configState,
        projectRoot,
        parseError: error
      });
      logger.log(JSON.stringify(result, null, 2));
      return result;
    }
    throw error;
  }

  if (parsedArgs.help) {
    logger.log(usageText());
    return { help: true };
  }

  const configState = parsedArgs.checkConfig ? await loadCollectionConfigState(projectRoot) : null;
  const config = configState?.config ?? await loadCollectionConfig(projectRoot);

  if (parsedArgs.checkConfig) {
    const result = buildCollectionConfigCheck({ args: parsedArgs, configState, projectRoot });
    logger.log(JSON.stringify(result, null, 2));
    return result;
  }

  const task = buildCollectionTask(parsedArgs, config, projectRoot, options.now ?? new Date());

  await prepareCollectionTask(task);

  const collector = new WechatStoreCollector({
    task,
    exportConfig: config.exportTasks,
    projectRoot,
    logger
  });

  return collector.run();
}

export function parseCollectArgs(argv) {
  const args = {
    headless: undefined,
    types: undefined,
    allShops: false,
    checkConfig: false
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];

    if (arg === '--help' || arg === '-h') {
      args.help = true;
      continue;
    }

    if (arg === 'check-config' || arg === '--check-config') {
      args.checkConfig = true;
      continue;
    }

    if (arg.startsWith('--check-config=')) {
      args.checkConfig = parseBoolean(arg.slice('--check-config='.length), '--check-config');
      continue;
    }

    if (arg === '--no-headless') {
      args.headless = false;
      continue;
    }

    if (arg === '--headless') {
      const next = argv[index + 1];
      if (next && !next.startsWith('--')) {
        args.headless = parseBoolean(next, '--headless');
        index += 1;
      } else {
        args.headless = true;
      }
      continue;
    }

    if (arg.startsWith('--headless=')) {
      args.headless = parseBoolean(arg.slice('--headless='.length), '--headless');
      continue;
    }

    if (arg === '--all-shops') {
      args.allShops = true;
      continue;
    }

    const option = readStringOption(argv, index, arg);
    if (option) {
      args[option.name] = option.value;
      index = option.nextIndex;
      continue;
    }

    throw new Error(`Unknown argument: ${arg}\n\n${usageText()}`);
  }

  if (args.types) {
    args.types = args.types.split(',').map((type) => type.trim()).filter(Boolean);
  }

  if (args.shops) {
    args.shops = args.shops.split(',').map((shop) => shop.trim()).filter(Boolean);
  }

  return args;
}

export async function loadCollectionConfig(projectRoot = PROJECT_ROOT) {
  const state = await loadCollectionConfigState(projectRoot);

  for (const fileState of Object.values(state.files)) {
    if (fileState.exists && !fileState.ok) {
      throw new Error(`Failed to read JSON config at ${fileState.path}: ${fileState.error}`);
    }
  }

  return state.config;
}

export async function loadCollectionConfigState(projectRoot = PROJECT_ROOT) {
  const paths = {
    shops: path.join(projectRoot, 'config', 'shops.json'),
    exportTasks: path.join(projectRoot, 'config', 'export-tasks.json')
  };

  const [shopsFile, exportTasksFile] = await Promise.all([
    readJsonFileState(paths.shops),
    readJsonFileState(paths.exportTasks)
  ]);

  return {
    config: {
      shops: shopsFile.value ?? { shops: [] },
      exportTasks: exportTasksFile.value ?? {}
    },
    files: {
      shops: shopsFile,
      export_tasks: exportTasksFile
    }
  };
}

export function buildCollectionConfigCheck({ args, configState, projectRoot = PROJECT_ROOT, parseError = null }) {
  const errors = [];
  const warnings = [];
  const config = configState?.config ?? { shops: { shops: [] }, exportTasks: {} };
  const fileStates = configState?.files ?? {};

  if (parseError) {
    addIssue(errors, 'invalid_arguments', parseError.message);
  }

  addConfigFileIssues(fileStates, errors);

  const dateRange = validateDateRange(args, errors);
  const shops = validateShopConfig(args, config.shops, errors, warnings);
  const types = validateExportTaskConfig(args, config.exportTasks, errors, warnings, shops);

  return {
    check_config: true,
    ok: errors.length === 0,
    errors,
    warnings,
    shops,
    types,
    date_range: dateRange,
    config_paths: buildConfigPathSummary(fileStates, projectRoot)
  };
}

export function buildCollectionTask(args, config, projectRoot = PROJECT_ROOT, now = new Date()) {
  assertShopSelection(args);
  assertRequired(args.from, '--from');
  assertRequired(args.to, '--to');
  assertDate(args.from, '--from');
  assertDate(args.to, '--to');

  if (args.from > args.to) {
    throw new Error(`--from must be earlier than or equal to --to. Received ${args.from} > ${args.to}.`);
  }

  const defaults = config.exportTasks?.defaults ?? {};
  const supportedTypes = [...new Set([...DEFAULT_EXPORT_TYPES, ...Object.keys(config.exportTasks?.types ?? {})])];
  const fallbackTypes = supportedTypes.filter((type) => SAFE_DEFAULT_EXPORT_TYPES.includes(type));
  const requestedTypes = args.types ?? defaults.types ?? fallbackTypes;
  const types = normalizeTypes(requestedTypes, supportedTypes);
  const headless = args.headless ?? defaults.headless ?? DEFAULT_HEADLESS;
  const shopSelection = buildShopSelection(args, config.shops);
  const taskShopSegment = shopSelection.mode === 'all'
    ? 'all_shops'
    : shopSelection.shops.map((shop) => safePathSegment(shop.id)).join('-');
  const taskId = [
    'collect',
    taskShopSegment,
    args.from,
    args.to,
    compactUtcTimestamp(now)
  ].filter(Boolean).join('_');

  const downloadDir = path.join(projectRoot, 'data', 'raw', taskId);
  const profileDir = path.join(projectRoot, 'data', 'browser-profile');

  return {
    task_id: taskId,
    created_at: now.toISOString(),
    shop_id: shopSelection.mode === 'single' ? shopSelection.shops[0]?.id : null,
    shop_selection: shopSelection,
    shops: shopSelection.shops,
    from: args.from,
    to: args.to,
    types,
    headless,
    login_timeout_ms: Number(defaults.loginTimeoutMs ?? 10 * 60 * 1000),
    download_timeout_ms: Number(defaults.downloadTimeoutMs ?? 2 * 60 * 1000),
    project_root: projectRoot,
    profile_dir: profileDir,
    download_dir: downloadDir
  };
}

export async function prepareCollectionTask(task) {
  await fs.mkdir(task.download_dir, { recursive: true });
}

function readStringOption(argv, index, arg) {
  const optionNames = new Map([
    ['--shop-id', 'shopId'],
    ['--shops', 'shops'],
    ['--from', 'from'],
    ['--to', 'to'],
    ['--types', 'types']
  ]);

  for (const [flag, name] of optionNames.entries()) {
    if (arg === flag) {
      const next = argv[index + 1];
      if (!next || next.startsWith('--')) {
        throw new Error(`${flag} requires a value.`);
      }
      return { name, value: next, nextIndex: index + 1 };
    }

    if (arg.startsWith(`${flag}=`)) {
      const value = arg.slice(flag.length + 1);
      if (!value) {
        throw new Error(`${flag} requires a value.`);
      }
      return { name, value, nextIndex: index };
    }
  }

  return null;
}

function hasCheckConfigMode(argv) {
  return argv.some((arg) => arg === 'check-config' || arg === '--check-config' || arg.startsWith('--check-config='));
}

function parseBoolean(value, flag) {
  if (['true', '1', 'yes', 'y'].includes(String(value).toLowerCase())) {
    return true;
  }
  if (['false', '0', 'no', 'n'].includes(String(value).toLowerCase())) {
    return false;
  }
  throw new Error(`${flag} expects true or false. Received: ${value}`);
}

function normalizeTypes(requestedTypes, supportedTypes) {
  const types = Array.isArray(requestedTypes)
    ? requestedTypes
    : String(requestedTypes).split(',').map((type) => type.trim()).filter(Boolean);

  if (types.length === 0) {
    throw new Error('At least one export type is required.');
  }

  const unknown = types.filter((type) => !supportedTypes.includes(type));
  if (unknown.length > 0) {
    throw new Error(`Unsupported export type(s): ${unknown.join(', ')}. Supported: ${supportedTypes.join(', ')}.`);
  }

  return [...new Set(types)];
}

function addConfigFileIssues(fileStates, errors) {
  const required = [
    ['shops', 'config/shops.json'],
    ['export_tasks', 'config/export-tasks.json']
  ];

  for (const [name, displayPath] of required) {
    const fileState = fileStates[name];

    if (!fileState?.exists) {
      addIssue(errors, 'missing_config_file', `${displayPath} was not found.`, {
        config: name,
        path: fileState?.path ?? displayPath
      });
      continue;
    }

    if (!fileState.ok) {
      addIssue(errors, 'invalid_config_json', `${displayPath} is not valid JSON: ${fileState.error}`, {
        config: name,
        path: fileState.path
      });
    }
  }
}

function validateDateRange(args, errors) {
  const from = args.from ?? null;
  const to = args.to ?? null;

  if (!from) {
    addIssue(errors, 'missing_date', '--from is required.', { flag: '--from' });
  } else if (!isIsoDate(from)) {
    addIssue(errors, 'invalid_date', `--from must use YYYY-MM-DD format. Received: ${from}`, { flag: '--from', value: from });
  }

  if (!to) {
    addIssue(errors, 'missing_date', '--to is required.', { flag: '--to' });
  } else if (!isIsoDate(to)) {
    addIssue(errors, 'invalid_date', `--to must use YYYY-MM-DD format. Received: ${to}`, { flag: '--to', value: to });
  }

  if (isIsoDate(from) && isIsoDate(to) && from > to) {
    addIssue(errors, 'invalid_date_range', `--from must be earlier than or equal to --to. Received ${from} > ${to}.`, {
      from,
      to
    });
  }

  return {
    from,
    to,
    valid: isIsoDate(from) && isIsoDate(to) && from <= to
  };
}

function validateShopConfig(args, shopsConfig, errors, warnings) {
  const configuredShops = Array.isArray(shopsConfig?.shops) ? shopsConfig.shops : [];
  const configuredIds = configuredShops.map((shop) => shop?.id).filter(Boolean);
  const duplicateIds = duplicates(configuredIds);
  const selectionModes = [
    args.shopId ? '--shop-id' : null,
    args.shops?.length ? '--shops' : null,
    args.allShops ? '--all-shops' : null
  ].filter(Boolean);
  let selectionMode = selectionModes[0] ?? null;
  let requestedShopIds = [];
  let selectedShops = [];

  if (!Array.isArray(shopsConfig?.shops)) {
    addIssue(errors, 'invalid_shops_config', 'config/shops.json must contain a shops array.', {
      path: 'shops'
    });
  }

  for (const duplicateId of duplicateIds) {
    addIssue(errors, 'duplicate_shop_id', `Duplicate shop id in config/shops.json: ${duplicateId}`, {
      shop_id: duplicateId
    });
  }

  for (const [index, shop] of configuredShops.entries()) {
    if (!shop?.id) {
      addIssue(errors, 'missing_shop_id', `Shop at index ${index} is missing id.`, {
        index
      });
    }
  }

  if (selectionModes.length === 0) {
    addIssue(errors, 'missing_shop_selection', 'One shop selection option is required: --shop-id, --shops, or --all-shops.');
  }

  if (selectionModes.length > 1) {
    addIssue(errors, 'conflicting_shop_selection', `Choose only one shop selection mode. Received: ${selectionModes.join(', ')}.`, {
      modes: selectionModes
    });
    selectionMode = 'conflict';
  }

  if (args.allShops && selectionModes.length === 1) {
    selectionMode = 'all';
    selectedShops = configuredShops;
    requestedShopIds = configuredIds;
  } else if (args.shops?.length && selectionModes.length === 1) {
    selectionMode = 'list';
    requestedShopIds = args.shops;
    selectedShops = args.shops.map((shopId) => resolveShopForCheck(shopId, configuredShops, errors));
  } else if (args.shopId && selectionModes.length === 1) {
    selectionMode = 'single';
    requestedShopIds = [args.shopId];
    selectedShops = [resolveShopForCheck(args.shopId, configuredShops, errors)];
  }

  const missingSelectedIds = selectedShops.filter((shop) => !shop).length;
  selectedShops = selectedShops.filter(Boolean);

  if (args.allShops && selectedShops.length === 0) {
    const discoveryEnabled = shopsConfig?.discovery?.enabled !== false;
    const message = discoveryEnabled
      ? '--all-shops selected no configured shops. Runtime may still discover shops only after browser selectors are calibrated.'
      : '--all-shops selected no enabled shops and discovery is disabled.';
    addIssue(warnings, 'all_shops_empty_config', message);
  }

  const enabledSelectedShops = selectedShops.filter((shop) => shop.enabled !== false);
  const disabledSelectedShops = selectedShops.filter((shop) => shop.enabled === false);
  const configuredEnabledShops = configuredShops.filter((shop) => shop?.enabled !== false);

  if (selectedShops.length > 0 && enabledSelectedShops.length === 0) {
    addIssue(warnings, 'no_enabled_selected_shops', 'Selected shops are all disabled; collection would only write skipped items.');
  }

  return {
    selection_mode: selectionMode,
    requested_shop_ids: requestedShopIds,
    configured_count: configuredShops.length,
    configured_enabled_count: configuredEnabledShops.length,
    selected_count: selectedShops.length + missingSelectedIds,
    selected_enabled_count: enabledSelectedShops.length,
    selected_disabled_count: disabledSelectedShops.length,
    selected: selectedShops.map((shop) => ({
      id: shop.id ?? null,
      name: shop.name ?? null,
      switchLabel: shop.switchLabel ?? null,
      useCurrentShop: shop.useCurrentShop === true,
      enabled: shop.enabled !== false
    }))
  };
}

function validateExportTaskConfig(args, exportTasks, errors, warnings, shopCheck = null) {
  const configuredTypes = exportTasks?.types && typeof exportTasks.types === 'object' && !Array.isArray(exportTasks.types)
    ? exportTasks.types
    : {};
  const configuredTypeNames = Object.keys(configuredTypes);
  const supportedTypes = [...new Set([...DEFAULT_EXPORT_TYPES, ...configuredTypeNames])];
  const fallbackTypes = supportedTypes.filter((type) => SAFE_DEFAULT_EXPORT_TYPES.includes(type));
  const defaultTypes = normalizeTypesForCheck(exportTasks?.defaults?.types);
  const requestedTypes = args.types ?? defaultTypes ?? fallbackTypes;
  const requestedSource = args.types ? 'args' : defaultTypes ? 'config.defaults.types' : 'safe-defaults';
  const normalizedRequestedTypes = normalizeTypesForCheck(requestedTypes) ?? [];
  const selectedTypes = [...new Set(normalizedRequestedTypes)];
  const unknownTypes = selectedTypes.filter((type) => !supportedTypes.includes(type));
  const duplicateRequestedTypes = duplicates(normalizedRequestedTypes);
  const missingConfiguredTasks = selectedTypes.filter((type) => !configuredTypes[type]);
  const defaultUnknownTypes = (defaultTypes ?? []).filter((type) => !supportedTypes.includes(type));
  const defaultUnsafeTypes = (defaultTypes ?? []).filter((type) => !SAFE_DEFAULT_EXPORT_TYPES.includes(type));
  const defaultMissingConfiguredTasks = (defaultTypes ?? []).filter((type) => !configuredTypes[type]);

  if (!exportTasks || typeof exportTasks !== 'object' || Array.isArray(exportTasks)) {
    addIssue(errors, 'invalid_export_tasks_config', 'config/export-tasks.json must contain a JSON object.');
  }

  if (!exportTasks?.types || typeof exportTasks.types !== 'object' || Array.isArray(exportTasks.types)) {
    addIssue(errors, 'missing_export_task_types', 'config/export-tasks.json must contain a types object.');
  }

  if (normalizedRequestedTypes.length === 0) {
    addIssue(errors, 'missing_export_types', 'At least one export type is required.');
  }

  for (const duplicateType of duplicateRequestedTypes) {
    addIssue(warnings, 'duplicate_requested_type', `Duplicate requested export type ignored: ${duplicateType}`, {
      type: duplicateType
    });
  }

  if (unknownTypes.length > 0) {
    addIssue(errors, 'unknown_export_type', `Unsupported export type(s): ${unknownTypes.join(', ')}.`, {
      types: unknownTypes,
      supported_types: supportedTypes
    });
  }

  if (missingConfiguredTasks.length > 0) {
    addIssue(errors, 'missing_export_task_config', `Requested export type(s) are missing config/export-tasks.json task definitions: ${missingConfiguredTasks.join(', ')}.`, {
      types: missingConfiguredTasks
    });
  }

  if (!defaultTypes || defaultTypes.length === 0) {
    addIssue(warnings, 'default_types_missing', `defaults.types is missing or empty; check-config will fall back to ${fallbackTypes.join(', ')}.`);
  }

  if (defaultUnknownTypes.length > 0) {
    addIssue(errors, 'default_types_unknown', `defaults.types contains unsupported export type(s): ${defaultUnknownTypes.join(', ')}.`, {
      types: defaultUnknownTypes
    });
  }

  if (defaultMissingConfiguredTasks.length > 0) {
    addIssue(warnings, 'default_types_missing_task_config', `defaults.types includes type(s) without task definitions: ${defaultMissingConfiguredTasks.join(', ')}.`, {
      types: defaultMissingConfiguredTasks
    });
  }

  if (defaultUnsafeTypes.length > 0) {
    addIssue(warnings, 'default_types_not_safe_subset', `defaults.types includes non-safe default type(s): ${defaultUnsafeTypes.join(', ')}. Safe defaults are ${SAFE_DEFAULT_EXPORT_TYPES.join(', ')}.`, {
      types: defaultUnsafeTypes,
      safe_defaults: SAFE_DEFAULT_EXPORT_TYPES
    });
  }

  const taskChecks = selectedTypes.map((type) => {
    const definition = configuredTypes[type] ?? null;
    const issues = [];

    if (!definition) {
      issues.push({
        severity: 'error',
        code: 'missing_task_config',
        path: `types.${type}`
      });
      return {
        type,
        configured: false,
        path_configured: false,
        selectors_configured: false,
        issues
      };
    }

    const usesUrl = Object.hasOwn(definition, 'url');
    const target = usesUrl ? definition.url : definition.path;
    const navigation = exportNavigationSelectorForCheck(definition);
    const frameUrlIncludes = exportFrameUrlIncludesForCheck(definition);
    const dateRangeMode = exportDateRangeModeForCheck(definition);
    const dateRangeSelectors = exportDateRangeSelectorsForCheck(definition);
    const hasTarget = !isBlankOrTodo(target);
    const hasNavigation = Boolean(navigation && !isBlankOrTodo(navigation.selector));
    const hasFrameUrlIncludes = Boolean(frameUrlIncludes && frameUrlIncludes.values.length > 0 && frameUrlIncludes.values.every((value) => !isBlankOrTodo(value)));

    if (!hasTarget && !hasNavigation) {
      issues.push({
        severity: 'error',
        code: 'missing_or_todo_entry',
        path: `types.${type}.${usesUrl ? 'url' : 'path'} or navigationSelector`
      });
    }

    if (navigation && isBlankOrTodo(navigation.selector)) {
      issues.push({
        severity: 'error',
        code: 'missing_or_todo_navigation_selector',
        path: `types.${type}.${navigation.path}`
      });
    }

    for (const selectorName of REQUIRED_EXPORT_SELECTORS) {
      if (isBlankOrTodo(definition.selectors?.[selectorName])) {
        issues.push({
          severity: 'error',
          code: 'missing_or_todo_selector',
          path: `types.${type}.selectors.${selectorName}`
        });
      }
    }

    return {
      type,
      configured: true,
      path_configured: hasTarget,
      navigation_configured: hasNavigation,
      frame_configured: hasFrameUrlIncludes,
      date_range_mode: dateRangeMode,
      date_range_selectors_configured: dateRangeMode !== 'custom-picker' || DATE_RANGE_COMMON_SELECTORS.every((selectorName) => !isBlankOrTodo(dateRangeSelectors[selectorName]) || !isBlankOrTodo(exportTasks?.selectors?.[selectorName])),
      selectors_configured: REQUIRED_EXPORT_SELECTORS.every((selectorName) => !isBlankOrTodo(definition.selectors?.[selectorName])),
      issues
    };
  });

  addSelectorIssues(exportTasks, taskChecks, errors, warnings, shopCheck);

  return {
    requested: selectedTypes,
    requested_source: requestedSource,
    defaults: defaultTypes ?? [],
    supported: supportedTypes,
    configured: configuredTypeNames,
    missing_config_for_requested: missingConfiguredTasks,
    tasks: taskChecks
  };
}

function exportNavigationSelectorForCheck(definition) {
  for (const name of EXPORT_NAVIGATION_SELECTOR_NAMES) {
    const selector = readDottedValue(definition, name);
    if (selector !== undefined) {
      return { path: name, selector };
    }
  }
  return null;
}

function exportFrameUrlIncludesForCheck(definition) {
  for (const name of EXPORT_FRAME_URL_INCLUDE_NAMES) {
    const configured = readDottedValue(definition, name);
    if (configured !== undefined) {
      const values = Array.isArray(configured) ? configured : [configured];
      return { path: name, values: values.map((value) => String(value).trim()).filter(Boolean) };
    }
  }
  return null;
}

function exportDateRangeModeForCheck(definition) {
  return definition.dateRange?.mode ?? definition.selectors?.dateRange?.mode ?? 'custom-picker';
}

function exportDateRangeSelectorsForCheck(definition) {
  const result = {};
  for (const selectorName of DATE_RANGE_COMMON_SELECTORS) {
    for (const pathExpression of EXPORT_DATE_RANGE_SELECTOR_NAMES.filter((name) => name.endsWith(`.${selectorName}`) || name === `selectors.${selectorName}`)) {
      const value = readDottedValue(definition, pathExpression);
      if (value !== undefined) {
        result[selectorName] = value;
        break;
      }
    }
  }
  return result;
}

function readDottedValue(value, pathExpression) {
  return pathExpression.split('.').reduce((current, key) => current?.[key], value);
}

function addSelectorIssues(exportTasks, taskChecks, errors, warnings, shopCheck = null) {
  const commonSelectors = exportTasks?.selectors ?? {};
  const needsShopSwitch = shopCheck?.selected?.some((shop) => !shop.useCurrentShop) ?? true;
  const needsGlobalDateRangePicker = taskChecks.some((taskCheck) => (
    taskCheck.date_range_mode === 'custom-picker' && !taskCheck.date_range_selectors_configured
  ));

  for (const selectorName of REQUIRED_COMMON_SELECTORS) {
    if (isBlankOrTodo(commonSelectors[selectorName])) {
      addIssue(errors, 'missing_common_selector', `Common selector "${selectorName}" is missing, empty, or still TODO.`, {
        path: `selectors.${selectorName}`
      });
    }
  }

  if (needsGlobalDateRangePicker) {
    for (const selectorName of DATE_RANGE_COMMON_SELECTORS) {
      if (isBlankOrTodo(commonSelectors[selectorName])) {
        addIssue(errors, 'missing_common_selector', `Common selector "${selectorName}" is missing, empty, or still TODO.`, {
          path: `selectors.${selectorName}`
        });
      }
    }
  }

  for (const selectorName of SHOP_SWITCH_COMMON_SELECTORS) {
    if (isBlankOrTodo(commonSelectors[selectorName])) {
      const target = needsShopSwitch ? errors : warnings;
      addIssue(
        target,
        needsShopSwitch ? 'missing_common_selector' : 'shop_switch_selector_not_calibrated',
        needsShopSwitch
          ? `Common selector "${selectorName}" is missing, empty, or still TODO.`
          : `Shop switch selector "${selectorName}" is not calibrated; current-shop runs can proceed, but multi-shop switching is not ready.`,
        { path: `selectors.${selectorName}` }
      );
    }
  }

  for (const selectorName of OPTIONAL_COMMON_SELECTORS) {
    if (selectorName in commonSelectors && isBlankOrTodo(commonSelectors[selectorName])) {
      addIssue(warnings, 'todo_optional_common_selector', `Optional common selector "${selectorName}" is empty or still TODO.`, {
        path: `selectors.${selectorName}`
      });
    }
  }

  for (const taskCheck of taskChecks) {
    for (const issue of taskCheck.issues) {
      const target = issue.severity === 'warning' ? warnings : errors;
      addIssue(target, issue.code, `Export type "${taskCheck.type}" has ${issue.code.replaceAll('_', ' ')} at ${issue.path}.`, {
        type: taskCheck.type,
        path: issue.path
      });
    }
  }
}

function resolveShopForCheck(shopId, configuredShops, errors) {
  const shop = configuredShops.find((item) => item?.id === shopId);

  if (!shop && configuredShops.length > 0) {
    addIssue(errors, 'unknown_shop_id', `Shop "${shopId}" was not found in config/shops.json.`, {
      shop_id: shopId
    });
    return null;
  }

  return shop ?? { id: shopId };
}

async function readJsonFileState(filePath) {
  try {
    const content = await fs.readFile(filePath, 'utf8');
    return {
      path: filePath,
      exists: true,
      ok: true,
      value: JSON.parse(content),
      error: null
    };
  } catch (error) {
    if (error.code === 'ENOENT') {
      return {
        path: filePath,
        exists: false,
        ok: false,
        value: null,
        error: 'File not found'
      };
    }

    return {
      path: filePath,
      exists: true,
      ok: false,
      value: null,
      error: error.message
    };
  }
}

function buildConfigPathSummary(fileStates, projectRoot) {
  return {
    project_root: projectRoot,
    shops: summarizeConfigFile(fileStates.shops, path.join(projectRoot, 'config', 'shops.json')),
    export_tasks: summarizeConfigFile(fileStates.export_tasks, path.join(projectRoot, 'config', 'export-tasks.json'))
  };
}

function summarizeConfigFile(fileState, fallbackPath) {
  return {
    path: fileState?.path ?? fallbackPath,
    exists: Boolean(fileState?.exists),
    ok: Boolean(fileState?.ok),
    error: fileState?.error ?? null
  };
}

function normalizeTypesForCheck(types) {
  if (types == null) {
    return null;
  }

  if (Array.isArray(types)) {
    return types.map((type) => String(type).trim()).filter(Boolean);
  }

  return String(types).split(',').map((type) => type.trim()).filter(Boolean);
}

function addIssue(target, code, message, details = {}) {
  target.push({
    code,
    message,
    ...details
  });
}

function duplicates(values) {
  const seen = new Set();
  const duplicateValues = new Set();

  for (const value of values) {
    if (seen.has(value)) {
      duplicateValues.add(value);
    }
    seen.add(value);
  }

  return [...duplicateValues];
}

function isBlankOrTodo(value) {
  return value == null || String(value).trim() === '' || String(value).includes('TODO');
}

function isIsoDate(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value ?? '');
}

function buildShopSelection(args, shopsConfig) {
  const modes = [
    args.shopId ? '--shop-id' : null,
    args.shops?.length ? '--shops' : null,
    args.allShops ? '--all-shops' : null
  ].filter(Boolean);

  if (modes.length > 1) {
    throw new Error(`Choose only one shop selection mode. Received: ${modes.join(', ')}.`);
  }

  if (args.allShops) {
    return {
      mode: 'all',
      shops: configuredEnabledShops(shopsConfig),
      discovery: shopsConfig?.discovery ?? {}
    };
  }

  if (args.shops?.length) {
    return {
      mode: 'list',
      shops: args.shops.map((shopId) => resolveShop(shopId, shopsConfig)),
      requested_shop_ids: args.shops,
      discovery: shopsConfig?.discovery ?? {}
    };
  }

  return {
    mode: 'single',
    shops: [resolveShop(args.shopId, shopsConfig)],
    requested_shop_ids: [args.shopId],
    discovery: shopsConfig?.discovery ?? {}
  };
}

function configuredEnabledShops(shopsConfig) {
  const shops = Array.isArray(shopsConfig?.shops) ? shopsConfig.shops : [];
  return shops;
}

function resolveShop(shopId, shopsConfig) {
  const shops = Array.isArray(shopsConfig?.shops) ? shopsConfig.shops : [];
  const shop = shops.find((item) => item.id === shopId);

  if (!shop && shops.length > 0) {
    throw new Error(`Shop "${shopId}" was not found in config/shops.json.`);
  }

  return shop ?? { id: shopId };
}

function assertRequired(value, flag) {
  if (!value) {
    throw new Error(`${flag} is required.\n\n${usageText()}`);
  }
}

function assertShopSelection(args) {
  if (!args.shopId && !args.shops?.length && !args.allShops) {
    throw new Error(`One shop selection option is required: --shop-id, --shops, or --all-shops.\n\n${usageText()}`);
  }
}

function assertDate(value, flag) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new Error(`${flag} must use YYYY-MM-DD format. Received: ${value}`);
  }
}

function usageText() {
  return [
    'Usage:',
    '  npm run collect -- --shop-id <id> --from YYYY-MM-DD --to YYYY-MM-DD --types orders,products,compass [--headless false]',
    '  npm run collect -- --shops shopA,shopB --from YYYY-MM-DD --to YYYY-MM-DD --types orders,products',
    '  npm run collect -- --all-shops --from YYYY-MM-DD --to YYYY-MM-DD --types orders,products,transactions',
    '  npm run collect -- --check-config --shop-id <id> --from YYYY-MM-DD --to YYYY-MM-DD --types orders,products',
    '',
    'Notes:',
    '  - --check-config only reads config and prints JSON; it does not start Playwright or create downloads.',
    '  - Choose exactly one shop selector: --shop-id, --shops, or --all-shops.',
    '  - Uses data/browser-profile as the Playwright persistent profile directory.',
    '  - Downloads are saved under data/raw/<task_id>/<shop_id>/<export_type>/ when the collector is run.',
    '  - Real selectors must be calibrated before collecting from store.weixin.qq.com.'
  ].join('\n');
}
