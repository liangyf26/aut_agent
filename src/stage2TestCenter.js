const fs = require('fs/promises');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { execFile } = require('child_process');
const {
  resolvePythonCommand,
  execFileWithCapture
} = require('./stage2OperationCenter');

const ROOT_DIR = path.join(__dirname, '..');
const STAGE2_DIR = path.join(ROOT_DIR, 'artifacts', 'stage2');
const TEST_CENTER_DIR = path.join(STAGE2_DIR, 'test_center_runs');
const TESTS_DIR = path.join(ROOT_DIR, 'prototype', 'stage2', 'tests');
const DEFAULT_CDP_URL = process.env.STAGE2_CDP_URL || 'http://localhost:9222';
const DEFAULT_MAX_BUFFER = 20 * 1024 * 1024;
const SAFE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$/;
const SAFE_RUN_ID_PATTERN = /^tc_\d{8}_\d{6}_[a-f0-9]{8}$/;
const LOCAL_CDP_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);

class TestCenterInputError extends Error {
  constructor(message) {
    super(message);
    this.statusCode = 400;
  }
}

// menu/page/feature 三个发现阶段的 pytest 文件不含 real_browser 冒烟测试
// 之外真正的单元/集成测试；execution 阶段额外把人工接管解决的回归测试
// 一起纳入，因为它是阶段E缺口修复新增的、与 execution_goal 共享同一产物目录。
const UNIT_TEST_SUITES = {
  menu: {
    label: '菜单发现（阶段B）单元测试',
    files: [
      'test_menu_goal_loader.py',
      'test_menu_goal_classifier.py',
      'test_menu_goal_discovery_adapter.py',
      'test_menu_goal_fixture_writer.py',
      'test_menu_goal_orchestrator.py'
    ]
  },
  page: {
    label: '页面发现（阶段C）单元测试',
    files: ['test_page_goal_integration.py']
  },
  feature: {
    label: '功能点发现（阶段D）单元测试',
    files: ['test_feature_goal_integration.py']
  },
  execution: {
    label: '执行（阶段E）单元测试',
    files: ['test_execution_goal_integration.py', 'test_resolve_goal_loop_takeover.py']
  },
  real_browser_smoke: {
    label: '真实浏览器冒烟测试（需本机 Playwright，未安装会被跳过）',
    files: [
      'test_menu_goal_real_browser_smoke.py',
      'test_page_goal_real_browser_smoke.py',
      'test_feature_goal_real_browser_smoke.py',
      'test_execution_goal_real_browser_smoke.py'
    ]
  }
};

function nowIso() {
  return new Date().toISOString();
}

async function pathExists(filePath) {
  try {
    await fs.access(filePath);
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

async function writeJson(filePath, payload) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
}

function isWithinDir(filePath, parentDir) {
  const resolvedPath = path.resolve(filePath);
  const resolvedParent = path.resolve(parentDir);
  return resolvedPath === resolvedParent || resolvedPath.startsWith(`${resolvedParent}${path.sep}`);
}

function relativeArtifact(filePath) {
  return path.relative(ROOT_DIR, filePath).replace(/\\/g, '/');
}

function trimPreview(value, limit = 4000) {
  const text = String(value || '').trim();
  return text.length > limit ? `${text.slice(0, limit)}...` : text;
}

function requireHttpUrl(value, fieldName) {
  const text = String(value || '').trim();
  try {
    const url = new URL(text);
    if (!['http:', 'https:'].includes(url.protocol)) {
      throw new Error('invalid protocol');
    }
    return url.toString();
  } catch {
    throw new TestCenterInputError(`${fieldName} 必须是 http(s) URL。`);
  }
}

function normalizeCdpUrl(value) {
  const normalized = requireHttpUrl(value || DEFAULT_CDP_URL, 'cdpUrl');
  const url = new URL(normalized);
  if (process.env.STAGE2_ALLOW_REMOTE_CDP !== '1' && !LOCAL_CDP_HOSTS.has(url.hostname)) {
    throw new TestCenterInputError('cdpUrl 只允许 localhost、127.0.0.1 或 ::1；如需远程调试端口，请先显式配置白名单。');
  }
  return normalized;
}

function requireInt(value, fieldName, min, max, fallback) {
  if (value === undefined || value === null || value === '') {
    return fallback;
  }
  const number = Number(value);
  if (!Number.isInteger(number) || number < min || number > max) {
    throw new TestCenterInputError(`${fieldName} 必须是 ${min} 到 ${max} 之间的整数。`);
  }
  return number;
}

function requireSafeId(value, fieldName) {
  const text = String(value || '').trim();
  if (!SAFE_ID_PATTERN.test(text)) {
    throw new TestCenterInputError(`${fieldName} 只能包含字母、数字、下划线或连字符，并且必须以字母或数字开头。`);
  }
  return text;
}

function resolveSafeArtifactPath(value, fieldName, stage2Dir = STAGE2_DIR) {
  const text = String(value || '').trim();
  if (!text) {
    throw new TestCenterInputError(`${fieldName} 不能为空。`);
  }
  const candidate = path.isAbsolute(text) ? path.resolve(text) : path.resolve(ROOT_DIR, text);
  if (!isWithinDir(candidate, stage2Dir)) {
    throw new TestCenterInputError(`${fieldName} 必须位于 artifacts/stage2 目录内。`);
  }
  return candidate;
}

function createRunId() {
  const stamp = new Date()
    .toISOString()
    .replace(/[-:]/g, '')
    .replace('T', '_')
    .slice(0, 15);
  return `tc_${stamp}_${crypto.randomBytes(4).toString('hex')}`;
}

// ---------------------------------------------------------------------------
// junit-xml 解析：pytest 自带 --junit-xml，无需额外依赖（package.json 目前零
// 依赖）。只需要 <testcase classname name time> 及其子节点是否有
// <failure>/<error>/<skipped>，不需要完整 XML DOM，正则足够可靠。
// ---------------------------------------------------------------------------

function decodeXmlEntities(value) {
  return String(value || '')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(Number(code)))
    .replace(/&#x([0-9a-fA-F]+);/g, (_, code) => String.fromCharCode(parseInt(code, 16)))
    .replace(/&amp;/g, '&');
}

function extractAttr(tag, name) {
  // Leading \s (not \b) matters: "classname=" contains "name=" as a
  // substring, and \b sits between word chars too loosely for this case
  // when preceded by another attribute value ending in a word char.
  const match = tag.match(new RegExp(`(?:^|\\s)${name}="([^"]*)"`));
  return match ? decodeXmlEntities(match[1]) : '';
}

function parseJUnitXml(xmlText) {
  const text = String(xmlText || '');
  const testcases = [];
  const testcaseRegex = /<testcase\b([^>]*?)(\/>|>([\s\S]*?)<\/testcase>)/g;
  let match;
  while ((match = testcaseRegex.exec(text)) !== null) {
    const [, attrsRaw, , body] = match;
    const classname = extractAttr(attrsRaw, 'classname');
    const name = extractAttr(attrsRaw, 'name');
    const timeText = extractAttr(attrsRaw, 'time');
    const time = timeText ? Number(timeText) : null;
    const innerBody = body || '';

    let status = 'passed';
    let message = '';
    const failureMatch = innerBody.match(/<failure\b([^>]*)>([\s\S]*?)<\/failure>|<failure\b([^/]*)\/>/);
    const errorMatch = innerBody.match(/<error\b([^>]*)>([\s\S]*?)<\/error>|<error\b([^/]*)\/>/);
    const skippedMatch = innerBody.match(/<skipped\b([^>]*)>([\s\S]*?)<\/skipped>|<skipped\b([^/]*)\/>/);

    if (failureMatch) {
      status = 'failed';
      message = decodeXmlEntities(extractAttr(failureMatch[1] || '', 'message')) || decodeXmlEntities(failureMatch[2] || '').trim();
    } else if (errorMatch) {
      status = 'failed';
      message = decodeXmlEntities(extractAttr(errorMatch[1] || '', 'message')) || decodeXmlEntities(errorMatch[2] || '').trim();
    } else if (skippedMatch) {
      status = 'skipped';
      message = decodeXmlEntities(extractAttr(skippedMatch[1] || '', 'message')) || decodeXmlEntities(skippedMatch[2] || '').trim();
    }

    testcases.push({
      classname,
      name,
      time,
      status,
      message: trimPreview(message, 1000)
    });
  }

  const totals = testcases.reduce(
    (acc, testcase) => {
      acc[testcase.status] = (acc[testcase.status] || 0) + 1;
      acc.total += 1;
      return acc;
    },
    { total: 0, passed: 0, failed: 0, skipped: 0 }
  );

  return { testcases, totals };
}

// ---------------------------------------------------------------------------
// 单元测试执行
// ---------------------------------------------------------------------------

async function runUnitTestSuite(kind, dependencies = {}) {
  const suite = UNIT_TEST_SUITES[kind];
  if (!suite) {
    throw new TestCenterInputError(`不支持的单元测试分组：${kind}`);
  }

  const pythonCommand = await (dependencies.resolvePythonCommand || resolvePythonCommand)();
  const junitPath = path.join(
    await fs.mkdtemp(path.join(os.tmpdir(), 'stage2-test-center-')),
    'junit.xml'
  );
  const args = [
    '-m',
    'pytest',
    ...suite.files.map((file) => path.join('prototype', 'stage2', 'tests', file)),
    '-v',
    `--junit-xml=${junitPath}`
  ];

  const startedAt = nowIso();
  const runner = dependencies.execFileRunner;
  const commandRun = await execFileWithCapture(
    pythonCommand,
    args,
    {
      cwd: ROOT_DIR,
      windowsHide: true,
      timeout: 10 * 60_000,
      maxBuffer: DEFAULT_MAX_BUFFER
    },
    runner
  );
  const finishedAt = nowIso();

  const junitXml = await fs.readFile(junitPath, 'utf8').catch(() => '');
  await fs.rm(path.dirname(junitPath), { recursive: true, force: true }).catch(() => {});

  const parsed = junitXml ? parseJUnitXml(junitXml) : { testcases: [], totals: { total: 0, passed: 0, failed: 0, skipped: 0 } };

  return {
    kind,
    label: suite.label,
    files: suite.files,
    command: { executable: pythonCommand, args },
    startedAt,
    finishedAt,
    exitCode: commandRun.error?.code ?? 0,
    durationMs: commandRun.durationMs,
    stdoutPreview: trimPreview(commandRun.stdout),
    stderrPreview: trimPreview(commandRun.stderr),
    testcases: parsed.testcases,
    totals: parsed.totals,
    overallStatus: parsed.totals.failed > 0 ? 'failed' : (parsed.totals.total === 0 ? 'unknown' : 'passed')
  };
}

// ---------------------------------------------------------------------------
// 目标循环链式阶段（menu -> page -> feature -> execution）
// ---------------------------------------------------------------------------

function stage2BaseArgs(flag) {
  return ['-m', 'prototype.stage2.main', flag];
}

const GOAL_CHAIN_STAGES = {
  menu: {
    id: 'menu',
    label: '菜单发现（阶段B）',
    flag: '--run-menu-goal',
    timeoutMs: 10 * 60_000,
    fields: [
      { name: 'cdpUrl', label: 'CDP URL', help: '已登录目标系统的 Chrome 远程调试地址，默认 http://localhost:9222。' },
      { name: 'maxPages', label: '页面/菜单展开预算', help: '菜单展开与页面探索的预算上限，超过后停止发现，默认 5。' }
    ],
    buildArgs: (params, _context) => {
      const args = stage2BaseArgs('--run-menu-goal');
      args.push('--cdp-url', normalizeCdpUrl(params.cdpUrl));
      if (params.runId) {
        args.push('--goal-chain-run-id', requireSafeId(params.runId, 'runId'));
      }
      args.push('--goal-chain-max-pages', String(requireInt(params.maxPages, 'maxPages', 1, 100, 5)));
      return args;
    },
    outputDir: (runId, stage2Dir) => path.join(stage2Dir, 'menu_goal_runs', runId),
    chainOutputKey: 'menu_entries_raw_path'
  },
  page: {
    id: 'page',
    label: '页面发现（阶段C）',
    flag: '--run-page-goal',
    timeoutMs: 10 * 60_000,
    fields: [
      { name: 'cdpUrl', label: 'CDP URL', help: '已登录目标系统的 Chrome 远程调试地址。' },
      { name: 'menuEntriesPath', label: 'menu_entries_raw.json 路径', help: '上一步菜单发现写出的原始条目文件（端到端模式下自动填充）。' },
      { name: 'maxPages', label: '页面探索预算', help: '默认 5。' },
      { name: 'maxFeaturesPerPage', label: '每页功能点预算', help: '默认 6。' }
    ],
    buildArgs: (params, context) => {
      const args = stage2BaseArgs('--run-page-goal');
      args.push('--goal-chain-menu-entries', resolveSafeArtifactPath(params.menuEntriesPath, 'menuEntriesPath', context.stage2Dir));
      args.push('--cdp-url', normalizeCdpUrl(params.cdpUrl));
      if (params.runId) {
        args.push('--goal-chain-run-id', requireSafeId(params.runId, 'runId'));
      }
      args.push('--goal-chain-max-pages', String(requireInt(params.maxPages, 'maxPages', 1, 100, 5)));
      args.push('--goal-chain-max-features-per-page', String(requireInt(params.maxFeaturesPerPage, 'maxFeaturesPerPage', 1, 100, 6)));
      return args;
    },
    outputDir: (runId, stage2Dir) => path.join(stage2Dir, 'page_goal_runs', runId),
    chainOutputKey: 'page_entries_path'
  },
  feature: {
    id: 'feature',
    label: '功能点发现（阶段D）',
    flag: '--run-feature-goal',
    timeoutMs: 10 * 60_000,
    fields: [
      { name: 'cdpUrl', label: 'CDP URL', help: '已登录目标系统的 Chrome 远程调试地址。' },
      { name: 'pageEntriesPath', label: 'page_entries.json 路径', help: '上一步页面发现写出的产物（端到端模式下自动填充），仅 status=reachable 的条目会被处理。' },
      { name: 'maxFeaturesPerPage', label: '每页功能点预算', help: '默认 6。' }
    ],
    buildArgs: (params, context) => {
      const args = stage2BaseArgs('--run-feature-goal');
      args.push('--goal-chain-page-entries', resolveSafeArtifactPath(params.pageEntriesPath, 'pageEntriesPath', context.stage2Dir));
      args.push('--cdp-url', normalizeCdpUrl(params.cdpUrl));
      if (params.runId) {
        args.push('--goal-chain-run-id', requireSafeId(params.runId, 'runId'));
      }
      args.push('--goal-chain-max-features-per-page', String(requireInt(params.maxFeaturesPerPage, 'maxFeaturesPerPage', 1, 100, 6)));
      return args;
    },
    outputDir: (runId, stage2Dir) => path.join(stage2Dir, 'feature_goal_runs', runId),
    chainOutputKey: 'generated_test_cases_path'
  },
  execution: {
    id: 'execution',
    label: '执行（阶段E）',
    flag: '--run-execution-goal',
    timeoutMs: 10 * 60_000,
    fields: [
      { name: 'cdpUrl', label: 'CDP URL', help: '仅 mode=real_browser 时需要。' },
      { name: 'testCasesPath', label: 'generated_test_cases.json 路径', help: '上一步功能点发现写出的产物（端到端模式下自动填充）。' },
      { name: 'mode', label: '执行模式', help: 'fixture_simulated（默认，纯模拟，不碰真实系统，安全）或 real_browser（真实点击/填表/提交生产系统，需谨慎）。' },
      { name: 'maxRounds', label: '自动重试轮次', help: '仅 fixture_simulated 生效：允许自动重试可重试失败（如 LOCATOR_UNSTABLE）的轮次数，默认 1；real_browser 恒定跑 1 轮，不受此参数影响。' }
    ],
    buildArgs: (params, context) => {
      const args = stage2BaseArgs('--run-execution-goal');
      args.push('--execution-goal-test-cases', resolveSafeArtifactPath(params.testCasesPath, 'testCasesPath', context.stage2Dir));
      const mode = params.mode === 'real_browser' ? 'real_browser' : 'fixture_simulated';
      args.push('--execution-goal-mode', mode);
      if (mode === 'real_browser') {
        args.push('--cdp-url', normalizeCdpUrl(params.cdpUrl));
      }
      const executionRunId = params.runId || `execution_goal_${mode}_run`;
      args.push('--execution-goal-run-id', requireSafeId(executionRunId, 'runId'));
      args.push('--execution-goal-max-rounds', String(requireInt(params.maxRounds, 'maxRounds', 1, 20, 1)));
      return args;
    },
    outputDir: (runId, stage2Dir) => path.join(stage2Dir, 'execution_goal_runs', runId),
    chainOutputKey: null
  }
};

const GOAL_CHAIN_ORDER = ['menu', 'page', 'feature', 'execution'];

function defaultRunIdFor(stageId) {
  const fallback = {
    menu: 'menu_goal_real_browser_run',
    page: 'page_goal_real_browser_run',
    feature: 'feature_goal_real_browser_run',
    execution: 'execution_goal_real_browser_run'
  };
  return fallback[stageId];
}

async function runGoalChainStage(stageId, params = {}, dependencies = {}) {
  const stage = GOAL_CHAIN_STAGES[stageId];
  if (!stage) {
    throw new TestCenterInputError(`不支持的 goal-chain 阶段：${stageId}`);
  }

  const stage2Dir = dependencies.stage2Dir || STAGE2_DIR;
  const pythonCommand = await (dependencies.resolvePythonCommand || resolvePythonCommand)();
  const args = stage.buildArgs(params, { stage2Dir });
  const startedAt = nowIso();
  const commandRun = await execFileWithCapture(
    pythonCommand,
    args,
    {
      cwd: ROOT_DIR,
      windowsHide: true,
      timeout: stage.timeoutMs,
      maxBuffer: DEFAULT_MAX_BUFFER
    },
    dependencies.execFileRunner
  );
  const finishedAt = nowIso();

  let parsedStdout = null;
  const trimmedStdout = String(commandRun.stdout || '').trim();
  if (trimmedStdout) {
    try {
      parsedStdout = JSON.parse(trimmedStdout);
    } catch {
      parsedStdout = null;
    }
  }

  const runId = params.runId
    ? requireSafeId(params.runId, 'runId')
    : (stageId === 'execution' && params.mode)
      ? `execution_goal_${params.mode}_run`
      : defaultRunIdFor(stageId);
  const outputDir = stage.outputDir(runId, stage2Dir);
  const runSummary = await readJsonIfExists(path.join(outputDir, 'run_summary.json'));
  const humanTakeover = await readJsonIfExists(path.join(outputDir, 'human_takeover.json'));
  const humanTakeoverResolution = await readJsonIfExists(path.join(outputDir, 'human_takeover_resolution.json'));
  const runReport = await readJsonIfExists(path.join(outputDir, 'reports', 'run_report.json'));

  const evaluation = evaluateGoalLoopStepResult({
    stageId,
    exitCode: commandRun.error?.code ?? 0,
    runSummary,
    humanTakeover,
    humanTakeoverResolution,
    runReport
  });

  return {
    stageId,
    label: stage.label,
    runId,
    outputDir: relativeArtifact(outputDir),
    command: { executable: pythonCommand, args },
    startedAt,
    finishedAt,
    durationMs: commandRun.durationMs,
    exitCode: commandRun.error?.code ?? 0,
    stdoutPreview: trimPreview(commandRun.stdout),
    stderrPreview: trimPreview(commandRun.stderr),
    parsedStdout,
    runSummary,
    humanTakeover,
    humanTakeoverResolution,
    runReport,
    evaluation,
    chainOutputPath: stage.chainOutputKey && parsedStdout
      ? parsedStdout[stage.chainOutputKey] || null
      : null
  };
}

// ---------------------------------------------------------------------------
// 结果评价规则：单元测试和端到端测试的结果展示都调用这一套，不重复定义。
// ---------------------------------------------------------------------------

const HUMAN_REQUIRED_GOAL_STATUSES = new Set(['waiting_human', 'blocked_by_policy', 'blocked_by_executor']);
const FAILED_GOAL_STATUSES = new Set(['failed_max_rounds', 'stopped_no_progress']);

function evaluateGoalLoopStepResult({ stageId, exitCode, runSummary, humanTakeover, humanTakeoverResolution, runReport }) {
  if (exitCode) {
    return {
      verdict: 'failed',
      reason: `命令以非零退出码 ${exitCode} 结束，视为该步骤失败。`
    };
  }

  if (humanTakeover && humanTakeover.status === 'waiting_human') {
    const resolvedReady = Boolean(humanTakeoverResolution && humanTakeoverResolution.ready_to_resume);
    if (!resolvedReady) {
      return {
        verdict: 'needs_human',
        reason: humanTakeover.waiting_reason
          ? `存在未处理的人工接管请求（${humanTakeover.waiting_reason}），需人工介入，不计入失败。`
          : '存在未处理的人工接管请求，需人工介入，不计入失败。'
      };
    }
  }

  if (runReport && runReport.summary) {
    const reportStatus = runReport.summary.status;
    if (reportStatus === 'needs_review') {
      return { verdict: 'needs_human', reason: 'run_report.summary.status=needs_review：存在暂停项待人工复核。' };
    }
    if (reportStatus === 'completed_with_failures') {
      return { verdict: 'failed', reason: 'run_report.summary.status=completed_with_failures：存在执行失败的用例。' };
    }
    if (reportStatus === 'completed') {
      return { verdict: 'passed', reason: 'run_report.summary.status=completed：全部执行完成且无失败。' };
    }
  }

  if (runSummary) {
    const rootConclusion = runSummary.root_conclusion || runSummary.stop_reason;
    if (typeof runSummary.failed === 'number' && runSummary.failed > 0) {
      return { verdict: 'failed', reason: `run_summary.failed=${runSummary.failed}：存在失败目标。` };
    }
    if (rootConclusion && FAILED_GOAL_STATUSES.has(rootConclusion)) {
      return { verdict: 'failed', reason: `root_conclusion=${rootConclusion}：判定失败。` };
    }
    if (rootConclusion && HUMAN_REQUIRED_GOAL_STATUSES.has(rootConclusion)) {
      return { verdict: 'needs_human', reason: `root_conclusion=${rootConclusion}：需人工介入。` };
    }
    if (typeof runSummary.succeeded === 'number' && runSummary.succeeded > 0) {
      return { verdict: 'passed', reason: `run_summary.succeeded=${runSummary.succeeded}：至少一个目标成功，无失败/人工项。` };
    }
  }

  return { verdict: 'unknown', reason: '未找到足以判断通过/失败的产物字段（run_summary/run_report 均缺失或字段不足）。' };
}

// ---------------------------------------------------------------------------
// 端到端链式执行：menu -> page -> feature -> execution，非 passed 就停止。
// ---------------------------------------------------------------------------

async function runGoalChainEndToEnd(params = {}, dependencies = {}) {
  return _runGoalChainEndToEndInternal(params, dependencies, null);
}

async function runGoalChainEndToEndAsync(params = {}, dependencies = {}) {
  return new Promise((resolve, reject) => {
    _runGoalChainEndToEndInternal(params, dependencies, (payload) => {
      // progress callback fires after each stage — the caller persists it
      dependencies.onE2eProgress?.(payload);
    }).then(resolve).catch(reject);
  });
}

async function _runGoalChainEndToEndInternal(params = {}, dependencies = {}, onProgress) {
  const sharedRunId = params.runId ? requireSafeId(params.runId, 'runId') : null;
  const steps = [];
  let previousChainOutputPath = null;

  for (const stageId of GOAL_CHAIN_ORDER) {
    const stage = GOAL_CHAIN_STAGES[stageId];
    const stageParams = {
      ...(params.stageParams?.[stageId] || {}),
      cdpUrl: params.cdpUrl,
      runId: sharedRunId
    };

    if (onProgress) {
      onProgress({ status: 'running', currentStage: stageId, currentLabel: stage.label, steps: [...steps] });
    }

    if (stageId === 'page') {
      stageParams.menuEntriesPath = previousChainOutputPath;
    } else if (stageId === 'feature') {
      stageParams.pageEntriesPath = previousChainOutputPath;
    } else if (stageId === 'execution') {
      stageParams.testCasesPath = previousChainOutputPath;
      stageParams.mode = params.executionMode || 'fixture_simulated';
    }

    let stepResult;
    try {
      stepResult = await runGoalChainStage(stageId, stageParams, dependencies);
    } catch (error) {
      const finalResult = {
        steps: [...steps, {
          stageId,
          label: stage.label,
          evaluation: { verdict: 'failed', reason: error.message },
          error: error.message
        }],
        stoppedAt: stageId,
        stoppedReason: error.message
      };
      if (onProgress) onProgress({ ...finalResult, status: 'completed' });
      return finalResult;
    }

    steps.push(stepResult);

    if (stepResult.evaluation.verdict !== 'passed') {
      const finalResult = { steps, stoppedAt: stageId, stoppedReason: stepResult.evaluation.reason };
      if (onProgress) onProgress({ ...finalResult, status: 'completed' });
      return finalResult;
    }

    previousChainOutputPath = stepResult.chainOutputPath;
    if (stage.chainOutputKey && !previousChainOutputPath) {
      const reason = `${stage.label} 未在 stdout 中返回 ${stage.chainOutputKey}，无法继续下一阶段。`;
      const finalResult = { steps, stoppedAt: stageId, stoppedReason: reason };
      if (onProgress) onProgress({ ...finalResult, status: 'completed' });
      return finalResult;
    }
  }

  const finalResult = { steps, stoppedAt: null, stoppedReason: null };
  if (onProgress) onProgress({ ...finalResult, status: 'completed' });
  return finalResult;
}

// ---------------------------------------------------------------------------
// 会话落盘：供页面刷新后仍能看到历史记录。
// ---------------------------------------------------------------------------

async function persistTestCenterRun(kindLabel, payload, options = {}) {
  const dir = options.testCenterDir || TEST_CENTER_DIR;
  const runId = options.runId || createRunId();
  const runDir = options.runDir || path.join(dir, runId);
  const record = {
    schemaVersion: 'stage2_test_center_run.v1',
    runId,
    kindLabel,
    createdAt: nowIso(),
    payload
  };
  await writeJson(path.join(runDir, 'result.json'), record);
  return { runId, runDir: relativeArtifact(runDir), record };
}

async function listTestCenterRuns(options = {}) {
  const dir = options.testCenterDir || TEST_CENTER_DIR;
  let entries;
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch {
    return [];
  }
  const runs = await Promise.all(
    entries
      .filter((entry) => entry.isDirectory() && SAFE_RUN_ID_PATTERN.test(entry.name))
      .map((entry) => readJsonIfExists(path.join(dir, entry.name, 'result.json')))
  );
  return runs
    .filter(Boolean)
    .sort((left, right) => String(right.createdAt || '').localeCompare(String(left.createdAt || '')));
}

async function resolveTestCenterArtifact(runId, options = {}) {
  const dir = options.testCenterDir || TEST_CENTER_DIR;
  if (!SAFE_RUN_ID_PATTERN.test(String(runId || ''))) {
    return null;
  }
  const filePath = path.join(dir, runId, 'result.json');
  if (!isWithinDir(filePath, dir) || !(await pathExists(filePath))) {
    return null;
  }
  return { path: filePath, fileName: 'result.json' };
}

module.exports = {
  TestCenterInputError,
  UNIT_TEST_SUITES,
  GOAL_CHAIN_STAGES,
  GOAL_CHAIN_ORDER,
  TEST_CENTER_DIR,
  createRunId,
  parseJUnitXml,
  runUnitTestSuite,
  runGoalChainStage,
  runGoalChainEndToEnd,
  runGoalChainEndToEndAsync,
  evaluateGoalLoopStepResult,
  persistTestCenterRun,
  listTestCenterRuns,
  resolveTestCenterArtifact,
  writeJson,
  readJsonIfExists,
  nowIso,
  SAFE_RUN_ID_PATTERN
};
