const crypto = require('crypto');
const fs = require('fs/promises');
const path = require('path');

const ROOT_DIR = path.join(__dirname, '..');
const DEFAULT_STAGE2_RUNS_DIR = path.join(ROOT_DIR, 'artifacts', 'stage2', 'runs');
const DEFAULT_CDP_URL = 'http://localhost:9222/';
const SAFE_ID_PATTERN = /^[A-Za-z0-9_-]{1,120}$/;
const LOCAL_CDP_HOSTS = new Set(['localhost', '127.0.0.1', '::1']);

const ARTIFACTS = {
  run_manifest: 'run_manifest.json',
  input_config: 'input_config.json',
  progress_events: 'progress_events.jsonl',
  current_status: 'current_status.json',
  preflight_result: 'preflight_result.json',
  system_map: 'system_map.json',
  navigation_tree: 'navigation_tree.json',
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

function normalizeInputConfig(body = {}) {
  const systemName = normalizeText(body.systemName || body.system_name || body.targetName, '未命名系统');
  const entryUrl = normalizeHttpUrl(body.entryUrl || body.entry_url || body.homeUrl || body.pageUrl, 'entryUrl');

  return {
    schema_version: 'stage2_input_config.v3',
    system_name: systemName,
    entry_url: entryUrl,
    cdp_url: normalizeCdpUrl(body.cdpUrl || body.cdp_url),
    test_account_note: normalizeOptionalText(body.testAccountNote || body.test_account_note || body.accountNotes),
    login_mode: normalizeText(body.loginMode || body.login_mode, 'human_takeover_or_existing_session'),
    scope: normalizeOptionalText(body.scope || body.scopeText || body.explorationScope),
    safety_policy: normalizeText(body.safetyPolicy || body.safety_policy, 'low_risk_only'),
    max_pages: normalizeInteger(body.maxPages || body.max_pages, 30, 1, 200),
    max_features_per_page: normalizeInteger(body.maxFeaturesPerPage || body.max_features_per_page, 30, 1, 200),
    auto_continue: body.autoContinue === true || body.auto_continue === true,
    created_from: normalizeText(body.createdFrom || body.created_from, 'run_center_v3')
  };
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

function summarizeExecution(items = []) {
  const byStatus = {};
  for (const item of items) {
    const status = item.status || 'unknown';
    byStatus[status] = (byStatus[status] || 0) + 1;
  }
  return {
    total: items.length,
    passed: byStatus.passed || 0,
    failed: byStatus.failed || 0,
    skipped: byStatus.skipped || 0,
    needs_review: byStatus.needs_review || 0,
    by_status: byStatus
  };
}

function buildPublicRun(runDir, manifest, artifacts = {}) {
  const executionItems = artifacts.execution_results?.items || [];
  return {
    runId: manifest.run_id,
    systemName: manifest.system_name,
    entryUrl: manifest.entry_url,
    status: manifest.status,
    currentRoundId: manifest.current_round_id,
    createdAt: manifest.created_at,
    updatedAt: manifest.updated_at,
    startedAt: manifest.started_at,
    finishedAt: manifest.finished_at,
    rounds: manifest.rounds || [],
    summary: {
      pageEntries: summarizeItems(artifacts.page_entries?.items),
      featurePoints: summarizeItems(artifacts.feature_points?.items),
      generatedTestCases: summarizeItems(artifacts.generated_test_cases?.items),
      execution: summarizeExecution(executionItems),
      pendingHumanTasks: (artifacts.human_tasks?.items || []).filter((item) => item.status === 'pending').length,
      nextDecision: artifacts.next_round_plan?.decision || null
    },
    artifacts: artifactRefs(manifest.run_id)
  };
}

async function loadRunArtifacts(runDir) {
  const keys = [
    'input_config',
    'current_status',
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
  return Object.fromEntries(entries);
}

async function createV3Run(body = {}, options = {}) {
  const runsDir = getRunsDir(options);
  const runId = createRunId();
  const runDir = path.join(runsDir, runId);
  const createdAt = nowIso();
  const inputConfig = normalizeInputConfig(body);
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

  return { run: buildPublicRun(runDir, savedManifest, await loadRunArtifacts(runDir)) };
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

function makePreflightResult(manifest, inputConfig) {
  return {
    schema_version: 'stage2_preflight_result.v3',
    run_id: manifest.run_id,
    status: 'needs_executor',
    checks: {
      input_config: { ok: true },
      cdp_url: { ok: true, url: inputConfig.cdp_url },
      python_orchestrator: {
        ok: false,
        reason: 'Node v3 API 已建立 run 契约；真实 Python v3 orchestrator 仍需接入。'
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

function makeExecutionResults(testCases) {
  return {
    schema_version: 'stage2_execution_results.v3',
    items: testCases.items.map((testCase) => ({
      test_case_id: testCase.test_case_id,
      status: testCase.requires_human_confirmation ? 'needs_review' : 'skipped',
      verdict: testCase.requires_human_confirmation
        ? '需要人工确认后才能执行。'
        : '已生成执行型测试用例，等待 Python v3 执行器接入后自动执行。',
      actions: [],
      page_feedback: [],
      screenshot_refs: [],
      failure_reason: testCase.requires_human_confirmation
        ? 'manual_review_required'
        : 'python_v3_executor_not_connected',
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
    if (result.status === 'passed') {
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

  const skipped = (executionResults.items || []).filter((item) => item.failure_reason === 'python_v3_executor_not_connected');
  if (skipped.length) {
    tasks.push({
      task_id: 'task_connect_executor_or_confirm_plan',
      task_type: 'review_next_round_plan',
      title: '确认下一轮执行计划',
      reason: '后端已生成 v3 run 产物契约，但真实浏览器执行需由 Python v3 orchestrator 接入。',
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

function makeRoundAnalysis({ manifest, pageEntries, featurePoints, testCases, executionResults, screenshotsIndex }) {
  const executionSummary = summarizeExecution(executionResults.items || []);
  const failureClusters = groupFailures(executionResults);
  const humanTaskReasons = failureClusters
    .filter((cluster) => ['manual_review_required', 'python_v3_executor_not_connected'].includes(cluster.reason))
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
        title: '接入 Python v3 orchestrator 后替换占位执行结果',
        confidence: 0.95,
        evidence_refs: ['execution_results']
      }
    ],
    confidence: 0.72
  };

  const nextRoundPlan = {
    schema_version: 'stage2_next_round_plan.v3',
    current_round_id: analysis.round_id,
    should_continue: false,
    decision: failureClusters.length ? 'wait_human_review' : 'stop_goal_completed',
    next_round_goal: failureClusters.length
      ? '接入真实执行器或由人工确认计划后，执行低风险用例并补齐截图证据。'
      : '本 run 已完成当前范围。',
    target_page_entry_ids: (pageEntries.items || []).map((item) => item.page_entry_id),
    target_feature_point_ids: (featurePoints.items || [])
      .filter((item) => item.auto_verifiable)
      .map((item) => item.feature_point_id),
    planned_improvements: analysis.improvement_candidates,
    risk_level: 'low',
    requires_human_approval: failureClusters.length > 0
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

async function updateRunStatus(runDir, manifest, status, phase, message, extra = {}) {
  const updated = {
    ...manifest,
    status,
    updated_at: nowIso()
  };
  const saved = await saveManifest(runDir, updated);
  await writeJson(path.join(runDir, ARTIFACTS.current_status), buildCurrentStatus(saved, phase, message, extra));
  await appendEvent(runDir, { type: 'status_changed', status, phase, message });
  return saved;
}

async function startV3Run(runId, body = {}, options = {}) {
  const { runDir, manifest } = await readManifest(runId, options);
  const inputConfig = await readJsonRequired(path.join(runDir, ARTIFACTS.input_config), 'input_config 缺失。');
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
    started_at: manifest.started_at || startedAt,
    updated_at: startedAt,
    current_round_id: roundId,
    rounds: [...(manifest.rounds || []).filter((item) => item.round_id !== roundId), nextRound]
  };
  runningManifest = await saveManifest(runDir, runningManifest);
  await writeJson(path.join(runDir, ARTIFACTS.current_status), buildCurrentStatus(
    runningManifest,
    'running_contract_pipeline',
    '正在生成 v3 run 稳定产物契约。'
  ));
  await appendEvent(runDir, { type: 'run_started', run_id: runId, round_id: roundId });

  const preflightResult = makePreflightResult(runningManifest, inputConfig);
  const { systemMap, navigationTree, pageEntries } = makeDiscoveryArtifacts(runningManifest, inputConfig);
  const featurePoints = makeFeatureArtifacts(inputConfig);
  const discoveryReview = makeDiscoveryReview(featurePoints);
  const testCases = makeGeneratedTestCases(featurePoints);
  const executionResults = makeExecutionResults(testCases);
  const screenshotsIndex = makeScreenshotsIndex();
  const analysisPack = makeRoundAnalysis({
    manifest: runningManifest,
    pageEntries,
    featurePoints,
    testCases,
    executionResults,
    screenshotsIndex
  });

  await writeJson(path.join(runDir, ARTIFACTS.preflight_result), preflightResult);
  await writeJson(path.join(runDir, ARTIFACTS.system_map), systemMap);
  await writeJson(path.join(runDir, ARTIFACTS.navigation_tree), navigationTree);
  await writeJson(path.join(runDir, ARTIFACTS.page_entries), pageEntries);
  await writeJson(path.join(runDir, ARTIFACTS.feature_points), featurePoints);
  await writeJson(path.join(runDir, ARTIFACTS.discovery_review), discoveryReview);
  await writeJson(path.join(runDir, ARTIFACTS.generated_test_cases), testCases);
  await writeJson(path.join(runDir, ARTIFACTS.execution_results), executionResults);
  await writeJson(path.join(runDir, ARTIFACTS.screenshots_index), screenshotsIndex);
  await persistAnalysisPack(runDir, analysisPack);

  const completedRound = {
    ...nextRound,
    finished_at: nowIso(),
    output_artifacts: [
      'preflight_result',
      'system_map',
      'navigation_tree',
      'page_entries',
      'feature_points',
      'discovery_review',
      'generated_test_cases',
      'execution_results',
      'round_analysis',
      'next_round_plan',
      'human_tasks'
    ],
    status: analysisPack.nextRoundPlan.requires_human_approval ? 'waiting_human' : 'completed'
  };
  const finalStatus = analysisPack.nextRoundPlan.requires_human_approval ? 'waiting_human' : 'completed';
  const finalMessage = analysisPack.nextRoundPlan.requires_human_approval
    ? '首轮产物已生成，等待运行中心人工确认下一步。'
    : '首轮产物已生成。';
  const finalManifest = await updateRunStatus(
    runDir,
    {
      ...runningManifest,
      rounds: [...(runningManifest.rounds || []).filter((item) => item.round_id !== roundId), completedRound]
    },
    finalStatus,
    'round_analysis',
    finalMessage,
    { decision: analysisPack.nextRoundPlan.decision }
  );

  return { run: buildPublicRun(runDir, finalManifest, await loadRunArtifacts(runDir)) };
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
  return { run: buildPublicRun(runDir, saved, await loadRunArtifacts(runDir)) };
}

async function analyzeV3Run(runId, options = {}) {
  const { runDir, manifest } = await readManifest(runId, options);
  const pageEntries = await readJsonRequired(path.join(runDir, ARTIFACTS.page_entries), 'page_entries 缺失，不能复盘。');
  const featurePoints = await readJsonRequired(path.join(runDir, ARTIFACTS.feature_points), 'feature_points 缺失，不能复盘。');
  const testCases = await readJsonRequired(path.join(runDir, ARTIFACTS.generated_test_cases), 'generated_test_cases 缺失，不能复盘。');
  const executionResults = await readJsonRequired(path.join(runDir, ARTIFACTS.execution_results), 'execution_results 缺失，不能复盘。');
  const screenshotsIndex = await readJsonIfExists(path.join(runDir, ARTIFACTS.screenshots_index)) || makeScreenshotsIndex();

  const analysisPack = makeRoundAnalysis({
    manifest,
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
    'ai_round_analysis',
    'AI 复盘产物已生成。',
    { decision: analysisPack.nextRoundPlan.decision }
  );
  return {
    run: buildPublicRun(runDir, saved, await loadRunArtifacts(runDir)),
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
  return { run: buildPublicRun(runDir, saved, await loadRunArtifacts(runDir)), humanTasks: nextHumanTasks };
}

async function continueNextRound(runId, body = {}, options = {}) {
  const { runDir, manifest } = await readManifest(runId, options);
  const nextRoundPlan = await readJsonRequired(path.join(runDir, ARTIFACTS.next_round_plan), 'next_round_plan 缺失。');
  const approved = body.approved === true || body.decision === 'approve';
  if (nextRoundPlan.requires_human_approval && !approved) {
    const saved = await updateRunStatus(
      runDir,
      manifest,
      'waiting_human',
      'next_round_blocked',
      '下一轮计划需要人工批准后才能继续。',
      { decision: nextRoundPlan.decision }
    );
    return { run: buildPublicRun(runDir, saved, await loadRunArtifacts(runDir)), nextRoundPlan };
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
  return { run: buildPublicRun(runDir, saved, await loadRunArtifacts(runDir)), nextRoundPlan };
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
      page_entries: summarizeItems(pageEntries.items),
      feature_points: summarizeItems(featurePoints.items),
      generated_test_cases: summarizeItems(testCases.items),
      execution: executionSummary,
      pending_human_tasks: (humanTasks.items || []).filter((item) => item.status === 'pending').length,
      next_decision: nextRoundPlan.decision
    },
    sections: [
      { title: '运行概况', facts: [{ label: 'run_id', value: manifest.run_id }, { label: 'status', value: manifest.status }] },
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
    `- 页面入口: ${reportJson.summary.page_entries}`,
    `- 功能点: ${reportJson.summary.feature_points}`,
    `- 执行型测试用例: ${reportJson.summary.generated_test_cases}`,
    `- 执行结果: passed ${executionSummary.passed}, failed ${executionSummary.failed}, skipped ${executionSummary.skipped}, needs_review ${executionSummary.needs_review}`,
    `- 下一轮决策: ${nextRoundPlan.decision}`,
    '',
    '## 说明',
    '',
    '本报告由 Node.js v3 Run API 基于稳定 artifact 契约生成。当前后端不会替代 Python v3 执行器执行真实浏览器动作；真实执行证据接入后会补齐截图、动作日志和页面反馈。',
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
  return { run: buildPublicRun(runDir, saved, await loadRunArtifacts(runDir)), report: reportJson };
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
