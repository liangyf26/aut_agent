const test = require('node:test');
const assert = require('node:assert/strict');

const {
  loadStage2Overview,
  resolveHumanLoopArtifact,
  resolveStage2RunArtifact
} = require('./stage2Dashboard');

test('loadStage2Overview exposes human-loop and baseline-freeze summaries without local paths', async () => {
  const overview = await loadStage2Overview();

  assert.ok(overview);
  assert.ok(Array.isArray(overview.runSummaries));
  assert.ok(Array.isArray(overview.sessionSummaries));

  if (overview.sessionSummaries.length) {
    const session = overview.sessionSummaries[0];
    assert.equal(typeof session.sessionId, 'string');
    assert.ok(Array.isArray(session.timeline));
  }

  if (overview.humanLoopSummary) {
    assert.equal(typeof overview.humanLoopSummary.sessionId, 'string');
    assert.ok(Array.isArray(overview.humanLoopSummary.artifacts));
    for (const artifact of overview.humanLoopSummary.artifacts) {
      assert.ok(artifact.href.startsWith('/api/stage2/human-loop/'));
      assert.equal(Object.prototype.hasOwnProperty.call(artifact, 'path'), false);
    }
  }

  if (overview.latestBaselineFreezeManifest?.recommendedPrimaryRun) {
    const recommendedRun = overview.latestBaselineFreezeManifest.recommendedPrimaryRun;
    assert.equal(typeof recommendedRun.model, 'string');
    assert.ok(Array.isArray(recommendedRun.artifacts));
    for (const artifact of recommendedRun.artifacts) {
      assert.ok(artifact.href.startsWith('/api/stage2/runs/'));
      assert.equal(Object.prototype.hasOwnProperty.call(artifact, 'path'), false);
    }
  }
});

test('resolveHumanLoopArtifact returns latest session review artifacts', async () => {
  const overview = await loadStage2Overview();

  if (!overview.humanLoopSummary) {
    return;
  }

  const artifact = await resolveHumanLoopArtifact(
    overview.humanLoopSummary.sessionId,
    'recording_summary_json'
  );

  assert.ok(artifact);
  assert.equal(artifact.key, 'recording_summary_json');
  assert.equal(artifact.fileName, 'recording_summary.json');
});

test('resolveStage2RunArtifact resolves promotion and baseline review artifacts', async () => {
  const overview = await loadStage2Overview();
  const run = overview.runSummaries.find((item) => item.stats.promotionCandidates > 0) || overview.runSummaries[0];

  if (!run) {
    return;
  }

  const promotionArtifact = await resolveStage2RunArtifact(run.runId, 'promotion_candidates_json');
  assert.ok(promotionArtifact);
  assert.equal(promotionArtifact.fileName, 'promotion_candidates.json');

  const baselineArtifact = await resolveStage2RunArtifact(run.runId, 'baseline_snapshot_json');
  assert.ok(baselineArtifact);
  assert.equal(baselineArtifact.fileName, 'baseline_snapshot.json');
});
