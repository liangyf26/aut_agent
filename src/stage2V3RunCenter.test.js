const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs/promises');
const http = require('http');
const os = require('os');
const path = require('path');

const {
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

async function withFakeOpenAiCompatibleServer(callback) {
  const requests = [];
  const server = http.createServer(async (req, res) => {
    if (req.method === 'POST' && req.url === '/v1/chat/completions') {
      const chunks = [];
      for await (const chunk of req) {
        chunks.push(chunk);
      }
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
      requests.push(body);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        id: `chatcmpl_${requests.length}`,
        object: 'chat.completion',
        choices: [{
          index: 0,
          message: body.tools
            ? { role: 'assistant', tool_calls: [{ id: 'call_1', type: 'function', function: { name: 'ping', arguments: '{}' } }] }
            : { role: 'assistant', content: '{"ok":true}' },
          finish_reason: body.tools ? 'tool_calls' : 'stop'
        }]
      }));
      return;
    }
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'not found' }));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const { port } = server.address();
    return await callback(`http://127.0.0.1:${port}/v1`, requests);
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
  await fs.writeFile(path.join(pythonRunDir, 'menu_tree.json'), JSON.stringify({
    schema_version: 'stage2_menu_tree.v1',
    status: 'completed',
    root_count: 3,
    entry_count: 4,
    leaf_count: 2,
    nodes: [{
      menu_id: 'menu_business',
      text: '业务办理',
      level: 1,
      is_leaf: false,
      status: 'expanded',
      children: [{
        menu_id: 'menu_online_apply',
        text: '线上备案申请',
        level: 2,
        parent_id: 'menu_business',
        is_leaf: true,
        status: 'discovered'
      }]
    }, {
      menu_id: 'menu_query',
      text: '备案查询',
      level: 1,
      is_leaf: true,
      status: 'discovered'
    }, {
      menu_id: 'menu_system',
      text: '系统管理',
      level: 1,
      is_leaf: false,
      status: 'expanded'
    }]
  }, null, 2));
  await fs.writeFile(path.join(pythonRunDir, 'menu_entries.json'), JSON.stringify({
    schema_version: 'stage2_menu_entries.v1',
    items: [{
      menu_id: 'menu_business',
      text: '业务办理',
      level: 1,
      is_leaf: false,
      status: 'expanded',
      source: 'playwright.menu_discovery'
    }, {
      menu_id: 'menu_online_apply',
      text: '线上备案申请',
      level: 2,
      parent_id: 'menu_business',
      menu_path: ['业务办理', '线上备案申请'],
      is_leaf: true,
      status: 'discovered',
      source: 'playwright.menu_discovery',
      screenshot_refs: ['menu_business_after_expand']
    }, {
      menu_id: 'menu_query',
      text: '备案查询',
      level: 1,
      menu_path: ['备案查询'],
      is_leaf: true,
      status: 'discovered',
      source: 'playwright.menu_discovery'
    }, {
      menu_id: 'menu_system',
      text: '系统管理',
      level: 1,
      menu_path: ['系统管理'],
      is_leaf: false,
      status: 'expanded',
      source: 'playwright.menu_discovery'
    }]
  }, null, 2));
  await fs.writeFile(path.join(pythonRunDir, 'menu_traversal_log.jsonl'), [
    JSON.stringify({ event: 'expand', menu_id: 'menu_business', status: 'success', screenshot_ref: 'menu_business_after_expand' }),
    JSON.stringify({ event: 'scan', menu_id: 'menu_query', status: 'leaf_discovered' })
  ].join('\n'));
  await fs.writeFile(path.join(pythonRunDir, 'page_exploration_log.jsonl'), [
    JSON.stringify({ event: 'enter_menu_leaf', menu_id: 'menu_online_apply', status: 'reachable', page_entry_id: 'page_home' }),
    JSON.stringify({ event: 'enter_menu_leaf', menu_id: 'menu_query', status: 'not_attempted', failure_reason: 'max_pages_reached' })
  ].join('\n'));
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

test('stage2 v3 run center loads model profiles, preflights them, and persists run selection', async () => {
  await withFakeOpenAiCompatibleServer(async (baseUrl, requests) => {
    await withTempRunsDir(async (runsDir) => {
      const configPath = path.join(runsDir, 'stage2-model-profiles.json');
      await fs.writeFile(configPath, JSON.stringify({
        schema_version: 'stage2_model_profiles.v1',
        profiles: [
          {
            id: 'deepseek-v4',
            label: 'DeepSeek V4',
            provider: 'openai_compatible',
            baseUrl,
            apiKey: 'test-key',
            model: 'deepseek-v4-flash',
            browserUseMode: 'chatopenai_structured'
          },
          {
            id: 'offline-model',
            label: 'Offline Model',
            provider: 'openai_compatible',
            baseUrl: 'http://127.0.0.1:1/v1',
            apiKey: 'test-key',
            model: 'offline-model'
          }
        ]
      }, null, 2));

      const preflight = await checkStage2ModelProfiles({ configPath, timeoutMs: 800 });
      assert.equal(preflight.schema_version, 'stage2_model_profile_preflight.v1');
      assert.equal(preflight.profiles.length, 2);
      assert.equal(preflight.profiles[0].id, 'deepseek-v4');
      assert.equal(preflight.profiles[0].status, 'available');
      assert.equal(preflight.profiles[0].capability_tags.chat_completion, true);
      assert.equal(preflight.profiles[0].capability_tags.json_object_response_format, true);
      assert.equal(preflight.profiles[0].capability_tags.json_schema_response_format, true);
      assert.equal(preflight.profiles[0].capability_tags.tool_calling, true);
      assert.equal(preflight.profiles[0].capability_tags.browser_use_chatopenai_structured, true);
      assert.equal(preflight.profiles[1].status, 'unavailable');
      assert.ok(requests.length >= 4);

      const created = await createV3Run({
        systemName: '多模型样例系统',
        entryUrl: 'https://example.com/home',
        cdpUrl: 'http://localhost:9222',
        modelProfileIds: ['deepseek-v4', 'offline-model']
      }, { runsDir, modelProfileConfigPath: configPath });

      assert.deepEqual(created.run.modelProfileIds, ['deepseek-v4', 'offline-model']);
      assert.equal(created.run.modelProfiles[0].label, 'DeepSeek V4');
      assert.equal(created.run.modelProfiles[0].apiKeyConfigured, true);
      assert.equal(created.run.modelProfiles[0].apiKey, undefined);

      const received = [];
      await startV3Run(created.run.runId, { executionMode: 'real_browser' }, {
        runsDir,
        modelProfileConfigPath: configPath,
        pythonRunner: async ({ args, artifactRoot }) => {
          received.push({ args });
          const runId = argValue(args, '--v3-run-id');
          const pythonRunDir = await writeFakePythonV3Artifacts(artifactRoot, runId);
          return {
            stdout: JSON.stringify({ run_id: runId, run_dir: pythonRunDir, status: 'completed' }),
            stderr: ''
          };
        }
      });

      assert.deepEqual(
        received.map((call) => argValue(call.args, '--v3-model-profile')),
        ['deepseek-v4', 'offline-model']
      );

      const inputConfig = await readJson(path.join(runsDir, created.run.runId, 'input_config.json'));
      assert.deepEqual(inputConfig.selected_model_profile_ids, ['deepseek-v4', 'offline-model']);
      assert.equal(inputConfig.selected_model_profiles[0].id, 'deepseek-v4');
      assert.equal(inputConfig.selected_model_profiles[0].api_key_configured, true);
      assert.equal(inputConfig.selected_model_profiles[0].api_key, undefined);
    });
  });
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

test('stage2 v3 run center exposes first-round menu discovery separately from browser targets', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '追本溯源管理系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222',
      scope: '优先完成“线上备案申请”页面'
    }, { runsDir });

    const started = await startV3Run(created.run.runId, { executionMode: 'real_browser' }, {
      runsDir,
      pythonRunner: async ({ args, artifactRoot }) => {
        const runId = argValue(args, '--v3-run-id');
        const pythonRunDir = await writeFakePythonV3Artifacts(artifactRoot, runId);
        return {
          stdout: JSON.stringify({ run_id: runId, run_dir: pythonRunDir, status: 'completed' }),
          stderr: ''
        };
      }
    });

    assert.ok(started.run.artifacts.menu_tree.href.includes('/artifacts/menu_tree'));
    assert.ok(started.run.artifacts.menu_entries.href.includes('/artifacts/menu_entries'));
    assert.ok(started.run.artifacts.page_exploration_log.href.includes('/artifacts/page_exploration_log'));
    assert.equal(started.run.summary.menuEntries, 4);
    assert.equal(started.run.summary.menuLeaves, 2);
    assert.equal(started.run.summary.menuRoots, 3);
    assert.equal(started.run.summary.browserTargets, 0);
    assert.equal(started.run.summary.pageEntries, 1);

    const run = await getV3Run(created.run.runId, { runsDir });
    assert.equal(run.artifacts.menu_tree.root_count, 3);
    assert.equal(run.artifacts.menu_entries.items[1].text, '线上备案申请');
    assert.match(run.artifacts.menu_traversal_log, /menu_business_after_expand/);
    assert.match(run.artifacts.page_exploration_log, /enter_menu_leaf/);
    assert.match(run.run.summary.countExplanation.menu_leaf_vs_page_entries, /menu leaves attempted/);
  });
});

test('stage2 v3 run center tracks prioritized and waived target pages across Python bridge', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '追本溯源管理系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222',
      scope: '第一轮完整遍历菜单入口',
      prioritizedTargets: ['线上备案申请', '备案进度查询'],
      waivedTargets: ['旧版备案页面']
    }, { runsDir });
    let received = null;

    const started = await startV3Run(created.run.runId, { executionMode: 'real_browser' }, {
      runsDir,
      pythonRunner: async ({ args, artifactRoot }) => {
        received = { args };
        const runId = argValue(args, '--v3-run-id');
        const pythonRunDir = await writeFakePythonV3Artifacts(artifactRoot, runId);
        return {
          stdout: JSON.stringify({ run_id: runId, run_dir: pythonRunDir, status: 'completed' }),
          stderr: ''
        };
      }
    });

    const inputConfig = await readJson(path.join(runsDir, created.run.runId, 'input_config.json'));
    assert.deepEqual(inputConfig.prioritized_targets, ['线上备案申请', '备案进度查询']);
    assert.deepEqual(inputConfig.waived_targets, ['旧版备案页面']);
    assert.deepEqual(
      received.args.filter((item, index) => received.args[index - 1] === '--v3-prioritized-target'),
      ['线上备案申请', '备案进度查询']
    );
    assert.deepEqual(
      received.args.filter((item, index) => received.args[index - 1] === '--v3-waived-target'),
      ['旧版备案页面']
    );

    assert.equal(started.run.summary.targetTracking.total, 3);
    assert.equal(started.run.summary.targetTracking.found, 1);
    assert.equal(started.run.summary.targetTracking.missed, 1);
    assert.equal(started.run.summary.targetTracking.waived, 1);
    assert.equal(started.run.targetTracking.items[0].status, 'found');
    assert.equal(started.run.targetTracking.items[0].matched_items[0].kind, 'menu_entry');
    assert.equal(started.run.targetTracking.items[0].matched_items[0].menu_id, 'menu_online_apply');
    assert.deepEqual(started.run.missedTargets, ['备案进度查询']);

    const runDir = path.join(runsDir, created.run.runId);
    const analysis = await readJson(path.join(runDir, 'round_analysis.json'));
    const nextRoundPlan = await readJson(path.join(runDir, 'next_round_plan.json'));
    assert.deepEqual(analysis.missing_scope_targets, ['备案进度查询']);
    assert.equal(analysis.target_tracking.find((item) => item.target === '旧版备案页面').status, 'waived');
    assert.deepEqual(nextRoundPlan.target_search_goals, ['备案进度查询']);
  });
});

test('stage2 v3 run center returns visible operation feedback and persisted status context', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '操作反馈样例系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222'
    }, { runsDir });

    assert.equal(created.operation.action, 'create_run');
    assert.equal(created.operation.status, 'succeeded');
    assert.match(created.operation.message, /已创建|草稿/);
    assert.equal(created.operation.error, null);
    assert.equal(created.run.currentStatus.phase, 'draft');
    assert.match(created.run.currentStatus.message, /等待启动/);
    assert.ok(created.run.recentEvents.some((event) => event.type === 'run_created'));

    const started = await startV3Run(created.run.runId, { executionMode: 'contract_only' }, { runsDir });
    assert.equal(started.operation.action, 'start');
    assert.equal(started.operation.status, 'blocked');
    assert.match(started.operation.nextAction, /人工确认|审核|继续/);
    assert.equal(started.run.status, 'waiting_human');
    assert.equal(started.run.currentStatus.phase, 'round_analysis');
    assert.ok(started.run.recentEvents.some((event) => event.type === 'status_changed'));

    const paused = await setV3RunLifecycleStatus(created.run.runId, 'pause', {}, { runsDir });
    assert.equal(paused.operation.action, 'pause');
    assert.equal(paused.operation.status, 'succeeded');
    assert.equal(paused.run.status, 'paused');
    assert.equal(paused.run.currentStatus.phase, 'pause');

    const resumed = await setV3RunLifecycleStatus(created.run.runId, 'resume', {}, { runsDir });
    assert.equal(resumed.operation.action, 'resume');
    assert.equal(resumed.operation.status, 'running');
    assert.equal(resumed.run.status, 'running');
    assert.equal(resumed.operation.error, null);

    const blocked = await continueNextRound(created.run.runId, {}, { runsDir });
    assert.equal(blocked.operation.action, 'continue_next_round');
    assert.equal(blocked.operation.status, 'blocked');
    assert.match(blocked.operation.message, /人工批准|人工确认/);
    assert.equal(blocked.run.status, 'waiting_human');

    const listed = await listV3Runs({ runsDir });
    const listedRun = listed.runs.find((run) => run.runId === created.run.runId);
    assert.equal(listedRun.currentStatus.phase, 'next_round_blocked');
    assert.ok(listedRun.recentEvents.length >= 3);
  });
});

test('stage2 v3 run center requires explicit confirmation for full access mode', async () => {
  await withTempRunsDir(async (runsDir) => {
    await assert.rejects(
      () => createV3Run({
        systemName: '全权限未确认系统',
        entryUrl: 'https://example.com/home',
        cdpUrl: 'http://localhost:9222',
        safetyPolicy: 'test_env_full_access',
        allowedSideEffects: ['submit', 'delete']
      }, { runsDir }),
      /测试环境全权限模式必须先/
    );
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
    assert.equal(argValue(received.args, '--v3-safety-policy'), 'low_risk_only');

    const runDir = path.join(runsDir, created.run.runId);
    const executionResults = await readJson(path.join(runDir, 'execution_results.json'));
    const preflight = await readJson(path.join(runDir, 'preflight_result.json'));
    assert.equal(executionResults.items[0].status, 'passed');
    assert.equal(executionResults.items[0].execution_mode, 'real_browser');
    assert.equal(preflight.checks.python_orchestrator.ok, true);
  });
});

test('stage2 v3 run center exposes execution verdict counts and recent evidence', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '执行证据样例系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222'
    }, { runsDir });
    const started = await startV3Run(created.run.runId, {
      executionMode: 'real_browser'
    }, {
      runsDir,
      pythonRunner: async ({ args, artifactRoot }) => {
        const runId = argValue(args, '--v3-run-id');
        const pythonRunDir = await writeFakePythonV3Artifacts(artifactRoot, runId, {
          executionItems: [{
            test_case_id: 'case_nav',
            feature_point_id: 'feature_nav',
            status: 'passed',
            verdict: 'passed',
            execution_mode: 'real_browser',
            actions: [{ action: 'goto', status: 'passed' }],
            page_feedback: ['首页加载完成'],
            screenshot_refs: ['screenshots/home_entry.png'],
            failure_reason: null,
            manual_confirmation_required: false
          }, {
            test_case_id: 'case_query',
            feature_point_id: 'feature_query',
            status: 'failed',
            verdict: 'failed',
            execution_mode: 'real_browser',
            actions: [{ action: 'click', target: '查询', status: 'failed' }],
            page_feedback: ['列表没有刷新'],
            screenshot_refs: ['screenshots/query_failure.png'],
            failure_reason: 'assertion_failed',
            manual_confirmation_required: true
          }, {
            test_case_id: 'case_delete',
            feature_point_id: 'feature_delete',
            status: 'skipped_by_policy',
            verdict: 'skipped',
            execution_mode: 'real_browser',
            actions: [],
            page_feedback: [],
            screenshot_refs: [],
            failure_reason: 'policy_denied',
            manual_confirmation_required: true
          }]
        });
        return {
          stdout: JSON.stringify({ run_id: runId, run_dir: pythonRunDir, status: 'waiting_human' }),
          stderr: ''
        };
      }
    });

    assert.equal(started.run.summary.execution.total, 3);
    assert.equal(started.run.summary.execution.passed, 1);
    assert.equal(started.run.summary.execution.failed, 1);
    assert.equal(started.run.summary.execution.blocked, 1);
    assert.equal(started.run.summary.execution.skipped, 1);
    assert.deepEqual(
      started.run.summary.execution.recentEvidence.map((item) => item.testCaseId),
      ['case_nav', 'case_query', 'case_delete']
    );
    assert.equal(started.run.summary.execution.recentEvidence[0].screenshotRefs[0], 'screenshots/home_entry.png');
    assert.equal(started.run.summary.execution.recentEvidence[1].failureReason, 'assertion_failed');
    assert.equal(started.run.summary.execution.recentEvidence[2].failureReason, 'policy_denied');
  });
});

test('stage2 v3 run center executes selected model profiles sequentially and writes comparison summary', async () => {
  await withTempRunsDir(async (runsDir) => {
    const modelProfiles = [{
      id: 'qwen',
      label: 'Qwen',
      provider: 'openai_compatible',
      model: 'qwen-test',
      apiKeyConfigured: true
    }, {
      id: 'deepseek',
      label: 'DeepSeek',
      provider: 'openai_compatible',
      model: 'deepseek-test',
      apiKeyConfigured: true
    }];
    const created = await createV3Run({
      systemName: '多模型样例系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222',
      modelProfileIds: ['qwen', 'deepseek']
    }, { runsDir, modelProfiles });
    const calls = [];

    const started = await startV3Run(created.run.runId, {
      executionMode: 'real_browser'
    }, {
      runsDir,
      modelProfiles,
      pythonRunner: async ({ args, artifactRoot }) => {
        calls.push({ model: argValue(args, '--v3-model-profile'), args });
        const runId = argValue(args, '--v3-run-id');
        const pythonRunDir = await writeFakePythonV3Artifacts(artifactRoot, runId, {
          executionItems: [{
            test_case_id: `case_${calls.length}`,
            status: calls.length === 1 ? 'passed' : 'failed',
            verdict: calls.length === 1 ? 'passed' : 'failed',
            execution_mode: 'real_browser',
            actions: [{ action: 'goto', status: 'passed' }],
            page_feedback: [`model ${calls.length}`],
            screenshot_refs: [`screenshots/model_${calls.length}.png`],
            failure_reason: calls.length === 1 ? null : 'assertion_failed',
            manual_confirmation_required: calls.length !== 1
          }]
        });
        return {
          stdout: JSON.stringify({ run_id: runId, run_dir: pythonRunDir, status: 'completed' }),
          stderr: ''
        };
      }
    });

    assert.deepEqual(calls.map((item) => item.model), ['qwen', 'deepseek']);
    assert.equal(started.run.summary.modelComparison.total, 2);
    assert.equal(started.run.summary.modelComparison.completed, 2);
    assert.equal(started.run.summary.modelComparison.failed, 0);

    const runDir = path.join(runsDir, created.run.runId);
    const comparison = await readJson(path.join(runDir, 'model_comparison.json'));
    assert.equal(comparison.items.length, 2);
    assert.deepEqual(comparison.items.map((item) => item.model_profile_id), ['qwen', 'deepseek']);
    assert.ok(comparison.items.every((item) => item.shared_config_signature === comparison.shared_config_signature));
    assert.equal(comparison.items[0].execution.passed, 1);
    assert.equal(comparison.items[1].execution.failed, 1);
    assert.ok(comparison.items[0].artifacts.run_dir.includes('model_attempts'));
  });
});

test('stage2 v3 multi-model comparison continues after one model failure', async () => {
  await withTempRunsDir(async (runsDir) => {
    const modelProfiles = [{
      id: 'broken',
      label: 'Broken Model',
      provider: 'openai_compatible',
      model: 'broken-test',
      apiKeyConfigured: true
    }, {
      id: 'steady',
      label: 'Steady Model',
      provider: 'openai_compatible',
      model: 'steady-test',
      apiKeyConfigured: true
    }];
    const created = await createV3Run({
      systemName: '多模型失败续跑系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222',
      modelProfileIds: ['broken', 'steady']
    }, { runsDir, modelProfiles });
    const calls = [];

    const started = await startV3Run(created.run.runId, {
      executionMode: 'real_browser'
    }, {
      runsDir,
      modelProfiles,
      pythonRunner: async ({ args, artifactRoot }) => {
        const model = argValue(args, '--v3-model-profile');
        calls.push(model);
        if (model === 'broken') {
          const error = new Error('model route failed');
          error.code = 3;
          error.stderr = 'model unavailable';
          throw error;
        }
        const runId = argValue(args, '--v3-run-id');
        const pythonRunDir = await writeFakePythonV3Artifacts(artifactRoot, runId);
        return {
          stdout: JSON.stringify({ run_id: runId, run_dir: pythonRunDir, status: 'completed' }),
          stderr: ''
        };
      }
    });

    assert.deepEqual(calls, ['broken', 'steady']);
    assert.equal(started.run.status, 'completed');
    assert.equal(started.run.summary.modelComparison.total, 2);
    assert.equal(started.run.summary.modelComparison.completed, 1);
    assert.equal(started.run.summary.modelComparison.failed, 1);

    const comparison = await readJson(path.join(runsDir, created.run.runId, 'model_comparison.json'));
    assert.equal(comparison.items[0].status, 'failed');
    assert.equal(comparison.items[0].failure_reason, 'python_v3_orchestrator_failed');
    assert.equal(comparison.items[1].status, 'completed');
  });
});

test('stage2 v3 run center keeps next round open when scoped target is not discovered', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '目标页面未命中系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222',
      scope: '优先完成“备案进度查询”页面'
    }, { runsDir });
    let received = null;
    const started = await startV3Run(created.run.runId, {
      executionMode: 'real_browser'
    }, {
      runsDir,
      pythonRunner: async ({ args, artifactRoot }) => {
        received = { args };
        const runId = argValue(args, '--v3-run-id');
        const pythonRunDir = await writeFakePythonV3Artifacts(artifactRoot, runId);
        return {
          stdout: JSON.stringify({ run_id: runId, run_dir: pythonRunDir, status: 'completed' }),
          stderr: ''
        };
      }
    });

    assert.equal(argValue(received.args, '--v3-scope'), '优先完成“备案进度查询”页面');
    assert.equal(started.run.summary.nextDecision, 'auto_continue');

    const runDir = path.join(runsDir, created.run.runId);
    const analysis = await readJson(path.join(runDir, 'round_analysis.json'));
    const nextRoundPlan = await readJson(path.join(runDir, 'next_round_plan.json'));
    assert.deepEqual(analysis.missing_scope_targets, ['备案进度查询']);
    assert.equal(analysis.ai_provider_status, 'not_connected');
    assert.equal(nextRoundPlan.decision, 'auto_continue');
    assert.equal(nextRoundPlan.should_continue, true);
    assert.match(nextRoundPlan.next_round_goal, /备案进度查询/);
  });
});

test('stage2 v3 continue next round starts execution instead of ending in planned state', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '下一轮执行系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222',
      scope: '优先完成“备案进度查询”页面'
    }, { runsDir });
    const calls = [];
    const pythonRunner = async ({ args, artifactRoot }) => {
      calls.push(args);
      const runId = argValue(args, '--v3-run-id');
      const pythonRunDir = await writeFakePythonV3Artifacts(artifactRoot, runId);
      return {
        stdout: JSON.stringify({ run_id: runId, run_dir: pythonRunDir, status: 'completed' }),
        stderr: ''
      };
    };

    await startV3Run(created.run.runId, { executionMode: 'real_browser' }, { runsDir, pythonRunner });
    const continued = await continueNextRound(created.run.runId, {}, { runsDir, pythonRunner });

    assert.equal(calls.length, 2);
    assert.notEqual(continued.run.status, 'planned');
    assert.equal(continued.run.status, 'completed');
    assert.equal(continued.run.currentRoundId, 'round_002');
    assert.equal(continued.operation.status, 'succeeded');
    assert.ok(continued.run.recentEvents.some((event) => event.type === 'next_round_queued'));
    assert.ok(continued.run.recentEvents.some((event) => event.type === 'status_changed' && event.phase === 'round_analysis'));
  });
});

test('stage2 v3 continue next round returns precise blockers for unmet prerequisites', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '下一轮阻塞系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222'
    }, { runsDir });
    await startV3Run(created.run.runId, { executionMode: 'contract_only' }, { runsDir });
    const runDir = path.join(runsDir, created.run.runId);

    const cases = [{
      name: 'executor',
      plan: { decision: 'auto_continue', prerequisite_blockers: [{ code: 'executor_unavailable' }] },
      code: 'executor_unavailable',
      hint: /执行器|Python/
    }, {
      name: 'browser-use',
      plan: { decision: 'auto_continue', prerequisite_blockers: [{ code: 'browser_use_unavailable' }] },
      code: 'browser_use_unavailable',
      hint: /Browser-use/
    }, {
      name: 'playwright',
      plan: { decision: 'auto_continue', prerequisite_blockers: [{ code: 'playwright_disconnected' }] },
      code: 'playwright_disconnected',
      hint: /Playwright|CDP/
    }, {
      name: 'model',
      plan: { decision: 'auto_continue', prerequisite_blockers: [{ code: 'model_unavailable' }] },
      code: 'model_unavailable',
      hint: /模型/
    }, {
      name: 'human',
      plan: { decision: 'wait_human_review', requires_human_approval: true },
      code: 'human_approval_required',
      hint: /人工/
    }, {
      name: 'budget',
      plan: { decision: 'stop_budget_exhausted' },
      code: 'budget_exhausted',
      hint: /预算|上限/
    }, {
      name: 'no-improvement',
      plan: { decision: 'stop_no_improvement' },
      code: 'no_improvement',
      hint: /改进/
    }];

    for (const item of cases) {
      await fs.writeFile(path.join(runDir, 'next_round_plan.json'), JSON.stringify({
        schema_version: 'stage2_next_round_plan.v3',
        current_round_id: 'round_001',
        should_continue: item.plan.decision === 'auto_continue',
        next_round_goal: '继续下一轮。',
        target_page_entry_ids: [],
        target_feature_point_ids: [],
        planned_improvements: [],
        risk_level: 'low',
        requires_human_approval: false,
        ...item.plan
      }, null, 2));

      const blocked = await continueNextRound(created.run.runId, {}, { runsDir });
      assert.equal(blocked.operation.status, 'blocked', item.name);
      assert.equal(blocked.operation.error.code, item.code, item.name);
      assert.match(blocked.operation.nextAction, item.hint, item.name);
      assert.notEqual(blocked.run.status, 'planned', item.name);
    }
  });
});

test('stage2 v3 run center persists and forwards test environment full access policy', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: '全权限测试系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222',
      safetyPolicy: 'test_env_full_access',
      fullAccessConfirmed: true,
      allowedSideEffects: ['submit', 'delete', 'approve']
    }, { runsDir });
    let received = null;
    await startV3Run(created.run.runId, {
      executionMode: 'real_browser'
    }, {
      runsDir,
      pythonRunner: async ({ args, artifactRoot }) => {
        received = { args };
        const runId = argValue(args, '--v3-run-id');
        const pythonRunDir = await writeFakePythonV3Artifacts(artifactRoot, runId, {
          executionItems: [{
            test_case_id: 'case_nav',
            status: 'side_effect_executed',
            verdict: '已执行提交动作。',
            started_at: '2026-06-24T00:00:00.000Z',
            finished_at: '2026-06-24T00:00:01.000Z',
            action_type: 'submit',
            control_label: '提交',
            policy_decision: { decision: 'allowed', reason_code: 'test_env_full_access_allowlisted' },
            before_screenshot_ref: 'side_effect_001_before',
            after_screenshot_ref: 'side_effect_001_after',
            failure_reason: null,
            execution_mode: 'real_browser'
          }]
        });
        return {
          stdout: JSON.stringify({ run_id: runId, run_dir: pythonRunDir, status: 'completed' }),
          stderr: ''
        };
      }
    });

    const runDir = path.join(runsDir, created.run.runId);
    const manifest = await readJson(path.join(runDir, 'run_manifest.json'));
    const inputConfig = await readJson(path.join(runDir, 'input_config.json'));
    const executionResults = await readJson(path.join(runDir, 'execution_results.json'));
    const publicRun = await getV3Run(created.run.runId, { runsDir });
    assert.equal(manifest.safety_policy, 'test_env_full_access');
    assert.equal(publicRun.run.fullAccessConfirmed, true);
    assert.equal(inputConfig.full_access_confirmed, true);
    assert.deepEqual(inputConfig.allowed_side_effect_actions, ['submit', 'delete', 'approve']);
    assert.equal(argValue(received.args, '--v3-safety-policy'), 'test_env_full_access');
    assert.deepEqual(
      received.args.filter((item, index) => received.args[index - 1] === '--v3-allow-side-effect-action'),
      ['submit', 'delete', 'approve']
    );
    assert.equal(executionResults.items[0].status, 'side_effect_executed');
    assert.deepEqual(executionResults.items[0].screenshot_refs, ['side_effect_001_before', 'side_effect_001_after']);
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

    const continued = await continueNextRound(created.run.runId, {}, { runsDir });
    assert.equal(continued.run.status, 'completed');
    assert.equal(continued.run.currentRoundId, 'round_001');
    assert.equal(continued.run.rounds.length, 1);
    const currentStatus = await readJson(path.join(runDir, 'current_status.json'));
    assert.equal(currentStatus.message, '当前目标已完成，无需进入下一轮；可生成报告或创建新的更大范围 run。');
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
    assert.equal(started.operation.status, 'failed');
    assert.equal(started.operation.error.code, 'real_browser_execution_failed');
    assert.equal(started.run.operability.kind, 'executor_unavailable');
    assert.equal(started.run.operability.actionable, false);
    const runDir = path.join(runsDir, created.run.runId);
    const preflight = await readJson(path.join(runDir, 'preflight_result.json'));
    const executionResults = await readJson(path.join(runDir, 'execution_results.json'));
    assert.equal(preflight.checks.python_orchestrator.failure_reason, 'python_executor_unavailable');
    assert.ok(executionResults.items.every((item) => item.failure_reason === 'python_executor_unavailable'));

    const list = await listV3Runs({ runsDir });
    assert.equal(list.runs[0].operability.kind, 'executor_unavailable');
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

test('stage2 v3 run center merges evidence-bound AI round review when a model profile is available', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: 'AI 复盘系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222',
      modelProfileIds: ['reviewer'],
      modelProfiles: [{
        id: 'reviewer',
        label: 'Reviewer',
        provider: 'openai_compatible',
        model: 'review-model',
        apiKeyConfigured: true,
        capabilityTags: { jsonSchema: true }
      }]
    }, { runsDir, modelProfiles: [{
      id: 'reviewer',
      label: 'Reviewer',
      provider: 'openai_compatible',
      model: 'review-model',
      apiKeyEnv: 'REVIEWER_API_KEY'
    }] });
    await startV3Run(created.run.runId, { executionMode: 'contract_only' }, { runsDir });

    const analyzed = await analyzeV3Run(created.run.runId, {
      runsDir,
      aiReviewRunner: async ({ evidenceBundle, modelProfile }) => ({
        analysis_mode: 'ai_assisted_review',
        model_profile_id: modelProfile.id,
        confidence: 0.88,
        coverage_summary: { summary: `reviewed ${evidenceBundle.page_entries.length} pages` },
        failure_summary: { summary: '需要补齐真实执行证据。' },
        evidence_quality: { status: 'partial', summary: '有执行结果，无真实截图。' },
        improvement_candidates: [{
          candidate_id: 'connect_real_browser',
          title: '连接真实浏览器后重跑',
          evidence_refs: ['execution_results']
        }],
        learned_rules: ['真实截图缺失时不得声称执行通过'],
        next_round_recommendations: ['连接 Playwright 后重试']
      })
    });

    assert.equal(analyzed.roundAnalysis.analysis_mode, 'ai_assisted_review');
    assert.equal(analyzed.roundAnalysis.ai_provider_status, 'completed');
    assert.equal(analyzed.roundAnalysis.model_profile.id, 'reviewer');
    assert.equal(analyzed.roundAnalysis.improvement_candidates[0].review_status, 'evidence_bound');
    assert.deepEqual(analyzed.roundAnalysis.learned_rules, ['真实截图缺失时不得声称执行通过']);
  });
});

test('stage2 v3 run center degrades AI review to rule review when model is unavailable or output is invalid', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: 'AI 降级系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222'
    }, { runsDir });
    await startV3Run(created.run.runId, { executionMode: 'contract_only' }, { runsDir });

    const unavailable = await analyzeV3Run(created.run.runId, { runsDir });
    assert.equal(unavailable.roundAnalysis.analysis_mode, 'deterministic_rule_review');
    assert.equal(unavailable.roundAnalysis.ai_provider_status, 'model_unavailable');

    const createdWithModel = await createV3Run({
      systemName: 'AI 非法输出系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222',
      modelProfileIds: ['reviewer']
    }, { runsDir, modelProfiles: [{
      id: 'reviewer',
      label: 'Reviewer',
      provider: 'openai_compatible',
      model: 'review-model',
      apiKeyEnv: 'REVIEWER_API_KEY'
    }] });
    await startV3Run(createdWithModel.run.runId, { executionMode: 'contract_only' }, { runsDir });
    const invalid = await analyzeV3Run(createdWithModel.run.runId, {
      runsDir,
      aiReviewRunner: async () => ({ invalid: true })
    });
    assert.equal(invalid.roundAnalysis.analysis_mode, 'deterministic_rule_review');
    assert.equal(invalid.roundAnalysis.ai_provider_status, 'invalid_ai_output');
    assert.ok(invalid.roundAnalysis.review_errors.some((item) => item.code === 'invalid_ai_output'));
  });
});

test('stage2 v3 run center marks AI direction claims without evidence as needs evidence', async () => {
  await withTempRunsDir(async (runsDir) => {
    const created = await createV3Run({
      systemName: 'AI 证据绑定系统',
      entryUrl: 'https://example.com/home',
      cdpUrl: 'http://localhost:9222',
      modelProfileIds: ['reviewer'],
      modelProfiles: [{
        id: 'reviewer',
        label: 'Reviewer',
        provider: 'openai_compatible',
        model: 'review-model',
        apiKeyConfigured: true,
        capabilityTags: { jsonSchema: true }
      }]
    }, { runsDir, modelProfiles: [{
      id: 'reviewer',
      label: 'Reviewer',
      provider: 'openai_compatible',
      model: 'review-model',
      apiKeyEnv: 'REVIEWER_API_KEY'
    }] });
    await startV3Run(created.run.runId, { executionMode: 'contract_only' }, { runsDir });

    const analyzed = await analyzeV3Run(created.run.runId, {
      runsDir,
      aiReviewRunner: async () => ({
        analysis_mode: 'ai_assisted_review',
        confidence: 0.64,
        improvement_candidates: [{
          candidate_id: 'unsupported_claim',
          title: '声称提交链路已稳定',
          evidence_refs: []
        }]
      })
    });

    assert.equal(analyzed.roundAnalysis.analysis_mode, 'ai_assisted_review');
    assert.equal(analyzed.roundAnalysis.improvement_candidates[0].review_status, 'needs_evidence');
    assert.equal(analyzed.roundAnalysis.improvement_candidates[0].blocks_next_round, true);
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
    assert.notEqual(continued.run.status, 'planned');
    assert.equal(continued.run.status, 'waiting_human');
    assert.equal(continued.run.currentRoundId, 'round_002');
  });
});
