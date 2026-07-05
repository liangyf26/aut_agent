const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs/promises');
const os = require('os');
const path = require('path');

const {
  parseJUnitXml,
  evaluateGoalLoopStepResult,
  runGoalChainStage,
  runGoalChainEndToEnd,
  listTestCenterRuns,
  persistTestCenterRun,
  resolveTestCenterArtifact
} = require('./stage2TestCenter');

async function withTempDir(prefix, callback) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), prefix));
  try {
    return await callback(dir);
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
}

async function writeJson(filePath, payload) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(payload, null, 2), 'utf8');
}

// ---------------------------------------------------------------------------
// parseJUnitXml
// ---------------------------------------------------------------------------

test('parseJUnitXml parses passed, failed and skipped testcases', () => {
  const xml = `<?xml version="1.0" encoding="utf-8"?>
<testsuites>
<testsuite name="pytest" errors="0" failures="1" skipped="1" tests="3" time="0.123">
<testcase classname="tests.test_menu_goal_loader" name="test_load_ok" time="0.01" />
<testcase classname="tests.test_menu_goal_loader" name="test_load_bad" time="0.02">
<failure message="AssertionError: expected 1 got 2">Traceback</failure>
</testcase>
<testcase classname="tests.test_menu_goal_loader" name="test_load_skip" time="0.00">
<skipped message="playwright not installed" type="pytest.skip"></skipped>
</testcase>
</testsuite>
</testsuites>`;

  const result = parseJUnitXml(xml);
  assert.equal(result.totals.total, 3);
  assert.equal(result.totals.passed, 1);
  assert.equal(result.totals.failed, 1);
  assert.equal(result.totals.skipped, 1);

  assert.equal(result.testcases[0].name, 'test_load_ok');
  assert.equal(result.testcases[0].status, 'passed');

  assert.equal(result.testcases[1].name, 'test_load_bad');
  assert.equal(result.testcases[1].status, 'failed');
  assert.equal(result.testcases[1].message, 'AssertionError: expected 1 got 2');

  assert.equal(result.testcases[2].name, 'test_load_skip');
  assert.equal(result.testcases[2].status, 'skipped');
  assert.equal(result.testcases[2].message, 'playwright not installed');
});

test('parseJUnitXml decodes XML entities in failure messages', () => {
  const xml = `<testsuite>
<testcase classname="c" name="n" time="0.0">
<failure message="a &amp; b &lt;value&gt;">line1&#10;line2</failure>
</testcase>
</testsuite>`;
  const result = parseJUnitXml(xml);
  assert.equal(result.testcases[0].message, 'a & b <value>');
});

test('parseJUnitXml returns empty totals for empty input', () => {
  const result = parseJUnitXml('');
  assert.deepEqual(result.totals, { total: 0, passed: 0, failed: 0, skipped: 0 });
  assert.deepEqual(result.testcases, []);
});

// ---------------------------------------------------------------------------
// evaluateGoalLoopStepResult — 覆盖评价规则表的每一行
// ---------------------------------------------------------------------------

test('evaluateGoalLoopStepResult: non-zero exit code is always failed', () => {
  const result = evaluateGoalLoopStepResult({ stageId: 'menu', exitCode: 1, runSummary: { succeeded: 3 } });
  assert.equal(result.verdict, 'failed');
});

test('evaluateGoalLoopStepResult: unresolved human_takeover is needs_human, not failed', () => {
  const result = evaluateGoalLoopStepResult({
    stageId: 'execution',
    exitCode: 0,
    humanTakeover: { status: 'waiting_human', waiting_reason: 'permission_blocked' },
    humanTakeoverResolution: null
  });
  assert.equal(result.verdict, 'needs_human');
  assert.ok(result.reason.includes('permission_blocked'));
});

test('evaluateGoalLoopStepResult: human_takeover with ready_to_resume resolution is not blocking by itself', () => {
  const result = evaluateGoalLoopStepResult({
    stageId: 'execution',
    exitCode: 0,
    humanTakeover: { status: 'waiting_human', waiting_reason: 'permission_blocked' },
    humanTakeoverResolution: { ready_to_resume: true },
    runSummary: { succeeded: 1, failed: 0 }
  });
  assert.equal(result.verdict, 'passed');
});

test('evaluateGoalLoopStepResult: run_report.summary.status=needs_review is needs_human', () => {
  const result = evaluateGoalLoopStepResult({
    stageId: 'execution',
    exitCode: 0,
    runReport: { summary: { status: 'needs_review' } }
  });
  assert.equal(result.verdict, 'needs_human');
});

test('evaluateGoalLoopStepResult: run_report.summary.status=completed_with_failures is failed', () => {
  const result = evaluateGoalLoopStepResult({
    stageId: 'execution',
    exitCode: 0,
    runReport: { summary: { status: 'completed_with_failures' } }
  });
  assert.equal(result.verdict, 'failed');
});

test('evaluateGoalLoopStepResult: run_report.summary.status=completed is passed', () => {
  const result = evaluateGoalLoopStepResult({
    stageId: 'execution',
    exitCode: 0,
    runReport: { summary: { status: 'completed' } }
  });
  assert.equal(result.verdict, 'passed');
});

test('evaluateGoalLoopStepResult: run_summary.failed > 0 without run_report is failed', () => {
  const result = evaluateGoalLoopStepResult({
    stageId: 'menu',
    exitCode: 0,
    runSummary: { total_goals: 3, succeeded: 1, failed: 2, pending: 0 }
  });
  assert.equal(result.verdict, 'failed');
});

test('evaluateGoalLoopStepResult: root_conclusion in failed statuses is failed', () => {
  const result = evaluateGoalLoopStepResult({
    stageId: 'menu',
    exitCode: 0,
    runSummary: { total_goals: 1, succeeded: 0, failed: 0, pending: 0, root_conclusion: 'failed_max_rounds' }
  });
  assert.equal(result.verdict, 'failed');
});

test('evaluateGoalLoopStepResult: root_conclusion in human-required statuses is needs_human', () => {
  const result = evaluateGoalLoopStepResult({
    stageId: 'menu',
    exitCode: 0,
    runSummary: { total_goals: 1, succeeded: 0, failed: 0, pending: 0, root_conclusion: 'waiting_human' }
  });
  assert.equal(result.verdict, 'needs_human');
});

test('evaluateGoalLoopStepResult: succeeded > 0 with no failure signal is passed', () => {
  const result = evaluateGoalLoopStepResult({
    stageId: 'menu',
    exitCode: 0,
    runSummary: { total_goals: 3, succeeded: 3, failed: 0, pending: 0 }
  });
  assert.equal(result.verdict, 'passed');
});

test('evaluateGoalLoopStepResult: no usable artifacts is unknown', () => {
  const result = evaluateGoalLoopStepResult({ stageId: 'menu', exitCode: 0 });
  assert.equal(result.verdict, 'unknown');
});

// ---------------------------------------------------------------------------
// runGoalChainStage — 单阶段执行 + 产物读取 + 评价
// ---------------------------------------------------------------------------

test('runGoalChainStage reads run_summary.json from the injected stage2Dir and evaluates it', async () => {
  await withTempDir('stage2-test-center-stage-', async (stage2Dir) => {
    await writeJson(path.join(stage2Dir, 'menu_goal_runs', 'demo_run_for_test', 'run_summary.json'), {
      run_id: 'demo_run_for_test',
      total_goals: 2,
      succeeded: 2,
      failed: 0,
      pending: 0
    });

    let capturedArgs = null;
    const result = await runGoalChainStage(
      'menu',
      { cdpUrl: 'http://localhost:9222', maxPages: 5, runId: 'demo_run_for_test' },
      {
        stage2Dir,
        resolvePythonCommand: async () => 'python',
        execFileRunner: (_command, args, _options, callback) => {
          capturedArgs = args;
          callback(null, '{"menu_entries_raw_path":"artifacts/stage2/menu_goal_runs/demo_run_for_test/menu_entries_raw.json"}', '');
        }
      }
    );

    assert.ok(capturedArgs.includes('--run-menu-goal'));
    assert.ok(capturedArgs.includes('--cdp-url'));
    assert.ok(capturedArgs.includes('http://localhost:9222/'));
    assert.ok(capturedArgs.includes('--goal-chain-run-id'));
    assert.ok(capturedArgs.includes('demo_run_for_test'));
    assert.equal(result.stageId, 'menu');
    assert.equal(result.exitCode, 0);
    assert.equal(result.chainOutputPath, 'artifacts/stage2/menu_goal_runs/demo_run_for_test/menu_entries_raw.json');
    assert.equal(result.runSummary.total_goals, 2);
    assert.equal(result.evaluation.verdict, 'passed');
  });
});

test('runGoalChainStage marks the step failed when the command exits non-zero', async () => {
  const result = await runGoalChainStage(
    'menu',
    { cdpUrl: 'http://localhost:9222', maxPages: 5, runId: 'demo_run_failure' },
    {
      resolvePythonCommand: async () => 'python',
      execFileRunner: (_command, _args, _options, callback) => {
        const error = new Error('boom');
        error.code = 1;
        callback(error, '', 'Traceback: something broke');
      }
    }
  );

  assert.equal(result.exitCode, 1);
  assert.equal(result.evaluation.verdict, 'failed');
});

test('runGoalChainStage rejects an unsafe menuEntriesPath outside artifacts/stage2', async () => {
  await withTempDir('stage2-test-center-unsafe-', async (stage2Dir) => {
    await assert.rejects(
      () => runGoalChainStage('page', { cdpUrl: 'http://localhost:9222', menuEntriesPath: '../../etc/passwd' }, {
        stage2Dir,
        resolvePythonCommand: async () => 'python',
        execFileRunner: (_c, _a, _o, callback) => callback(null, '{}', '')
      }),
      /artifacts\/stage2/
    );
  });
});

// ---------------------------------------------------------------------------
// runGoalChainEndToEnd — 中途失败/需人工即停
// ---------------------------------------------------------------------------

test('runGoalChainEndToEnd stops at the first non-passed stage and does not run later stages', async () => {
  await withTempDir('stage2-test-center-e2e-stop-', async (stage2Dir) => {
    const menuOutputDir = path.join(stage2Dir, 'menu_goal_runs', 'e2e_stop_test');
    await writeJson(path.join(menuOutputDir, 'run_summary.json'), {
      run_id: 'e2e_stop_test',
      total_goals: 1,
      succeeded: 1,
      failed: 0,
      pending: 0
    });
    const menuEntriesRawPath = path.join(menuOutputDir, 'menu_entries_raw.json').replace(/\\/g, '\\\\');

    let callCount = 0;
    const result = await runGoalChainEndToEnd(
      { cdpUrl: 'http://localhost:9222', runId: 'e2e_stop_test' },
      {
        stage2Dir,
        resolvePythonCommand: async () => 'python',
        execFileRunner: (_command, args, _options, callback) => {
          callCount += 1;
          if (args.includes('--run-menu-goal')) {
            callback(null, `{"menu_entries_raw_path":"${menuEntriesRawPath}"}`, '');
            return;
          }
          if (args.includes('--run-page-goal')) {
            const error = new Error('page stage exploded');
            error.code = 2;
            callback(error, '', 'boom');
            return;
          }
          throw new Error(`unexpected stage invoked: ${args.join(' ')}`);
        }
      }
    );

    assert.equal(callCount, 2);
    assert.equal(result.stoppedAt, 'page');
    assert.equal(result.steps.length, 2);
    assert.equal(result.steps[0].evaluation.verdict, 'passed');
    assert.equal(result.steps[1].evaluation.verdict, 'failed');
  });
});

test('runGoalChainEndToEnd runs all four stages when each stage passes', async () => {
  await withTempDir('stage2-test-center-e2e-full-', async (stage2Dir) => {
    const runId = 'e2e_full_test';
    const dirFor = (kindDir) => path.join(stage2Dir, kindDir, runId);
    const asPath = (kindDir, fileName) => path.join(dirFor(kindDir), fileName).replace(/\\/g, '\\\\');

    for (const kindDir of ['menu_goal_runs', 'page_goal_runs', 'feature_goal_runs']) {
      await writeJson(path.join(dirFor(kindDir), 'run_summary.json'), {
        run_id: runId,
        total_goals: 1,
        succeeded: 1,
        failed: 0,
        pending: 0
      });
    }
    await writeJson(path.join(dirFor('execution_goal_runs'), 'reports', 'run_report.json'), {
      summary: { status: 'completed' }
    });

    const seenFlags = [];
    const result = await runGoalChainEndToEnd(
      { cdpUrl: 'http://localhost:9222', runId, executionMode: 'fixture_simulated' },
      {
        stage2Dir,
        resolvePythonCommand: async () => 'python',
        execFileRunner: (_command, args, _options, callback) => {
          const flag = args.find((value) => value.startsWith('--run-'));
          seenFlags.push(flag);
          if (flag === '--run-menu-goal') {
            callback(null, `{"menu_entries_raw_path":"${asPath('menu_goal_runs', 'menu_entries_raw.json')}"}`, '');
          } else if (flag === '--run-page-goal') {
            callback(null, `{"page_entries_path":"${asPath('page_goal_runs', 'page_entries.json')}"}`, '');
          } else if (flag === '--run-feature-goal') {
            callback(null, `{"generated_test_cases_path":"${asPath('feature_goal_runs', 'generated_test_cases.json')}"}`, '');
          } else if (flag === '--run-execution-goal') {
            callback(null, `{"run_id":"${runId}"}`, '');
          }
        }
      }
    );

    assert.equal(seenFlags.length, 4);
    assert.deepEqual(seenFlags, ['--run-menu-goal', '--run-page-goal', '--run-feature-goal', '--run-execution-goal']);
    assert.equal(result.stoppedAt, null);
    assert.equal(result.steps.length, 4);
    assert.ok(result.steps.every((step) => step.evaluation.verdict === 'passed'));
  });
});

// ---------------------------------------------------------------------------
// 会话落盘
// ---------------------------------------------------------------------------

test('persistTestCenterRun writes a result.json and listTestCenterRuns reads it back newest-first', async () => {
  await withTempDir('stage2-test-center-runs-', async (testCenterDir) => {
    const first = await persistTestCenterRun('单元测试', { totals: { passed: 1 } }, { testCenterDir });
    await new Promise((resolve) => setTimeout(resolve, 5));
    const second = await persistTestCenterRun('端到端测试', { stoppedAt: null }, { testCenterDir });

    const runs = await listTestCenterRuns({ testCenterDir });
    assert.equal(runs.length, 2);
    assert.equal(runs[0].runId, second.runId);
    assert.equal(runs[0].kindLabel, '端到端测试');
    assert.equal(runs[1].runId, first.runId);
    assert.equal(runs[1].kindLabel, '单元测试');
  });
});

test('resolveTestCenterArtifact rejects unknown runId and path traversal', async () => {
  await withTempDir('stage2-test-center-artifact-', async (testCenterDir) => {
    await writeJson(path.join(testCenterDir, 'tc_20260706_000000_bbbbbbbb', 'result.json'), { runId: 'x' });

    assert.equal(await resolveTestCenterArtifact('../../etc/passwd', { testCenterDir }), null);
    assert.equal(await resolveTestCenterArtifact('not-a-real-run-id', { testCenterDir }), null);

    const artifact = await resolveTestCenterArtifact('tc_20260706_000000_bbbbbbbb', { testCenterDir });
    assert.ok(artifact);
    assert.equal(artifact.fileName, 'result.json');
  });
});
