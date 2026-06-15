const { analyzeRequirements, generateTestCases } = require('./agents');
const { runWebUiAssessment, generateRegressionCode } = require('./executors');

function createProject(input) {
  const now = new Date().toISOString();
  return {
    id: `ap-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    name: input.name || '未命名评测项目',
    client: input.client || '',
    vendor: input.vendor || '',
    sut: {
      name: input.sutName || '',
      baseUrl: input.sutBaseUrl || '',
      environment: input.environment || '测试环境',
      accountNotes: input.accountNotes || ''
    },
    scope: input.scope || '',
    documentText: input.documentText || '',
    riskPolicy: input.riskPolicy || '高风险操作需人工确认',
    status: '资料待解析',
    featureModules: [],
    acceptanceCriteria: [],
    testScenarios: [],
    testCases: [],
    executions: null,
    defects: [],
    report: null,
    generatedCode: '',
    createdAt: now,
    updatedAt: now
  };
}

async function analyzeProject(project) {
  const analysis = await analyzeRequirements(project);
  return {
    ...project,
    ...analysis,
    status: '用例待生成'
  };
}

function designTestCases(project) {
  const testCases = generateTestCases(project);
  return {
    ...project,
    testCases,
    status: '用例待执行'
  };
}

function executeProject(project) {
  const executions = runWebUiAssessment(project);
  const generatedCode = generateRegressionCode({ ...project, executions });
  return {
    ...project,
    executions,
    defects: executions.defects,
    generatedCode,
    status: '评测报告待生成'
  };
}

function buildReport(project) {
  const summary = project.executions?.summary || {
    total: 0,
    passed: 0,
    failed: 0,
    environmentIssues: 0,
    manualReview: 0,
    passRate: 0
  };

  const report = {
    title: `${project.name} 评测报告`,
    generatedAt: new Date().toISOString(),
    conclusion: summary.failed > 0
      ? '被测系统存在未满足验收项的功能缺陷，建议整改后复测。'
      : '当前执行范围内未发现明确功能缺陷，需结合人工确认项形成最终验收意见。',
    coverage: {
      featureModules: project.featureModules.length,
      featurePoints: project.featureModules.reduce((count, item) => count + item.featurePoints.length, 0),
      acceptanceCriteria: project.acceptanceCriteria.length,
      testCases: project.testCases.length,
      executedCases: summary.total
    },
    verdictSummary: summary,
    defects: project.defects,
    riskNotes: [
      project.riskPolicy,
      '当前 MVP 使用模拟执行器，真实浏览器执行接入后应以 Playwright trace、截图和网络日志作为正式证据。',
      '移动 App 和 Windows 独立应用第一阶段仅纳入手工证据与报告汇总范围。'
    ]
  };

  return {
    ...project,
    report,
    status: '评测报告已生成'
  };
}

module.exports = {
  createProject,
  analyzeProject,
  designTestCases,
  executeProject,
  buildReport
};
