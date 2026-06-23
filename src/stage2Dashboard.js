const crypto = require('crypto');
const fs = require('fs/promises');
const path = require('path');

const STAGE2_DIR = path.join(__dirname, '..', 'artifacts', 'stage2');
const SESSIONS_DIR = path.join(STAGE2_DIR, 'sessions');
const OPERATIONS_DIR = path.join(STAGE2_DIR, 'operations');
const TEMPLATE_ROOT = path.join(__dirname, '..', 'prototype', 'stage2', 'templates');
const RUN_DIR_PATTERN = /^\d{8}_\d{6}_.+/;
const HUMAN_LOOP_SESSION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const OPERATION_SESSION_PATTERN = /^[A-Za-z0-9_-]{1,120}$/;
const OPERATION_ARTIFACT_KEY_PATTERN = /^[A-Za-z0-9_-]{1,120}$/;
const TEXT_EXTENSIONS = new Set(['.json', '.jsonl', '.md', '.txt', '.log']);
const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.svg']);
const DOCUMENT_EXTENSIONS = new Set(['.pdf']);

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

function factMap(facts = []) {
  return Object.fromEntries((facts || []).map((item) => [item.label, item.value]));
}

function firstPresent(...values) {
  for (const value of values) {
    if (value === null || value === undefined) {
      continue;
    }
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (trimmed) {
        return trimmed;
      }
      continue;
    }
    return value;
  }
  return null;
}

function textOrNull(value) {
  const normalized = firstPresent(value);
  return normalized === null || normalized === undefined ? null : String(normalized);
}

function uniqueTexts(...groups) {
  const seen = new Set();
  const values = [];

  const add = (candidate) => {
    if (candidate === null || candidate === undefined) {
      return;
    }
    const text = String(candidate).trim();
    if (!text || seen.has(text)) {
      return;
    }
    seen.add(text);
    values.push(text);
  };

  for (const group of groups) {
    if (Array.isArray(group)) {
      group.forEach(add);
    } else {
      add(group);
    }
  }

  return values;
}

function findReportSection(runReport, title) {
  if (!Array.isArray(runReport?.sections)) {
    return null;
  }
  return runReport.sections.find((section) => section?.title === title) || null;
}

function normalizeActionItem(action = {}, fallback = {}) {
  const executionHints = action?.execution_hints && typeof action.execution_hints === 'object' && !Array.isArray(action.execution_hints)
    ? action.execution_hints
    : {};

  const actionId = textOrNull(firstPresent(action.action_id, action.item_id, fallback.actionId, fallback.itemId));
  const title = textOrNull(firstPresent(action.title, action.name, fallback.title, actionId, '未命名动作'));

  return {
    actionId,
    clusterId: textOrNull(firstPresent(action.cluster_id, fallback.clusterId)),
    title: title || '未命名动作',
    stage: textOrNull(firstPresent(action.stage, fallback.stage)),
    owner: textOrNull(firstPresent(action.owner, fallback.owner)),
    strategy: textOrNull(firstPresent(action.strategy, fallback.strategy)),
    priority: textOrNull(firstPresent(action.priority, fallback.priority)),
    status: textOrNull(firstPresent(action.status, fallback.status)),
    reason: textOrNull(firstPresent(action.reason, action.summary, fallback.reason)),
    expectedOutcome: textOrNull(firstPresent(action.expected_outcome, fallback.expectedOutcome)),
    retryMode: textOrNull(firstPresent(
      executionHints.workflow_retry_mode,
      executionHints.validation_retry_mode,
      fallback.retryMode
    )),
    notes: uniqueTexts(action.notes, fallback.notes)
  };
}

function buildRetryActionCatalog(retryPlan, retryPlanSection) {
  const sourceItems = Array.isArray(retryPlan?.actions) && retryPlan.actions.length
    ? retryPlan.actions
    : (retryPlanSection?.items || []);
  return sourceItems
    .map((item) => normalizeActionItem(item))
    .filter((item) => item.actionId || item.title);
}

function buildScheduledActions(nextRoundDecision, retryActions, nextRoundSection) {
  const scheduledIds = Array.isArray(nextRoundDecision?.scheduled_action_ids)
    ? nextRoundDecision.scheduled_action_ids.map((item) => String(item))
    : [];
  const actionById = new Map(retryActions.map((item) => [item.actionId, item]).filter(([key]) => key));

  if (scheduledIds.length) {
    return scheduledIds.map((actionId) => {
      const action = actionById.get(actionId);
      return action
        ? { ...action }
        : normalizeActionItem({}, { actionId, title: actionId, status: nextRoundDecision?.status || null });
    });
  }

  if (nextRoundDecision?.status === 'scheduled' && retryActions.length) {
    return retryActions.map((item) => ({ ...item }));
  }

  return (nextRoundSection?.items || [])
    .map((item) => normalizeActionItem(item))
    .filter((action) => action.actionId || action.title);
}

function buildEffectiveHumanTakeover(humanTakeover, nextRoundDecision, nextRoundSection) {
  if (humanTakeover) {
    return humanTakeover;
  }

  if ((nextRoundDecision?.status || factMap(nextRoundSection?.facts).status) !== 'needs_review') {
    return null;
  }

  return {
    status: 'needs_review',
    target_stage: nextRoundDecision?.target_stage || factMap(nextRoundSection?.facts).target_stage || null,
    waiting_reason: nextRoundDecision?.primary_reason || factMap(nextRoundSection?.facts).primary_reason || null,
    resume_command: null,
    pending_actions: [],
    notes: uniqueTexts(nextRoundDecision?.notes, nextRoundSection?.notes)
  };
}

function buildPendingHumanActions(effectiveHumanTakeover) {
  return Array.isArray(effectiveHumanTakeover?.pending_actions)
    ? effectiveHumanTakeover.pending_actions
      .map((item) => normalizeActionItem(item))
      .filter((action) => action.actionId || action.title)
    : [];
}

function buildControlLoop({
  runReport,
  nextRoundDecision,
  stopConditions,
  retryPlan,
  effectiveHumanTakeover,
  humanTakeoverResolution
}) {
  const retryPlanSection = findReportSection(runReport, 'Retry Plan');
  const stopSection = findReportSection(runReport, 'Stop Conditions');
  const nextRoundSection = findReportSection(runReport, 'Next Round Decision');
  const retryFacts = factMap(retryPlanSection?.facts);
  const stopFacts = factMap(stopSection?.facts);
  const nextRoundFacts = factMap(nextRoundSection?.facts);
  const retryActions = buildRetryActionCatalog(retryPlan, retryPlanSection);
  const scheduledActions = buildScheduledActions(nextRoundDecision, retryActions, nextRoundSection);
  const pendingHumanActions = buildPendingHumanActions(effectiveHumanTakeover);

  return {
    nextRound: {
      status: textOrNull(firstPresent(nextRoundDecision?.status, nextRoundFacts.status)),
      shouldStart: firstPresent(
        nextRoundDecision?.should_start_next_round,
        nextRoundFacts.should_start_next_round
      ),
      currentRound: firstPresent(nextRoundDecision?.current_round, nextRoundFacts.current_round),
      nextRound: firstPresent(nextRoundDecision?.next_round, nextRoundFacts.next_round),
      targetStage: textOrNull(firstPresent(
        nextRoundDecision?.target_stage,
        nextRoundFacts.target_stage,
        effectiveHumanTakeover?.target_stage
      )),
      reason: textOrNull(firstPresent(nextRoundDecision?.primary_reason, nextRoundFacts.primary_reason)),
      notes: uniqueTexts(nextRoundDecision?.notes, nextRoundSection?.notes)
    },
    stopConditions: {
      status: textOrNull(firstPresent(stopConditions?.status, stopFacts.status)),
      shouldStop: firstPresent(stopConditions?.should_stop, stopFacts.should_stop),
      reason: textOrNull(firstPresent(stopConditions?.primary_reason, stopFacts.primary_reason)),
      notes: uniqueTexts(stopConditions?.notes, stopSection?.notes)
    },
    retryPlan: {
      status: textOrNull(firstPresent(retryPlan?.status, retryFacts.status)),
      goal: textOrNull(firstPresent(retryPlan?.goal)),
      notes: uniqueTexts(retryPlan?.notes, retryPlanSection?.notes)
    },
    scheduledActions,
    pendingHumanActions,
    humanTakeover: effectiveHumanTakeover ? {
      status: textOrNull(firstPresent(effectiveHumanTakeover.status, 'needs_review')),
      targetStage: textOrNull(firstPresent(effectiveHumanTakeover.target_stage)),
      waitingReason: textOrNull(firstPresent(
        effectiveHumanTakeover.waiting_reason,
        nextRoundDecision?.primary_reason,
        nextRoundFacts.primary_reason
      )),
      resumeCommand: textOrNull(firstPresent(effectiveHumanTakeover.resume_command)),
      resolutionStatus: textOrNull(firstPresent(humanTakeoverResolution?.status)),
      resolvedAt: textOrNull(firstPresent(humanTakeoverResolution?.resolved_at)),
      resolutionOperator: textOrNull(firstPresent(humanTakeoverResolution?.operator_id)),
      resolutionNote: textOrNull(firstPresent(humanTakeoverResolution?.note)),
      readyToResume: firstPresent(humanTakeoverResolution?.ready_to_resume),
      notes: uniqueTexts(
        effectiveHumanTakeover.notes,
        humanTakeoverResolution?.note,
        nextRoundDecision?.notes,
        nextRoundSection?.notes
      )
    } : {
      status: 'none',
      targetStage: null,
      waitingReason: null,
      resumeCommand: null,
      resolutionStatus: textOrNull(firstPresent(humanTakeoverResolution?.status)),
      resolvedAt: textOrNull(firstPresent(humanTakeoverResolution?.resolved_at)),
      resolutionOperator: textOrNull(firstPresent(humanTakeoverResolution?.operator_id)),
      resolutionNote: textOrNull(firstPresent(humanTakeoverResolution?.note)),
      readyToResume: firstPresent(humanTakeoverResolution?.ready_to_resume),
      notes: []
    }
  };
}

function isWithinStage2Root(filePath) {
  const resolvedRoot = path.resolve(STAGE2_DIR);
  const resolvedPath = path.resolve(filePath);
  return resolvedPath === resolvedRoot || resolvedPath.startsWith(`${resolvedRoot}${path.sep}`);
}

function safeArtifactPath(filePath) {
  if (!filePath) {
    return null;
  }
  const resolvedPath = path.resolve(String(filePath));
  if (!isWithinStage2Root(resolvedPath)) {
    return null;
  }
  return resolvedPath;
}

function inferArtifactKind(filePath) {
  const extension = path.extname(filePath).toLowerCase();
  if (TEXT_EXTENSIONS.has(extension)) {
    return 'text';
  }
  if (IMAGE_EXTENSIONS.has(extension)) {
    return 'image';
  }
  if (DOCUMENT_EXTENSIONS.has(extension)) {
    return 'document';
  }
  return 'file';
}

function sanitizeKeyPart(value) {
  return String(value || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 80) || 'artifact';
}

function ensureUniqueKey(baseKey, usedKeys) {
  let candidate = sanitizeKeyPart(baseKey);
  let index = 1;
  while (usedKeys.has(candidate)) {
    candidate = `${sanitizeKeyPart(baseKey)}_${index}`;
    index += 1;
  }
  usedKeys.add(candidate);
  return candidate;
}

function buildSessionDirectoryName(sessionId) {
  const safePrefix = sanitizeKeyPart(sessionId).slice(0, 48) || 'session';
  const suffix = crypto.createHash('sha1').update(String(sessionId)).digest('hex').slice(0, 10);
  return `${safePrefix}_${suffix}`;
}

function compareIsoDesc(left, right) {
  return String(right || '').localeCompare(String(left || ''));
}

function compareRunsByRecency(left, right) {
  return compareIsoDesc(left?.updatedAt, right?.updatedAt) || String(right?.runId || '').localeCompare(String(left?.runId || ''));
}

function buildRunSessionId({ currentStatus, runReport, roundInput, runId }) {
  const reportSummary = runReport?.summary || {};
  const templateName = currentStatus?.template_name || reportSummary.template_name || roundInput?.template_name || 'unknown-template';
  const modelName = currentStatus?.model_name || reportSummary.model_name || roundInput?.model_name || 'unknown-model';
  return textOrNull(firstPresent(
    roundInput?.orchestration_stream_id,
    reportSummary.orchestration_stream_id,
    currentStatus?.orchestration_stream_id,
    `${templateName}::${modelName}`,
    runId
  ));
}

function buildSessionTimelineRecord(run) {
  return {
    runId: run.runId,
    templateName: run.templateName,
    modelName: run.modelName,
    projectName: run.projectName,
    orchestrationRound: run.orchestrationRound,
    previousRunId: run.previousRunId,
    overallStatus: run.overallStatus,
    currentPhase: run.currentPhase,
    currentPhaseLabel: run.currentPhaseLabel,
    currentStepLabel: run.currentStepLabel,
    currentTargetLabel: run.currentTargetLabel,
    latestMessage: run.latestMessage,
    nextAction: run.nextAction,
    nextRoundStatus: run.nextRound?.status || null,
    nextRoundShouldStart: run.nextRound?.shouldStart ?? null,
    nextRoundTargetStage: run.nextRound?.targetStage || null,
    waitingHuman: run.humanTakeover?.status && run.humanTakeover.status !== 'none',
    waitingReason: run.waitingReason || run.humanTakeover?.waitingReason || null,
    humanTakeoverStatus: run.humanTakeover?.status || 'none',
    humanTakeoverResolutionStatus: run.humanTakeover?.resolutionStatus || null,
    humanTakeoverResolvedAt: run.humanTakeover?.resolvedAt || null,
    humanTakeoverReadyToResume: run.humanTakeover?.readyToResume ?? null,
    pendingActionCount: run.humanTakeover?.pendingActionCount || 0,
    scheduledActionCount: run.actionCenter?.scheduledActionCount || 0,
    resumeCommand: run.humanTakeover?.resumeCommand || null,
    elapsedMs: run.elapsedMs || null,
    updatedAt: run.updatedAt || '',
    notes: run.notes || []
  };
}

function buildSessionSummary(sessionId, runs) {
  const orderedRuns = runs.slice().sort(compareRunsByRecency);
  const latestRun = orderedRuns[0];
  const oldestRun = runs.slice().sort((left, right) => compareIsoDesc(left?.updatedAt, right?.updatedAt)).at(-1) || latestRun;
  const unresolvedHumanRun = orderedRuns.find((run) =>
    run.humanTakeover?.status
    && run.humanTakeover.status !== 'none'
    && run.humanTakeover.resolutionStatus !== 'resolved'
  ) || null;

  return {
    schema_version: 'stage2_orchestration_session_summary.v1',
    session_id: sessionId,
    session_directory: buildSessionDirectoryName(sessionId),
    template_name: latestRun?.templateName || '未知模板',
    model_name: latestRun?.modelName || '未知模型',
    project_name: latestRun?.projectName || '第二阶段原型',
    run_ids: orderedRuns.map((run) => run.runId),
    run_count: orderedRuns.length,
    latest_run_id: latestRun?.runId || null,
    latest_run_status: latestRun?.overallStatus || 'unknown',
    latest_next_round_status: latestRun?.nextRound?.status || null,
    latest_message: latestRun?.latestMessage || null,
    waiting_human: Boolean(unresolvedHumanRun),
    unresolved_human_run_id: unresolvedHumanRun?.runId || null,
    latest_resume_command: unresolvedHumanRun?.humanTakeover?.resumeCommand || latestRun?.humanTakeover?.resumeCommand || null,
    started_at: oldestRun?.updatedAt || '',
    updated_at: latestRun?.updatedAt || '',
    stats: {
      failed_runs: orderedRuns.filter((run) => run.overallStatus === 'failed').length,
      waiting_human_runs: orderedRuns.filter((run) => run.humanTakeover?.status && run.humanTakeover.status !== 'none').length,
      scheduled_next_round_runs: orderedRuns.filter((run) => run.nextRound?.shouldStart).length,
      promotion_candidate_total: orderedRuns.reduce((total, run) => total + Number(run.stats?.promotionCandidates || 0), 0)
    }
  };
}

async function syncStage2SessionArtifacts(runSummaries) {
  const groupedRuns = new Map();

  for (const run of runSummaries) {
    const sessionId = textOrNull(firstPresent(run.sessionId, run.orchestrationStreamId));
    if (!sessionId) {
      continue;
    }
    if (!groupedRuns.has(sessionId)) {
      groupedRuns.set(sessionId, []);
    }
    groupedRuns.get(sessionId).push(run);
  }

  const sessionSummaries = Array.from(groupedRuns.entries())
    .map(([sessionId, runs]) => {
      const summary = buildSessionSummary(sessionId, runs);
      return {
        sessionId,
        directoryName: summary.session_directory,
        templateName: summary.template_name,
        modelName: summary.model_name,
        projectName: summary.project_name,
        runIds: summary.run_ids,
        runCount: summary.run_count,
        latestRunId: summary.latest_run_id,
        latestRunStatus: summary.latest_run_status,
        latestNextRoundStatus: summary.latest_next_round_status,
        latestMessage: summary.latest_message,
        waitingHuman: summary.waiting_human,
        unresolvedHumanRunId: summary.unresolved_human_run_id,
        latestResumeCommand: summary.latest_resume_command,
        startedAt: summary.started_at,
        updatedAt: summary.updated_at,
        stats: summary.stats,
        timeline: runs
          .slice()
          .sort(compareRunsByRecency)
          .map(buildSessionTimelineRecord)
      };
    })
    .sort((left, right) => compareIsoDesc(left.updatedAt, right.updatedAt) || left.sessionId.localeCompare(right.sessionId));

  await fs.mkdir(SESSIONS_DIR, { recursive: true });

  await Promise.all(sessionSummaries.map(async (session) => {
    const sessionDir = path.join(SESSIONS_DIR, session.directoryName);
    await fs.mkdir(sessionDir, { recursive: true });
    await fs.writeFile(
      path.join(sessionDir, 'session_summary.json'),
      JSON.stringify({
        schema_version: 'stage2_orchestration_session_summary.v1',
        session_id: session.sessionId,
        session_directory: session.directoryName,
        template_name: session.templateName,
        model_name: session.modelName,
        project_name: session.projectName,
        run_ids: session.runIds,
        run_count: session.runCount,
        latest_run_id: session.latestRunId,
        latest_run_status: session.latestRunStatus,
        latest_next_round_status: session.latestNextRoundStatus,
        latest_message: session.latestMessage,
        waiting_human: session.waitingHuman,
        unresolved_human_run_id: session.unresolvedHumanRunId,
        latest_resume_command: session.latestResumeCommand,
        started_at: session.startedAt,
        updated_at: session.updatedAt,
        stats: session.stats
      }, null, 2),
      'utf8'
    );
    await fs.writeFile(
      path.join(sessionDir, 'session_timeline.json'),
      JSON.stringify({
        schema_version: 'stage2_orchestration_session_timeline.v1',
        session_id: session.sessionId,
        generated_at: new Date().toISOString(),
        runs: session.timeline
      }, null, 2),
      'utf8'
    );
  }));

  await fs.writeFile(
    path.join(SESSIONS_DIR, 'index.json'),
    JSON.stringify({
      schema_version: 'stage2_orchestration_session_index.v1',
      generated_at: new Date().toISOString(),
      session_count: sessionSummaries.length,
      sessions: sessionSummaries.map((session) => ({
        session_id: session.sessionId,
        session_directory: session.directoryName,
        template_name: session.templateName,
        model_name: session.modelName,
        project_name: session.projectName,
        run_count: session.runCount,
        latest_run_id: session.latestRunId,
        latest_run_status: session.latestRunStatus,
        latest_next_round_status: session.latestNextRoundStatus,
        waiting_human: session.waitingHuman,
        updated_at: session.updatedAt
      }))
    }, null, 2),
    'utf8'
  );

  return sessionSummaries;
}

async function readSessionSummariesFromArtifacts() {
  const indexPath = path.join(SESSIONS_DIR, 'index.json');
  const indexPayload = await readJsonIfExists(indexPath);
  if (!indexPayload || !Array.isArray(indexPayload.sessions) || !indexPayload.sessions.length) {
    return [];
  }

  const sessions = await Promise.all(indexPayload.sessions.map(async (entry) => {
    const directoryName = textOrNull(firstPresent(entry.session_directory));
    if (!directoryName) {
      return null;
    }
    const sessionDir = path.join(SESSIONS_DIR, directoryName);
    const [summary, timeline] = await Promise.all([
      readJsonIfExists(path.join(sessionDir, 'session_summary.json')),
      readJsonIfExists(path.join(sessionDir, 'session_timeline.json'))
    ]);
    if (!summary) {
      return null;
    }
    return {
      sessionId: textOrNull(firstPresent(summary.session_id, entry.session_id)),
      directoryName,
      templateName: textOrNull(firstPresent(summary.template_name, entry.template_name)),
      modelName: textOrNull(firstPresent(summary.model_name, entry.model_name)),
      projectName: textOrNull(firstPresent(summary.project_name, entry.project_name)),
      runIds: Array.isArray(summary.run_ids) ? summary.run_ids : [],
      runCount: Number(firstPresent(summary.run_count, entry.run_count, 0)) || 0,
      latestRunId: textOrNull(firstPresent(summary.latest_run_id, entry.latest_run_id)),
      latestRunStatus: textOrNull(firstPresent(summary.latest_run_status, entry.latest_run_status)),
      latestNextRoundStatus: textOrNull(firstPresent(summary.latest_next_round_status, entry.latest_next_round_status)),
      latestMessage: textOrNull(firstPresent(summary.latest_message)),
      waitingHuman: Boolean(firstPresent(summary.waiting_human, entry.waiting_human)),
      unresolvedHumanRunId: textOrNull(firstPresent(summary.unresolved_human_run_id)),
      latestResumeCommand: textOrNull(firstPresent(summary.latest_resume_command)),
      startedAt: textOrNull(firstPresent(summary.started_at)),
      updatedAt: textOrNull(firstPresent(summary.updated_at, entry.updated_at)),
      stats: summary.stats && typeof summary.stats === 'object' ? {
        failedRuns: Number(firstPresent(summary.stats.failed_runs, 0)) || 0,
        waitingHumanRuns: Number(firstPresent(summary.stats.waiting_human_runs, 0)) || 0,
        scheduledNextRoundRuns: Number(firstPresent(summary.stats.scheduled_next_round_runs, 0)) || 0,
        promotionCandidateTotal: Number(firstPresent(summary.stats.promotion_candidate_total, 0)) || 0
      } : {},
      timeline: Array.isArray(timeline?.runs) ? timeline.runs : []
    };
  }));

  return sessions
    .filter(Boolean)
    .sort((left, right) => compareIsoDesc(left.updatedAt, right.updatedAt) || left.sessionId.localeCompare(right.sessionId));
}

function toPublicArtifactDescriptor(descriptor) {
  const { path: artifactPath, ...rest } = descriptor;
  return rest;
}

function toPublicActionCenter(actionCenter) {
  return {
    resumeCommand: actionCenter.resumeCommand,
    pendingActionCount: actionCenter.pendingActionCount,
    scheduledActionCount: actionCenter.scheduledActionCount,
    controlLoop: actionCenter.controlLoop,
    artifactGroups: actionCenter.artifactGroups.map((group) => ({
      ...group,
      items: group.items.map(toPublicArtifactDescriptor)
    }))
  };
}

function flattenArtifactGroups(actionCenter) {
  return actionCenter.artifactGroups.flatMap((group) => group.items);
}

function buildRunArtifactHref(runId, artifactKey) {
  return `/api/stage2/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactKey)}`;
}

function buildHumanLoopArtifactHref(sessionId, artifactKey) {
  return `/api/stage2/human-loop/${encodeURIComponent(sessionId)}/artifacts/${encodeURIComponent(artifactKey)}`;
}

function buildOperationArtifactHref(sessionId, artifactKey) {
  return `/api/stage2/operation/artifacts/${encodeURIComponent(sessionId)}/${encodeURIComponent(artifactKey)}`;
}

function isWithinRoot(filePath, rootPath) {
  const resolvedRoot = path.resolve(rootPath);
  const resolvedPath = path.resolve(filePath);
  return resolvedPath === resolvedRoot || resolvedPath.startsWith(`${resolvedRoot}${path.sep}`);
}

function safeOperationArtifactPath(filePath) {
  if (!filePath || typeof filePath !== 'string') {
    return null;
  }

  const resolvedPath = path.resolve(__dirname, '..', filePath);
  if (![STAGE2_DIR, TEMPLATE_ROOT].some((root) => isWithinRoot(resolvedPath, root))) {
    return null;
  }
  return resolvedPath;
}

async function fileExists(filePath) {
  try {
    const stat = await fs.stat(filePath);
    return stat.isFile();
  } catch {
    return false;
  }
}

async function buildStaticArtifactDescriptor({
  key,
  href,
  label,
  description,
  filePath
}) {
  const resolvedPath = safeArtifactPath(filePath);
  if (!resolvedPath || !(await pathExists(resolvedPath))) {
    return null;
  }

  return {
    key,
    label,
    description: description || '',
    kind: inferArtifactKind(resolvedPath),
    fileName: path.basename(resolvedPath),
    href,
    path: resolvedPath
  };
}

async function buildActionCenter(runId, runDir, {
  runReport,
  humanTakeover,
  humanTakeoverResolution,
  nextRoundDecision,
  retryPlan,
  stopConditions,
  effectiveHumanTakeover
}) {
  const usedKeys = new Set();
  const seenPaths = new Set();
  const groupOrder = [
    { key: 'operations', label: '运行操作' },
    { key: 'control', label: '控制闭环' },
    { key: 'discovery', label: '发现说明' },
    { key: 'evidence', label: '关键证据' },
    { key: 'assets', label: '项目产物' }
  ];
  const groupMap = new Map(groupOrder.map((group) => [group.key, []]));

  const addDescriptor = async ({ groupKey, label, filePath, description, preferredKey }) => {
    const resolvedPath = safeArtifactPath(filePath);
    if (!resolvedPath || seenPaths.has(resolvedPath) || !(await pathExists(resolvedPath))) {
      return;
    }
    seenPaths.add(resolvedPath);
    const key = ensureUniqueKey(preferredKey || `${groupKey}_${label}_${path.basename(resolvedPath)}`, usedKeys);
    groupMap.get(groupKey).push({
      key,
      label,
      description: description || '',
      kind: inferArtifactKind(resolvedPath),
      fileName: path.basename(resolvedPath),
      href: `/api/stage2/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(key)}`,
      path: resolvedPath
    });
  };

  const coreArtifacts = [
    {
      groupKey: 'operations',
      label: 'Run Report.md',
      filePath: path.join(runDir, 'reports', 'run_report.md'),
      description: '可读报告',
      preferredKey: 'run_report_md'
    },
    {
      groupKey: 'operations',
      label: 'Run Report.json',
      filePath: path.join(runDir, 'reports', 'run_report.json'),
      description: '结构化报告',
      preferredKey: 'run_report_json'
    },
    {
      groupKey: 'operations',
      label: 'Human Takeover',
      filePath: path.join(runDir, 'human_takeover.json'),
      description: '人工接管恢复包',
      preferredKey: 'human_takeover_json'
    },
    {
      groupKey: 'operations',
      label: 'Human Resolution',
      filePath: path.join(runDir, 'human_takeover_resolution.json'),
      description: '人工处理完成标记',
      preferredKey: 'human_takeover_resolution_json'
    },
    {
      groupKey: 'control',
      label: 'Promotion Candidates',
      filePath: path.join(runDir, 'promotion_candidates.json'),
      description: '候选沉淀清单',
      preferredKey: 'promotion_candidates_json'
    },
    {
      groupKey: 'control',
      label: 'Next Round Decision',
      filePath: path.join(runDir, 'next_round_decision.json'),
      description: '下一轮调度决策',
      preferredKey: 'next_round_decision_json'
    },
    {
      groupKey: 'control',
      label: 'Retry Plan',
      filePath: path.join(runDir, 'retry_plan.json'),
      description: '重试计划',
      preferredKey: 'retry_plan_json'
    },
    {
      groupKey: 'control',
      label: 'Stop Conditions',
      filePath: path.join(runDir, 'stop_conditions.json'),
      description: '停止条件判定',
      preferredKey: 'stop_conditions_json'
    },
    {
      groupKey: 'control',
      label: 'Current Status',
      filePath: path.join(runDir, 'current_status.json'),
      description: '当前运行状态快照',
      preferredKey: 'current_status_json'
    },
    {
      groupKey: 'control',
      label: 'Phase Summary',
      filePath: path.join(runDir, 'phase_summary.json'),
      description: '阶段汇总',
      preferredKey: 'phase_summary_json'
    },
    {
      groupKey: 'operations',
      label: 'Progress View',
      filePath: path.join(runDir, 'reports', 'progress_view.md'),
      description: '运行进度视图',
      preferredKey: 'progress_view_md'
    },
    {
      groupKey: 'assets',
      label: 'Baseline Snapshot',
      filePath: path.join(runDir, 'baseline_snapshot.json'),
      description: '项目级基线快照',
      preferredKey: 'baseline_snapshot_json'
    },
    {
      groupKey: 'assets',
      label: 'Runtime Data',
      filePath: path.join(runDir, 'runtime_data.json'),
      description: '运行期数据快照',
      preferredKey: 'runtime_data_json'
    },
    {
      groupKey: 'discovery',
      label: 'Routing Summary',
      filePath: path.join(runDir, 'routing_summary.json'),
      description: '模型路由解释',
      preferredKey: 'routing_summary_json'
    },
    {
      groupKey: 'discovery',
      label: 'Discovery Strategy',
      filePath: path.join(runDir, 'discovery_strategy.json'),
      description: '发现策略说明',
      preferredKey: 'discovery_strategy_json'
    },
    {
      groupKey: 'discovery',
      label: 'Page Entries',
      filePath: path.join(runDir, 'page_entries.json'),
      description: '页面入口清单',
      preferredKey: 'page_entries_json'
    },
    {
      groupKey: 'discovery',
      label: 'Feature Points',
      filePath: path.join(runDir, 'feature_points.json'),
      description: '功能点清单',
      preferredKey: 'feature_points_json'
    },
    {
      groupKey: 'discovery',
      label: 'Discovery Result',
      filePath: path.join(runDir, 'discovery_result.json'),
      description: '发现摘要',
      preferredKey: 'discovery_result_json'
    },
    {
      groupKey: 'discovery',
      label: 'Discovery Review Queue',
      filePath: path.join(runDir, 'discovery_review_queue.json'),
      description: '人工审核占位',
      preferredKey: 'discovery_review_queue_json'
    }
  ];

  for (const artifact of coreArtifacts) {
    await addDescriptor(artifact);
  }

  for (const artifact of runReport?.key_artifacts || []) {
    await addDescriptor({
      groupKey: 'evidence',
      label: artifact.label || path.basename(String(artifact.path || 'artifact')),
      filePath: artifact.path,
      description: '关键证据',
      preferredKey: `evidence_${artifact.label || path.basename(String(artifact.path || 'artifact'))}`
    });
  }

  for (const assetGroup of runReport?.project_assets || []) {
    for (const artifact of assetGroup.artifacts || []) {
      await addDescriptor({
        groupKey: 'assets',
        label: artifact.label || path.basename(String(artifact.path || 'artifact')),
        filePath: artifact.path,
        description: assetGroup.name || '项目产物',
        preferredKey: `asset_${assetGroup.name}_${artifact.label || path.basename(String(artifact.path || 'artifact'))}`
      });
    }
  }

  const controlLoop = buildControlLoop({
    runReport,
    nextRoundDecision,
    stopConditions,
    retryPlan,
    effectiveHumanTakeover,
    humanTakeoverResolution
  });

  return {
    resumeCommand: controlLoop.humanTakeover.resumeCommand || humanTakeover?.resume_command || null,
    pendingActionCount: controlLoop.pendingHumanActions.length,
    scheduledActionCount: controlLoop.scheduledActions.length,
    controlLoop,
    artifactGroups: groupOrder
      .map((group) => ({
        key: group.key,
        label: group.label,
        items: groupMap.get(group.key) || []
      }))
      .filter((group) => group.items.length > 0)
  };
}

function normalizePhaseTimeline(phaseSummary) {
  if (!phaseSummary?.phases) {
    return [];
  }
  return Object.values(phaseSummary.phases)
    .sort((left, right) => new Date(left.updated_at || 0) - new Date(right.updated_at || 0))
    .map((phase) => ({
      key: phase.phase,
      label: phase.phase_label,
      status: phase.status,
      currentRoundLabel: phase.current_round?.label || '',
      lastStepLabel: phase.last_step?.label || '',
      lastTargetLabel: phase.last_target?.label || '',
      message: phase.last_message || '',
      nextAction: phase.next_action || '',
      updatedAt: phase.updated_at || ''
    }));
}

function summarizePromotionReview(runReport = {}) {
  const summary = runReport?.promotion_candidate_summary || {};
  const candidates = Array.isArray(summary.candidates) && summary.candidates.length
    ? summary.candidates
    : (Array.isArray(runReport?.promotion_candidates) ? runReport.promotion_candidates : []);
  const summaryFacts = factMap(summary.facts);
  const summaryExtra = summary?.extra && typeof summary.extra === 'object' && !Array.isArray(summary.extra)
    ? summary.extra
    : {};

  const titleSource = candidates
    .map((candidate) => textOrNull(firstPresent(candidate?.name, candidate?.title, candidate?.item_id)))
    .filter(Boolean);

  return {
    summary: textOrNull(firstPresent(summary.summary)),
    candidateCount: candidates.length,
    topCandidateTitles: titleSource.slice(0, 3),
    approvalNotes: uniqueTexts(summary.approval_notes),
    evidenceRequirements: uniqueTexts(summary.evidence_requirements),
    reviewStatus: textOrNull(firstPresent(summaryExtra.review_status, summaryFacts.review_status)),
    manualReviewRequired: firstPresent(summaryExtra.manual_review_required, summaryFacts.manual_review_required),
    reviewStatusBreakdown: summaryExtra.review_status_breakdown || {},
    promotionTargetBreakdown: summaryExtra.promotion_target_breakdown || {},
    promotionRecommendationBreakdown: summaryExtra.promotion_recommendation_breakdown || {},
    baselineFreezeCandidateCount: Number(firstPresent(
      summaryExtra.baseline_freeze_candidate_count,
      Array.isArray(summaryExtra.baseline_freeze_candidate_ids) ? summaryExtra.baseline_freeze_candidate_ids.length : null,
      0
    )) || 0,
    readyCandidateCount: Number(firstPresent(
      Array.isArray(summaryExtra.ready_candidate_ids) ? summaryExtra.ready_candidate_ids.length : null,
      summaryExtra.review_status_breakdown?.ready_for_review,
      0
    )) || 0,
    deferredCandidateCount: Number(firstPresent(
      Array.isArray(summaryExtra.deferred_candidate_ids) ? summaryExtra.deferred_candidate_ids.length : null,
      0
    )) || 0
  };
}

function summarizeRun({
  runId,
  currentStatus,
  nextRoundDecision,
  effectiveHumanTakeover,
  humanTakeoverResolution,
  phaseSummary,
  runReport,
  roundInput,
  actionCenter
}) {
  const reportSummary = runReport?.summary || {};
  const summaryFacts = factMap(reportSummary.facts);
  const currentPhaseLabel = currentStatus?.current_phase_label
    || normalizePhaseTimeline(phaseSummary).at(-1)?.label
    || '未知阶段';
  const phaseTimeline = normalizePhaseTimeline(phaseSummary);
  const orchestrationStreamId = buildRunSessionId({ currentStatus, runReport, roundInput, runId });

  return {
    runId,
    sessionId: orchestrationStreamId,
    orchestrationStreamId,
    orchestrationRound: firstPresent(roundInput?.round_index, reportSummary.orchestration_round),
    previousRunId: textOrNull(firstPresent(roundInput?.previous_run_id, reportSummary.previous_run_id)),
    templateName: currentStatus?.template_name || reportSummary.template_name || '未知模板',
    modelName: currentStatus?.model_name || summaryFacts.model_name || '未知模型',
    projectName: currentStatus?.project_name || reportSummary.project_name || '第二阶段原型',
    overallStatus: currentStatus?.overall_status || reportSummary.status || 'unknown',
    currentPhase: currentStatus?.current_phase || phaseTimeline.at(-1)?.key || '',
    currentPhaseLabel,
    currentRoundLabel: currentStatus?.current_round?.label || '',
    currentStepLabel: currentStatus?.current_step?.label || phaseTimeline.at(-1)?.lastStepLabel || '',
    currentTargetLabel: currentStatus?.current_target?.label || phaseTimeline.at(-1)?.lastTargetLabel || '',
    latestMessage: currentStatus?.latest_message || reportSummary.stop_reason || reportSummary.summary || '暂无摘要',
    nextAction: currentStatus?.next_action || reportSummary.next_action || phaseTimeline.at(-1)?.nextAction || '等待后续动作',
    waitingReason: currentStatus?.waiting_reason || actionCenter.controlLoop.humanTakeover.waitingReason || null,
    blockedReason: currentStatus?.blocked_reason || nextRoundDecision?.primary_reason || null,
    elapsedMs: currentStatus?.elapsed_ms || null,
    updatedAt: currentStatus?.updated_at || reportSummary.finished_at || reportSummary.started_at || '',
    stats: {
      pageEntries: currentStatus?.stats?.page_entries_discovered || runReport?.page_entries?.length || 0,
      featurePoints: currentStatus?.stats?.feature_points_discovered || runReport?.feature_points?.length || 0,
      verificationSuccesses: currentStatus?.stats?.verification_successes || runReport?.success_items?.length || 0,
      failureClusters: reportSummary.failure_cluster_count || runReport?.failure_clusters?.length || 0,
      promotionCandidates: reportSummary.promotion_candidate_count || runReport?.promotion_candidates?.length || 0
    },
    promotionReview: summarizePromotionReview(runReport),
    nextRound: {
      status: actionCenter.controlLoop.nextRound.status || reportSummary.next_round_status || null,
      shouldStart: actionCenter.controlLoop.nextRound.shouldStart ?? reportSummary.next_round_should_start ?? null,
      targetStage: actionCenter.controlLoop.nextRound.targetStage || null,
      reason: actionCenter.controlLoop.nextRound.reason || null
    },
    humanTakeover: effectiveHumanTakeover ? {
      status: actionCenter.controlLoop.humanTakeover.status || 'needs_review',
      targetStage: actionCenter.controlLoop.humanTakeover.targetStage || null,
      waitingReason: actionCenter.controlLoop.humanTakeover.waitingReason || null,
      resumeCommand: actionCenter.controlLoop.humanTakeover.resumeCommand || null,
      pendingActionCount: actionCenter.controlLoop.pendingHumanActions.length,
      resolutionStatus: actionCenter.controlLoop.humanTakeover.resolutionStatus || textOrNull(firstPresent(humanTakeoverResolution?.status)),
      resolvedAt: actionCenter.controlLoop.humanTakeover.resolvedAt || textOrNull(firstPresent(humanTakeoverResolution?.resolved_at)),
      resolutionOperator: actionCenter.controlLoop.humanTakeover.resolutionOperator || textOrNull(firstPresent(humanTakeoverResolution?.operator_id)),
      resolutionNote: actionCenter.controlLoop.humanTakeover.resolutionNote || textOrNull(firstPresent(humanTakeoverResolution?.note)),
      readyToResume: actionCenter.controlLoop.humanTakeover.readyToResume ?? firstPresent(humanTakeoverResolution?.ready_to_resume),
      notes: actionCenter.controlLoop.humanTakeover.notes
    } : {
      status: 'none',
      targetStage: null,
      waitingReason: null,
      resumeCommand: null,
      pendingActionCount: 0,
      resolutionStatus: textOrNull(firstPresent(humanTakeoverResolution?.status)),
      resolvedAt: textOrNull(firstPresent(humanTakeoverResolution?.resolved_at)),
      resolutionOperator: textOrNull(firstPresent(humanTakeoverResolution?.operator_id)),
      resolutionNote: textOrNull(firstPresent(humanTakeoverResolution?.note)),
      readyToResume: firstPresent(humanTakeoverResolution?.ready_to_resume),
      notes: []
    },
    actionCenter: toPublicActionCenter(actionCenter),
    phaseTimeline,
    recentEvents: currentStatus?.recent_events || [],
    reportSummary: runReport?.summary || null,
    notes: runReport?.notes || []
  };
}

async function listRunDirectories() {
  try {
    const entries = await fs.readdir(STAGE2_DIR, { withFileTypes: true });
    return entries
      .filter((entry) => entry.isDirectory() && RUN_DIR_PATTERN.test(entry.name))
      .sort((left, right) => right.name.localeCompare(left.name));
  } catch {
    return [];
  }
}

async function readRunDirectory(runDirName) {
  const runDir = path.join(STAGE2_DIR, runDirName);
  const [
    currentStatus,
    nextRoundDecision,
    humanTakeover,
    humanTakeoverResolution,
    phaseSummary,
    runReport,
    roundInput,
    retryPlan,
    stopConditions
  ] = await Promise.all([
    readJsonIfExists(path.join(runDir, 'current_status.json')),
    readJsonIfExists(path.join(runDir, 'next_round_decision.json')),
    readJsonIfExists(path.join(runDir, 'human_takeover.json')),
    readJsonIfExists(path.join(runDir, 'human_takeover_resolution.json')),
    readJsonIfExists(path.join(runDir, 'phase_summary.json')),
    readJsonIfExists(path.join(runDir, 'reports', 'run_report.json')),
    readJsonIfExists(path.join(runDir, 'round_input.json')),
    readJsonIfExists(path.join(runDir, 'retry_plan.json')),
    readJsonIfExists(path.join(runDir, 'stop_conditions.json'))
  ]);

  if (!currentStatus && !runReport) {
    return null;
  }

  const nextRoundSection = findReportSection(runReport, 'Next Round Decision');
  const effectiveHumanTakeover = buildEffectiveHumanTakeover(humanTakeover, nextRoundDecision, nextRoundSection);

  const actionCenter = await buildActionCenter(
    currentStatus?.run_id || runDirName,
    runDir,
    {
      runReport,
      humanTakeover,
      humanTakeoverResolution,
      nextRoundDecision,
      retryPlan,
      stopConditions,
      effectiveHumanTakeover
    }
  );

  return summarizeRun({
    runId: currentStatus?.run_id || runDirName,
    currentStatus,
    nextRoundDecision,
    effectiveHumanTakeover,
    humanTakeoverResolution,
    phaseSummary,
    runReport,
    roundInput,
    actionCenter
  });
}

async function resolveStage2RunArtifact(runId, artifactKey) {
  if (!RUN_DIR_PATTERN.test(runId)) {
    return null;
  }

  const runDir = path.join(STAGE2_DIR, runId);
  const [humanTakeover, humanTakeoverResolution, runReport, nextRoundDecision, retryPlan, stopConditions] = await Promise.all([
    readJsonIfExists(path.join(runDir, 'human_takeover.json')),
    readJsonIfExists(path.join(runDir, 'human_takeover_resolution.json')),
    readJsonIfExists(path.join(runDir, 'reports', 'run_report.json')),
    readJsonIfExists(path.join(runDir, 'next_round_decision.json')),
    readJsonIfExists(path.join(runDir, 'retry_plan.json')),
    readJsonIfExists(path.join(runDir, 'stop_conditions.json'))
  ]);

  if (!(await pathExists(runDir))) {
    return null;
  }

  const effectiveHumanTakeover = buildEffectiveHumanTakeover(
    humanTakeover,
    nextRoundDecision,
    findReportSection(runReport, 'Next Round Decision')
  );
  const actionCenter = await buildActionCenter(runId, runDir, {
    runReport,
    humanTakeover,
    humanTakeoverResolution,
    nextRoundDecision,
    retryPlan,
    stopConditions,
    effectiveHumanTakeover
  });
  return flattenArtifactGroups(actionCenter).find((item) => item.key === artifactKey) || null;
}

function countHumanLoopFieldMappings(draft = {}) {
  if (!Array.isArray(draft?.steps)) {
    return 0;
  }

  const uniqueRefs = new Set();
  for (const step of draft.steps) {
    const candidateRef = textOrNull(step?.field_mapping?.candidate_data_ref);
    if (candidateRef) {
      uniqueRefs.add(candidateRef);
    }
  }
  return uniqueRefs.size;
}

function countHumanLoopLocators(draft = {}) {
  if (!Array.isArray(draft?.steps)) {
    return 0;
  }

  const uniqueLocators = new Set();
  for (const step of draft.steps) {
    const locator = textOrNull(step?.locator);
    if (locator) {
      uniqueLocators.add(locator);
    }
  }
  return uniqueLocators.size;
}

function summarizeHumanLoopDraft(draft = {}) {
  const metadata = draft?.metadata && typeof draft.metadata === 'object' ? draft.metadata : {};
  const fieldCatalog = metadata?.field_catalog && typeof metadata.field_catalog === 'object' && !Array.isArray(metadata.field_catalog)
    ? metadata.field_catalog
    : {};
  const candidateSchema = metadata?.candidate_data_schema && typeof metadata.candidate_data_schema === 'object'
    ? metadata.candidate_data_schema
    : {};
  const candidateFieldRules = candidateSchema?.field_rules && typeof candidateSchema.field_rules === 'object' && !Array.isArray(candidateSchema.field_rules)
    ? candidateSchema.field_rules
    : {};
  const sampleDataRefs = uniqueTexts(
    (draft.steps || []).map((step) => step?.field_mapping?.candidate_data_ref)
  ).slice(0, 5);
  const sampleFieldKeys = [
    ...Object.keys(fieldCatalog),
    ...Object.keys(candidateFieldRules)
  ].filter((value, index, array) => array.indexOf(value) === index).slice(0, 5);

  return {
    draftVersion: textOrNull(firstPresent(draft?.version)),
    draftStepCount: Array.isArray(draft?.steps) ? draft.steps.length : 0,
    candidateFieldMappingCount: Number(firstPresent(
      metadata?.candidate_field_mapping_count,
      countHumanLoopFieldMappings(draft),
      0
    )) || 0,
    candidateLocatorCount: Number(firstPresent(
      metadata?.candidate_locator_count,
      countHumanLoopLocators(draft),
      0
    )) || 0,
    candidateDataFieldCount: Object.keys(candidateFieldRules).length,
    draftNotes: uniqueTexts(draft?.notes).slice(0, 3),
    aliasDraftSummary: {
      fieldCount: Math.max(Object.keys(fieldCatalog).length, Object.keys(candidateFieldRules).length),
      sampleFieldKeys,
      hasCandidateSchema: Object.keys(candidateFieldRules).length > 0,
      sampleDataRefs
    }
  };
}

async function buildHumanLoopArtifactDescriptors(sessionId, sessionDir) {
  const definitions = [
    {
      key: 'recording_summary_json',
      label: 'Recording Summary',
      description: '人工录制摘要',
      filePath: path.join(sessionDir, 'recording_summary.json')
    },
    {
      key: 'candidate_template_draft_json',
      label: 'Candidate Draft',
      description: '模板候选草稿',
      filePath: path.join(sessionDir, 'candidate_template_draft.json')
    },
    {
      key: 'candidate_template_review_json',
      label: 'Candidate Review',
      description: '模板候选审阅包',
      filePath: path.join(sessionDir, 'candidate_template_review.json')
    },
    {
      key: 'key_screenshots_json',
      label: 'Key Screenshots',
      description: '关键截图索引',
      filePath: path.join(sessionDir, 'key_screenshots.json')
    },
    {
      key: 'session_json',
      label: 'Session Snapshot',
      description: '录制会话快照',
      filePath: path.join(sessionDir, 'session.json')
    }
  ];

  return (await Promise.all(definitions.map((definition) => buildStaticArtifactDescriptor({
    ...definition,
    href: buildHumanLoopArtifactHref(sessionId, definition.key)
  })))).filter(Boolean);
}

async function resolveHumanLoopArtifact(sessionId, artifactKey) {
  if (!HUMAN_LOOP_SESSION_PATTERN.test(sessionId)) {
    return null;
  }

  const sessionDir = path.join(STAGE2_DIR, 'human_loop', sessionId);
  if (!(await pathExists(sessionDir))) {
    return null;
  }

  const descriptors = await buildHumanLoopArtifactDescriptors(sessionId, sessionDir);
  return descriptors.find((item) => item.key === artifactKey) || null;
}

async function readLatestHumanLoopSummary() {
  const humanLoopDir = path.join(STAGE2_DIR, 'human_loop');
  try {
    const entries = await fs.readdir(humanLoopDir, { withFileTypes: true });
    const latestDir = entries
      .filter((entry) => entry.isDirectory())
      .sort((left, right) => right.name.localeCompare(left.name))[0];
    if (!latestDir) {
      return null;
    }
    const sessionDir = path.join(humanLoopDir, latestDir.name);
    const [summary, draft, review, session, artifacts] = await Promise.all([
      readJsonIfExists(path.join(sessionDir, 'recording_summary.json')),
      readJsonIfExists(path.join(sessionDir, 'candidate_template_draft.json')),
      readJsonIfExists(path.join(sessionDir, 'candidate_template_review.json')),
      readJsonIfExists(path.join(sessionDir, 'session.json')),
      buildHumanLoopArtifactDescriptors(latestDir.name, sessionDir)
    ]);
    if (!summary) {
      return null;
    }
    const draftSummary = summarizeHumanLoopDraft(draft);
    const reviewFieldMappings = Array.isArray(review?.field_mappings) ? review.field_mappings : [];
    const reviewSampleFieldKeys = reviewFieldMappings
      .map((item) => textOrNull(firstPresent(item?.project_field_key, item?.draft_field_key)))
      .filter(Boolean)
      .slice(0, 5);
    return {
      sessionId: latestDir.name,
      durationMs: summary.duration_ms || 0,
      actionEventCount: summary.action_event_count || 0,
      keyScreenshotCount: summary.key_screenshot_count || 0,
      uniqueTargetCount: summary.unique_target_count || 0,
      warnings: summary.warnings || [],
      templateName: textOrNull(firstPresent(session?.session?.template_name, draft?.template_name)),
      operatorId: textOrNull(firstPresent(session?.session?.operator_id, draft?.metadata?.operator_id)),
      startUrl: textOrNull(firstPresent(session?.session?.start_url, draft?.page_entry?.url)),
      taskDescription: textOrNull(firstPresent(session?.session?.task_description)),
      pageUrlCount: Array.isArray(summary.page_urls) ? summary.page_urls.length : 0,
      frameUrlCount: Array.isArray(summary.frame_urls) ? summary.frame_urls.length : 0,
      draftVersion: draftSummary.draftVersion,
      draftStepCount: draftSummary.draftStepCount,
      candidateFieldMappingCount: Number(firstPresent(
        review?.mapping_summary?.candidate_field_count,
        draftSummary.candidateFieldMappingCount,
        0
      )) || 0,
      candidateLocatorCount: draftSummary.candidateLocatorCount,
      candidateDataFieldCount: Math.max(
        draftSummary.candidateDataFieldCount,
        Number(firstPresent(review?.mapping_summary?.candidate_field_count, 0)) || 0
      ),
      mappedProjectFieldCount: Number(firstPresent(review?.mapping_summary?.mapped_project_field_count, 0)) || 0,
      needsReviewCount: Number(firstPresent(review?.mapping_summary?.needs_review_count, 0)) || 0,
      draftNotes: uniqueTexts(review?.notes, draftSummary.draftNotes).slice(0, 4),
      aliasDraftSummary: draftSummary.aliasDraftSummary,
      quality: summary.quality || draft?.metadata?.quality || null,
      interactionSourceCounts: summary.interaction_source_counts || draft?.metadata?.interaction_source_counts || {},
      eventTypeCounts: summary.event_type_counts || draft?.metadata?.capture_summary?.event_type_counts || {},
      projectFieldCandidateCount: Number(firstPresent(review?.project_field_context?.candidate_count, 0)) || 0,
      reviewFieldKeys: reviewSampleFieldKeys.length
        ? reviewSampleFieldKeys
        : draftSummary.aliasDraftSummary.sampleFieldKeys,
      artifacts: artifacts.map(toPublicArtifactDescriptor)
    };
  } catch {
    return null;
  }
}

async function buildRunArtifactReferences(runId, artifactDefinitions) {
  const items = await Promise.all(artifactDefinitions.map((definition) => buildStaticArtifactDescriptor({
    ...definition,
    href: buildRunArtifactHref(runId, definition.key)
  })));
  return items.filter(Boolean);
}

async function summarizeBaselineFreezeManifest(manifest) {
  if (!manifest || typeof manifest !== 'object') {
    return null;
  }

  const recommendedRun = manifest.recommended_primary_run || null;
  const runId = textOrNull(firstPresent(
    recommendedRun?.run_dir ? path.basename(String(recommendedRun.run_dir)) : null
  ));

  const artifactReferences = runId && RUN_DIR_PATTERN.test(runId)
    ? await buildRunArtifactReferences(runId, [
      {
        key: 'baseline_snapshot_json',
        label: 'Baseline Snapshot',
        description: '项目级基线快照',
        filePath: recommendedRun?.artifacts?.baseline_snapshot
      },
      {
        key: 'runtime_data_json',
        label: 'Runtime Data',
        description: '运行期数据快照',
        filePath: recommendedRun?.artifacts?.runtime_data
      },
      {
        key: 'run_report_json',
        label: 'Run Report.json',
        description: '结构化报告',
        filePath: recommendedRun?.artifacts?.run_report_json
      },
      {
        key: 'run_report_md',
        label: 'Run Report.md',
        description: '可读报告',
        filePath: recommendedRun?.artifacts?.run_report_markdown
      },
      {
        key: 'progress_view_md',
        label: 'Progress View',
        description: '运行进度视图',
        filePath: recommendedRun?.artifacts?.progress_view
      }
    ])
    : [];

  return {
    freezeRecommended: Boolean(manifest.freeze_recommended),
    selectionReason: textOrNull(firstPresent(manifest.selection_reason)),
    runCount: Number(firstPresent(manifest.run_count, 0)) || 0,
    successfulRunCount: Number(firstPresent(manifest.successful_run_count, 0)) || 0,
    templateName: textOrNull(firstPresent(manifest.template_name)),
    notes: uniqueTexts(manifest.notes),
    recommendedPrimaryRun: recommendedRun ? {
      model: textOrNull(firstPresent(recommendedRun.model)),
      runId: runId && RUN_DIR_PATTERN.test(runId) ? runId : null,
      status: textOrNull(firstPresent(recommendedRun.status)),
      elapsedMs: Number(firstPresent(recommendedRun.elapsed_ms, 0)) || 0,
      roundCount: Number(firstPresent(recommendedRun.round_count, 0)) || 0,
      nextRoundStatus: textOrNull(firstPresent(recommendedRun.next_round_status)),
      shouldStartNextRound: firstPresent(recommendedRun.should_start_next_round),
      triggeredStopConditions: uniqueTexts(recommendedRun.triggered_stop_conditions),
      artifacts: artifactReferences.map(toPublicArtifactDescriptor)
    } : null
  };
}

function buildOperationParamSummary(params = {}) {
  return {
    systemName: textOrNull(firstPresent(params.systemName, params.targetName)),
    systemKey: textOrNull(firstPresent(params.systemKey)),
    systemMapTemplate: textOrNull(firstPresent(params.systemMapTemplate)),
    targetTemplate: textOrNull(firstPresent(params.targetTemplate)),
    startUrl: textOrNull(firstPresent(params.startUrl)),
    pageUrl: textOrNull(firstPresent(params.pageUrl)),
    pageName: textOrNull(firstPresent(params.pageName)),
    scenarioKind: textOrNull(firstPresent(params.scenarioKind)),
    model: textOrNull(firstPresent(params.model)),
    cdpUrl: textOrNull(firstPresent(params.cdpUrl)),
    runDir: textOrNull(firstPresent(params.runDir))
  };
}

function normalizeOperationStep(step = {}) {
  return {
    stepId: textOrNull(firstPresent(step.step_id, step.stepId)),
    label: textOrNull(firstPresent(step.label)),
    manualStep: Number(firstPresent(step.manual_step, step.manualStep, 0)) || 0,
    status: textOrNull(firstPresent(step.status, 'unknown')),
    startedAt: textOrNull(firstPresent(step.started_at, step.startedAt)),
    finishedAt: textOrNull(firstPresent(step.finished_at, step.finishedAt)),
    resultPath: textOrNull(firstPresent(step.result_path, step.resultPath)),
    stdoutPreview: textOrNull(firstPresent(step.stdout_preview, step.stdoutPreview)),
    stderrPreview: textOrNull(firstPresent(step.stderr_preview, step.stderrPreview)),
    error: textOrNull(firstPresent(step.error))
  };
}

function sortOperationSteps(steps = {}) {
  return Object.values(steps || {})
    .map(normalizeOperationStep)
    .filter((step) => step.stepId)
    .sort((left, right) => {
      const manualOrder = Number(left.manualStep || 0) - Number(right.manualStep || 0);
      if (manualOrder) {
        return manualOrder;
      }
      return String(left.startedAt || '').localeCompare(String(right.startedAt || ''));
    });
}

function summarizeOperationStatus(steps, lastStepId) {
  if (!steps.length) {
    return {
      status: 'idle',
      lastStepId: lastStepId || null,
      lastStepStatus: null,
      runningStepId: null,
      failedStepCount: 0,
      completedStepCount: 0
    };
  }

  const runningStep = steps.find((step) => step.status === 'running') || null;
  const lastStep = steps.find((step) => step.stepId === lastStepId) || steps.at(-1);
  const failedStepCount = steps.filter((step) => step.status === 'failed').length;
  const completedStepCount = steps.filter((step) => step.status === 'completed').length;
  const status = runningStep
    ? 'running'
    : (lastStep?.status || (failedStepCount ? 'failed' : 'idle'));

  return {
    status,
    lastStepId: lastStep?.stepId || lastStepId || null,
    lastStepStatus: lastStep?.status || null,
    runningStepId: runningStep?.stepId || null,
    failedStepCount,
    completedStepCount
  };
}

function collectOperationPayloadPaths(payload, candidates) {
  if (!payload || typeof payload !== 'object') {
    return;
  }

  const add = (filePath, label, groupKey, preferredKey, description) => {
    if (typeof filePath === 'string' && filePath.trim()) {
      candidates.push({ filePath, label, groupKey, preferredKey, description });
    }
  };

  for (const key of [
    'progress_file',
    'progress_view',
    'page_entries',
    'feature_points',
    'retry_plan',
    'discovery_strategy',
    'routing_summary',
    'checklist_path',
    'markdown_path',
    'json_path',
    'navigation_tree_path',
    'page_semantic_summary_path',
    'navigation_nodes_path',
    'discovery_result_path',
    'screenshot_records_path',
    'candidate_review_path',
    'report_path'
  ]) {
    add(payload[key], path.basename(String(payload[key] || key)), 'evidence', key, '命令输出引用');
  }

  for (const directoryKey of ['run_dir', 'output_dir']) {
    const directory = textOrNull(firstPresent(payload[directoryKey]));
    if (!directory) {
      continue;
    }
    for (const fileName of [
      'validation_result.json',
      'verification_result.json',
      'network_events.json',
      'routing_summary.json',
      'discovery_strategy.json',
      'page_entries.json',
      'feature_points.json',
      'discovery_result.json',
      'navigation_tree.json',
      'navigation_nodes.json',
      'page_semantic_summary.json'
    ]) {
      add(path.join(directory, fileName), fileName, fileName.includes('validation') || fileName.includes('verification') || fileName.includes('network')
        ? 'validation'
        : 'discovery', `${directoryKey}_${fileName}`, '命令输出目录中的关键产物');
    }
  }

  for (const artifact of payload.artifacts || []) {
    add(
      artifact?.path,
      artifact?.label || path.basename(String(artifact?.path || 'artifact')),
      'validation',
      artifact?.label || path.basename(String(artifact?.path || 'artifact')),
      '验证产物'
    );
  }
}

async function readOperationCommandPayloads(steps) {
  const payloads = [];
  for (const step of steps) {
    const resultPath = safeOperationArtifactPath(step.resultPath);
    if (!resultPath || !(await fileExists(resultPath))) {
      continue;
    }
    const result = await readJsonIfExists(resultPath);
    if (result?.payload && typeof result.payload === 'object') {
      payloads.push(result.payload);
    }
  }
  return payloads;
}

function buildOperationArtifactCandidates(sessionId, sessionDir, state, params, steps, commandPayloads) {
  const classifyArtifactGroup = (filePath, fallback = 'evidence') => {
    const fileName = path.basename(String(filePath || '')).toLowerCase();
    if ([
      'operation_state.json',
      'events.jsonl'
    ].includes(fileName) || fileName.startsWith('command_result_')) {
      return 'operations';
    }
    if ([
      'navigation_tree.json',
      'page_semantic_summary.json',
      'navigation_nodes.json',
      'page_entries.json',
      'feature_points.json',
      'discovery_result.json',
      'discovery_review_queue.json',
      'routing_summary.json',
      'discovery_strategy.json'
    ].includes(fileName)) {
      return 'discovery';
    }
    if (fileName.startsWith('template_revision_checklist')) {
      return 'checklist';
    }
    if ([
      'validation_result.json',
      'verification_result.json',
      'network_events.json'
    ].includes(fileName)) {
      return 'validation';
    }
    if (fileName.startsWith('latest_validation_matrix')) {
      return 'matrix';
    }
    return fallback;
  };

  const candidates = [
    {
      groupKey: 'operations',
      key: 'operation_state',
      label: 'Operation State',
      description: '新系统接入操作状态',
      filePath: path.join(sessionDir, 'operation_state.json')
    },
    {
      groupKey: 'operations',
      key: 'events',
      label: 'Events',
      description: '新系统接入事件流',
      filePath: path.join(sessionDir, 'events.jsonl')
    }
  ];

  for (const step of steps) {
    if (!step.resultPath) {
      continue;
    }
    candidates.push({
      groupKey: 'operations',
      key: `command_result_${step.stepId}`,
      label: `${step.label || step.stepId}结果`,
      description: '步骤命令执行结果',
      filePath: step.resultPath
    });
  }

  for (const [key, artifact] of Object.entries(state.artifacts || {})) {
    candidates.push({
      groupKey: classifyArtifactGroup(artifact?.path, 'operations'),
      key,
      label: artifact?.label || path.basename(String(artifact?.path || key)),
      description: '操作会话已登记产物',
      filePath: artifact?.path
    });
  }

  const templateNames = uniqueTexts(params.systemMapTemplate, params.targetTemplate);
  for (const templateName of templateNames) {
    const discoveryRoot = path.join(STAGE2_DIR, `live_discovery_${templateName}`);
    for (const fileName of [
      'navigation_tree.json',
      'page_semantic_summary.json',
      'navigation_nodes.json',
      'page_entries.json',
      'feature_points.json',
      'discovery_result.json',
      'discovery_review_queue.json',
      'routing_summary.json',
      'discovery_strategy.json'
    ]) {
      candidates.push({
        groupKey: 'discovery',
        label: fileName,
        description: '系统地图 / 页面发现产物',
        filePath: path.join(discoveryRoot, fileName),
        preferredKey: `${templateName}_${fileName}`
      });
    }
  }

  const checklistTemplate = textOrNull(firstPresent(params.targetTemplate));
  if (checklistTemplate) {
    for (const fileName of ['template_revision_checklist.json', 'template_revision_checklist.md']) {
      candidates.push({
        groupKey: 'checklist',
        label: fileName,
        description: '模板修订清单',
        filePath: path.join(TEMPLATE_ROOT, checklistTemplate, '_revision_checklist', fileName),
        preferredKey: `${checklistTemplate}_${fileName}`
      });
    }
  }

  for (const fileName of ['latest_validation_matrix.json', 'latest_validation_matrix.md']) {
    candidates.push({
      groupKey: 'matrix',
      label: fileName,
      description: '统一验证汇总产物',
      filePath: path.join(STAGE2_DIR, 'validation_matrix', fileName),
      preferredKey: fileName
    });
  }

  for (const payload of commandPayloads) {
    collectOperationPayloadPaths(payload, candidates);
  }

  return candidates.map((candidate) => ({
    ...candidate,
    key: candidate.key || candidate.preferredKey || candidate.label || path.basename(String(candidate.filePath || 'artifact'))
  }));
}

async function buildOperationArtifactGroups(sessionId, sessionDir, state, params, steps, commandPayloads) {
  const groupOrder = [
    { key: 'operations', label: '操作会话' },
    { key: 'discovery', label: '发现产物' },
    { key: 'checklist', label: '修订清单' },
    { key: 'validation', label: '验证产物' },
    { key: 'matrix', label: '验证矩阵' },
    { key: 'evidence', label: '输出引用' }
  ];
  const groupMap = new Map(groupOrder.map((group) => [group.key, []]));
  const usedKeys = new Set();
  const seenPaths = new Set();
  const nextArtifactIndex = { ...(state.artifacts || {}) };
  let shouldPersistArtifactIndex = false;

  for (const candidate of buildOperationArtifactCandidates(sessionId, sessionDir, state, params, steps, commandPayloads)) {
    const resolvedPath = safeOperationArtifactPath(candidate.filePath);
    if (!resolvedPath || seenPaths.has(resolvedPath) || !(await fileExists(resolvedPath))) {
      continue;
    }
    const key = ensureUniqueKey(candidate.key, usedKeys);
    if (!OPERATION_ARTIFACT_KEY_PATTERN.test(key)) {
      continue;
    }

    seenPaths.add(resolvedPath);
    const groupKey = groupMap.has(candidate.groupKey) ? candidate.groupKey : 'evidence';
    const descriptor = {
      key,
      label: candidate.label || path.basename(resolvedPath),
      description: candidate.description || '',
      kind: inferArtifactKind(resolvedPath),
      fileName: path.basename(resolvedPath),
      href: buildOperationArtifactHref(sessionId, key),
      path: resolvedPath
    };
    groupMap.get(groupKey).push(descriptor);

    const indexedDescriptor = {
      key: descriptor.key,
      label: descriptor.label,
      fileName: descriptor.fileName,
      path: descriptor.path,
      href: descriptor.href
    };
    const currentDescriptor = nextArtifactIndex[key] || {};
    const currentResolvedPath = safeOperationArtifactPath(currentDescriptor.path);
    if (
      currentDescriptor.label !== indexedDescriptor.label
      || currentDescriptor.fileName !== indexedDescriptor.fileName
      || currentResolvedPath !== indexedDescriptor.path
      || currentDescriptor.href !== indexedDescriptor.href
    ) {
      nextArtifactIndex[key] = indexedDescriptor;
      shouldPersistArtifactIndex = true;
    }
  }

  if (shouldPersistArtifactIndex) {
    await fs.writeFile(
      path.join(sessionDir, 'operation_state.json'),
      JSON.stringify({ ...state, artifacts: nextArtifactIndex }, null, 2),
      'utf8'
    );
  }

  return groupOrder
    .map((group) => ({
      key: group.key,
      label: group.label,
      items: (groupMap.get(group.key) || []).map(toPublicArtifactDescriptor)
    }))
    .filter((group) => group.items.length > 0);
}

async function readOperationSession(sessionId) {
  if (!OPERATION_SESSION_PATTERN.test(sessionId)) {
    return null;
  }

  const sessionDir = path.join(OPERATIONS_DIR, sessionId);
  const state = await readJsonIfExists(path.join(sessionDir, 'operation_state.json'));
  if (!state || typeof state !== 'object') {
    return null;
  }

  const params = state.params && typeof state.params === 'object' ? state.params : {};
  const steps = sortOperationSteps(state.steps);
  const statusSummary = summarizeOperationStatus(steps, textOrNull(firstPresent(state.last_step_id, state.lastStepId)));
  const commandPayloads = await readOperationCommandPayloads(steps);
  const artifactGroups = await buildOperationArtifactGroups(sessionId, sessionDir, state, params, steps, commandPayloads);

  return {
    sessionId: textOrNull(firstPresent(state.session_id, sessionId)),
    createdAt: textOrNull(firstPresent(state.created_at)),
    updatedAt: textOrNull(firstPresent(state.updated_at)),
    status: statusSummary.status,
    lastStepId: statusSummary.lastStepId,
    lastStepStatus: statusSummary.lastStepStatus,
    runningStepId: statusSummary.runningStepId,
    failedStepCount: statusSummary.failedStepCount,
    completedStepCount: statusSummary.completedStepCount,
    paramsSummary: buildOperationParamSummary(params),
    steps,
    artifactGroups,
    artifacts: artifactGroups.flatMap((group) => group.items)
  };
}

async function readOperationCenterOverview() {
  let entries = [];
  try {
    entries = await fs.readdir(OPERATIONS_DIR, { withFileTypes: true });
  } catch {
    return {
      summary: {
        sessionCount: 0,
        latestSessionId: null,
        runningCount: 0,
        failedCount: 0,
        completedCount: 0
      },
      latestSession: null,
      sessions: []
    };
  }

  const sessions = (await Promise.all(entries
    .filter((entry) => entry.isDirectory() && OPERATION_SESSION_PATTERN.test(entry.name))
    .map((entry) => readOperationSession(entry.name))))
    .filter(Boolean)
    .sort((left, right) => compareIsoDesc(left.updatedAt, right.updatedAt) || right.sessionId.localeCompare(left.sessionId));

  return {
    summary: {
      sessionCount: sessions.length,
      latestSessionId: sessions[0]?.sessionId || null,
      runningCount: sessions.filter((session) => session.status === 'running').length,
      failedCount: sessions.filter((session) => session.status === 'failed' || session.failedStepCount > 0).length,
      completedCount: sessions.filter((session) => session.status === 'completed').length
    },
    latestSession: sessions[0] || null,
    sessions
  };
}

async function loadStage2Overview() {
  const [
    latestDailyReport,
    latestBaselineFreezeManifest,
    latestValidationMatrix,
    latestModelComparison,
    humanLoopSummary,
    operationCenter,
    runDirectories
  ] = await Promise.all([
    readJsonIfExists(path.join(STAGE2_DIR, 'latest_platform_daily_report.json')),
    readJsonIfExists(path.join(STAGE2_DIR, 'latest_baseline_freeze_manifest.json')),
    readJsonIfExists(path.join(STAGE2_DIR, 'validation_matrix', 'latest_validation_matrix.json')),
    readJsonIfExists(path.join(STAGE2_DIR, 'latest_model_comparison.json')),
    readLatestHumanLoopSummary(),
    readOperationCenterOverview(),
    listRunDirectories()
  ]);

  const runSummaries = (await Promise.all(runDirectories.map((entry) => readRunDirectory(entry.name))))
    .filter(Boolean);
  let sessionSummaries = await readSessionSummariesFromArtifacts();
  if (!sessionSummaries.length) {
    sessionSummaries = await syncStage2SessionArtifacts(runSummaries);
  }

  return {
    summary: {
      sessionCount: sessionSummaries.length,
      runCount: runSummaries.length,
      runningCount: runSummaries.filter((item) => item.overallStatus === 'running').length,
      waitingHumanCount: runSummaries.filter((item) => item.waitingReason || item.humanTakeover.status !== 'none').length,
      failedCount: runSummaries.filter((item) => item.overallStatus === 'failed').length,
      scheduledNextRoundCount: runSummaries.filter((item) => item.nextRound.shouldStart).length
    },
    latestValidationMatrix: latestValidationMatrix ? {
      status: latestValidationMatrix.summary?.status || 'unknown',
      targetCount: latestValidationMatrix.summary?.target_count || 0,
      executedCount: latestValidationMatrix.summary?.executed_count || 0,
      passedCount: latestValidationMatrix.summary?.passed_count || 0,
      failedCount: latestValidationMatrix.summary?.failed_count || 0,
      skippedCount: latestValidationMatrix.summary?.skipped_count || 0,
      byMode: latestValidationMatrix.summary?.by_mode || {},
      bySystem: latestValidationMatrix.summary?.by_system || {}
    } : null,
    latestDailyReport: latestDailyReport ? {
      summary: latestDailyReport.summary || '',
      runCount: factMap(latestDailyReport.facts).run_count || latestDailyReport.run_summaries?.length || 0,
      successfulRuns: factMap(latestDailyReport.facts).successful_runs || 0,
      failedRuns: factMap(latestDailyReport.facts).failed_runs || 0,
      modelsCovered: factMap(latestDailyReport.facts).models_covered || [],
      watchItems: latestDailyReport.daily_summary?.watch_items || []
    } : null,
    latestModelComparison: latestModelComparison ? {
      summary: latestModelComparison.summary || '',
      items: latestModelComparison.items || []
    } : null,
    latestBaselineFreezeManifest: await summarizeBaselineFreezeManifest(latestBaselineFreezeManifest),
    humanLoopSummary,
    operationCenter,
    sessionSummaries,
    runSummaries
  };
}

module.exports = {
  loadStage2Overview,
  resolveHumanLoopArtifact,
  resolveStage2RunArtifact,
  syncStage2SessionArtifacts
};
