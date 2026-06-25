const crypto = require('crypto');
const { execFile } = require('child_process');
const fsSync = require('fs');
const fs = require('fs/promises');
const path = require('path');
const { promisify } = require('util');

const ROOT_DIR = path.join(__dirname, '..');
const DEFAULT_STAGE2_RUNS_DIR = path.join(ROOT_DIR, 'artifacts', 'stage2', 'runs');
const DEFAULT_CDP_URL = 'http://localhost:9222/';
const DEFAULT_MODEL_PROFILES_PATH = path.join(ROOT_DIR, 'config', 'stage2-model-profiles.json');
const SAFE_ID_PATTERN = /^[A-Za-z0-9_-]{1,120}$/;
const LOCAL_CDP_HOSTS = new Set(['localhost', '127.0.0.1', '::1']);
const DEFAULT_PYTHON_COMMAND = process.env.STAGE2_PYTHON || process.env.PYTHON || 'python';
const DEFAULT_PYTHON_TIMEOUT_MS = 10 * 60 * 1000;
const SAFETY_POLICY_LOW_RISK_ONLY = 'low_risk_only';
const SAFETY_POLICY_TEST_ENV_FULL_ACCESS = 'test_env_full_access';
const DEFAULT_FULL_ACCESS_ACTIONS = ['create', 'edit', 'submit', 'delete', 'approve', 'save', 'remove'];
const execFileAsync = promisify(execFile);

const ARTIFACTS = {
  run_manifest: 'run_manifest.json',
  input_config: 'input_config.json',
  progress_events: 'progress_events.jsonl',
  current_status: 'current_status.json',
  preflight_result: 'preflight_result.json',
  system_map: 'system_map.json',
  navigation_tree: 'navigation_tree.json',
  menu_tree: 'menu_tree.json',
  menu_entries: 'menu_entries.json',
  menu_traversal_log: 'menu_traversal_log.jsonl',
  page_entries: 'page_entries.json',
  feature_points: 'feature_points.json',
  discovery_review: 'discovery_review.json',
  generated_test_cases: 'generated_test_cases.json',
  execution_results: 'execution_results.json',
  screenshots_index: 'screenshots_index.json',
  failure_summary: 'failure_summary.json',
  round_analysis: 'round_analysis.json',
  failure_clusters: 'failure_clusters.json',
  improvement_candidates: 'improvement_candidates.json',
  next_round_plan: 'next_round_plan.json',
  human_tasks: 'human_tasks.json',
  promotion_candidates: 'promotion_candidates.json',
  python_execution: 'python_execution.json',
  run_report_json: path.join('reports', 'run_report.json'),
  run_report_md: path.join('reports', 'run_report.md')
};

class Stage2V3InputError extends Error {
  constructor(message, statusCode = 400) {
    super(message);
    this.name = 'Stage2V3InputError';
    this.statusCode = statusCode;
  }
}

function nowIso() {
  return new Date().toISOString();
}

function createRunId() {
  const stamp = new Date()
    .toISOString()
    .replace(/[-:]/g, '')
    .replace('T', '_')
    .slice(0, 15);
  return `stage2_v3_${stamp}_${crypto.randomBytes(4).toString('hex')}`;
}

function ensureSafeId(value, label = 'id') {
  const text = String(value || '').trim();
  if (!SAFE_ID_PATTERN.test(text)) {
    throw new Stage2V3InputError(`${label} 只能包含字母、数字、下划线或连字符。`);
  }
  return text;
}

function getRunsDir(options = {}) {
  return options.runsDir || DEFAULT_STAGE2_RUNS_DIR;
}

function getRunDir(runId, options = {}) {
  const safeRunId = ensureSafeId(runId, 'runId');
  return path.join(getRunsDir(options), safeRunId);
}

function artifactPath(runDir, artifactKey) {
  const relativePath = ARTIFACTS[artifactKey];
  if (!relativePath) {
    throw new Stage2V3InputError('不支持的 v3 artifact key。', 404);
  }
  return path.join(runDir, relativePath);
}

function relativeArtifact(filePath) {
  return path.relative(ROOT_DIR, filePath).replace(/\\/g, '/');
}

function buildArtifactHref(runId, artifactKey) {
  return `/api/stage2/v3/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactKey)}`;
}

function artifactRefs(runId) {
  return Object.fromEntries(Object.keys(ARTIFACTS).map((key) => [
    key,
    { key, href: buildArtifactHref(runId, key) }
  ]));
}

function normalizeText(value, fallback = '') {
  const text = String(value || '').trim();
  return text || fallback;
}

function normalizeOptionalText(value) {
  const text = String(value || '').trim();
  return text || null;
}

function normalizeModelProfileId(value) {
  return String(value || '').trim().replace(/[^A-Za-z0-9_.-]/g, '_').slice(0, 120);
}

function normalizeArray(value) {
  if (Array.isArray(value)) {
    return value.map((item) => String(item || '').trim()).filter(Boolean);
  }
  if (typeof value === 'string') {
    return value.split(/[,;\s]+/).map((item) => item.trim()).filter(Boolean);
  }
  return [];
}

function modelProfilesConfigPath(options = {}) {
  return options.configPath
    || options.modelProfileConfigPath
    || process.env.STAGE2_MODEL_PROFILES_PATH
    || DEFAULT_MODEL_PROFILES_PATH;
}

function redactModelProfile(profile) {
  return {
    id: profile.id,
    label: profile.label,
    provider: profile.provider,
    baseUrl: profile.baseUrl,
    base_url: profile.baseUrl,
    model: profile.model,
    browserUseMode: profile.browserUseMode,
    browser_use_mode: profile.browserUseMode,
    apiKeyConfigured: Boolean(profile.apiKey),
    api_key_configured: Boolean(profile.apiKey)
  };
}

function loadStage2ModelProfiles(options = {}) {
  const configPath = modelProfilesConfigPath(options);
  if (!fsSync.existsSync(configPath)) {
    return {
      schema_version: 'stage2_model_profiles.v1',
      config_path: configPath,
      profiles: []
    };
  }
  const payload = JSON.parse(fsSync.readFileSync(configPath, 'utf8'));
  const profiles = (payload.profiles || []).map((profile) => {
    const id = normalizeModelProfileId(profile.id || profile.name || profile.model);
    const apiKeyEnv = normalizeOptionalText(profile.apiKeyEnv || profile.api_key_env);
    return {
      id,
      label: normalizeText(profile.label || profile.name, id),
      provider: normalizeText(profile.provider || profile.type, 'openai_compatible'),
      baseUrl: normalizeText(profile.baseUrl || profile.base_url, '').replace(/\/$/, ''),
      apiKey: normalizeText(profile.apiKey || profile.api_key || (apiKeyEnv ? process.env[apiKeyEnv] : ''), ''),
      apiKeyEnv,
      model: normalizeText(profile.model, id),
      browserUseMode: normalizeOptionalText(profile.browserUseMode || profile.browser_use_mode)
    };
  }).filter((profile) => profile.id);
  return {
    schema_version: 'stage2_model_profiles.v1',
    config_path: configPath,
    profiles
  };
}

function selectModelProfiles(selectedIds, availableProfiles) {
  const byId = new Map((availableProfiles || []).map((profile) => [profile.id, profile]));
  return selectedIds.map((id) => byId.get(id)).filter(Boolean).map(redactModelProfile);
}

function normalizeHttpUrl(value, fieldName, fallback = null) {
  const raw = normalizeText(value, fallback || '');
  if (!raw) {
    return null;
  }
  try {
    const url = new URL(raw);
    if (!['http:', 'https:'].includes(url.protocol)) {
      throw new Error('invalid protocol');
    }
    return url.toString();
  } catch {
    throw new Stage2V3InputError(`${fieldName} 必须是 http(s) URL。`);
  }
}

function normalizeCdpUrl(value) {
  const urlText = normalizeHttpUrl(value, 'cdpUrl', DEFAULT_CDP_URL);
  const url = new URL(urlText);
  if (process.env.STAGE2_ALLOW_REMOTE_CDP !== '1' && !LOCAL_CDP_HOSTS.has(url.hostname)) {
    throw new Stage2V3InputError('cdpUrl 只允许 localhost、127.0.0.1 或 ::1；如需远程调试端口，请先显式配置白名单。');
  }
  return url.toString();
}

function normalizeInputConfig(body = {}, options = {}) {
  const systemName = normalizeText(body.systemName || body.system_name || body.targetName, '未命名系统');
  const entryUrl = normalizeHttpUrl(body.entryUrl || body.entry_url || body.homeUrl || body.pageUrl, 'entryUrl');
  const safetyPolicy = normalizeSafetyPolicy(body.safetyPolicy || body.safety_policy);
  const configuredProfiles = options.modelProfiles || loadStage2ModelProfiles(options).profiles;
  const selectedModelProfileIds = normalizeArray(
    body.modelProfileIds
      || body.model_profile_ids
      || body.selectedModelProfileIds
      || body.selected_model_profile_ids
      || body.modelProfiles
      || body.model_profiles
      || body.model
  ).map(normalizeModelProfileId).filter(Boolean);
  const selectedModelProfiles = selectModelProfiles(selectedModelProfileIds, configuredProfiles);
  const allowedSideEffectActions = normalizeAllowedSideEffectActions(
    body.allowedSideEffectActions
      || body.allowed_side_effect_actions
      || body.allowedSideEffects
      || body.allowed_side_effects
      || body.sideEffectAllowlist
      || body.side_effect_allowlist
  );
  const fullAccessConfirmed = body.fullAccessConfirmed === true
    || body.full_access_confirmed === true
    || body.safetyPolicyConfirmed === true
    || body.safety_policy_confirmed === true;
  if (safetyPolicy === SAFETY_POLICY_TEST_ENV_FULL_ACCESS && !fullAccessConfirmed) {
    throw new Stage2V3InputError('测试环境全权限模式必须先在运行中心明确确认，不能静默启用副作用动作。');
  }

  return {
    schema_version: 'stage2_input_config.v3',
    system_name: systemName,
    entry_url: entryUrl,
    cdp_url: normalizeCdpUrl(body.cdpUrl || body.cdp_url),
    test_account_note: normalizeOptionalText(body.testAccountNote || body.test_account_note || body.accountNotes),
    login_mode: normalizeText(body.loginMode || body.login_mode, 'human_takeover_or_existing_session'),
    scope: normalizeOptionalText(body.scope || body.scopeText || body.explorationScope),
    safety_policy: safetyPolicy,
    full_access_confirmed: safetyPolicy === SAFETY_POLICY_TEST_ENV_FULL_ACCESS && fullAccessConfirmed,
    allowed_side_effect_actions: safetyPolicy === SAFETY_POLICY_TEST_ENV_FULL_ACCESS
      ? (allowedSideEffectActions.length ? allowedSideEffectActions : DEFAULT_FULL_ACCESS_ACTIONS)
      : [],
    selected_model_profile_ids: selectedModelProfileIds,
    selected_model_profiles: selectedModelProfiles,
    max_pages: normalizeInteger(body.maxPages || body.max_pages, 30, 1, 200),
    max_features_per_page: normalizeInteger(body.maxFeaturesPerPage || body.max_features_per_page, 30, 1, 200),
    auto_continue: body.autoContinue === true || body.auto_continue === true,
    created_from: normalizeText(body.createdFrom || body.created_from, 'run_center_v3')
  };
}

function normalizeSafetyPolicy(value) {
  const policy = normalizeText(value, SAFETY_POLICY_LOW_RISK_ONLY).toLowerCase().replace(/-/g, '_');
  if (['test_env_full_access', 'full_access', 'testing_full_access', 'test_full_access'].includes(policy)) {
    return SAFETY_POLICY_TEST_ENV_FULL_ACCESS;
  }
  return SAFETY_POLICY_LOW_RISK_ONLY;
}

function normalizeAllowedSideEffectActions(value) {
  const rawItems = Array.isArray(value)
    ? value
    : typeof value === 'string'
      ? value.split(/[,\s;]+/)
      : [];
  const aliases = new Map([
    ['approval', 'approve'],
    ['audit', 'approve'],
    ['confirm', 'submit'],
    ['new', 'create'],
    ['add', 'create'],
    ['update', 'edit'],
    ['remove', 'delete']
  ]);
  const allowed = new Set(['create', 'edit', 'submit', 'delete', 'approve', 'save', 'remove', '*']);
  const normalized = [];
  const seen = new Set();
  for (const item of rawItems) {
    const token = normalizeText(item, '').toLowerCase().replace(/-/g, '_');
    const canonical = aliases.get(token) || token;
    if (!canonical || !allowed.has(canonical) || seen.has(canonical)) {
      continue;
    }
    seen.add(canonical);
    normalized.push(canonical);
  }
  return normalized;
}

function normalizeInteger(value, fallback, min, max) {
  if (value === undefined || value === null || value === '') {
    return fallback;
  }
  const number = Number(value);
  if (!Number.isInteger(number) || number < min || number > max) {
    throw new Stage2V3InputError(`数值参数必须是 ${min} 到 ${max} 之间的整数。`);
  }
  return number;
}

function normalizeExecutionMode(value) {
  const mode = normalizeText(value, 'real_browser');
  if (['contract_placeholder', 'placeholder', 'safe_placeholder'].includes(mode)) {
    return 'contract_only';
  }
  if (!['real_browser', 'contract_only'].includes(mode)) {
    throw new Stage2V3InputError('executionMode 仅支持 real_browser 或 contract_only。');
  }
  return mode;
}

async function pathExists(targetPath) {
  try {
    await fs.access(targetPath);
    return true;
  } catch {
    return false;
  }
}

async function readJsonIfExists(filePath) {
  try {
    const raw = await fs.readFile(filePath, 'utf8');
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

async function readTextIfExists(filePath) {
  try {
    return await fs.readFile(filePath, 'utf8');
  } catch {
    return null;
  }
}

async function readProgressEventsIfExists(filePath, limit = 20) {
  try {
    const raw = await fs.readFile(filePath, 'utf8');
    return raw
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => {
        try {
          return JSON.parse(line);
        } catch {
          return { type: 'unparseable_progress_event', raw: line };
        }
      })
      .slice(-limit);
  } catch {
    return [];
  }
}

async function readJsonRequired(filePath, message) {
  const value = await readJsonIfExists(filePath);
  if (!value) {
    throw new Stage2V3InputError(message, 404);
  }
  return value;
}

async function writeJson(filePath, payload) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
}

async function copyTextFileIfExists(sourcePath, targetPath) {
  if (!(await pathExists(sourcePath))) {
    return false;
  }
  await fs.mkdir(path.dirname(targetPath), { recursive: true });
  await fs.copyFile(sourcePath, targetPath);
  return true;
}

async function checkBrowserPreflight(cdpUrl = DEFAULT_CDP_URL, options = {}) {
  const normalizedCdpUrl = normalizeCdpUrl(cdpUrl);
  const timeoutMs = normalizeInteger(options.timeoutMs || options.timeout_ms, 3000, 500, 30000);
  const checkedAt = nowIso();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const buildUrl = (relativePath) => {
    const base = new URL(normalizedCdpUrl);
    return new URL(relativePath, `${base.origin}/`).toString();
  };
  try {
    const versionResponse = await fetch(buildUrl('/json/version'), {
      signal: controller.signal,
      cache: 'no-store'
    });
    if (!versionResponse.ok) {
      throw new Error(`CDP /json/version 返回 HTTP ${versionResponse.status}`);
    }
    const version = await versionResponse.json();
    let targetCount = null;
    try {
      const listResponse = await fetch(buildUrl('/json/list'), {
        signal: controller.signal,
        cache: 'no-store'
      });
      if (listResponse.ok) {
        const targets = await listResponse.json();
        targetCount = Array.isArray(targets) ? targets.length : null;
      }
    } catch {
      targetCount = null;
    }
    return {
      ok: true,
      status: 'connected',
      cdpUrl: normalizedCdpUrl,
      browser: version.Browser || '',
      protocolVersion: version['Protocol-Version'] || '',
      webSocketDebuggerUrl: version.webSocketDebuggerUrl || '',
      targetCount,
      checkedAt,
      message: version.Browser
        ? `已连接 ${version.Browser}`
        : '已连接 Chrome DevTools Protocol。'
    };
  } catch (error) {
    return {
      ok: false,
      status: error.name === 'AbortError' ? 'timeout' : 'unreachable',
      cdpUrl: normalizedCdpUrl,
      checkedAt,
      message: error.name === 'AbortError'
        ? `CDP 预检超时：${normalizedCdpUrl}`
        : `CDP 不可用：${error.message}`
    };
  } finally {
    clearTimeout(timer);
  }
}

async function postModelProbe(profile, body, options = {}) {
  const timeoutMs = normalizeInteger(options.timeoutMs || options.timeout_ms, 3000, 500, 30000);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const endpoint = new URL('chat/completions', `${profile.baseUrl.replace(/\/$/, '')}/`).toString();
    const headers = { 'Content-Type': 'application/json' };
    if (profile.apiKey) {
      headers.Authorization = `Bearer ${profile.apiKey}`;
    }
    const response = await fetch(endpoint, {
      method: 'POST',
      signal: controller.signal,
      headers,
      body: JSON.stringify({
        model: profile.model,
        messages: [{ role: 'user', content: 'Return {"ok": true}.' }],
        max_tokens: 32,
        ...body
      })
    });
    if (!response.ok) {
      return { ok: false, reason: `HTTP ${response.status}` };
    }
    const payload = await response.json().catch(() => ({}));
    return { ok: true, payload };
  } catch (error) {
    return {
      ok: false,
      reason: error.name === 'AbortError' ? 'timeout' : error.message
    };
  } finally {
    clearTimeout(timer);
  }
}

async function preflightModelProfile(profile, options = {}) {
  const checkedAt = nowIso();
  if (profile.provider !== 'openai_compatible') {
    return {
      id: profile.id,
      label: profile.label,
      provider: profile.provider,
      model: profile.model,
      status: 'unsupported',
      checked_at: checkedAt,
      capability_tags: {},
      checks: { provider: { ok: false, reason: '当前仅支持 OpenAI-compatible 模型预检。' } },
      profile: redactModelProfile(profile)
    };
  }
  const plain = await postModelProbe(profile, {}, options);
  const jsonObject = plain.ok ? await postModelProbe(profile, {
    response_format: { type: 'json_object' }
  }, options) : { ok: false, reason: plain.reason };
  const jsonSchema = plain.ok ? await postModelProbe(profile, {
    response_format: {
      type: 'json_schema',
      json_schema: {
        name: 'stage2_model_preflight',
        schema: {
          type: 'object',
          properties: { ok: { type: 'boolean' } },
          required: ['ok'],
          additionalProperties: false
        }
      }
    }
  }, options) : { ok: false, reason: plain.reason };
  const tools = plain.ok ? await postModelProbe(profile, {
    tools: [{
      type: 'function',
      function: {
        name: 'ping',
        description: 'Capability probe ping.',
        parameters: {
          type: 'object',
          properties: {},
          additionalProperties: false
        }
      }
    }]
  }, options) : { ok: false, reason: plain.reason };
  const capabilityTags = {
    chat_completion: plain.ok,
    json_object_response_format: jsonObject.ok,
    json_schema_response_format: jsonSchema.ok,
    tool_calling: tools.ok,
    browser_use_chatopenai_structured: plain.ok
      && jsonSchema.ok
      && profile.browserUseMode === 'chatopenai_structured'
  };
  return {
    id: profile.id,
    label: profile.label,
    provider: profile.provider,
    model: profile.model,
    status: plain.ok ? 'available' : 'unavailable',
    checked_at: checkedAt,
    capability_tags: capabilityTags,
    checks: {
      chat_completion: { ok: plain.ok, reason: plain.reason || null },
      json_object_response_format: { ok: jsonObject.ok, reason: jsonObject.reason || null },
      json_schema_response_format: { ok: jsonSchema.ok, reason: jsonSchema.reason || null },
      tool_calling: { ok: tools.ok, reason: tools.reason || null },
      browser_use: {
        ok: capabilityTags.browser_use_chatopenai_structured,
        mode: profile.browserUseMode || null
      }
    },
    profile: redactModelProfile(profile)
  };
}

async function checkStage2ModelProfiles(options = {}) {
  const config = loadStage2ModelProfiles(options);
  const profiles = [];
  for (const profile of config.profiles) {
    profiles.push(await preflightModelProfile(profile, options));
  }
  return {
    schema_version: 'stage2_model_profile_preflight.v1',
    config_path: config.config_path,
    checked_at: nowIso(),
    profiles
  };
}

async function appendEvent(runDir, event) {
  await fs.mkdir(runDir, { recursive: true });
  await fs.appendFile(
    path.join(runDir, ARTIFACTS.progress_events),
    `${JSON.stringify({ at: nowIso(), ...event })}\n`,
    'utf8'
  );
}

function buildManifest({ runId, inputConfig, status, createdAt, updatedAt, rounds = [] }) {
  return {
    schema_version: 'stage2_run_manifest.v3',
    run_id: runId,
    system_name: inputConfig.system_name,
    entry_url: inputConfig.entry_url,
    cdp_url: inputConfig.cdp_url,
    safety_policy: inputConfig.safety_policy,
    full_access_confirmed: inputConfig.full_access_confirmed,
    allowed_side_effect_actions: inputConfig.allowed_side_effect_actions,
    selected_model_profile_ids: inputConfig.selected_model_profile_ids,
    selected_model_profiles: inputConfig.selected_model_profiles,
    status,
    created_at: createdAt,
    updated_at: updatedAt,
    started_at: null,
    finished_at: null,
    current_round_id: rounds.at(-1)?.round_id || null,
    rounds,
    artifact_paths: {}
  };
}

function withArtifactPaths(manifest, runDir) {
  return {
    ...manifest,
    artifact_paths: Object.fromEntries(Object.entries(ARTIFACTS).map(([key, relativePath]) => [
      key,
      relativeArtifact(path.join(runDir, relativePath))
    ]))
  };
}

async function readManifest(runId, options = {}) {
  const runDir = getRunDir(runId, options);
  const manifest = await readJsonRequired(
    path.join(runDir, ARTIFACTS.run_manifest),
    'v3 run 不存在。'
  );
  return { runDir, manifest };
}

async function saveManifest(runDir, manifest) {
  const nextManifest = withArtifactPaths(manifest, runDir);
  await writeJson(path.join(runDir, ARTIFACTS.run_manifest), nextManifest);
  return nextManifest;
}

function buildCurrentStatus(manifest, phase, message, extra = {}) {
  return {
    schema_version: 'stage2_current_status.v3',
    run_id: manifest.run_id,
    status: manifest.status,
    phase,
    current_round_id: manifest.current_round_id,
    message,
    updated_at: manifest.updated_at,
    ...extra
  };
}

function summarizeItems(items) {
  return Array.isArray(items) ? items.length : 0;
}

function artifactItems(payload, ...keys) {
  if (!payload) {
    return [];
  }
  if (Array.isArray(payload)) {
    return payload;
  }
  for (const key of keys) {
    if (Array.isArray(payload[key])) {
      return payload[key];
    }
  }
  return Array.isArray(payload.items) ? payload.items : [];
}

function numberOrZero(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function summarizeExecution(items = []) {
  const byStatus = {};
  for (const item of items) {
    const status = item.status || 'unknown';
    byStatus[status] = (byStatus[status] || 0) + 1;
  }
  const passed = (byStatus.passed || 0)
    + (byStatus.real_passed || 0)
    + (byStatus.side_effect_executed || 0);
  const failed = (byStatus.failed || 0)
    + (byStatus.real_failed || 0)
    + (byStatus.login_required || 0)
    + (byStatus.side_effect_failed || 0);
  const skipped = (byStatus.skipped || 0)
    + (byStatus.skipped_no_executor || 0)
    + (byStatus.skipped_not_observed || 0);
  return {
    total: items.length,
    passed,
    failed,
    skipped,
    needs_review: byStatus.needs_review || 0,
    by_status: byStatus
  };
}

function buildPublicRun(runDir, manifest, artifacts = {}) {
  const executionItems = artifacts.execution_results?.items || [];
  const menuEntryItems = artifactItems(artifacts.menu_entries, 'menu_entries');
  const executionMode = artifacts.current_status?.execution_mode || manifest.execution_mode || null;
  const currentStatus = artifacts.current_status || buildCurrentStatus(
    manifest,
    manifest.status || 'unknown',
    'run 状态摘要不可用。'
  );
  const recentEvents = Array.isArray(artifacts.progress_events) ? artifacts.progress_events : [];
  return {
    runId: manifest.run_id,
    systemName: manifest.system_name,
    entryUrl: manifest.entry_url,
    status: manifest.status,
    executionMode,
    safetyPolicy: manifest.safety_policy || artifacts.input_config?.safety_policy || SAFETY_POLICY_LOW_RISK_ONLY,
    allowedSideEffectActions: manifest.allowed_side_effect_actions
      || artifacts.input_config?.allowed_side_effect_actions
      || [],
    modelProfileIds: manifest.selected_model_profile_ids
      || artifacts.input_config?.selected_model_profile_ids
      || [],
    modelProfiles: manifest.selected_model_profiles
      || artifacts.input_config?.selected_model_profiles
      || [],
    fullAccessConfirmed: Boolean(manifest.full_access_confirmed || artifacts.input_config?.full_access_confirmed),
    currentRoundId: manifest.current_round_id,
    createdAt: manifest.created_at,
    updatedAt: manifest.updated_at,
    startedAt: manifest.started_at,
    finishedAt: manifest.finished_at,
    currentStatus,
    latestMessage: currentStatus.message || null,
    recentEvents,
    operability: makeRunOperability(manifest, artifacts, currentStatus),
    rounds: manifest.rounds || [],
    summary: {
      menuEntries: summarizeItems(menuEntryItems),
      menuLeaves: menuEntryItems.filter((item) => item && item.is_leaf).length,
      menuRoots: numberOrZero(artifacts.menu_tree?.root_count),
      browserTargets: numberOrZero(
        artifacts.preflight_result?.browser_target_count
        || artifacts.round_analysis?.coverage?.browser_target_count
      ),
      pageEntries: summarizeItems(artifacts.page_entries?.items),
      featurePoints: summarizeItems(artifacts.feature_points?.items),
      generatedTestCases: summarizeItems(artifacts.generated_test_cases?.items),
      execution: summarizeExecution(executionItems),
      pendingHumanTasks: (artifacts.human_tasks?.items || []).filter((item) => item.status === 'pending').length,
      nextDecision: artifacts.next_round_plan?.decision || null,
      executionMode,
      safetyPolicy: manifest.safety_policy || artifacts.input_config?.safety_policy || SAFETY_POLICY_LOW_RISK_ONLY
    },
    artifacts: artifactRefs(manifest.run_id)
  };
}

async function loadRunArtifacts(runDir) {
  const keys = [
    'input_config',
    'current_status',
    'preflight_result',
    'menu_tree',
    'menu_entries',
    'page_entries',
    'feature_points',
    'generated_test_cases',
    'execution_results',
    'round_analysis',
    'next_round_plan',
    'human_tasks',
    'run_report_json'
  ];
  const entries = await Promise.all(keys.map(async (key) => [
    key,
    await readJsonIfExists(artifactPath(runDir, key))
  ]));
  return {
    ...Object.fromEntries(entries),
    menu_traversal_log: await readTextIfExists(path.join(runDir, ARTIFACTS.menu_traversal_log)),
    progress_events: await readProgressEventsIfExists(path.join(runDir, ARTIFACTS.progress_events))
  };
}

function actionName(action) {
  const aliases = {
    'continue-next-round': 'continue_next_round',
    'generate-report': 'generate_report',
    'analyze-round': 'analyze_round',
    'save-human-task': 'save_human_task'
  };
  return aliases[action] || String(action || '').replace(/-/g, '_');
}

function operationStatusForRun(run) {
  if (!run) {
    return 'failed';
  }
  if (run.status === 'failed') {
    return 'failed';
  }
  if (['waiting_human'].includes(run.status)) {
    return 'blocked';
  }
  if (['running'].includes(run.status)) {
    return 'running';
  }
  if (['planned'].includes(run.status)) {
    return 'queued';
  }
  return 'succeeded';
}

function nextActionForRun(run) {
  if (!run) {
    return '检查运行中心错误并重试。';
  }
  const pendingTasks = run.summary?.pendingHumanTasks || 0;
  if (run.status === 'draft') {
    return '启动自动评测。';
  }
  if (run.status === 'waiting_human') {
    return pendingTasks > 0
      ? '请在运行中心完成人工确认或审核任务后继续。'
      : '请确认下一轮计划后继续。';
  }
  if (run.status === 'failed') {
    return '查看错误详情和执行证据，修复后重试。';
  }
  if (run.status === 'running') {
    return '等待执行器推进，或刷新查看最新进度。';
  }
  if (run.status === 'paused') {
    return '可以继续、停止或查看当前证据。';
  }
  if (run.status === 'planned') {
    return '等待执行器推进下一轮。';
  }
  if (run.status === 'completed') {
    return '生成报告，或创建新的更大范围 run。';
  }
  if (run.status === 'stopped') {
    return 'run 已停止，可查看报告或创建新 run。';
  }
  return '查看运行中心建议的下一步动作。';
}

function makeOperationFeedback(action, run, overrides = {}) {
  const status = overrides.status || operationStatusForRun(run);
  const message = overrides.message || run?.latestMessage || '操作已提交。';
  return {
    schema_version: 'stage2_v3_operation_feedback.v1',
    action: actionName(action),
    status,
    tone: overrides.tone || (
      status === 'failed' ? 'error' : status === 'blocked' ? 'warning' : status === 'running' ? 'info' : 'success'
    ),
    message,
    nextAction: overrides.nextAction || nextActionForRun(run),
    error: overrides.error || (status === 'failed'
      ? {
          code: run?.currentStatus?.phase || run?.status || 'failed',
          message
        }
      : null)
  };
}

function makeRunOperability(manifest, artifacts = {}, currentStatus = null) {
  const failureReason = artifacts.preflight_result?.checks?.python_orchestrator?.failure_reason
    || artifacts.execution_results?.items?.find((item) => item.failure_reason)?.failure_reason
    || null;
  if (failureReason === 'python_executor_unavailable') {
    return {
      kind: 'executor_unavailable',
      actionable: false,
      reason: 'Python 执行器不可用，真实浏览器 run 不能继续执行。',
      blocker: failureReason
    };
  }
  if (manifest.status === 'stopped') {
    return {
      kind: 'read_only_v3_run',
      actionable: false,
      reason: 'run 已停止，仅可查看产物和报告。',
      blocker: 'stopped'
    };
  }
  if (manifest.status === 'completed' && currentStatus?.phase === 'next_round_not_required') {
    return {
      kind: 'read_only_v3_run',
      actionable: false,
      reason: 'run 当前目标已完成，无需继续操作。',
      blocker: 'goal_completed'
    };
  }
  return {
    kind: 'actionable_v3_run',
    actionable: true,
    reason: '可通过运行中心提交启动、暂停、复盘、人工任务或报告动作。',
    blocker: null
  };
}

async function createV3Run(body = {}, options = {}) {
  const runsDir = getRunsDir(options);
  const runId = createRunId();
  const runDir = path.join(runsDir, runId);
  const createdAt = nowIso();
  const inputConfig = normalizeInputConfig(body, options);
  const manifest = buildManifest({
    runId,
    inputConfig,
    status: 'draft',
    createdAt,
    updatedAt: createdAt,
    rounds: []
  });

  await fs.mkdir(runDir, { recursive: true });
  await writeJson(path.join(runDir, ARTIFACTS.input_config), inputConfig);
  const savedManifest = await saveManifest(runDir, manifest);
  await writeJson(path.join(runDir, ARTIFACTS.current_status), buildCurrentStatus(
    savedManifest,
    'draft',
    'run 草稿已创建，等待启动。'
  ));
  await writeJson(path.join(runDir, ARTIFACTS.human_tasks), {
    schema_version: 'stage2_human_tasks.v3',
    items: []
  });
  await appendEvent(runDir, { type: 'run_created', run_id: runId, status: 'draft' });

  const run = buildPublicRun(runDir, savedManifest, await loadRunArtifacts(runDir));
  return {
    run,
    operation: makeOperationFeedback('create_run', run, {
      status: 'succeeded',
      message: 'v3 run 草稿已创建，等待启动。'
    })
  };
}

async function listV3Runs(options = {}) {
  const runsDir = getRunsDir(options);
  await fs.mkdir(runsDir, { recursive: true });
  const dirents = await fs.readdir(runsDir, { withFileTypes: true });
  const runs = [];
  for (const dirent of dirents) {
    if (!dirent.isDirectory() || !SAFE_ID_PATTERN.test(dirent.name)) {
      continue;
    }
    const runDir = path.join(runsDir, dirent.name);
    const manifest = await readJsonIfExists(path.join(runDir, ARTIFACTS.run_manifest));
    if (!manifest?.schema_version?.startsWith('stage2_run_manifest.v3')) {
      continue;
    }
    runs.push(buildPublicRun(runDir, manifest, await loadRunArtifacts(runDir)));
  }
  runs.sort((left, right) => String(right.createdAt || '').localeCompare(String(left.createdAt || '')));
  return { runs };
}

async function getV3Run(runId, options = {}) {
  const { runDir, manifest } = await readManifest(runId, options);
  const artifacts = await loadRunArtifacts(runDir);
  return { run: buildPublicRun(runDir, manifest, artifacts), artifacts };
}

function makePreflightResult(manifest, inputConfig, executionMode = 'contract_only') {
  const isContractOnly = executionMode === 'contract_only';
  return {
    schema_version: 'stage2_preflight_result.v3',
    run_id: manifest.run_id,
    execution_mode: executionMode,
    status: isContractOnly ? 'contract_only' : 'pending_real_browser',
    checks: {
      input_config: { ok: true },
      cdp_url: { ok: true, url: inputConfig.cdp_url },
      python_orchestrator: {
        ok: !isContractOnly,
        reason: isContractOnly
          ? '本次按 contract_only 模式只生成 v3 产物契约，不执行真实浏览器。'
          : '准备调用 Python v3 orchestrator 执行真实浏览器链路。'
      }
    },
    created_at: nowIso()
  };
}

function makeDiscoveryArtifacts(manifest, inputConfig) {
  const pageEntry = {
    page_entry_id: 'page_home',
    name: `${inputConfig.system_name}首页`,
    url: inputConfig.entry_url,
    menu_path: [inputConfig.system_name],
    page_type: 'unknown',
    discovery_depth: 0,
    status: inputConfig.entry_url ? 'pending_executor' : 'needs_input',
    source: 'run_center_v3.input_config',
    screenshot_refs: []
  };
  const systemMap = {
    schema_version: 'stage2_system_map.v3',
    run_id: manifest.run_id,
    root: {
      name: inputConfig.system_name,
      url: inputConfig.entry_url,
      status: pageEntry.status
    },
    nodes: [pageEntry],
    generated_by: 'node.stage2V3RunCenter'
  };
  const navigationTree = {
    schema_version: 'stage2_navigation_tree.v3',
    run_id: manifest.run_id,
    items: [{
      id: pageEntry.page_entry_id,
      label: pageEntry.name,
      url: pageEntry.url,
      children: []
    }]
  };
  const pageEntries = {
    schema_version: 'stage2_page_entries.v3',
    items: [pageEntry]
  };
  return { systemMap, navigationTree, pageEntries };
}

function makeEmptyMenuArtifacts(status = 'not_available') {
  const menuTree = {
    schema_version: 'stage2_menu_tree.v1',
    status,
    root_count: 0,
    entry_count: 0,
    leaf_count: 0,
    nodes: [],
    notes: ['本轮未产出第一轮菜单遍历结果。']
  };
  const menuEntries = {
    schema_version: 'stage2_menu_entries.v1',
    items: [],
    menu_entries: [],
    entry_count: 0,
    leaf_count: 0
  };
  return { menuTree, menuEntries, menuTraversalLog: '' };
}

function normalizeMenuEntries(payload) {
  const items = artifactItems(payload, 'menu_entries')
    .filter((item) => item && typeof item === 'object');
  return {
    schema_version: payload?.schema_version || 'stage2_menu_entries.v1',
    ...payload,
    items,
    menu_entries: items,
    entry_count: numberOrZero(payload?.entry_count) || items.length,
    leaf_count: numberOrZero(payload?.leaf_count) || items.filter((item) => item.is_leaf).length
  };
}

function inferFeatureTypes(inputConfig) {
  const text = [
    inputConfig.system_name,
    inputConfig.scope,
    inputConfig.test_account_note
  ].filter(Boolean).join(' ');
  const featureTypes = [
    ['navigation', '页面入口可达性检查', 'safe_auto'],
    ['query', '默认查询或筛选检查', 'safe_auto']
  ];
  if (/详情|明细|查看/.test(text)) {
    featureTypes.push(['detail', '详情查看检查', 'safe_auto']);
  }
  if (/导出|下载/.test(text)) {
    featureTypes.push(['export', '导出入口检查', 'safe_auto']);
  }
  if (/新增|创建|录入|编辑|删除|审批|提交|发布/.test(text)) {
    featureTypes.push(['unknown', '高风险入口人工审核', 'requires_review']);
  }
  return featureTypes;
}

function makeFeatureArtifacts(inputConfig) {
  const items = inferFeatureTypes(inputConfig).map(([featureType, title, policy], index) => ({
    feature_point_id: `feature_${String(index + 1).padStart(3, '0')}`,
    page_entry_id: 'page_home',
    name: title,
    feature_type: featureType,
    risk_level: policy === 'safe_auto' ? 'low' : 'high',
    auto_verifiable: policy === 'safe_auto',
    verification_strategy: policy === 'safe_auto' ? `${featureType}_minimal_path` : 'manual_review_required',
    source: 'run_center_v3.heuristic_seed',
    confidence: policy === 'safe_auto' ? 0.55 : 0.35,
    review_status: policy === 'safe_auto' ? 'auto_included' : 'pending'
  }));

  return {
    schema_version: 'stage2_feature_points.v3',
    items
  };
}

function makeDiscoveryReview(featurePoints) {
  return {
    schema_version: 'stage2_discovery_review.v3',
    review_mode: 'default_safe_policy',
    page_decisions: [{ page_entry_id: 'page_home', decision: 'include', reason: '来自 run 输入入口。' }],
    feature_decisions: featurePoints.items.map((item) => ({
      feature_point_id: item.feature_point_id,
      decision: item.auto_verifiable ? 'include' : 'needs_human_review',
      reason: item.auto_verifiable ? '低风险启发式功能点。' : '疑似高风险或未知动作，需要界面化人工审核。'
    }))
  };
}

function makeGeneratedTestCases(featurePoints) {
  const items = featurePoints.items.map((feature) => ({
    test_case_id: `case_${feature.feature_point_id.replace(/^feature_/, '')}`,
    feature_point_id: feature.feature_point_id,
    title: `${feature.name} - 最小执行路径`,
    type_template: feature.feature_type,
    preconditions: ['使用当前测试账号已登录会话。', '从所属页面入口标准起点开始。'],
    steps: feature.auto_verifiable
      ? [
        { action: 'open_page_entry', target: feature.page_entry_id },
        { action: 'observe_default_state', target: feature.feature_point_id },
        { action: 'capture_key_evidence', target: feature.feature_point_id }
      ]
      : [
        { action: 'show_manual_review_task', target: feature.feature_point_id }
      ],
    expected_feedback: feature.auto_verifiable
      ? ['页面可达且无明显前端错误。']
      : ['人工在运行中心确认风险、数据和操作方式。'],
    risk_policy: feature.auto_verifiable ? 'safe_auto' : 'manual_review_required',
    assertions: feature.auto_verifiable ? ['page_loaded', 'visible_feedback_collected'] : [],
    requires_human_confirmation: !feature.auto_verifiable
  }));
  return {
    schema_version: 'stage2_generated_test_cases.v3',
    items
  };
}

function makeExecutionResults(testCases, reason = 'contract_only_mode') {
  return {
    schema_version: 'stage2_execution_results.v3',
    items: testCases.items.map((testCase) => ({
      test_case_id: testCase.test_case_id,
      status: testCase.requires_human_confirmation ? 'needs_review' : 'skipped',
      verdict: testCase.requires_human_confirmation
        ? '需要人工确认后才能执行。'
        : '已生成执行型测试用例，但本轮未执行真实浏览器动作。',
      actions: [],
      page_feedback: [],
      screenshot_refs: [],
      failure_reason: testCase.requires_human_confirmation
        ? 'manual_review_required'
        : reason,
      manual_confirmation_required: Boolean(testCase.requires_human_confirmation)
    }))
  };
}

function makeScreenshotsIndex() {
  return {
    schema_version: 'stage2_screenshots_index.v3',
    items: [],
    notes: ['Node v3 API 未直接驱动浏览器，截图由 Python v3 执行器接入后填充。']
  };
}

function groupFailures(executionResults) {
  const groups = new Map();
  for (const result of executionResults.items || []) {
    if (['passed', 'real_passed', 'side_effect_executed'].includes(result.status)) {
      continue;
    }
    const key = result.failure_reason || result.status || 'unknown';
    if (!groups.has(key)) {
      groups.set(key, []);
    }
    groups.get(key).push(result.test_case_id);
  }
  return [...groups.entries()].map(([reason, caseIds], index) => ({
    cluster_id: `cluster_${String(index + 1).padStart(3, '0')}`,
    reason,
    test_case_ids: caseIds,
    count: caseIds.length
  }));
}

function extractScopeTargets(inputConfig = {}) {
  const scope = normalizeOptionalText(
    inputConfig.scope
      || inputConfig.scopeText
      || inputConfig.explorationScope
      || inputConfig.exploration_scope
  );
  if (!scope) {
    return [];
  }
  const quoted = [...scope.matchAll(/[“"']([^”"']{2,80})[”"']/g)]
    .map((match) => normalizeOptionalText(match[1]))
    .filter(Boolean);
  if (!quoted.length && !/[页面入口]/.test(scope)) {
    return [];
  }
  const cleaned = scope
    .replace(/[“"'][^”"']{2,80}[”"']/g, ' ')
    .replace(/优先|完成|页面|入口|覆盖|测试|请|先|进行|的|和|、|，|。/g, ' ')
    .split(/\s+/)
    .map((item) => normalizeOptionalText(item))
    .filter((item) => item && item.length >= 2);
  return [...new Set([...quoted, ...cleaned])].slice(0, 5);
}

function itemMatchesScopeTarget(item, target) {
  const haystack = [
    item.name,
    item.title,
    item.url,
    item.menu_path,
    item.feature_type,
    item.page_type,
    item.source
  ]
    .flat()
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return haystack.includes(String(target || '').toLowerCase());
}

function findMissingScopeTargets(inputConfig, pageEntries, featurePoints) {
  const targets = extractScopeTargets(inputConfig);
  if (!targets.length) {
    return [];
  }
  const pages = pageEntries.items || [];
  const features = featurePoints.items || [];
  return targets.filter((target) => !pages.some((item) => itemMatchesScopeTarget(item, target))
    && !features.some((item) => itemMatchesScopeTarget(item, target)));
}

function buildHumanTasks(featurePoints, executionResults, nextRoundPlan = null) {
  const tasks = [];
  const reviewFeatures = (featurePoints.items || []).filter((item) => item.review_status === 'pending');
  if (reviewFeatures.length) {
    tasks.push({
      task_id: 'task_review_feature_points',
      task_type: 'review_feature_points',
      title: '审核高风险或未知功能点',
      reason: '发现疑似新增、编辑、删除、审批、提交等动作，不能由 AI 单独授权。',
      input_refs: ['feature_points', 'discovery_review'],
      ui_schema: {
        kind: 'table_review',
        selectable: true,
        columns: ['name', 'feature_type', 'risk_level', 'verification_strategy']
      },
      status: 'pending',
      result_artifact: null
    });
  }

  const skipped = (executionResults.items || []).filter((item) => [
    'contract_only_mode',
    'python_v3_executor_not_connected',
    'python_v3_orchestrator_failed',
    'python_executor_unavailable',
    'python_returned_safe_placeholder'
  ].includes(item.failure_reason));
  if (skipped.length) {
    tasks.push({
      task_id: 'task_connect_executor_or_confirm_plan',
      task_type: 'review_next_round_plan',
      title: '处理真实浏览器执行阻塞',
      reason: '本轮没有获得真实浏览器执行证据，请检查执行模式、Python v3 orchestrator、CDP 地址或人工确认下一步。',
      input_refs: ['generated_test_cases', 'execution_results', 'next_round_plan'],
      ui_schema: {
        kind: 'decision',
        actions: ['confirm_after_executor_ready', 'pause', 'stop']
      },
      status: 'pending',
      result_artifact: null
    });
  }

  if (nextRoundPlan?.requires_human_approval && !tasks.some((item) => item.task_id === 'task_connect_executor_or_confirm_plan')) {
    tasks.push({
      task_id: 'task_review_next_round_plan',
      task_type: 'review_next_round_plan',
      title: '审核下一轮计划',
      reason: '下一轮计划需要人工确认后继续。',
      input_refs: ['round_analysis', 'next_round_plan'],
      ui_schema: { kind: 'decision', actions: ['approve', 'revise', 'stop'] },
      status: 'pending',
      result_artifact: null
    });
  }

  return {
    schema_version: 'stage2_human_tasks.v3',
    items: tasks
  };
}

function makeRoundAnalysis({
  manifest,
  inputConfig = {},
  pageEntries,
  featurePoints,
  testCases,
  executionResults,
  screenshotsIndex
}) {
  const executionSummary = summarizeExecution(executionResults.items || []);
  const failureClusters = groupFailures(executionResults);
  const missingScopeTargets = findMissingScopeTargets(inputConfig, pageEntries, featurePoints);
  if (missingScopeTargets.length) {
    failureClusters.push({
      cluster_id: `cluster_${String(failureClusters.length + 1).padStart(3, '0')}`,
      reason: 'scope_target_not_found',
      test_case_ids: [],
      count: missingScopeTargets.length,
      target_texts: missingScopeTargets,
      suggestion: `本轮未发现用户指定目标：${missingScopeTargets.join('、')}。请扩大探索或先在浏览器展开/进入目标菜单后继续。`
    });
  }
  const humanTaskReasons = failureClusters
    .filter((cluster) => [
      'manual_review_required',
      'contract_only_mode',
      'python_v3_executor_not_connected',
      'python_v3_orchestrator_failed',
      'python_executor_unavailable',
      'python_returned_safe_placeholder',
      'scope_target_not_found'
    ].includes(cluster.reason))
    .map((cluster) => cluster.reason);

  const analysis = {
    schema_version: 'stage2_round_analysis.v3',
    round_id: manifest.current_round_id || 'round_001',
    goal: '建立 v3 run 级发现、用例生成、执行结果和复盘产物闭环。',
    coverage_summary: {
      page_entries: summarizeItems(pageEntries.items),
      feature_points: summarizeItems(featurePoints.items),
      generated_test_cases: summarizeItems(testCases.items),
      execution: executionSummary
    },
    failure_summary: {
      total_clusters: failureClusters.length,
      clusters: failureClusters
    },
    not_executed_reasons: [...new Set(failureClusters.map((item) => item.reason))],
    evidence_quality: {
      screenshot_count: summarizeItems(screenshotsIndex.items),
      has_action_log: false,
      status: summarizeItems(screenshotsIndex.items) > 0 ? 'partial' : 'missing_executor_evidence'
    },
    human_tasks: humanTaskReasons,
    improvement_candidates: [
      {
        candidate_id: 'improve_connect_python_v3_orchestrator',
        scope: 'runtime',
        title: '确保真实浏览器执行链路产出可复核证据',
        confidence: 0.95,
        evidence_refs: ['execution_results']
      }
    ],
    analysis_mode: 'deterministic_rule_review',
    ai_provider_status: 'not_connected',
    scope_targets: extractScopeTargets(inputConfig),
    missing_scope_targets: missingScopeTargets,
    confidence: 0.72
  };

  if (missingScopeTargets.length) {
    analysis.improvement_candidates.unshift({
      candidate_id: 'improve_scope_target_discovery',
      scope: 'discovery',
      title: `补齐目标页面发现：${missingScopeTargets.join('、')}`,
      confidence: 0.98,
      evidence_refs: ['input_config', 'page_entries', 'feature_points']
    });
  }

  const nextRoundPlan = {
    schema_version: 'stage2_next_round_plan.v3',
    current_round_id: analysis.round_id,
    should_continue: missingScopeTargets.length > 0,
    decision: missingScopeTargets.length ? 'auto_continue' : failureClusters.length ? 'wait_human_review' : 'stop_goal_completed',
    next_round_goal: missingScopeTargets.length
      ? `继续寻找用户指定目标页面：${missingScopeTargets.join('、')}。`
      : failureClusters.length
      ? '处理执行阻塞后，重新执行低风险用例并补齐截图证据。'
      : '本 run 已完成当前范围。',
    target_page_entry_ids: (pageEntries.items || []).map((item) => item.page_entry_id),
    target_feature_point_ids: (featurePoints.items || [])
      .filter((item) => item.auto_verifiable)
      .map((item) => item.feature_point_id),
    planned_improvements: analysis.improvement_candidates,
    risk_level: 'low',
    requires_human_approval: failureClusters.length > 0 && missingScopeTargets.length === 0
  };

  return {
    analysis,
    failureClusters: {
      schema_version: 'stage2_failure_clusters.v3',
      items: failureClusters
    },
    improvementCandidates: {
      schema_version: 'stage2_improvement_candidates.v3',
      items: analysis.improvement_candidates
    },
    nextRoundPlan,
    humanTasks: buildHumanTasks(featurePoints, executionResults, nextRoundPlan)
  };
}

function makeFailureSummary(failureClusters) {
  return {
    schema_version: 'stage2_failure_summary.v3',
    total_clusters: failureClusters.items.length,
    items: failureClusters.items
  };
}

function makePromotionCandidates() {
  return {
    schema_version: 'stage2_promotion_candidates.v3',
    items: []
  };
}

function parseJsonFromProcessOutput(stdout) {
  const text = String(stdout || '').trim();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    const start = text.indexOf('{');
    const end = text.lastIndexOf('}');
    if (start !== -1 && end > start) {
      try {
        return JSON.parse(text.slice(start, end + 1));
      } catch {
        return null;
      }
    }
    return null;
  }
}

async function resolvePythonCommand(options = {}) {
  if (options.pythonRunner) {
    return options.pythonCommand || DEFAULT_PYTHON_COMMAND;
  }
  const bundledPython = process.env.USERPROFILE
    ? path.join(process.env.USERPROFILE, '.cache', 'codex-runtimes', 'codex-primary-runtime', 'dependencies', 'python', 'python.exe')
    : null;
  const candidates = [
    options.pythonCommand,
    process.env.STAGE2_PYTHON,
    process.env.PYTHON,
    bundledPython,
    DEFAULT_PYTHON_COMMAND
  ].filter(Boolean);
  const seen = new Set();
  const errors = [];
  for (const command of candidates) {
    if (seen.has(command)) {
      continue;
    }
    seen.add(command);
    try {
      if (path.isAbsolute(command) && !(await pathExists(command))) {
        errors.push(`${command}: 不存在`);
        continue;
      }
      const result = await execFileAsync(command, [
        '-c',
        'import sys,json; print(json.dumps({"major":sys.version_info[0],"minor":sys.version_info[1]}))'
      ], { cwd: ROOT_DIR, timeout: 8000, windowsHide: true });
      const version = JSON.parse(String(result.stdout || '').trim());
      if (version.major > 3 || (version.major === 3 && version.minor >= 10)) {
        return command;
      }
      errors.push(`${command}: Python ${version.major}.${version.minor} 低于 3.10`);
    } catch (error) {
      errors.push(`${command}: ${error.message}`);
    }
  }
  throw new Stage2V3InputError(`找不到可运行第二阶段 v3 的 Python 3.10+：${errors.join('；')}`, 500);
}

async function runPythonV3Orchestrator({ runId, runsDir, runDir, inputConfig, body, roundId }, options = {}) {
  const executionMode = normalizeExecutionMode(body.executionMode || body.execution_mode || 'real_browser');
  const artifactRoot = runsDir;
  const command = await resolvePythonCommand(options);
  const args = [
    '-m',
    'prototype.stage2.main',
    '--run-v3',
    '--v3-run-id',
    runId,
    '--v3-artifact-root',
    artifactRoot,
    '--v3-execution-mode',
    executionMode,
    '--v3-reuse-run-dir',
    '--target-name',
    inputConfig.system_name,
    '--page-url',
    inputConfig.entry_url,
    '--cdp-url',
    inputConfig.cdp_url,
    '--v3-max-pages',
    String(normalizeInteger(body.maxPages || body.max_pages, inputConfig.max_pages, 1, 200)),
    '--v3-max-features-per-page',
    String(normalizeInteger(
      body.maxFeaturesPerPage || body.max_features_per_page,
      inputConfig.max_features_per_page,
      1,
      200
    )),
    '--v3-safety-policy',
    inputConfig.safety_policy || SAFETY_POLICY_LOW_RISK_ONLY
  ];
  if (inputConfig.scope) {
    args.push('--v3-scope', inputConfig.scope);
  }
  for (const action of inputConfig.allowed_side_effect_actions || []) {
    args.push('--v3-allow-side-effect-action', action);
  }
  for (const profileId of inputConfig.selected_model_profile_ids || []) {
    args.push('--v3-model-profile', profileId);
  }
  if (body.useLiveDiscovery !== false && body.use_live_discovery !== false) {
    args.push('--v3-use-live-discovery');
  }
  if (body.model) {
    args.push('--model', String(body.model));
  }

  await fs.mkdir(artifactRoot, { recursive: true });
  const timeoutMs = normalizeInteger(
    body.timeoutMs || body.timeout_ms,
    options.pythonTimeoutMs || DEFAULT_PYTHON_TIMEOUT_MS,
    1000,
    60 * 60 * 1000
  );
  const startedAt = nowIso();
  let result;
  try {
    if (options.pythonRunner) {
      result = await options.pythonRunner({ command, args, cwd: ROOT_DIR, artifactRoot, runsDir, runDir, timeoutMs });
    } else {
      result = await execFileAsync(command, args, {
        cwd: ROOT_DIR,
        timeout: timeoutMs,
        windowsHide: true,
        maxBuffer: 20 * 1024 * 1024
      });
    }
  } catch (error) {
    const stderr = String(error.stderr || error.message || '');
    const failureReason = error.code === 'ENOENT' ? 'python_executor_unavailable' : 'python_v3_orchestrator_failed';
    return {
      ok: false,
      failureReason,
      command,
      args,
      artifactRoot,
      startedAt,
      finishedAt: nowIso(),
      exitCode: typeof error.code === 'number' ? error.code : null,
      stdout: String(error.stdout || ''),
      stderr,
      error: error.message || stderr || failureReason
    };
  }

  const stdout = String(result?.stdout || '');
  const stderr = String(result?.stderr || '');
  const parsed = parseJsonFromProcessOutput(stdout);
  return {
    ok: true,
    command,
    args,
    artifactRoot,
    startedAt,
    finishedAt: nowIso(),
    exitCode: 0,
    stdout,
    stderr,
    result: parsed
  };
}

async function findPythonRunDir(processResult) {
  const explicit = processResult.result?.run_dir;
  if (explicit && await pathExists(explicit)) {
    return explicit;
  }
  const artifactPaths = processResult.result?.artifact_paths || {};
  for (const value of Object.values(artifactPaths)) {
    if (value) {
      const dir = path.dirname(value);
      if (await pathExists(dir)) {
        return dir;
      }
    }
  }
  const dirents = await fs.readdir(processResult.artifactRoot, { withFileTypes: true }).catch(() => []);
  const directories = dirents.filter((item) => item.isDirectory()).map((item) => path.join(processResult.artifactRoot, item.name));
  return directories[0] || null;
}

async function readPythonJson(pyRunDir, fileName) {
  return pyRunDir ? readJsonIfExists(path.join(pyRunDir, fileName)) : null;
}

function normalizePythonPageEntries(payload, inputConfig) {
  if (payload?.schema_version === 'stage2_page_entries.v3' && Array.isArray(payload.items)) {
    return payload;
  }
  const pages = payload?.pages || payload?.page_entries || payload?.items || [];
  return {
    schema_version: 'stage2_page_entries.v3',
    items: pages.map((page, index) => ({
      page_entry_id: page.page_entry_id || page.page_id || `page_${String(index + 1).padStart(3, '0')}`,
      name: page.name || page.title || `${inputConfig.system_name}页面${index + 1}`,
      url: page.url || inputConfig.entry_url,
      menu_path: page.menu_path || [inputConfig.system_name],
      page_type: page.page_type || page.semantic_page_type || 'unknown',
      discovery_depth: Number.isInteger(page.discovery_depth) ? page.discovery_depth : index === 0 ? 0 : 1,
      status: page.status || 'reachable',
      source: page.source || 'python_v3_orchestrator',
      evidence: page.evidence || {},
      screenshot_refs: page.screenshot_refs || page.screenshots || []
    }))
  };
}

function normalizePythonFeaturePoints(payload) {
  if (payload?.schema_version === 'stage2_feature_points.v3' && Array.isArray(payload.items)) {
    return payload;
  }
  const features = payload?.features || payload?.feature_points || payload?.items || [];
  return {
    schema_version: 'stage2_feature_points.v3',
    items: features.map((feature, index) => {
      const risk = feature.risk_level || 'low';
      return {
        feature_point_id: feature.feature_point_id || feature.feature_id || `feature_${String(index + 1).padStart(3, '0')}`,
        page_entry_id: feature.page_entry_id || feature.page_id || 'page_home',
        name: feature.name || feature.title || `功能点${index + 1}`,
        feature_type: feature.feature_type || feature.type || 'unknown',
        risk_level: risk,
        auto_verifiable: feature.auto_verifiable !== false && risk === 'low',
        verification_strategy: feature.verification_strategy || `${feature.feature_type || 'unknown'}_minimal_path`,
        locator_candidates: feature.locator_candidates || [],
        source: feature.source || 'python_v3_orchestrator',
        confidence: typeof feature.confidence === 'number' ? feature.confidence : 0.7,
        review_status: feature.review_status || (risk === 'low' ? 'auto_included' : 'pending')
      };
    })
  };
}

function normalizePythonTestCases(payload) {
  if (payload?.schema_version === 'stage2_generated_test_cases.v3' && Array.isArray(payload.items)) {
    return payload;
  }
  const cases = payload?.cases || payload?.test_cases || payload?.items || [];
  return {
    schema_version: 'stage2_generated_test_cases.v3',
    items: cases.map((testCase, index) => ({
      test_case_id: testCase.test_case_id || testCase.case_id || `case_${String(index + 1).padStart(3, '0')}`,
      feature_point_id: testCase.feature_point_id || testCase.feature_id || '',
      title: testCase.title || testCase.name || `执行型用例${index + 1}`,
      type_template: testCase.type_template || testCase.case_type || 'unknown',
      preconditions: testCase.preconditions || [],
      steps: testCase.steps || [],
      expected_feedback: testCase.expected_feedback || [testCase.expected_result].filter(Boolean),
      risk_policy: testCase.risk_policy || (testCase.auto_allowed === false ? 'manual_review_required' : 'safe_auto'),
      assertions: testCase.assertions || [],
      requires_human_confirmation: Boolean(testCase.requires_human_confirmation || testCase.auto_allowed === false)
    }))
  };
}

function normalizePythonExecutionResults(payload) {
  const results = payload?.schema_version === 'stage2_execution_results.v3' && Array.isArray(payload.items)
    ? payload.items
    : payload?.results || payload?.items || [];
  return {
    schema_version: 'stage2_execution_results.v3',
    items: results.map((result) => {
      let status = result.status || 'unknown';
      let failureReason = result.failure_reason || null;
      let manualConfirmationRequired = Boolean(result.manual_confirmation_required);
      if (status === 'passed_safe_placeholder') {
        status = 'skipped';
        failureReason = 'python_returned_safe_placeholder';
        manualConfirmationRequired = true;
      } else if (status === 'blocked_by_policy') {
        failureReason = failureReason || 'blocked_by_policy';
        manualConfirmationRequired = true;
      } else if (status === 'side_effect_failed') {
        failureReason = failureReason || 'side_effect_failed';
      }
      return {
        test_case_id: result.test_case_id || result.case_id || '',
        status,
        verdict: result.verdict || result.message || '',
        started_at: result.started_at || null,
        finished_at: result.finished_at || null,
        actions: result.actions || (result.action_type ? [{
          action: result.action_type,
          target: result.control_label || '',
          policy_decision: result.policy_decision || null
        }] : []),
        page_feedback: result.page_feedback || [result.visible_feedback].filter(Boolean),
        screenshot_refs: result.screenshot_refs || result.evidence || [
          result.before_screenshot_ref,
          result.after_screenshot_ref
        ].filter(Boolean),
        network_refs: result.network_refs || [],
        failure_reason: failureReason,
        manual_confirmation_required: manualConfirmationRequired,
        execution_mode: result.execution_mode || 'real_browser',
        side_effect: result.action_type ? {
          action_type: result.action_type,
          control_label: result.control_label || '',
          policy_decision: result.policy_decision || null,
          before_screenshot_ref: result.before_screenshot_ref || null,
          after_screenshot_ref: result.after_screenshot_ref || null,
          url_before: result.url_before || null,
          url_after: result.url_after || null,
          dialog_events: result.dialog_events || []
        } : null
      };
    })
  };
}

function normalizePythonHumanTasks(payload) {
  if (payload?.schema_version === 'stage2_human_tasks.v3' && Array.isArray(payload.items)) {
    return payload;
  }
  const tasks = payload?.tasks || [];
  return {
    schema_version: 'stage2_human_tasks.v3',
    items: tasks.map((task) => ({
      task_id: task.task_id,
      task_type: task.task_type || task.type || 'review_next_round_plan',
      title: task.title || '人工处理任务',
      reason: task.reason || task.ui_action || '需要在运行中心处理后继续。',
      input_refs: task.input_refs || [],
      ui_schema: task.ui_schema || { kind: 'decision', actions: ['approve', 'pause', 'stop'] },
      status: task.status === 'open' ? 'pending' : task.status || 'pending',
      result_artifact: task.result_artifact || null
    }))
  };
}

function normalizePythonNextRoundPlan(payload, executionResults, roundId) {
  const hasBlockingEvidenceGap = (executionResults.items || []).some((item) => [
    'contract_only_mode',
    'python_returned_safe_placeholder',
    'python_v3_orchestrator_failed',
    'python_executor_unavailable',
    'cdp_connect_failed',
    'playwright_missing',
    'login_required',
    'side_effect_failed'
  ].includes(item.failure_reason) || [
    'skipped_no_executor',
    'skipped_not_observed',
    'login_required',
    'real_failed',
    'failed',
    'side_effect_failed'
  ].includes(item.status));
  if (payload?.schema_version === 'stage2_next_round_plan.v3' && !hasBlockingEvidenceGap) {
    return payload;
  }
  return {
    schema_version: 'stage2_next_round_plan.v3',
    current_round_id: roundId,
    should_continue: Boolean(payload?.should_continue || payload?.should_start_next_round) && !hasBlockingEvidenceGap,
    decision: hasBlockingEvidenceGap
      ? 'wait_human_review'
      : payload?.decision || (payload?.status === 'blocked_waiting_human' ? 'wait_human_review' : payload?.should_start_next_round ? 'auto_continue' : 'stop_goal_completed'),
    next_round_goal: hasBlockingEvidenceGap
      ? '补齐真实浏览器执行证据后重新执行低风险用例。'
      : payload?.next_round_goal || payload?.primary_reason || '继续下一轮自动评测。',
    target_page_entry_ids: payload?.target_page_entry_ids || [],
    target_feature_point_ids: payload?.target_feature_point_ids || [],
    planned_improvements: payload?.planned_improvements || payload?.recommended_actions || [],
    risk_level: payload?.risk_level || 'low',
    requires_human_approval: Boolean(hasBlockingEvidenceGap || payload?.requires_human_approval || payload?.status === 'blocked_waiting_human')
  };
}

async function updateRunStatus(runDir, manifest, status, phase, message, extra = {}) {
  const updated = {
    ...manifest,
    status,
    execution_mode: extra.execution_mode || manifest.execution_mode || null,
    updated_at: nowIso()
  };
  const saved = await saveManifest(runDir, updated);
  await writeJson(path.join(runDir, ARTIFACTS.current_status), buildCurrentStatus(saved, phase, message, extra));
  await appendEvent(runDir, { type: 'status_changed', status, phase, message });
  return saved;
}

async function persistContractOnlyArtifacts(runDir, manifest, inputConfig, executionMode = 'contract_only') {
  const preflightResult = makePreflightResult(manifest, inputConfig, executionMode);
  const { systemMap, navigationTree, pageEntries } = makeDiscoveryArtifacts(manifest, inputConfig);
  const { menuTree, menuEntries, menuTraversalLog } = makeEmptyMenuArtifacts('contract_only');
  const featurePoints = makeFeatureArtifacts(inputConfig);
  const discoveryReview = makeDiscoveryReview(featurePoints);
  const testCases = makeGeneratedTestCases(featurePoints);
  const executionResults = makeExecutionResults(testCases, 'contract_only_mode');
  const screenshotsIndex = makeScreenshotsIndex();
  const analysisPack = makeRoundAnalysis({
    manifest,
    inputConfig,
    pageEntries,
    featurePoints,
    testCases,
    executionResults,
    screenshotsIndex
  });

  await writeJson(path.join(runDir, ARTIFACTS.preflight_result), preflightResult);
  await writeJson(path.join(runDir, ARTIFACTS.system_map), systemMap);
  await writeJson(path.join(runDir, ARTIFACTS.navigation_tree), navigationTree);
  await writeJson(path.join(runDir, ARTIFACTS.menu_tree), menuTree);
  await writeJson(path.join(runDir, ARTIFACTS.menu_entries), menuEntries);
  await fs.writeFile(path.join(runDir, ARTIFACTS.menu_traversal_log), menuTraversalLog, 'utf8');
  await writeJson(path.join(runDir, ARTIFACTS.page_entries), pageEntries);
  await writeJson(path.join(runDir, ARTIFACTS.feature_points), featurePoints);
  await writeJson(path.join(runDir, ARTIFACTS.discovery_review), discoveryReview);
  await writeJson(path.join(runDir, ARTIFACTS.generated_test_cases), testCases);
  await writeJson(path.join(runDir, ARTIFACTS.execution_results), executionResults);
  await writeJson(path.join(runDir, ARTIFACTS.screenshots_index), screenshotsIndex);
  await persistAnalysisPack(runDir, analysisPack);
  return analysisPack;
}

async function persistRealBrowserFailure(runDir, manifest, inputConfig, processResult, roundId) {
  const preflightResult = {
    schema_version: 'stage2_preflight_result.v3',
    run_id: manifest.run_id,
    execution_mode: 'real_browser',
    status: 'failed',
    checks: {
      input_config: { ok: true },
      cdp_url: { ok: true, url: inputConfig.cdp_url },
      python_orchestrator: {
        ok: false,
        reason: processResult.error || processResult.stderr || processResult.failureReason,
        failure_reason: processResult.failureReason
      }
    },
    command: { executable: processResult.command, args: processResult.args },
    started_at: processResult.startedAt,
    finished_at: processResult.finishedAt
  };
  const { systemMap, navigationTree, pageEntries } = makeDiscoveryArtifacts(manifest, inputConfig);
  const { menuTree, menuEntries, menuTraversalLog } = makeEmptyMenuArtifacts('failed');
  const featurePoints = makeFeatureArtifacts(inputConfig);
  const discoveryReview = makeDiscoveryReview(featurePoints);
  const testCases = makeGeneratedTestCases(featurePoints);
  const executionResults = {
    schema_version: 'stage2_execution_results.v3',
    items: testCases.items.map((testCase) => ({
      test_case_id: testCase.test_case_id,
      status: 'failed',
      verdict: `真实浏览器执行未完成：${processResult.error || processResult.failureReason}`,
      started_at: processResult.startedAt,
      finished_at: processResult.finishedAt,
      actions: [],
      page_feedback: [],
      screenshot_refs: [],
      network_refs: [],
      failure_reason: processResult.failureReason,
      manual_confirmation_required: true,
      execution_mode: 'real_browser'
    }))
  };
  const screenshotsIndex = makeScreenshotsIndex();
  const analysisPack = makeRoundAnalysis({
    manifest,
    inputConfig,
    pageEntries,
    featurePoints,
    testCases,
    executionResults,
    screenshotsIndex
  });
  analysisPack.nextRoundPlan.current_round_id = roundId;
  analysisPack.nextRoundPlan.decision = 'wait_human_review';
  analysisPack.nextRoundPlan.requires_human_approval = true;
  analysisPack.nextRoundPlan.next_round_goal = '修复真实浏览器执行环境后重新启动本轮低风险执行。';

  await writeJson(path.join(runDir, ARTIFACTS.python_execution), processResult);
  await writeJson(path.join(runDir, ARTIFACTS.preflight_result), preflightResult);
  await writeJson(path.join(runDir, ARTIFACTS.system_map), systemMap);
  await writeJson(path.join(runDir, ARTIFACTS.navigation_tree), navigationTree);
  await writeJson(path.join(runDir, ARTIFACTS.menu_tree), menuTree);
  await writeJson(path.join(runDir, ARTIFACTS.menu_entries), menuEntries);
  await fs.writeFile(path.join(runDir, ARTIFACTS.menu_traversal_log), menuTraversalLog, 'utf8');
  await writeJson(path.join(runDir, ARTIFACTS.page_entries), pageEntries);
  await writeJson(path.join(runDir, ARTIFACTS.feature_points), featurePoints);
  await writeJson(path.join(runDir, ARTIFACTS.discovery_review), discoveryReview);
  await writeJson(path.join(runDir, ARTIFACTS.generated_test_cases), testCases);
  await writeJson(path.join(runDir, ARTIFACTS.execution_results), executionResults);
  await writeJson(path.join(runDir, ARTIFACTS.screenshots_index), screenshotsIndex);
  await persistAnalysisPack(runDir, analysisPack);
  return analysisPack;
}

async function persistRealBrowserArtifacts(runDir, manifest, inputConfig, processResult, roundId) {
  const pyRunDir = await findPythonRunDir(processResult);
  const pythonPreflight = await readPythonJson(pyRunDir, 'preflight_result.json');
  const menuTree = await readPythonJson(pyRunDir, 'menu_tree.json') || makeEmptyMenuArtifacts().menuTree;
  const menuEntries = await readPythonJson(pyRunDir, 'menu_entries.json') || makeEmptyMenuArtifacts().menuEntries;
  const pageEntries = normalizePythonPageEntries(
    await readPythonJson(pyRunDir, 'page_entries.json') || await readPythonJson(pyRunDir, 'pages.json'),
    inputConfig
  );
  const featurePoints = normalizePythonFeaturePoints(
    await readPythonJson(pyRunDir, 'feature_points.json') || await readPythonJson(pyRunDir, 'features.json')
  );
  const testCases = normalizePythonTestCases(
    await readPythonJson(pyRunDir, 'generated_test_cases.json') || await readPythonJson(pyRunDir, 'cases.json')
  );
  const executionResults = normalizePythonExecutionResults(await readPythonJson(pyRunDir, 'execution_results.json'));
  const screenshotsIndex = await readPythonJson(pyRunDir, 'screenshots_index.json') || {
    schema_version: 'stage2_screenshots_index.v3',
    items: [],
    notes: ['Python v3 本轮未返回截图索引。']
  };
  const discoveryReview = makeDiscoveryReview(featurePoints);
  const { systemMap, navigationTree } = makeDiscoveryArtifacts(manifest, inputConfig);
  const pythonNextRoundPlan = await readPythonJson(pyRunDir, 'next_round_plan.json');
  const analysisPack = makeRoundAnalysis({
    manifest,
    inputConfig,
    pageEntries,
    featurePoints,
    testCases,
    executionResults,
    screenshotsIndex
  });
  const nodeNextRoundPlan = analysisPack.nextRoundPlan;
  analysisPack.nextRoundPlan = normalizePythonNextRoundPlan(pythonNextRoundPlan, executionResults, roundId);
  if ((analysisPack.analysis.missing_scope_targets || []).length) {
    analysisPack.nextRoundPlan = {
      ...nodeNextRoundPlan,
      current_round_id: roundId
    };
  }
  analysisPack.humanTasks = normalizePythonHumanTasks(await readPythonJson(pyRunDir, 'human_tasks.json'));
  if (analysisPack.nextRoundPlan.requires_human_approval) {
    const existingTaskIds = new Set(analysisPack.humanTasks.items.map((item) => item.task_id));
    if (!existingTaskIds.has('task_connect_executor_or_confirm_plan')) {
      analysisPack.humanTasks.items.push(...buildHumanTasks(featurePoints, executionResults, analysisPack.nextRoundPlan).items);
    }
  }

  await writeJson(path.join(runDir, ARTIFACTS.python_execution), { ...processResult, pythonRunDir: pyRunDir });
  await writeJson(path.join(runDir, ARTIFACTS.preflight_result), {
    schema_version: 'stage2_preflight_result.v3',
    run_id: manifest.run_id,
    execution_mode: 'real_browser',
    status: 'completed',
    browser_target_count: numberOrZero(
      pythonPreflight?.browser_target_count
      || pythonPreflight?.checks?.raw_cdp?.target_count
      || pythonPreflight?.checks?.browser_targets?.count
    ),
    python_preflight: pythonPreflight || null,
    checks: {
      input_config: { ok: true },
      cdp_url: { ok: true, url: inputConfig.cdp_url },
      python_orchestrator: { ok: true, run_dir: pyRunDir }
    },
    command: { executable: processResult.command, args: processResult.args },
    started_at: processResult.startedAt,
    finished_at: processResult.finishedAt
  });
  await writeJson(path.join(runDir, ARTIFACTS.system_map), systemMap);
  await writeJson(path.join(runDir, ARTIFACTS.navigation_tree), navigationTree);
  await writeJson(path.join(runDir, ARTIFACTS.menu_tree), menuTree);
  await writeJson(path.join(runDir, ARTIFACTS.menu_entries), normalizeMenuEntries(menuEntries));
  await copyTextFileIfExists(
    path.join(pyRunDir || '', 'menu_traversal_log.jsonl'),
    path.join(runDir, ARTIFACTS.menu_traversal_log)
  );
  await writeJson(path.join(runDir, ARTIFACTS.page_entries), pageEntries);
  await writeJson(path.join(runDir, ARTIFACTS.feature_points), featurePoints);
  await writeJson(path.join(runDir, ARTIFACTS.discovery_review), discoveryReview);
  await writeJson(path.join(runDir, ARTIFACTS.generated_test_cases), testCases);
  await writeJson(path.join(runDir, ARTIFACTS.execution_results), executionResults);
  await writeJson(path.join(runDir, ARTIFACTS.screenshots_index), screenshotsIndex);
  await copyTextFileIfExists(path.join(pyRunDir || '', 'report.md'), path.join(runDir, ARTIFACTS.run_report_md));
  await persistAnalysisPack(runDir, analysisPack);
  return analysisPack;
}

async function startV3Run(runId, body = {}, options = {}) {
  const { runDir, manifest } = await readManifest(runId, options);
  const inputConfig = await readJsonRequired(path.join(runDir, ARTIFACTS.input_config), 'input_config 缺失。');
  const executionMode = normalizeExecutionMode(body.executionMode || body.execution_mode || options.executionMode);
  const roundId = body.roundId || body.round_id || manifest.current_round_id || 'round_001';
  const startedAt = nowIso();
  const nextRound = {
    round_id: roundId,
    goal: normalizeText(body.goal || body.roundGoal, '首轮 v3 自动发现与低风险用例生成'),
    started_at: startedAt,
    finished_at: null,
    input_artifacts: ['input_config'],
    output_artifacts: [],
    status: 'running'
  };
  let runningManifest = {
    ...manifest,
    status: 'running',
    execution_mode: executionMode,
    started_at: manifest.started_at || startedAt,
    updated_at: startedAt,
    current_round_id: roundId,
    rounds: [...(manifest.rounds || []).filter((item) => item.round_id !== roundId), nextRound]
  };
  runningManifest = await saveManifest(runDir, runningManifest);
  await writeJson(path.join(runDir, ARTIFACTS.current_status), buildCurrentStatus(
    runningManifest,
    executionMode === 'real_browser' ? 'real_browser_execution' : 'running_contract_pipeline',
    executionMode === 'real_browser'
      ? '正在调用 Python v3 orchestrator 执行真实浏览器链路。'
      : '正在生成 v3 run 稳定产物契约，本轮不会执行真实浏览器。',
    { execution_mode: executionMode }
  ));
  await appendEvent(runDir, { type: 'run_started', run_id: runId, round_id: roundId, execution_mode: executionMode });

  let analysisPack;
  let executionFailed = false;
  if (executionMode === 'contract_only') {
    analysisPack = await persistContractOnlyArtifacts(runDir, runningManifest, inputConfig, executionMode);
  } else {
    const processResult = await runPythonV3Orchestrator({
      runId,
      runsDir: getRunsDir(options),
      runDir,
      inputConfig,
      body,
      roundId
    }, options);
    if (processResult.ok) {
      analysisPack = await persistRealBrowserArtifacts(runDir, runningManifest, inputConfig, processResult, roundId);
    } else {
      executionFailed = true;
      analysisPack = await persistRealBrowserFailure(runDir, runningManifest, inputConfig, processResult, roundId);
    }
  }

  const completedRound = {
    ...nextRound,
    finished_at: nowIso(),
    output_artifacts: [
      'preflight_result',
      'system_map',
      'navigation_tree',
      'menu_tree',
      'menu_entries',
      'menu_traversal_log',
      'page_entries',
      'feature_points',
      'discovery_review',
      'generated_test_cases',
      'execution_results',
      'round_analysis',
      'next_round_plan',
      'human_tasks'
    ],
    status: executionFailed
      ? 'failed'
      : analysisPack.nextRoundPlan.requires_human_approval ? 'waiting_human' : 'completed'
  };
  const finalStatus = executionFailed
    ? 'failed'
    : analysisPack.nextRoundPlan.requires_human_approval ? 'waiting_human' : 'completed';
  const finalMessage = executionFailed
    ? '真实浏览器执行失败，已写入可读错误和阻塞产物。'
    : analysisPack.nextRoundPlan.requires_human_approval
      ? '首轮产物已生成，等待运行中心人工确认下一步。'
      : '首轮真实浏览器执行产物已生成。';
  const finalManifest = await updateRunStatus(
    runDir,
    {
      ...runningManifest,
      rounds: [...(runningManifest.rounds || []).filter((item) => item.round_id !== roundId), completedRound]
    },
    finalStatus,
    executionFailed ? 'real_browser_execution_failed' : 'round_analysis',
    finalMessage,
    { decision: analysisPack.nextRoundPlan.decision, execution_mode: executionMode }
  );

  const run = buildPublicRun(runDir, finalManifest, await loadRunArtifacts(runDir));
  return { run, operation: makeOperationFeedback('start', run) };
}

async function persistAnalysisPack(runDir, analysisPack) {
  await writeJson(path.join(runDir, ARTIFACTS.round_analysis), analysisPack.analysis);
  await writeJson(path.join(runDir, ARTIFACTS.failure_clusters), analysisPack.failureClusters);
  await writeJson(path.join(runDir, ARTIFACTS.improvement_candidates), analysisPack.improvementCandidates);
  await writeJson(path.join(runDir, ARTIFACTS.next_round_plan), analysisPack.nextRoundPlan);
  await writeJson(path.join(runDir, ARTIFACTS.human_tasks), analysisPack.humanTasks);
  await writeJson(path.join(runDir, ARTIFACTS.failure_summary), makeFailureSummary(analysisPack.failureClusters));
  await writeJson(path.join(runDir, ARTIFACTS.promotion_candidates), makePromotionCandidates());
}

async function setV3RunLifecycleStatus(runId, action, body = {}, options = {}) {
  const { runDir, manifest } = await readManifest(runId, options);
  const statusByAction = {
    pause: 'paused',
    resume: 'running',
    stop: 'stopped'
  };
  const status = statusByAction[action];
  if (!status) {
    throw new Stage2V3InputError('不支持的 run 状态操作。');
  }
  const messageByAction = {
    pause: 'run 已暂停。',
    resume: 'run 已恢复，等待执行器继续推进。',
    stop: 'run 已停止。'
  };
  const nextManifest = {
    ...manifest,
    finished_at: action === 'stop' ? nowIso() : manifest.finished_at
  };
  const saved = await updateRunStatus(
    runDir,
    nextManifest,
    status,
    action,
    normalizeText(body.note, messageByAction[action]),
    { operator_id: normalizeOptionalText(body.operatorId || body.operator_id) }
  );
  const run = buildPublicRun(runDir, saved, await loadRunArtifacts(runDir));
  return { run, operation: makeOperationFeedback(action, run) };
}

async function analyzeV3Run(runId, options = {}) {
  const { runDir, manifest } = await readManifest(runId, options);
  const inputConfig = await readJsonIfExists(path.join(runDir, ARTIFACTS.input_config)) || {};
  const pageEntries = await readJsonRequired(path.join(runDir, ARTIFACTS.page_entries), 'page_entries 缺失，不能复盘。');
  const featurePoints = await readJsonRequired(path.join(runDir, ARTIFACTS.feature_points), 'feature_points 缺失，不能复盘。');
  const testCases = await readJsonRequired(path.join(runDir, ARTIFACTS.generated_test_cases), 'generated_test_cases 缺失，不能复盘。');
  const executionResults = await readJsonRequired(path.join(runDir, ARTIFACTS.execution_results), 'execution_results 缺失，不能复盘。');
  const screenshotsIndex = await readJsonIfExists(path.join(runDir, ARTIFACTS.screenshots_index)) || makeScreenshotsIndex();

  const analysisPack = makeRoundAnalysis({
    manifest,
    inputConfig,
    pageEntries,
    featurePoints,
    testCases,
    executionResults,
    screenshotsIndex
  });
  await persistAnalysisPack(runDir, analysisPack);
  const saved = await updateRunStatus(
    runDir,
    manifest,
    analysisPack.nextRoundPlan.requires_human_approval ? 'waiting_human' : 'completed',
    'rule_round_analysis',
    '规则复盘产物已生成；当前未接入可追踪 AI 模型调用。',
    { decision: analysisPack.nextRoundPlan.decision }
  );
  const run = buildPublicRun(runDir, saved, await loadRunArtifacts(runDir));
  return {
    run,
    operation: makeOperationFeedback('analyze_round', run),
    roundAnalysis: analysisPack.analysis,
    nextRoundPlan: analysisPack.nextRoundPlan,
    humanTasks: analysisPack.humanTasks
  };
}

async function saveHumanTaskResult(runId, body = {}, options = {}) {
  const { runDir, manifest } = await readManifest(runId, options);
  const taskId = ensureSafeId(body.taskId || body.task_id, 'taskId');
  const humanTasksPath = path.join(runDir, ARTIFACTS.human_tasks);
  const humanTasks = await readJsonIfExists(humanTasksPath) || { schema_version: 'stage2_human_tasks.v3', items: [] };
  const taskIndex = (humanTasks.items || []).findIndex((item) => item.task_id === taskId);
  if (taskIndex === -1) {
    throw new Stage2V3InputError('人工任务不存在。', 404);
  }
  const resultDir = path.join(runDir, 'human_task_results');
  const resultPath = path.join(resultDir, `${taskId}.json`);
  const resultPayload = {
    schema_version: 'stage2_human_task_result.v3',
    task_id: taskId,
    status: normalizeText(body.status, 'completed'),
    operator_id: normalizeOptionalText(body.operatorId || body.operator_id),
    note: normalizeOptionalText(body.note),
    result: body.result && typeof body.result === 'object' ? body.result : {},
    submitted_at: nowIso()
  };
  await writeJson(resultPath, resultPayload);
  const nextItems = [...humanTasks.items];
  nextItems[taskIndex] = {
    ...nextItems[taskIndex],
    status: resultPayload.status,
    result_artifact: relativeArtifact(resultPath),
    completed_at: resultPayload.submitted_at
  };
  const nextHumanTasks = { ...humanTasks, items: nextItems };
  await writeJson(humanTasksPath, nextHumanTasks);
  const pending = nextHumanTasks.items.filter((item) => item.status === 'pending').length;
  const saved = await updateRunStatus(
    runDir,
    manifest,
    pending ? manifest.status : 'ready_for_next_round',
    'human_task_saved',
    pending ? '人工任务结果已保存，仍有待处理任务。' : '人工任务已处理完毕，可继续下一轮。',
    { pending_human_tasks: pending }
  );
  const run = buildPublicRun(runDir, saved, await loadRunArtifacts(runDir));
  return { run, operation: makeOperationFeedback('save_human_task', run), humanTasks: nextHumanTasks };
}

async function continueNextRound(runId, body = {}, options = {}) {
  const { runDir, manifest } = await readManifest(runId, options);
  const nextRoundPlan = await readJsonRequired(path.join(runDir, ARTIFACTS.next_round_plan), 'next_round_plan 缺失。');
  const approved = body.approved === true || body.decision === 'approve';
  const stopDecision = [
    'stop_goal_completed',
    'stop_no_improvement',
    'stop_budget_exhausted'
  ].includes(nextRoundPlan.decision);
  if (nextRoundPlan.requires_human_approval && !approved) {
    const saved = await updateRunStatus(
      runDir,
      manifest,
      'waiting_human',
      'next_round_blocked',
      '下一轮计划需要人工批准后才能继续。',
      { decision: nextRoundPlan.decision }
    );
    const run = buildPublicRun(runDir, saved, await loadRunArtifacts(runDir));
    return { run, operation: makeOperationFeedback('continue_next_round', run), nextRoundPlan };
  }
  if (stopDecision) {
    const restoredRoundId = nextRoundPlan.current_round_id || manifest.current_round_id;
    const saved = await updateRunStatus(
      runDir,
      { ...manifest, current_round_id: restoredRoundId },
      'completed',
      'next_round_not_required',
      '当前目标已完成，无需进入下一轮；可生成报告或创建新的更大范围 run。',
      { decision: nextRoundPlan.decision, next_round_required: false }
    );
    const run = buildPublicRun(runDir, saved, await loadRunArtifacts(runDir));
    return { run, operation: makeOperationFeedback('continue_next_round', run), nextRoundPlan };
  }

  const currentRounds = manifest.rounds || [];
  const nextIndex = currentRounds.length + 1;
  const roundId = `round_${String(nextIndex).padStart(3, '0')}`;
  const round = {
    round_id: roundId,
    goal: normalizeText(body.goal, nextRoundPlan.next_round_goal || '继续执行下一轮低风险测试。'),
    started_at: nowIso(),
    finished_at: null,
    input_artifacts: ['round_analysis', 'next_round_plan', 'human_tasks'],
    output_artifacts: [],
    status: 'planned'
  };
  const saved = await updateRunStatus(
    runDir,
    {
      ...manifest,
      current_round_id: roundId,
      rounds: [...currentRounds, round]
    },
    'planned',
    'next_round_planned',
    '下一轮已创建，等待执行器推进。',
    { next_round_id: roundId }
  );
  const run = buildPublicRun(runDir, saved, await loadRunArtifacts(runDir));
  return { run, operation: makeOperationFeedback('continue_next_round', run), nextRoundPlan };
}

function makeReport({ manifest, pageEntries, featurePoints, testCases, executionResults, roundAnalysis, nextRoundPlan, humanTasks }) {
  const executionSummary = summarizeExecution(executionResults.items || []);
  const reportJson = {
    schema_version: 'stage2_run_report.v3',
    run_id: manifest.run_id,
    system_name: manifest.system_name,
    generated_at: nowIso(),
    summary: {
      status: manifest.status,
      safety_policy: manifest.safety_policy || SAFETY_POLICY_LOW_RISK_ONLY,
      allowed_side_effect_actions: manifest.allowed_side_effect_actions || [],
      page_entries: summarizeItems(pageEntries.items),
      feature_points: summarizeItems(featurePoints.items),
      generated_test_cases: summarizeItems(testCases.items),
      execution: executionSummary,
      pending_human_tasks: (humanTasks.items || []).filter((item) => item.status === 'pending').length,
      next_decision: nextRoundPlan.decision
    },
    sections: [
      {
        title: '运行概况',
        facts: [
          { label: 'run_id', value: manifest.run_id },
          { label: 'status', value: manifest.status },
          { label: 'safety_policy', value: manifest.safety_policy || SAFETY_POLICY_LOW_RISK_ONLY },
          { label: 'allowed_side_effect_actions', value: (manifest.allowed_side_effect_actions || []).join(', ') || 'none' }
        ]
      },
      { title: '页面入口发现', items: pageEntries.items || [] },
      { title: '功能点识别', items: featurePoints.items || [] },
      { title: '执行型测试用例', items: testCases.items || [] },
      { title: '自动执行结果', items: executionResults.items || [] },
      { title: 'AI 复盘与下一轮计划', facts: [{ label: 'decision', value: nextRoundPlan.decision }], items: roundAnalysis.improvement_candidates || [] },
      { title: '人工确认项', items: humanTasks.items || [] }
    ]
  };

  const md = [
    `# ${manifest.system_name} 第二阶段 v3 运行报告`,
    '',
    `- Run ID: ${manifest.run_id}`,
    `- 状态: ${manifest.status}`,
    `- 安全策略: ${manifest.safety_policy || SAFETY_POLICY_LOW_RISK_ONLY}`,
    `- 副作用白名单: ${(manifest.allowed_side_effect_actions || []).join(', ') || '无'}`,
    `- 页面入口: ${reportJson.summary.page_entries}`,
    `- 功能点: ${reportJson.summary.feature_points}`,
    `- 执行型测试用例: ${reportJson.summary.generated_test_cases}`,
    `- 执行结果: passed ${executionSummary.passed}, failed ${executionSummary.failed}, skipped ${executionSummary.skipped}, needs_review ${executionSummary.needs_review}`,
    `- 下一轮决策: ${nextRoundPlan.decision}`,
    '',
    '## 说明',
    '',
    '本报告由 Node.js v3 Run API 基于稳定 artifact 契约生成。`real_browser` 模式会调用 Python v3 orchestrator 获取真实浏览器证据；`contract_only` 或执行失败时，报告会明确标注未获得真实执行证据。',
    '',
    '## 人工确认项',
    '',
    ...(humanTasks.items || []).map((item) => `- [${item.status}] ${item.title}: ${item.reason}`)
  ].join('\n');

  return { reportJson, md };
}

async function generateV3Report(runId, options = {}) {
  const { runDir, manifest } = await readManifest(runId, options);
  const pageEntries = await readJsonIfExists(path.join(runDir, ARTIFACTS.page_entries)) || { items: [] };
  const featurePoints = await readJsonIfExists(path.join(runDir, ARTIFACTS.feature_points)) || { items: [] };
  const testCases = await readJsonIfExists(path.join(runDir, ARTIFACTS.generated_test_cases)) || { items: [] };
  const executionResults = await readJsonIfExists(path.join(runDir, ARTIFACTS.execution_results)) || { items: [] };
  const roundAnalysis = await readJsonIfExists(path.join(runDir, ARTIFACTS.round_analysis)) || { improvement_candidates: [] };
  const nextRoundPlan = await readJsonIfExists(path.join(runDir, ARTIFACTS.next_round_plan)) || { decision: 'unknown' };
  const humanTasks = await readJsonIfExists(path.join(runDir, ARTIFACTS.human_tasks)) || { items: [] };
  const { reportJson, md } = makeReport({
    manifest,
    pageEntries,
    featurePoints,
    testCases,
    executionResults,
    roundAnalysis,
    nextRoundPlan,
    humanTasks
  });
  await writeJson(path.join(runDir, ARTIFACTS.run_report_json), reportJson);
  await fs.writeFile(path.join(runDir, ARTIFACTS.run_report_md), `${md}\n`, 'utf8');
  const saved = await updateRunStatus(runDir, manifest, manifest.status, 'reporting', 'run 报告已生成。');
  const run = buildPublicRun(runDir, saved, await loadRunArtifacts(runDir));
  return { run, operation: makeOperationFeedback('generate_report', run), report: reportJson };
}

async function resolveV3RunArtifact(runId, artifactKey, options = {}) {
  const { runDir } = await readManifest(runId, options);
  const filePath = artifactPath(runDir, artifactKey);
  if (!(await pathExists(filePath))) {
    return null;
  }
  return {
    key: artifactKey,
    path: filePath,
    fileName: path.basename(filePath)
  };
}

module.exports = {
  ARTIFACTS,
  Stage2V3InputError,
  analyzeV3Run,
  checkBrowserPreflight,
  checkStage2ModelProfiles,
  continueNextRound,
  createV3Run,
  generateV3Report,
  getV3Run,
  listV3Runs,
  resolveV3RunArtifact,
  saveHumanTaskResult,
  setV3RunLifecycleStatus,
  startV3Run
};
