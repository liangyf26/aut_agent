const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs/promises');
const os = require('os');
const path = require('path');

const { GOAL_KINDS, listGoalLoopRuns, resolveGoalLoopRunArtifact } = require('./stage2GoalLoopRunCenter');

async function withTempStage2Dir(callback) {
  const stage2Dir = await fs.mkdtemp(path.join(os.tmpdir(), 'stage2-goal-loop-'));
  try {
    return await callback(stage2Dir);
  } finally {
    await fs.rm(stage2Dir, { recursive: true, force: true });
  }
}

async function writeJson(filePath, payload) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(payload, null, 2), 'utf8');
}

test('listGoalLoopRuns discovers a run per kind and reads its run_summary.json', async () => {
  await withTempStage2Dir(async (stage2Dir) => {
    await writeJson(path.join(stage2Dir, 'menu_goal_runs', 'run_001', 'menu_entries.json'), []);
    await writeJson(path.join(stage2Dir, 'menu_goal_runs', 'run_001', 'run_summary.json'), {
      run_id: 'run_001',
      domain: 'menu_discovery',
      total_goals: 3,
      succeeded: 2,
      failed: 1,
      pending: 0,
      generated_at: '2026-07-05T00:00:00+00:00'
    });

    await writeJson(path.join(stage2Dir, 'execution_goal_runs', 'run_002', 'execution_results.json'), []);
    await writeJson(path.join(stage2Dir, 'execution_goal_runs', 'run_002', 'human_takeover.json'), {
      status: 'waiting_human'
    });

    const runs = await listGoalLoopRuns({ stage2Dir });

    assert.deepEqual(Object.keys(runs).sort(), Object.keys(GOAL_KINDS).sort());
    assert.equal(runs.menu.length, 1);
    assert.equal(runs.menu[0].runId, 'run_001');
    assert.equal(runs.menu[0].summaryMissing, false);
    assert.equal(runs.menu[0].summary.total_goals, 3);
    assert.equal(runs.menu[0].generatedAt, '2026-07-05T00:00:00+00:00');
    const menuArtifactKeys = runs.menu[0].artifacts.map((item) => item.key);
    assert.ok(menuArtifactKeys.includes('menu_entries'));
    assert.ok(!menuArtifactKeys.includes('menu_entries_raw'));

    assert.equal(runs.page.length, 0);
    assert.equal(runs.feature.length, 0);

    assert.equal(runs.execution.length, 1);
    assert.equal(runs.execution[0].summaryMissing, true);
    const executionArtifactKeys = runs.execution[0].artifacts.map((item) => item.key);
    assert.ok(executionArtifactKeys.includes('human_takeover'));
    assert.ok(!executionArtifactKeys.includes('run_summary'));
  });
});

test('listGoalLoopRuns ignores directories without the kind anchor file', async () => {
  await withTempStage2Dir(async (stage2Dir) => {
    await fs.mkdir(path.join(stage2Dir, 'menu_goal_runs', 'incomplete_run'), { recursive: true });

    const runs = await listGoalLoopRuns({ stage2Dir });
    assert.equal(runs.menu.length, 0);
  });
});

test('resolveGoalLoopRunArtifact returns a safe path for a whitelisted artifact key', async () => {
  await withTempStage2Dir(async (stage2Dir) => {
    const entriesPath = path.join(stage2Dir, 'page_goal_runs', 'run_010', 'page_entries.json');
    await writeJson(entriesPath, [{ page_id: 'p1', status: 'reachable' }]);

    const artifact = await resolveGoalLoopRunArtifact('page', 'run_010', 'page_entries', { stage2Dir });
    assert.ok(artifact);
    assert.equal(artifact.fileName, 'page_entries.json');
    assert.equal(artifact.href, '/api/stage2/goal-loop/page/run_010/artifacts/page_entries');
    assert.equal(path.resolve(artifact.path), path.resolve(entriesPath));
  });
});

test('resolveGoalLoopRunArtifact rejects unknown kind, unknown artifact key, and path traversal', async () => {
  await withTempStage2Dir(async (stage2Dir) => {
    await writeJson(path.join(stage2Dir, 'feature_goal_runs', 'run_020', 'feature_points.json'), []);

    assert.equal(await resolveGoalLoopRunArtifact('bogus_kind', 'run_020', 'feature_points', { stage2Dir }), null);
    assert.equal(await resolveGoalLoopRunArtifact('feature', 'run_020', 'bogus_key', { stage2Dir }), null);
    assert.equal(await resolveGoalLoopRunArtifact('feature', '../../etc', 'feature_points', { stage2Dir }), null);
    assert.equal(await resolveGoalLoopRunArtifact('feature', 'run_020', 'feature_points', { stage2Dir: '/nonexistent' }), null);
  });
});
