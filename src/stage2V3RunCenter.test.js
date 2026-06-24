const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs/promises');
const http = require('http');
const os = require('os');
const path = require('path');

const {
  analyzeV3Run,
  checkBrowserPreflight,
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

async function withFakeCdpServer(callback) {
  const server = http.createServer((req, res) => {
    if (req.url === '/json/version') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        Browser: 'Chrome/149.0.7827.156',
        'Protocol-Version': '1.3',
        webSocketDebuggerUrl: 'ws://localhost:9222/devtools/browser/fake'
      }));
      return;
    }
    if (req.url === '/json/list') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify([{ type: 'page', url: 'https://example.com' }]));
      return;
    }
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'not found' }));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const { port } = server.address();
    return await callback(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

function argValue(args, name) {
  const index = args.indexOf(name);
  return index === -1 ? null : args[index + 1];
}

async function writeFakePythonV3Artifacts(artifactRoot, runId, overrides = {}) {
  const pythonRunDir = path.join(artifactRoot, runId);
  await fs.mkdir(pythonRunDir, { recursive: true });
  await fs.writeFile(path.join(pythonRunDir, 'page_entries.json'), JSON.stringify({
    schema_version: 'stage2_page_entries.v3',
    items: [{
      page_entry_id: 'page_home',
      name: '首页',
      url: 'https://example.com/home',
      menu_path: ['首页'],
      page_type: 'dashboard',
      discovery_depth: 0,
      status: 'reachable',
      source: 'fake_python',
      screenshot_refs: []
    }]
  }, null, 2));
  await fs.writeFile(path.join(pythonRunDir, 'feature_points.json'), JSON.stringify({
    schema_version: 'stage2_feature_points.v3',
    items: [{
      feature_point_id: 'feature_nav',
      page_entry_id: 'page_home',
      name: '首页可达',
      feature_type: 'navigation',
      risk_level: 'low',
      auto_verifiable: true,
      verification_strategy: 'navigation_minimal_path',
      locator_candidates: [],
      source: 'fake_python',
      confidence: 0.9,
      review_status: 'auto_included'
    }]
  }, null, 2));
  await fs.writeFile(path.join(pythonRunDir, 'generated_test_cases.json'), JSON.stringify({
    schema_version: 'stage2_generated_test_cases.v3',
    items: [{
      test_case_id: 'case_nav',
      feature_point_id: 'feature_nav',
      title: '首页可达基础验证',
      type_template: 'navigation',
      preconditions: [],
      steps: [{ action: 'goto', target: 'https://example.com/home' }],
      expected_feedback: ['页面可达'],
      risk_policy: 'safe_auto',
      assertions: ['page_loaded'],
      requires_human_confirmation: false
    }]
  }, null, 2));
  await fs.writeFile(path.join(pythonRunDir, 'execution_results.json'), JSON.stringify({
    schema_version: 'stage2_execution_results.v3',
    items: overrides.executionItems || [{
      test_case_id: 'case_nav',
      status: 'passed',
      verdict: '真实浏览器低风险路径可达。',
      started_at: '2026-06-24T00:00:00.000Z',
      finished_at: '2026-06-24T00:00:01.000Z',
      actions: [{ action: 'goto', ok: true }],
      page_feedback: ['loaded'],
      screenshot_refs: [],
      network_refs: [],
      failure_reason: null,
      manual_confirmation_required: false,
      execution_mode: 'real_browser'
    }]
  }, null, 2));
  await fs.writeFile(path.join(pythonRunDir, 'next_round_plan.json'), JSON.stringify({
    schema_version: 'stage2_next_round_plan.v3',
    current_round_id: 'round_001',
    should_continue: false,
    decision: 'stop_goal_completed',
    next_round_goal: '本轮已完成。',
    target_page_entry_ids: [],
    target_feature_point_ids: [],
    planned_improvements: [],
    risk_level: 'low',
    requires_human_approval: false
  }, null, 2));
  await fs.writeFile(path.join(pythonRunDir, 'human_tasks.json'), JSON.stringify({
    schema_version: 'stage2_human_tasks.v3',
    items: []
  }, null, 2));
  return pythonRunDir;
}

async function writeFakePythonV1Artifacts(artifactRoot, runId) {
  const pythonRunDir = path.join(artifactRoot, runId);
  await fs.mkdir(pythonRunDir, { recursive: true });
  const page = {
    page_entry_id: 'page_001',
    name: '真实首页',
    url: 'https://example.com/home',
    menu_path: ['真实首页'],
    page_type: 'landing',
    discovery_depth: 0,
    status: 'reachable',
    source: 'real_browser_cdp',
    screenshot_refs: ['screenshots/home_visible.png']
  };
  const feature = {
    feature_point_id: 'feature_001',
    page_entry_id: 'page_001',
    name: '页面可见性验证',
    feature_type: 'view',
    risk_level: 'low',
    auto_verifiable: true,
    verification_strategy: 'page_visible',
    locator_candidates: [],
    source: 'real_browser_page_visible',
    confidence: 0.95,
    review_status: 'auto_included'
  };
  const testCase = {
    test_case_id: 'case_001',
    feature_point_id: 'feature_001',
    title: '首页真实浏览器可见性验证',
    type_template: 'page_visible',
    preconditions: [],
    steps: [{ action: 'goto', target: 'https://example.com/home' }],
    expected_feedback: ['页面可见'],
    risk_policy: 'safe_auto',
    assertions: ['page_visible'],
    requires_human_confirmation: false
  };
  const result = {
    test_case_id: 'case_001',
    status: 'real_passed',
    verdict: '真实浏览器低风险页面可见性验证通过。',
    started_at: '2026-06-24T00:00:00.000Z',
    finished_at: '2026-06-24T00:00:01.000Z',
    actions: [{ action: 'goto', ok: true }],
    page_feedback: ['页面可见'],
    screenshot_refs: ['screenshots/home_visible.png'],
    network_refs: [],
    failure_reason: null,
    manual_confirmation_required: false,
    execution_mode: 'real_browser'
  };

  await fs.writeFile(path.join(pythonRunDir, 'page_entries.json'), JSON.stringify({
    schema_version: 'stage2_v3_run.v1',
    page_entries: [page],
    items: [page]
  }, null, 2));
  await fs.writeFile(path.join(pythonRunDir, 'feature_points.json'), JSON.stringify({
    schema_version: 'stage2_v3_run.v1',
    feature_points: [feature],
    items: [feature]
  }, null, 2));
  await fs.writeFile(path.join(pythonRunDir, 'generated_test_cases.json'), JSON.stringify({
    schema_version: 'stage2_v3_run.v1',
    test_cases: [testCase],
    items: [testCase]
  }, null, 2));
  await fs.writeFile(path.join(pythonRunDir, 'execution_results.json'), JSON.stringify({
    schema_version: 'stage2_v3_run.v1',
    results: [result],
    items: [result]
  }, null, 2));
  await fs.writeFile(path.join(pythonRunDir, 'next_round_plan.json'), JSON.stringify({
    schema_version: 'stage2_next_round_plan.v3',
    current_round_id: 'round_001',
    should_continue: false,
    decision: 'stop_goal_completed',
    next_round_goal: '本轮已完成。',
    target_page_entry_ids: [],
    target_feature_point_ids: [],
    planned_improvements: [],
    risk_level: 'low',
    requires_human_approval: false
  }, null, 2));
  await fs.writeFile(path.join(pythonRunDir, 'human_tasks.json'), JSON.stringify({
    schema_version: 'stage2_human_tasks.v3',
    items: []
  }, null, 2));
  return pythonRunDir;
}

test('stage2 v3 browser preflight checks the live CDP endpoint', async () => {
  await withFakeCdpServer(async (cdpUrl) => {
    const preflight = await checkBrowserPreflight(cdpUrl);

    assert.equal(preflight.ok, true);
    assert.equal(preflight.status, 'connected');
    assert.equal(preflight.browser, 'Chrome/149.0.7827.156');
    assert.equal(preflight.targetCount, 1);
    assert.match(preflight.message, /Chrome\/149/);
  });
});

test('stage2 v3 browser preflight returns visible unavailable state', async () => {
  await withFakeCdpServer(async (cdpUrl) => {
    const preflight = await checkBrowserPreflight(`${cdpUrl}/bad-path`);

    assert.equal(preflight.ok, true);
    assert.equal(preflight.status, 'connected');
  });
  const failed = await checkBrowserPreflight('http://127.0.0.1:1', { timeoutMs: 800 });
  assert.equal(failed.ok, false);
  assert.match(failed.message, /CDP/);
});

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

    const started = await startV3Run(created.run.runId, { executionMode: 'contract_only' }, { runsDir });
    assert.equal(started.run.status, 'waiting_human');
    assert.equal(started.run.executionMode, 'contract_only');
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
    assert.ok(run.artifacts.execution_results.items.every((item) => item.status !== 'passed'));
    assert.ok(run.artifacts.execution_results.items.some((item) => item.failure_reason === 'contract_only_mode'));
    assert.equal(run.artifacts.next_round_plan.requires_human_approval, true);
  });
});

test('stage2 v3 run center starts real_browser mode through Python v3 bridge', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '真实执行样例系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222'
    }, { runsDir });
    let received = null;
    const started = await startV3Run(created.run.runId, {
      executionMode: 'real_browser',
      maxPages: 3,
      maxFeaturesPerPage: 4
    }, {
      runsDir,
      pythonRunner: async ({ command, args, artifactRoot }) => {
        received = { command, args, artifactRoot };
        const runId = argValue(args, '--v3-run-id');
        const pythonRunDir = await writeFakePythonV3Artifacts(artifactRoot, runId);
        return {
          stdout: JSON.stringify({ run_id: runId, run_dir: pythonRunDir, status: 'completed' }),
          stderr: ''
        };
      }
    });

    assert.equal(started.run.status, 'completed');
    assert.equal(started.run.executionMode, 'real_browser');
    assert.equal(received.command, process.env.STAGE2_PYTHON || process.env.PYTHON || 'python');
    assert.equal(argValue(received.args, '--v3-run-id'), created.run.runId);
    assert.equal(argValue(received.args, '--v3-artifact-root'), received.artifactRoot);
    assert.equal(argValue(received.args, '--page-url'), 'https://example.com/home');
    assert.equal(argValue(received.args, '--cdp-url'), 'http://localhost:9222/');

    const runDir = path.join(runsDir, created.run.runId);
    const executionResults = await readJson(path.join(runDir, 'execution_results.json'));
    const preflight = await readJson(path.join(runDir, 'preflight_result.json'));
    assert.equal(executionResults.items[0].status, 'passed');
    assert.equal(executionResults.items[0].execution_mode, 'real_browser');
    assert.equal(preflight.checks.python_orchestrator.ok, true);
  });
});

test('stage2 v3 run center imports Python v1-shaped real browser artifacts', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '真实执行兼容系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222'
    }, { runsDir });

    const started = await startV3Run(created.run.runId, {
      executionMode: 'real_browser'
    }, {
      runsDir,
      pythonRunner: async ({ args, artifactRoot }) => {
        const runId = argValue(args, '--v3-run-id');
        const pythonRunDir = await writeFakePythonV1Artifacts(artifactRoot, runId);
        return {
          stdout: JSON.stringify({ run_id: runId, run_dir: pythonRunDir, status: 'completed' }),
          stderr: ''
        };
      }
    });

    assert.equal(started.run.status, 'completed');
    assert.equal(started.run.summary.pageEntries, 1);
    assert.equal(started.run.summary.featurePoints, 1);
    assert.equal(started.run.summary.generatedTestCases, 1);
    assert.equal(started.run.summary.execution.passed, 1);
    assert.equal(started.run.summary.execution.by_status.real_passed, 1);
    assert.equal(started.run.summary.nextDecision, 'stop_goal_completed');

    const runDir = path.join(runsDir, created.run.runId);
    const pageEntries = await readJson(path.join(runDir, 'page_entries.json'));
    const featurePoints = await readJson(path.join(runDir, 'feature_points.json'));
    const generatedTestCases = await readJson(path.join(runDir, 'generated_test_cases.json'));
    const roundAnalysis = await readJson(path.join(runDir, 'round_analysis.json'));
    assert.equal(pageEntries.schema_version, 'stage2_page_entries.v3');
    assert.equal(pageEntries.items.length, 1);
    assert.equal(featurePoints.items.length, 1);
    assert.equal(generatedTestCases.items.length, 1);
    assert.equal(roundAnalysis.coverage_summary.page_entries, 1);
    assert.equal(roundAnalysis.failure_summary.total_clusters, 0);
  });
});

test('stage2 v3 run center records Python failure as visible run failure artifacts', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '失败样例系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222'
    }, { runsDir });

    const started = await startV3Run(created.run.runId, {
      executionMode: 'real_browser'
    }, {
      runsDir,
      pythonRunner: async () => {
        const error = new Error('CDP connection refused');
        error.stderr = 'Cannot connect to http://localhost:9222';
        error.code = 2;
        throw error;
      }
    });

    assert.equal(started.run.status, 'failed');
    assert.equal(started.run.executionMode, 'real_browser');
    assert.equal(started.run.summary.nextDecision, 'wait_human_review');

    const runDir = path.join(runsDir, created.run.runId);
    const currentStatus = await readJson(path.join(runDir, 'current_status.json'));
    const executionResults = await readJson(path.join(runDir, 'execution_results.json'));
    const humanTasks = await readJson(path.join(runDir, 'human_tasks.json'));
    assert.equal(currentStatus.phase, 'real_browser_execution_failed');
    assert.match(currentStatus.message, /真实浏览器执行失败/);
    assert.ok(executionResults.items.every((item) => item.failure_reason === 'python_v3_orchestrator_failed'));
    assert.ok(humanTasks.items.some((item) => item.task_id === 'task_connect_executor_or_confirm_plan'));
  });
});

test('stage2 v3 run center records missing Python executor without silent success', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '缺执行器样例系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222'
    }, { runsDir });

    const started = await startV3Run(created.run.runId, { executionMode: 'real_browser' }, {
      runsDir,
      pythonRunner: async () => {
        const error = new Error('spawn python ENOENT');
        error.code = 'ENOENT';
        throw error;
      }
    });

    assert.equal(started.run.status, 'failed');
    const runDir = path.join(runsDir, created.run.runId);
    const preflight = await readJson(path.join(runDir, 'preflight_result.json'));
    const executionResults = await readJson(path.join(runDir, 'execution_results.json'));
    assert.equal(preflight.checks.python_orchestrator.failure_reason, 'python_executor_unavailable');
    assert.ok(executionResults.items.every((item) => item.failure_reason === 'python_executor_unavailable'));
  });
});

test('stage2 v3 run center does not treat Python safe placeholder as real browser success', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '占位返回样例系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222'
    }, { runsDir });

    const started = await startV3Run(created.run.runId, { executionMode: 'real_browser' }, {
      runsDir,
      pythonRunner: async ({ args, artifactRoot }) => {
        const runId = argValue(args, '--v3-run-id');
        const pythonRunDir = await writeFakePythonV3Artifacts(artifactRoot, runId, {
          executionItems: [{
            case_id: 'case_nav',
            feature_id: 'feature_nav',
            status: 'passed_safe_placeholder',
            execution_mode: 'safe_placeholder',
            started_at: '2026-06-24T00:00:00.000Z',
            finished_at: '2026-06-24T00:00:01.000Z',
            evidence: [],
            message: 'safe placeholder only'
          }]
        });
        return {
          stdout: JSON.stringify({ run_id: runId, run_dir: pythonRunDir, status: 'waiting_human' }),
          stderr: ''
        };
      }
    });

    assert.equal(started.run.status, 'waiting_human');
    const runDir = path.join(runsDir, created.run.runId);
    const executionResults = await readJson(path.join(runDir, 'execution_results.json'));
    const nextRoundPlan = await readJson(path.join(runDir, 'next_round_plan.json'));
    assert.equal(executionResults.items[0].status, 'skipped');
    assert.equal(executionResults.items[0].failure_reason, 'python_returned_safe_placeholder');
    assert.equal(nextRoundPlan.requires_human_approval, true);
  });
});

test('stage2 v3 run center performs deterministic analysis and writes report artifacts', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '示例系统',
      entryUrl: 'https://example.com/',
      cdpUrl: 'http://127.0.0.1:9222'
    }, { runsDir });
    await startV3Run(created.run.runId, { executionMode: 'contract_only' }, { runsDir });

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
    await startV3Run(created.run.runId, { executionMode: 'contract_only' }, { runsDir });

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
