const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs/promises');
const os = require('os');
const path = require('path');

const {
  OperationInputError,
  buildOperationCommand,
  runOperationStep
} = require('./stage2OperationCenter');

test('runOperationStep accepts onboarding parameter aliases and builds the expected CLI args', async () => {
  const operationsDir = await fs.mkdtemp(path.join(os.tmpdir(), 'stage2-operation-'));
  let capturedArgs = null;

  try {
    const result = await runOperationStep({
      stepId: 'bootstrap_template',
      parameters: {
        targetTemplate: 'demo_query_entry',
        homeUrl: 'https://example.com/query',
        pageName: '示例查询页',
        scenarioKind: 'query'
      }
    }, {
      operationsDir,
      execFileRunner: (_command, args, _options, callback) => {
        capturedArgs = args;
        callback(null, '{"run_dir":"runs/demo"}', '');
      }
    });

    assert.equal(result.result.status, 'completed');
    assert.ok(result.session.sessionId.startsWith('op_'));
    assert.deepEqual(capturedArgs, [
      '-m',
      'prototype.stage2.main',
      '--bootstrap-template',
      '--template',
      'demo_query_entry',
      '--page-url',
      'https://example.com/query',
      '--page-name',
      '示例查询页',
      '--scenario-kind',
      'query'
    ]);
  } finally {
    await fs.rm(operationsDir, { recursive: true, force: true });
  }
});

test('buildOperationCommand rejects unsupported scenario kinds before spawning a process', () => {
  assert.throws(
    () => buildOperationCommand('bootstrap_template', {
      templateName: 'demo_query_entry',
      pageUrl: 'https://example.com/query',
      pageName: '示例查询页',
      scenarioKind: 'submit'
    }),
    OperationInputError
  );
});

test('buildOperationCommand rejects remote CDP URLs by default', () => {
  assert.throws(
    () => buildOperationCommand('validation_matrix', {
      cdpUrl: 'http://example.com:9222'
    }),
    /cdpUrl 只允许/
  );
});

test('buildOperationCommand allows local CDP URLs', () => {
  const command = buildOperationCommand('validation_matrix', {
    cdpUrl: 'http://127.0.0.1:9222'
  });

  assert.deepEqual(command.args.slice(-2), ['--cdp-url', 'http://127.0.0.1:9222/']);
});
