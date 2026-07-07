const fs = require('fs/promises');
const path = require('path');
const { execFile } = require('child_process');
const { promisify } = require('util');
const { loadStage2Overview, syncStage2SessionArtifacts } = require('./stage2Dashboard');

const execFileAsync = promisify(execFile);

const ROOT_DIR = path.join(__dirname, '..');
const STAGE2_DIR = path.join(ROOT_DIR, 'artifacts', 'stage2');
const RUN_DIR_PATTERN = /^\d{8}_\d{6}_.+/;
const DEFAULT_CDP_URL = process.env.STAGE2_CDP_URL || 'http://localhost:9222';

async function readJsonIfExists(filePath) {
  try {
    const raw = await fs.readFile(filePath, 'utf8');
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

async function resolveStage2RunDir(runId) {
  if (!RUN_DIR_PATTERN.test(runId)) {
    throw new Error('无效的 runId。');
  }
  const runDir = path.join(STAGE2_DIR, runId);
  try {
    const stat = await fs.stat(runDir);
    if (!stat.isDirectory()) {
      throw new Error('指定 run 目录不存在。');
    }
  } catch {
    throw new Error('指定 run 目录不存在。');
  }
  return runDir;
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
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      continue;
    }
  }

  return 'python';
}

function parseStage2CommandOutput(stdout) {
  const trimmed = String(stdout || '').trim();
  if (!trimmed) {
    return null;
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

function buildResumeHumanTakeoverArgs(runDir, packet, options = {}) {
  const args = [
    '-m',
    'prototype.stage2.main',
    '--resume-human-takeover',
    runDir,
    '--cdp-url',
    options.cdpUrl || DEFAULT_CDP_URL,
    '--max-attempts',
    String(options.maxAttempts || packet?.resume_max_attempts || 3),
    '--max-rounds',
    String(options.maxRounds || packet?.resume_max_rounds || 1)
  ];

  if (options.operatorId) {
    args.push('--resume-operator', options.operatorId);
  }
  if (options.note) {
    args.push('--resume-note', options.note);
  }
  return args;
}

async function markHumanTakeoverResolved(
  {
    runId,
    operatorId,
    note,
    readyToResume = true,
    handledActionIds
  },
  dependencies = {}
) {
  const runDir = await resolveStage2RunDir(runId);
  const packet = await readJsonIfExists(path.join(runDir, 'human_takeover.json'));
  if (!packet) {
    throw new Error('当前 run 没有 human_takeover.json，无法标记人工处理完成。');
  }

  const resolvedAt = new Date().toISOString();
  const resolvedPayload = {
    schema_version: 'human_takeover_resolution.v1',
    status: 'resolved',
    run_id: runId,
    source_run_id: packet.source_run_id || runId,
    source_run_dir: packet.source_run_dir || runDir,
    operator_id: operatorId || 'run_center',
    note: note || 'Marked as resolved from run center.',
    ready_to_resume: Boolean(readyToResume),
    handled_action_ids: Array.isArray(handledActionIds) && handledActionIds.length
      ? handledActionIds
      : (Array.isArray(packet.scheduled_action_ids) ? packet.scheduled_action_ids : []),
    handled_action_count: Array.isArray(handledActionIds) && handledActionIds.length
      ? handledActionIds.length
      : (Array.isArray(packet.scheduled_action_ids) ? packet.scheduled_action_ids.length : 0),
    resume_command: packet.resume_command || null,
    resolved_at: resolvedAt
  };

  const resolutionPath = path.join(runDir, 'human_takeover_resolution.json');
  await fs.writeFile(resolutionPath, JSON.stringify(resolvedPayload, null, 2), 'utf8');
  const overviewLoader = dependencies.loadStage2Overview || loadStage2Overview;
  const sessionSync = dependencies.syncStage2SessionArtifacts || syncStage2SessionArtifacts;
  const overview = await overviewLoader();
  await sessionSync(overview.runSummaries || []);

  return {
    status: 'resolved',
    runId,
    resolutionPath,
    resolvedAt,
    readyToResume: resolvedPayload.ready_to_resume
  };
}

async function resumeHumanTakeover(
  {
    runId,
    cdpUrl,
    maxAttempts,
    maxRounds,
    operatorId,
    note
  },
  dependencies = {}
) {
  const runDir = await resolveStage2RunDir(runId);
  const packet = await readJsonIfExists(path.join(runDir, 'human_takeover.json'));
  if (!packet) {
    throw new Error('当前 run 没有 human_takeover.json，无法从平台内续跑。');
  }

  const pythonCommand = await resolvePythonCommand();
  const args = buildResumeHumanTakeoverArgs(runDir, packet, {
    cdpUrl,
    maxAttempts,
    maxRounds,
    operatorId,
    note
  });
  const runner = dependencies.execFileRunner || ((command, commandArgs) =>
    execFileAsync(command, commandArgs, {
      cwd: ROOT_DIR,
      windowsHide: true,
      maxBuffer: 10 * 1024 * 1024
    }));
  const result = await runner(pythonCommand, args);
  const parsed = parseStage2CommandOutput(result.stdout);

  return {
    status: 'completed',
    runId,
    resumedFromRunId: runId,
    latestRunId: parsed?.run_dir ? path.basename(String(parsed.run_dir)) : null,
    stdout: String(result.stdout || '').trim(),
    stderr: String(result.stderr || '').trim(),
    payload: parsed
  };
}

module.exports = {
  buildResumeHumanTakeoverArgs,
  markHumanTakeoverResolved,
  resumeHumanTakeover
};
