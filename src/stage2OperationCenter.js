const crypto = require('crypto');
const fs = require('fs/promises');
const http = require('http');
const https = require('https');
const path = require('path');
const { execFile } = require('child_process');

const ROOT_DIR = path.join(__dirname, '..');
const STAGE2_DIR = path.join(ROOT_DIR, 'artifacts', 'stage2');
const OPERATIONS_DIR = path.join(STAGE2_DIR, 'operations');
const DEFAULT_CDP_URL = process.env.STAGE2_CDP_URL || 'http://localhost:9222';
const DEFAULT_MAX_BUFFER = 20 * 1024 * 1024;
const SAFE_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$/;
const SAFE_SESSION_PATTERN = /^op_\d{8}_\d{6}_[a-f0-9]{8}$/;
const SAFE_TEXT_PATTERN = /^[^\r\n\t]{1,200}$/;
const SCENARIO_KINDS = new Set(['navigation', 'query', 'detail', 'create', 'edit', 'generic']);
const LOCAL_CDP_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);

class OperationInputError extends Error {
  constructor(message) {
    super(message);
    this.statusCode = 400;
  }
}

const STEP_DEFINITIONS = {
  check_environment: {
    id: 'check_environment',
    label: '检查本地执行环境',
    timeoutMs: 30_000,
    commandKind: 'environment'
  },
  explore_system_map: {
    id: 'explore_system_map',
    label: '探索系统地图',
    timeoutMs: 10 * 60_000,
    buildArgs: (params) => {
      const args = stage2BaseArgs('--explore-system-map');
      pushArg(args, '--target-name', requireText(params.targetName, 'targetName'));
      pushArg(args, '--template', requireSafeName(params.templateName, 'templateName'));
      pushArg(args, '--page-url', requireHttpUrl(params.pageUrl, 'pageUrl'));
      pushArg(args, '--cdp-url', normalizeCdpUrl(params.cdpUrl));
      pushOptionalArg(args, '--model', params.model);
      pushBooleanFlag(args, '--bootstrap-overwrite', params.bootstrapOverwrite);
      return args;
    }
  },
  routing_summary: {
    id: 'routing_summary',
    label: '生成模型路由摘要',
    timeoutMs: 60_000,
    buildArgs: (params) => {
      const args = stage2BaseArgs('--routing-summary');
      pushArg(args, '--template', requireSafeName(params.templateName, 'templateName'));
      pushOptionalArg(args, '--model', params.model);
      return args;
    }
  },
  bootstrap_template: {
    id: 'bootstrap_template',
    label: '生成模板骨架',
    timeoutMs: 60_000,
    buildArgs: (params) => {
      const args = stage2BaseArgs('--bootstrap-template');
      pushArg(args, '--template', requireSafeName(params.templateName, 'templateName'));
      pushArg(args, '--page-url', requireHttpUrl(params.pageUrl, 'pageUrl'));
      pushArg(args, '--page-name', requireText(params.pageName, 'pageName'));
      pushArg(args, '--scenario-kind', requireScenarioKind(params.scenarioKind));
      pushOptionalArg(args, '--feature-name', params.featureName);
      pushOptionalArg(args, '--feature-type', params.featureType);
      pushBooleanFlag(args, '--bootstrap-overwrite', params.bootstrapOverwrite);
      return args;
    }
  },
  live_discovery: {
    id: 'live_discovery',
    label: '执行自主发现',
    timeoutMs: 10 * 60_000,
    buildArgs: (params) => {
      const args = stage2BaseArgs('--live-discovery');
      pushArg(args, '--template', requireSafeName(params.templateName, 'templateName'));
      pushArg(args, '--cdp-url', normalizeCdpUrl(params.cdpUrl));
      pushOptionalArg(args, '--model', params.model);
      pushBooleanFlag(args, '--reuse-completed-discovery', params.reuseCompletedDiscovery);
      return args;
    }
  },
  capture_human_recording: {
    id: 'capture_human_recording',
    label: '采集人工录制',
    timeoutMs: 5 * 60_000,
    buildArgs: (params, context) => {
      const args = stage2BaseArgs('--capture-human-recording');
      pushArg(args, '--template', requireSafeName(params.templateName, 'templateName'));
      pushArg(args, '--cdp-url', normalizeCdpUrl(params.cdpUrl));
      pushArg(args, '--recording-session', optionalSafeName(params.recordingSession, `operation_${context.sessionId}`));
      pushArg(args, '--recording-operator', optionalSafeText(params.operatorId, 'run_center'));
      pushOptionalArg(args, '--recording-url', params.recordingUrl, requireHttpUrl);
      pushOptionalArg(args, '--recording-task', params.recordingTask);
      pushArg(args, '--capture-seconds', String(requireInt(params.captureSeconds, 'captureSeconds', 1, 600, 20)));
      return args;
    }
  },
  template_revision_checklist: {
    id: 'template_revision_checklist',
    label: '生成模板修订清单',
    timeoutMs: 60_000,
    buildArgs: (params) => {
      const args = stage2BaseArgs('--template-revision-checklist');
      pushArg(args, '--template', requireSafeName(params.templateName, 'templateName'));
      pushOptionalSafePath(args, '--discovery-dir', params.discoveryDir);
      pushOptionalSafePath(args, '--candidate-review', params.candidateReview);
      pushOptionalSafePath(args, '--checklist-output-dir', params.checklistOutputDir);
      return args;
    }
  },
  validate_connected_template: {
    id: 'validate_connected_template',
    label: '执行单模板连机验证',
    timeoutMs: 10 * 60_000,
    buildArgs: (params) => {
      const args = stage2BaseArgs('--validate-connected-template');
      args.push(requireSafeName(params.templateName, 'templateName'));
      pushArg(args, '--cdp-url', normalizeCdpUrl(params.cdpUrl));
      return args;
    }
  },
  resume_human_takeover: {
    id: 'resume_human_takeover',
    label: '恢复人工接管后的运行',
    timeoutMs: 10 * 60_000,
    buildArgs: (params) => {
      const args = stage2BaseArgs('--resume-human-takeover');
      args.push(resolveStage2RunDirParam(params.runDir || params.runId));
      pushArg(args, '--cdp-url', normalizeCdpUrl(params.cdpUrl));
      pushArg(args, '--max-attempts', String(requireInt(params.maxAttempts, 'maxAttempts', 1, 20, 3)));
      pushArg(args, '--max-rounds', String(requireInt(params.maxRounds, 'maxRounds', 1, 20, 1)));
      pushOptionalArg(args, '--resume-operator', params.operatorId);
      pushOptionalArg(args, '--resume-note', params.note);
      return args;
    }
  },
  validation_matrix: {
    id: 'validation_matrix',
    label: '执行统一验证矩阵',
    timeoutMs: 10 * 60_000,
    buildArgs: (params) => {
      const args = stage2BaseArgs('--validation-matrix');
      pushArg(args, '--cdp-url', normalizeCdpUrl(params.cdpUrl));
      return args;
    }
  }
};

function nowIso() {
  return new Date().toISOString();
}

function stage2BaseArgs(flag) {
  return ['-m', 'prototype.stage2.main', flag];
}

function createSessionId() {
  const stamp = new Date()
    .toISOString()
    .replace(/[-:]/g, '')
    .replace('T', '_')
    .slice(0, 15);
  return `op_${stamp}_${crypto.randomBytes(4).toString('hex')}`;
}

function normalizeStepId(stepId) {
  const normalized = String(stepId || '').trim();
  if (!STEP_DEFINITIONS[normalized]) {
    throw new OperationInputError('不支持的 operation step。');
  }
  return normalized;
}

function normalizeParams(body = {}) {
  const rawParams = body.params && typeof body.params === 'object' && !Array.isArray(body.params)
    ? body.params
    : (body.parameters && typeof body.parameters === 'object' && !Array.isArray(body.parameters)
      ? body.parameters
      : body);
  const params = rawParams || {};
  return {
    ...params,
    targetName: params.targetName || params.systemName,
    templateName: params.templateName || params.targetTemplate || params.systemMapTemplate || params.template,
    pageUrl: params.pageUrl || params.homeUrl || params.startUrl,
    runDir: params.runDir || params.run_dir,
    runId: params.runId || params.run_id
  };
}

function requireSafeName(value, fieldName) {
  const text = String(value || '').trim();
  if (!SAFE_NAME_PATTERN.test(text)) {
    throw new OperationInputError(`${fieldName} 只能包含字母、数字、下划线或连字符，并且必须以字母或数字开头。`);
  }
  return text;
}

function optionalSafeName(value, fallback) {
  if (value === undefined || value === null || String(value).trim() === '') {
    return fallback;
  }
  return requireSafeName(value, 'recordingSession');
}

function requireText(value, fieldName) {
  const text = String(value || '').trim();
  if (!SAFE_TEXT_PATTERN.test(text)) {
    throw new OperationInputError(`${fieldName} 不能为空，且不能包含控制字符。`);
  }
  return text;
}

function optionalSafeText(value, fallback) {
  if (value === undefined || value === null || String(value).trim() === '') {
    return fallback;
  }
  return requireText(value, 'text');
}

function requireScenarioKind(value) {
  const text = String(value || 'navigation').trim();
  if (!SCENARIO_KINDS.has(text)) {
    throw new OperationInputError('scenarioKind 必须是 navigation、query、detail、create、edit 或 generic。');
  }
  return text;
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
    throw new OperationInputError(`${fieldName} 必须是 http(s) URL。`);
  }
}

function normalizeCdpUrl(value) {
  const normalized = requireHttpUrl(value || DEFAULT_CDP_URL, 'cdpUrl');
  const url = new URL(normalized);
  if (process.env.STAGE2_ALLOW_REMOTE_CDP !== '1' && !LOCAL_CDP_HOSTS.has(url.hostname)) {
    throw new OperationInputError('cdpUrl 只允许 localhost、127.0.0.1 或 ::1；如需远程调试端口，请先显式配置白名单。');
  }
  return normalized;
}

function requireInt(value, fieldName, min, max, fallback) {
  if (value === undefined || value === null || value === '') {
    return fallback;
  }
  const number = Number(value);
  if (!Number.isInteger(number) || number < min || number > max) {
    throw new OperationInputError(`${fieldName} 必须是 ${min} 到 ${max} 之间的整数。`);
  }
  return number;
}

function pushArg(args, flag, value) {
  args.push(flag, String(value));
}

function pushOptionalArg(args, flag, value, validator = requireText) {
  if (value === undefined || value === null || String(value).trim() === '') {
    return;
  }
  args.push(flag, String(validator(value, flag.replace(/^--/, ''))));
}

function pushBooleanFlag(args, flag, value) {
  if (value === true) {
    args.push(flag);
  }
}

function isWithinDir(filePath, parentDir) {
  const resolvedPath = path.resolve(filePath);
  const resolvedParent = path.resolve(parentDir);
  return resolvedPath === resolvedParent || resolvedPath.startsWith(`${resolvedParent}${path.sep}`);
}

function resolveSafePathParam(value) {
  if (value === undefined || value === null || String(value).trim() === '') {
    return null;
  }
  const candidate = path.resolve(ROOT_DIR, String(value));
  const templateDir = path.join(ROOT_DIR, 'prototype', 'stage2', 'templates');
  if (!isWithinDir(candidate, STAGE2_DIR) && !isWithinDir(candidate, templateDir)) {
    throw new OperationInputError('路径参数只能指向 artifacts/stage2 或 prototype/stage2/templates 目录内。');
  }
  return candidate;
}

function pushOptionalSafePath(args, flag, value) {
  const safePath = resolveSafePathParam(value);
  if (safePath) {
    args.push(flag, safePath);
  }
}

function resolveStage2RunDirParam(value) {
  const text = String(value || '').trim();
  if (!text) {
    throw new OperationInputError('resume_human_takeover 需要 runDir 或 runId。');
  }
  const candidate = path.isAbsolute(text) ? path.resolve(text) : path.resolve(STAGE2_DIR, text);
  if (!isWithinDir(candidate, STAGE2_DIR)) {
    throw new OperationInputError('runDir 必须位于 artifacts/stage2 目录内。');
  }
  return candidate;
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
  await fs.writeFile(filePath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
}

async function appendEvent(sessionDir, event) {
  await fs.appendFile(
    path.join(sessionDir, 'events.jsonl'),
    `${JSON.stringify({ at: nowIso(), ...event })}\n`,
    'utf8'
  );
}

async function resolvePythonCommand() {
  const candidates = [
    process.env.PYTHON,
    path.join(ROOT_DIR, '.venv', 'Scripts', 'python.exe'),
    'python'
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (candidate === 'python') {
      return candidate;
    }
    if (await pathExists(candidate)) {
      return candidate;
    }
  }
  return 'python';
}

function execFileWithCapture(command, args, options, runner = execFile) {
  return new Promise((resolve) => {
    const startedAt = Date.now();
    runner(command, args, options, (error, stdout, stderr) => {
      resolve({
        error,
        stdout: String(stdout || ''),
        stderr: String(stderr || ''),
        durationMs: Date.now() - startedAt
      });
    });
  });
}

function tryParseJson(value) {
  const trimmed = String(value || '').trim();
  if (!trimmed) {
    return null;
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

function relativeArtifact(filePath) {
  return path.relative(ROOT_DIR, filePath).replace(/\\/g, '/');
}

function trimPreview(value) {
  const text = String(value || '').trim();
  return text.length > 2000 ? `${text.slice(0, 2000)}...` : text;
}

function buildOperationArtifactHref(sessionId, artifactKey) {
  return `/api/stage2/operation/artifacts/${encodeURIComponent(sessionId)}/${encodeURIComponent(artifactKey)}`;
}

async function createOrLoadSession(sessionId, operationsDir) {
  if (sessionId) {
    if (!SAFE_SESSION_PATTERN.test(sessionId)) {
      throw new OperationInputError('无效的 operation sessionId。');
    }
    const sessionDir = path.join(operationsDir, sessionId);
    const state = await readJsonIfExists(path.join(sessionDir, 'operation_state.json'));
    if (!state) {
      throw new OperationInputError('指定 operation session 不存在。');
    }
    return { sessionId, sessionDir, state };
  }

  const nextSessionId = createSessionId();
  const sessionDir = path.join(operationsDir, nextSessionId);
  const createdAt = nowIso();
  const state = {
    schemaVersion: 'stage2_operation_state.v1',
    sessionId: nextSessionId,
    status: 'created',
    createdAt,
    updatedAt: createdAt,
    currentStepId: null,
    latestResult: null,
    stepHistory: [],
    artifacts: {
      state: relativeArtifact(path.join(sessionDir, 'operation_state.json')),
      events: relativeArtifact(path.join(sessionDir, 'events.jsonl'))
    }
  };
  await fs.mkdir(sessionDir, { recursive: true });
  await writeJson(path.join(sessionDir, 'operation_state.json'), state);
  await appendEvent(sessionDir, { type: 'session_created', sessionId: nextSessionId });
  return { sessionId: nextSessionId, sessionDir, state };
}

function toPublicStepDefinition(definition) {
  return {
    stepId: definition.id,
    label: definition.label,
    timeoutMs: definition.timeoutMs
  };
}

function buildPublicState(state) {
  return {
    ...state,
    allowedSteps: Object.values(STEP_DEFINITIONS).map(toPublicStepDefinition)
  };
}

function allowedStepIds() {
  return Object.keys(STEP_DEFINITIONS);
}

function makeCommandResultBase({ sessionId, stepId, command, args, timeoutMs, startedAt }) {
  return {
    schemaVersion: 'stage2_operation_command_result.v1',
    sessionId,
    stepId,
    status: 'running',
    command: {
      executable: command,
      args,
      cwd: ROOT_DIR,
      timeoutMs
    },
    startedAt,
    finishedAt: null,
    durationMs: null,
    exitCode: null,
    signal: null,
    stdoutPreview: '',
    stderrPreview: '',
    parsedStdout: null,
    artifacts: {}
  };
}

async function runEnvironmentCheck({ sessionId, timeoutMs, params, dependencies }) {
  const pythonCommand = await resolvePythonCommand();
  const cdpUrl = normalizeCdpUrl(params.cdpUrl);
  const startedAt = nowIso();
  const result = makeCommandResultBase({
    sessionId,
    stepId: 'check_environment',
    command: pythonCommand,
    args: ['--version'],
    timeoutMs,
    startedAt
  });
  const commandRun = await execFileWithCapture(
    pythonCommand,
    ['--version'],
    {
      cwd: ROOT_DIR,
      windowsHide: true,
      timeout: timeoutMs,
      maxBuffer: DEFAULT_MAX_BUFFER
    },
    dependencies.execFileRunner
  );
  const cdpProbe = await probeCdpUrl(cdpUrl, dependencies.httpProbe);
  const finishedAt = nowIso();
  const checks = {
    python: {
      ok: !commandRun.error,
      executable: pythonCommand,
      version: trimPreview(commandRun.stdout || commandRun.stderr)
    },
    stage2Entrypoint: {
      ok: await pathExists(path.join(ROOT_DIR, 'prototype', 'stage2', 'main.py'))
    },
    templatesDir: {
      ok: await pathExists(path.join(ROOT_DIR, 'prototype', 'stage2', 'templates'))
    },
    artifactsDir: {
      ok: await pathExists(STAGE2_DIR)
    },
    cdp: cdpProbe
  };
  const ok = Object.values(checks).every((item) => item.ok);
  return {
    ...result,
    status: ok ? 'completed' : 'failed',
    finishedAt,
    durationMs: commandRun.durationMs,
    exitCode: commandRun.error?.code ?? 0,
    signal: commandRun.error?.signal || null,
    stdoutPreview: trimPreview(commandRun.stdout),
    stderrPreview: trimPreview(commandRun.stderr || commandRun.error?.message || ''),
    stdoutText: commandRun.stdout,
    stderrText: commandRun.stderr || commandRun.error?.message || '',
    parsedStdout: { ok, checks }
  };
}

function probeCdpUrl(cdpUrl, customProbe) {
  if (customProbe) {
    return customProbe(cdpUrl);
  }
  return new Promise((resolve) => {
    let settled = false;
    const target = new URL('/json/version', cdpUrl);
    const client = target.protocol === 'https:' ? https : http;
    const request = client.get(target, { timeout: 3000 }, (response) => {
      response.resume();
      response.on('end', () => {
        if (!settled) {
          settled = true;
          resolve({ ok: response.statusCode >= 200 && response.statusCode < 300, url: cdpUrl, statusCode: response.statusCode });
        }
      });
    });
    request.on('timeout', () => {
      request.destroy(new Error('CDP probe timed out.'));
    });
    request.on('error', (error) => {
      if (!settled) {
        settled = true;
        resolve({ ok: false, url: cdpUrl, error: error.message });
      }
    });
  });
}

async function executePythonStep({ sessionId, sessionDir, stepId, params, dependencies }) {
  const definition = STEP_DEFINITIONS[stepId];
  const pythonCommand = await resolvePythonCommand();
  const args = definition.buildArgs(params, { sessionId });
  const startedAt = nowIso();
  const result = makeCommandResultBase({
    sessionId,
    stepId,
    command: pythonCommand,
    args,
    timeoutMs: definition.timeoutMs,
    startedAt
  });
  const commandRun = await execFileWithCapture(
    pythonCommand,
    args,
    {
      cwd: ROOT_DIR,
      windowsHide: true,
      timeout: definition.timeoutMs,
      maxBuffer: DEFAULT_MAX_BUFFER
    },
    dependencies.execFileRunner
  );
  const finishedAt = nowIso();
  const error = commandRun.error;
  return {
    ...result,
    status: error ? 'failed' : 'completed',
    finishedAt,
    durationMs: commandRun.durationMs,
    exitCode: error?.code ?? 0,
    signal: error?.signal || null,
    stdoutPreview: trimPreview(commandRun.stdout),
    stderrPreview: trimPreview(commandRun.stderr || error?.message || ''),
    stdoutText: commandRun.stdout,
    stderrText: commandRun.stderr || error?.message || '',
    parsedStdout: tryParseJson(commandRun.stdout),
    error: error ? {
      message: error.message,
      code: error.code ?? null,
      signal: error.signal || null,
      killed: Boolean(error.killed)
    } : null,
    artifacts: {
      stdout: relativeArtifact(path.join(sessionDir, `stdout_${stepId}.txt`)),
      stderr: relativeArtifact(path.join(sessionDir, `stderr_${stepId}.txt`))
    }
  };
}

async function persistCommandArtifacts(sessionDir, stepId, commandResult) {
  const stdoutPath = path.join(sessionDir, `stdout_${stepId}.txt`);
  const stderrPath = path.join(sessionDir, `stderr_${stepId}.txt`);
  const {
    stdoutText,
    stderrText,
    ...resultForJson
  } = commandResult;
  await fs.writeFile(stdoutPath, stdoutText ? String(stdoutText) : '', 'utf8');
  await fs.writeFile(stderrPath, stderrText ? String(stderrText) : '', 'utf8');
  const payload = {
    ...resultForJson,
    artifacts: {
      ...(commandResult.artifacts || {}),
      stdout: relativeArtifact(stdoutPath),
      stderr: relativeArtifact(stderrPath),
      result: relativeArtifact(path.join(sessionDir, `command_result_${stepId}.json`))
    }
  };
  await writeJson(path.join(sessionDir, `command_result_${stepId}.json`), payload);
  return payload;
}

async function runOperationStep(body = {}, dependencies = {}) {
  const operationsDir = dependencies.operationsDir || OPERATIONS_DIR;
  const stepId = normalizeStepId(body.stepId || body.step_id);
  const definition = STEP_DEFINITIONS[stepId];
  const params = normalizeParams(body);
  await fs.mkdir(operationsDir, { recursive: true });
  const { sessionId, sessionDir, state } = await createOrLoadSession(body.sessionId || body.session_id, operationsDir);
  const startedAt = nowIso();
  const runningState = {
    ...state,
    status: 'running',
    params,
    currentStepId: stepId,
    updatedAt: startedAt,
    latestResult: {
      stepId,
      status: 'running',
      startedAt,
      label: definition.label
    }
  };
  await writeJson(path.join(sessionDir, 'operation_state.json'), runningState);
  await appendEvent(sessionDir, {
    type: 'step_started',
    sessionId,
    stepId,
    label: definition.label
  });

  const rawResult = definition.commandKind === 'environment'
    ? await runEnvironmentCheck({ sessionId, timeoutMs: definition.timeoutMs, params, dependencies })
    : await executePythonStep({ sessionId, sessionDir, stepId, params, dependencies });
  const commandResult = await persistCommandArtifacts(sessionDir, stepId, rawResult);
  const finishedAt = commandResult.finishedAt || nowIso();
  const nextState = {
    ...runningState,
    status: commandResult.status === 'completed' ? 'idle' : 'failed',
    currentStepId: null,
    updatedAt: finishedAt,
    latestResult: {
      stepId,
      label: definition.label,
      status: commandResult.status,
      startedAt: commandResult.startedAt,
      finishedAt,
      durationMs: commandResult.durationMs,
      exitCode: commandResult.exitCode,
      signal: commandResult.signal,
      parsedStdout: commandResult.parsedStdout,
      stdoutPreview: commandResult.stdoutPreview,
      stderrPreview: commandResult.stderrPreview,
      artifacts: commandResult.artifacts,
      resultArtifact: commandResult.artifacts?.result || null
    },
    stepHistory: [
      ...(runningState.stepHistory || []),
      {
        stepId,
        label: definition.label,
        status: commandResult.status,
        startedAt: commandResult.startedAt,
        finishedAt,
        durationMs: commandResult.durationMs,
        resultArtifact: commandResult.artifacts.result
      }
    ].slice(-50)
  };
  await writeJson(path.join(sessionDir, 'operation_state.json'), nextState);
  await appendEvent(sessionDir, {
    type: 'step_finished',
    sessionId,
    stepId,
    status: commandResult.status,
    durationMs: commandResult.durationMs
  });

  return {
    session: buildPublicState(nextState),
    result: commandResult,
    stepArtifacts: buildStepArtifactRefs(stepId, sessionId, params)
  };
}

async function checkEnvironment(body = {}, dependencies = {}) {
  return runOperationStep({ ...body, stepId: 'check_environment' }, dependencies);
}

async function listOperationSessions(options = {}) {
  const operationsDir = options.operationsDir || OPERATIONS_DIR;
  const limit = requireInt(options.limit, 'limit', 1, 100, 20);
  try {
    const entries = await fs.readdir(operationsDir, { withFileTypes: true });
    const sessions = (await Promise.all(entries
      .filter((entry) => entry.isDirectory())
      .map(async (entry) => readJsonIfExists(path.join(operationsDir, entry.name, 'operation_state.json')))))
      .filter(Boolean)
      .sort((left, right) => String(right.updatedAt || '').localeCompare(String(left.updatedAt || '')))
      .slice(0, limit)
      .map(buildPublicState);
    return { sessions };
  } catch {
    return { sessions: [] };
  }
}

async function getOperationState(options = {}) {
  const operationsDir = options.operationsDir || OPERATIONS_DIR;
  if (options.sessionId) {
    if (!SAFE_SESSION_PATTERN.test(options.sessionId)) {
      throw new OperationInputError('无效的 operation sessionId。');
    }
    const state = await readJsonIfExists(path.join(operationsDir, options.sessionId, 'operation_state.json'));
    if (!state) {
      throw new OperationInputError('指定 operation session 不存在。');
    }
    return { session: buildPublicState(state) };
  }
  const { sessions } = await listOperationSessions({ operationsDir, limit: 1 });
  return { session: sessions[0] || null, allowedSteps: Object.values(STEP_DEFINITIONS).map(toPublicStepDefinition) };
}

async function loadOperationCenter(options = {}) {
  const { sessions } = await listOperationSessions(options);
  return {
    currentSession: sessions[0] || null,
    sessions,
    allowedSteps: Object.values(STEP_DEFINITIONS).map(toPublicStepDefinition)
  };
}

async function resolveOperationArtifact(sessionId, artifactKey, options = {}) {
  const operationsDir = options.operationsDir || OPERATIONS_DIR;
  if (!SAFE_SESSION_PATTERN.test(sessionId)) {
    return null;
  }

  const fileNameByKey = {
    operation_state_json: 'operation_state.json',
    events_jsonl: 'events.jsonl'
  };
  for (const stepId of allowedStepIds()) {
    fileNameByKey[`command_result_${stepId}_json`] = `command_result_${stepId}.json`;
    fileNameByKey[`stdout_${stepId}_txt`] = `stdout_${stepId}.txt`;
    fileNameByKey[`stderr_${stepId}_txt`] = `stderr_${stepId}.txt`;
  }

  const fileName = fileNameByKey[artifactKey];
  if (fileName) {
    const filePath = path.join(operationsDir, sessionId, fileName);
    if (!isWithinDir(filePath, operationsDir) || !(await pathExists(filePath))) {
      return null;
    }
    return {
      key: artifactKey,
      path: filePath,
      fileName,
      relativePath: relativeArtifact(filePath)
    };
  }

  const state = await readJsonIfExists(path.join(operationsDir, sessionId, 'operation_state.json'));
  const indexedArtifact = state?.artifacts?.[artifactKey];
  const indexedPath = typeof indexedArtifact?.path === 'string' ? indexedArtifact.path : null;
  if (!indexedPath) {
    return null;
  }
  const filePath = path.resolve(indexedPath);
  if (!isWithinDir(filePath, STAGE2_DIR) && !isWithinDir(filePath, TEMPLATE_ROOT)) {
    return null;
  }
  if (!(await pathExists(filePath))) {
    return null;
  }
  return {
    key: artifactKey,
    path: filePath,
    fileName: indexedArtifact.fileName || path.basename(filePath),
    relativePath: relativeArtifact(filePath)
  };
}

function buildOperationCommand(stepId, params = {}, context = { sessionId: 'op_20260623_000000_00000000' }) {
  const normalizedStepId = normalizeStepId(stepId);
  const definition = STEP_DEFINITIONS[normalizedStepId];
  if (definition.commandKind === 'environment') {
    return { commandKind: 'environment', args: ['--version'] };
  }
  return {
    commandKind: 'python',
    args: definition.buildArgs(params, context),
    timeoutMs: definition.timeoutMs
  };
}

function buildStepArtifactRefs(stepId, sessionId, params = {}) {
  const referencesByStep = {
    explore_system_map: [
      ['navigation_tree.json', `${params.templateName || params.systemMapTemplate}_navigation_tree.json`],
      ['page_semantic_summary.json', `${params.templateName || params.systemMapTemplate}_page_semantic_summary.json`],
      ['page_entries.json', `${params.templateName || params.systemMapTemplate}_page_entries.json`]
    ],
    live_discovery: [
      ['page_entries.json', `${params.templateName}_page_entries.json`],
      ['feature_points.json', `${params.templateName}_feature_points.json`],
      ['discovery_review_queue.json', `${params.templateName}_discovery_review_queue.json`]
    ],
    template_revision_checklist: [
      ['template_revision_checklist.json', 'checklist_output_dir_template_revision_checklist.json'],
      ['template_revision_checklist.md', 'checklist_output_dir_template_revision_checklist.md']
    ],
    validate_connected_template: [
      ['validation_result.json', 'run_dir_validation_result.json'],
      ['verification_result.json', 'run_dir_verification_result.json'],
      ['network_events.json', 'run_dir_network_events.json']
    ],
    validation_matrix: [
      ['latest_validation_matrix.json', 'latest_validation_matrix.json'],
      ['latest_validation_matrix.md', 'latest_validation_matrix.md']
    ]
  };
  const pairs = referencesByStep[stepId] || [];
  return pairs.map(([label, artifactKey]) => ({
    label,
    artifactKey,
    href: buildOperationArtifactHref(sessionId, artifactKey)
  }));
}

module.exports = {
  STEP_DEFINITIONS,
  OperationInputError,
  buildOperationCommand,
  buildStepArtifactRefs,
  checkEnvironment,
  checkOperationEnvironment: checkEnvironment,
  getOperationState,
  loadOperationCenter,
  listOperationSessions,
  resolveOperationArtifact,
  runOperationStep
};
