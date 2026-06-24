const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs/promises');
const os = require('os');
const path = require('path');

const {
  analyzeV3Run,
  continueNextRound,
  createV3Run,
  generateV3Report,
  getV3Run,
  listV3Runs,
  resolveV3RunArtifact,
  saveHumanTaskResult,
  startV3Run
} = require('./stage2V3RunCenter');

async function withTempRunsDir(callback) {
  const runsDir = await fs.mkdtemp(path.join(os.tmpdir(), 'stage2-v3-runs-'));
  try {
    return await callback(runsDir);
  } finally {
    await fs.rm(runsDir, { recursive: true, force: true });
  }
}

async function readJson(filePath) {
  return JSON.parse(await fs.readFile(filePath, 'utf8'));
}

test('stage2 v3 run center creates a draft run and starts a stable artifact contract', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '追本溯源管理系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222',
      scope: '优先覆盖查询、详情和导出入口'
    }, { runsDir });

    assert.equal(created.run.status, 'draft');
    assert.ok(created.run.runId.startsWith('stage2_v3_'));
    assert.ok(created.run.artifacts.page_entries.href.startsWith('/api/stage2/v3/runs/'));

    const started = await startV3Run(created.run.runId, {}, { runsDir });
    assert.equal(started.run.status, 'waiting_human');
    assert.equal(started.run.summary.pageEntries, 1);
    assert.equal(started.run.summary.featurePoints, 4);
    assert.equal(started.run.summary.generatedTestCases, 4);
    assert.equal(started.run.summary.execution.skipped, 4);
    assert.equal(started.run.summary.nextDecision, 'wait_human_review');

    const run = await getV3Run(created.run.runId, { runsDir });
    assert.equal(run.artifacts.page_entries.schema_version, 'stage2_page_entries.v3');
    assert.equal(run.artifacts.feature_points.schema_version, 'stage2_feature_points.v3');
    assert.equal(run.artifacts.generated_test_cases.schema_version, 'stage2_generated_test_cases.v3');
    assert.equal(run.artifacts.execution_results.schema_version, 'stage2_execution_results.v3');
    assert.equal(run.artifacts.next_round_plan.requires_human_approval, true);
  });
});

test('stage2 v3 run center performs deterministic analysis and writes report artifacts', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '示例系统',
      entryUrl: 'https://example.com/',
      cdpUrl: 'http://127.0.0.1:9222'
    }, { runsDir });
    await startV3Run(created.run.runId, {}, { runsDir });

    const analyzed = await analyzeV3Run(created.run.runId, { runsDir });
    assert.equal(analyzed.roundAnalysis.schema_version, 'stage2_round_analysis.v3');
    assert.equal(analyzed.nextRoundPlan.schema_version, 'stage2_next_round_plan.v3');
    assert.equal(analyzed.nextRoundPlan.decision, 'wait_human_review');
    assert.ok(analyzed.humanTasks.items.some((item) => item.task_type === 'review_next_round_plan'));

    const reported = await generateV3Report(created.run.runId, { runsDir });
    assert.equal(reported.report.schema_version, 'stage2_run_report.v3');

    const reportArtifact = await resolveV3RunArtifact(created.run.runId, 'run_report_md', { runsDir });
    assert.ok(reportArtifact);
    assert.equal(reportArtifact.fileName, 'run_report.md');
    const reportText = await fs.readFile(reportArtifact.path, 'utf8');
    assert.match(reportText, /第二阶段 v3 运行报告/);

    const list = await listV3Runs({ runsDir });
    assert.equal(list.runs.length, 1);
    assert.equal(list.runs[0].runId, created.run.runId);
  });
});

test('stage2 v3 run center saves human task results and gates next round approval', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '高风险样例系统',
      entryUrl: 'https://example.com/',
      cdpUrl: 'http://localhost:9222',
      scope: '包含新增、删除和审批动作'
    }, { runsDir });
    await startV3Run(created.run.runId, {}, { runsDir });

    const runDir = path.join(runsDir, created.run.runId);
    const humanTasksBefore = await readJson(path.join(runDir, 'human_tasks.json'));
    assert.ok(humanTasksBefore.items.some((item) => item.task_id === 'task_review_feature_points'));

    const saved = await saveHumanTaskResult(created.run.runId, {
      taskId: 'task_review_feature_points',
      operatorId: 'tester',
      note: '保留查询和详情，危险动作继续人工审核。',
      result: { approvedFeaturePointIds: ['feature_001'] }
    }, { runsDir });
    const savedTask = saved.humanTasks.items.find((item) => item.task_id === 'task_review_feature_points');
    assert.equal(savedTask.status, 'completed');
    assert.match(savedTask.result_artifact, /human_task_results/);

    const blocked = await continueNextRound(created.run.runId, {}, { runsDir });
    assert.equal(blocked.run.status, 'waiting_human');

    const continued = await continueNextRound(created.run.runId, { approved: true }, { runsDir });
    assert.equal(continued.run.status, 'planned');
    assert.equal(continued.run.currentRoundId, 'round_002');
  });
});
