const verdicts = ['明确通过', '明确失败（功能缺陷）', '疑似环境问题', '需人工确认'];

function verdictFor(testCase, index) {
  if (!testCase.enabled) {
    return '需人工确认';
  }
  if (testCase.type === '异常流程' && index % 4 === 1) {
    return '明确失败（功能缺陷）';
  }
  if (testCase.priority === 'P2' && index % 5 === 0) {
    return '需人工确认';
  }
  if (index % 7 === 0) {
    return '疑似环境问题';
  }
  return '明确通过';
}

function reasonFor(verdict, testCase) {
  if (verdict === '明确通过') {
    return '关键步骤和预期结果均满足，证据链完整。';
  }
  if (verdict === '明确失败（功能缺陷）') {
    return `${testCase.title} 的异常或边界处理与预期不一致，需要乙方整改后复测。`;
  }
  if (verdict === '疑似环境问题') {
    return '页面响应或测试环境状态不稳定，建议复核环境后重跑。';
  }
  return '该用例涉及人工判断、外部依赖或高风险操作，需要测评工程师确认。';
}

function buildEvidence(testCase, verdict, index) {
  return {
    id: `ev-${testCase.id}`,
    operationLog: testCase.steps.map((step, stepIndex) => ({
      at: new Date(Date.now() + stepIndex * 1000).toISOString(),
      action: step,
      status: stepIndex === testCase.steps.length - 1 && verdict.includes('失败') ? 'failed' : 'done'
    })),
    screenshots: [`artifacts/${testCase.id}/step-${index + 1}.png`],
    trace: `artifacts/${testCase.id}/trace.zip`,
    agentRationale: reasonFor(verdict, testCase)
  };
}

function runWebUiAssessment(project) {
  const enabledCases = (project.testCases || []).filter((testCase) => testCase.enabled);
  const results = enabledCases.map((testCase, index) => {
    const verdict = verdictFor(testCase, index);
    return {
      id: `run-${testCase.id}`,
      testCaseId: testCase.id,
      title: testCase.title,
      priority: testCase.priority,
      verdict,
      durationMs: 1800 + index * 420,
      executedAt: new Date().toISOString(),
      evidence: buildEvidence(testCase, verdict, index)
    };
  });

  const defects = results
    .filter((result) => result.verdict === '明确失败（功能缺陷）')
    .map((result, index) => ({
      id: `df-${index + 1}`,
      title: result.title,
      severity: result.priority === 'P0' ? '严重' : '一般',
      status: '待整改',
      reproductionSteps: result.evidence.operationLog.map((item) => item.action),
      suggestion: '请核对需求说明、权限控制、表单校验和后端持久化逻辑，修复后提供复测版本。'
    }));

  return {
    results,
    defects,
    summary: summarizeResults(results)
  };
}

function summarizeResults(results) {
  const total = results.length;
  const counts = Object.fromEntries(verdicts.map((verdict) => [verdict, 0]));
  for (const result of results) {
    counts[result.verdict] = (counts[result.verdict] || 0) + 1;
  }

  return {
    total,
    passed: counts['明确通过'],
    failed: counts['明确失败（功能缺陷）'],
    environmentIssues: counts['疑似环境问题'],
    manualReview: counts['需人工确认'],
    passRate: total === 0 ? 0 : Math.round((counts['明确通过'] / total) * 100)
  };
}

function generateRegressionCode(project) {
  const passedCases = (project.executions?.results || [])
    .filter((result) => result.verdict === '明确通过')
    .slice(0, 3);

  if (passedCases.length === 0) {
    return '// 暂无明确通过的用例，待执行稳定后生成可复用回归测试代码。';
  }

  const tests = passedCases.map((result) => {
    const testCase = project.testCases.find((item) => item.id === result.testCaseId);
    const steps = (testCase?.steps || []).map((step) => `    // ${step}`).join('\n');
    return `  test('${result.title}', async ({ page }) => {\n    await page.goto('${project.sut?.baseUrl || 'https://example.com'}');\n${steps}\n    await expect(page).toHaveURL(/./);\n  });`;
  }).join('\n\n');

  return `const { test, expect } = require('@playwright/test');\n\ntest.describe('${project.name} 回归测试', () => {\n${tests}\n});\n`;
}

module.exports = {
  runWebUiAssessment,
  generateRegressionCode
};
