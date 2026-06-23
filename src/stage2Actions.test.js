const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs/promises');
const os = require('os');
const path = require('path');

const {
  buildResumeHumanTakeoverArgs,
  markHumanTakeoverResolved,
  resumeHumanTakeover
} = require('./stage2Actions');

const repoRoot = process.cwd();

test('buildResumeHumanTakeoverArgs prefers packet defaults and appends operator metadata', () => {
  const args = buildResumeHumanTakeoverArgs('C:\\tmp\\runA', {
    resume_max_attempts: 4,
    resume_max_rounds: 2
  }, {
    cdpUrl: 'http://localhost:9555',
    operatorId: 'tester-1',
    note: 'ready to continue'
  });

  assert.deepEqual(args, [
    '-m',
    'prototype.stage2.main',
    '--resume-human-takeover',
    'C:\\tmp\\runA',
    '--cdp-url',
    'http://localhost:9555',
    '--max-attempts',
    '4',
    '--max-rounds',
    '2',
    '--resume-operator',
    'tester-1',
    '--resume-note',
    'ready to continue'
  ]);
});

test('markHumanTakeoverResolved writes resolution payload without claiming automatic fix', async () => {
  const tempBase = path.join(repoRoot, 'tmp');
  await fs.mkdir(tempBase, { recursive: true });
  const tempRoot = await fs.mkdtemp(path.join(tempBase, 'stage2-actions-'));
  const runId = '20260623_130000_modelA';
  const runDir = path.join(repoRoot, 'artifacts', 'stage2', runId);
  await fs.mkdir(runDir, { recursive: true });
  await fs.writeFile(path.join(runDir, 'human_takeover.json'), JSON.stringify({
    source_run_id: runId,
    source_run_dir: runDir,
    scheduled_action_ids: ['retry-001'],
    resume_command: 'python -m prototype.stage2.main --resume-human-takeover ...'
  }, null, 2), 'utf8');

  try {
    const result = await markHumanTakeoverResolved({
      runId,
      operatorId: 'run-center',
      note: 'manual workaround applied',
      readyToResume: true
    });

    assert.equal(result.status, 'resolved');
    const resolutionPath = path.join(runDir, 'human_takeover_resolution.json');
    const resolution = JSON.parse(await fs.readFile(resolutionPath, 'utf8'));
    assert.equal(resolution.status, 'resolved');
    assert.equal(resolution.note, 'manual workaround applied');
    assert.equal(resolution.ready_to_resume, true);
    assert.deepEqual(resolution.handled_action_ids, ['retry-001']);
  } finally {
    await fs.rm(runDir, { recursive: true, force: true });
    await fs.rm(tempRoot, { recursive: true, force: true });
  }
});

test('markHumanTakeoverResolved refreshes session artifacts after resolution', async () => {
  const tempBase = path.join(repoRoot, 'tmp');
  await fs.mkdir(tempBase, { recursive: true });
  const tempRoot = await fs.mkdtemp(path.join(tempBase, 'stage2-actions-'));
  const runId = '20260623_132500_modelA';
  const runDir = path.join(repoRoot, 'artifacts', 'stage2', runId);
  await fs.mkdir(runDir, { recursive: true });
  await fs.writeFile(path.join(runDir, 'human_takeover.json'), JSON.stringify({
    source_run_id: runId,
    source_run_dir: runDir,
    scheduled_action_ids: ['retry-002'],
    resume_command: 'python -m prototype.stage2.main --resume-human-takeover ...'
  }, null, 2), 'utf8');

  try {
    let capturedRunSummaries = null;
    const result = await markHumanTakeoverResolved(
      {
        runId,
        operatorId: 'run-center',
        note: 'manual review completed',
        readyToResume: false
      },
      {
        loadStage2Overview: async () => ({
          runSummaries: [
            {
              runId,
              orchestrationStreamId: 'session::A',
              waitingHuman: false,
              humanTakeover: { status: 'resolved' }
            }
          ]
        }),
        syncStage2SessionArtifacts: async (runSummaries) => {
          capturedRunSummaries = runSummaries;
          return [];
        }
      }
    );

    assert.equal(result.status, 'resolved');
    assert.ok(Array.isArray(capturedRunSummaries));
    assert.equal(capturedRunSummaries.length, 1);
    assert.equal(capturedRunSummaries[0].runId, runId);
  } finally {
    await fs.rm(runDir, { recursive: true, force: true });
    await fs.rm(tempRoot, { recursive: true, force: true });
  }
});

test('resumeHumanTakeover delegates to python entrypoint and returns parsed payload', async () => {
  const tempBase = path.join(repoRoot, 'tmp');
  await fs.mkdir(tempBase, { recursive: true });
  const tempRoot = await fs.mkdtemp(path.join(tempBase, 'stage2-actions-'));
  const runId = '20260623_131000_modelA';
  const runDir = path.join(repoRoot, 'artifacts', 'stage2', runId);
  await fs.mkdir(runDir, { recursive: true });
  await fs.writeFile(path.join(runDir, 'human_takeover.json'), JSON.stringify({
    resume_max_attempts: 3,
    resume_max_rounds: 1
  }, null, 2), 'utf8');

  try {
    let capturedCommand = null;
    let capturedArgs = null;
    const result = await resumeHumanTakeover(
      {
        runId,
        operatorId: 'tester-2',
        note: 'resume from test'
      },
      {
        execFileRunner: async (command, args) => {
          capturedCommand = command;
          capturedArgs = args;
          return {
            stdout: JSON.stringify({
              run_dir: path.join(tempRoot, 'artifacts', 'stage2', '20260623_132000_modelA'),
              status: 'completed'
            }),
            stderr: ''
          };
        }
      }
    );

    assert.ok(capturedCommand);
    assert.ok(capturedArgs.includes('--resume-human-takeover'));
    assert.ok(capturedArgs.includes('--resume-operator'));
    assert.equal(result.status, 'completed');
    assert.equal(result.latestRunId, '20260623_132000_modelA');
    assert.equal(result.payload.status, 'completed');
  } finally {
    await fs.rm(runDir, { recursive: true, force: true });
    await fs.rm(tempRoot, { recursive: true, force: true });
  }
});
