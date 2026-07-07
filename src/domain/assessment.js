const { analyzeRequirements, generateTestCases } = require('./agents');
const { runWebUiAssessment, generateRegressionCode } = require('./executors');

const stageCatalog = [
  { key: 'setup', label: '项目资料' },
  { key: 'analysis', label: '需求解析' },
  { key: 'cases', label: '用例设计' },
  { key: 'execution', label: '执行验证' },
  { key: 'report', label: '报告汇总' }
];

function countFeaturePoints(project) {
  return (project.featureModules || []).reduce((total, item) => total + item.featurePoints.length, 0);
}

function buildActivityEvent({
  phaseKey,
  stepLabel,
  title,
  detail,
  tone = 'info',
  currentObject = '',
  nextAction = ''
}) {
  const phase = stageCatalog.find((item) => item.key === phaseKey) || stageCatalog[0];
  return {
    id: `evt-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    at: new Date().toISOString(),
    phaseKey: phase.key,
    phaseLabel: phase.label,
    stepLabel,
    title,
    detail,
    tone,
    currentObject,
    nextAction
  };
}

function appendActivityLog(project, event) {
  return [...(project.activityLog || []), event].slice(-16);
}

function resolvePhaseKey(project) {
  if (project.report) return 'report';
  if (project.executions?.results?.length) return 'execution';
  if (project.testCases?.length) return 'cases';
  if (project.featureModules?.length) return 'analysis';
  return 'setup';
}

function buildStageStates(phaseKey, completeAll = false) {
  const currentIndex = stageCatalog.findIndex((item) => item.key === phaseKey);
  return stageCatalog.map((stage, index) => {
    let state = 'pending';
    if (completeAll) {
      state = 'completed';
    } else if (index < currentIndex) {
      state = 'completed';
    } else if (index === currentIndex) {
      state = 'current';
    }

    return {
      ...stage,
      state
    };
  });
}

function detectBlockers(project) {
  const blockers = [];
  const summary = project.executions?.summary;
  if (!project.sut?.baseUrl) {
    blockers.push({
      tone: 'warning',
      title: '缺少系统地址',
      detail: '建议先补充被测系统访问地址，避免后续执行上下文不完整。'
    });
  }
  if (!project.documentText && !project.scope) {
    blockers.push({
      tone: 'manual',
      title: '需求输入较少',
      detail: '当前仍偏第一阶段工作流，建议补充范围或需求资料，便于生成稳定用例。'
    });
  }
  if (summary?.manualReview > 0) {
    blockers.push({
      tone: 'manual',
      title: '存在待人工确认项',
      detail: `当前有 ${summary.manualReview} 条执行结果需要人工确认后再形成最终结论。`
    });
  }
  if (summary?.environmentIssues > 0) {
    blockers.push({
      tone: 'warning',
      title: '存在环境不稳定项',
      detail: `当前有 ${summary.environmentIssues} 条结果被标记为疑似环境问题，建议复核环境后再复跑。`
    });
  }
  return blockers;
}

function buildStepLabel(project, phaseKey) {
  const summary = project.executions?.summary;
  switch (phaseKey) {
    case 'setup':
      return '维护项目资料与被测系统上下文';
    case 'analysis':
      return project.featureModules?.length
        ? `已识别 ${project.featureModules.length} 个功能模块与 ${project.acceptanceCriteria.length} 条验收项`
        : '解析需求资料并提取验收范围';
    case 'cases':
      return project.testCases?.length
        ? `已生成 ${project.testCases.length} 条测试用例，等待执行`
        : '生成可审核的测试用例';
    case 'execution':
      return summary
        ? `已执行 ${summary.total} 条用例，待汇总结论`
        : '执行评测并收集证据';
    case 'report':
      return project.report ? '评测结论与结构化产物已可复核' : '汇总执行结果并生成报告';
    default:
      return '等待平台动作';
  }
}

function buildCurrentObject(project, phaseKey) {
  if (phaseKey === 'analysis' && project.featureModules?.length) {
    return project.featureModules[0].name;
  }
  if (phaseKey === 'cases' && project.testCases?.length) {
    return project.testCases[0].title;
  }
  if (phaseKey === 'execution' && project.executions?.results?.length) {
    const failed = project.executions.results.find((item) => item.verdict.includes('失败'));
    const manual = project.executions.results.find((item) => item.verdict.includes('人工'));
    return failed?.title || manual?.title || project.executions.results[0].title;
  }
  if (phaseKey === 'report' && project.report) {
    return project.report.title;
  }
  return project.sut?.name || project.name;
}

function buildNextAction(project, phaseKey, blockers) {
  if (!project.id) {
    return '先保存项目资料';
  }
  if (blockers.some((item) => item.title === '存在待人工确认项')) {
    return '先人工复核待确认项，再决定是否生成报告';
  }

  switch (phaseKey) {
    case 'setup':
      return '点击“解析需求”进入下一阶段';
    case 'analysis':
      return '点击“生成用例”整理验证路径';
    case 'cases':
      return '点击“执行评测”收集第一轮证据';
    case 'execution':
      return '点击“生成报告”汇总结论与风险';
    case 'report':
      return project.defects?.length
        ? '复核缺陷清单并安排复测'
        : '复核报告后沉淀回归代码';
    default:
      return '等待下一步操作';
  }
}

function buildRunCenter(project) {
  const phaseKey = resolvePhaseKey(project);
  const blockers = detectBlockers(project);
  const summary = project.executions?.summary || {
    total: 0,
    passed: 0,
    failed: 0,
    environmentIssues: 0,
    manualReview: 0,
    passRate: 0
  };
  const latestEvent = (project.activityLog || []).at(-1) || null;
  const isComplete = phaseKey === 'report' && Boolean(project.report);

  return {
    currentPhaseKey: phaseKey,
    currentPhaseLabel: stageCatalog.find((item) => item.key === phaseKey)?.label || '项目资料',
    currentStepLabel: buildStepLabel(project, phaseKey),
    currentObjectLabel: buildCurrentObject(project, phaseKey),
    roundLabel: summary.total > 0 ? '验证第 1 轮' : '初始化轮',
    nextAction: buildNextAction(project, phaseKey, blockers),
    latestEventTitle: latestEvent?.title || '等待开始',
    latestEventAt: latestEvent?.at || project.updatedAt,
    blockers,
    statusTone: blockers.length ? blockers[0].tone : (isComplete ? 'success' : 'info'),
    summary: {
      moduleCount: project.featureModules?.length || 0,
      featurePointCount: countFeaturePoints(project),
      criteriaCount: project.acceptanceCriteria?.length || 0,
      caseCount: project.testCases?.length || 0,
      passRate: summary.passRate,
      executedCount: summary.total,
      failedCount: summary.failed,
      manualReviewCount: summary.manualReview
    },
    stageStates: buildStageStates(phaseKey, isComplete)
  };
}

function applyProjectInput(project, input) {
  return {
    ...project,
    name: input.name || project.name || '未命名评测项目',
    client: input.client || '',
    vendor: input.vendor || '',
    sut: {
      ...project.sut,
      name: input.sutName || '',
      baseUrl: input.sutBaseUrl || '',
      environment: input.environment || project.sut?.environment || '测试环境',
      accountNotes: input.accountNotes || ''
    },
    scope: input.scope || '',
    documentText: input.documentText || '',
    riskPolicy: input.riskPolicy || project.riskPolicy || '高风险操作需人工确认'
  };
}

function createProject(input) {
  const now = new Date().toISOString();
  const project = applyProjectInput({
    id: `ap-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    name: '未命名评测项目',
    client: '',
    vendor: '',
    sut: {
      name: '',
      baseUrl: '',
      environment: '测试环境',
      accountNotes: ''
    },
    scope: '',
    documentText: '',
    riskPolicy: '高风险操作需人工确认',
    status: '资料待解析',
    featureModules: [],
    acceptanceCriteria: [],
    testScenarios: [],
    testCases: [],
    executions: null,
    defects: [],
    report: null,
    generatedCode: '',
    activityLog: [],
    createdAt: now,
    updatedAt: now
  }, input);

  project.activityLog = appendActivityLog(project, buildActivityEvent({
    phaseKey: 'setup',
    stepLabel: '创建项目',
    title: '项目已创建',
    detail: '可以继续补充系统资料，并进入解析与验证流程。',
    currentObject: project.sut.name || project.name,
    nextAction: '点击“解析需求”进入下一阶段'
  }));
  project.runCenter = buildRunCenter(project);
  return project;
}

function updateProject(project, input) {
  const next = applyProjectInput(project, input);
  next.activityLog = appendActivityLog(next, buildActivityEvent({
    phaseKey: resolvePhaseKey(next),
    stepLabel: '更新项目资料',
    title: '项目资料已更新',
    detail: '如果资料发生明显变化，建议重新执行当前阶段以刷新结果。',
    tone: 'info',
    currentObject: next.sut.name || next.name,
    nextAction: next.runCenter?.nextAction || '检查阶段状态后继续'
  }));
  next.runCenter = buildRunCenter(next);
  return next;
}

function hydrateProject(project) {
  const next = {
    ...project,
    activityLog: project.activityLog || []
  };
  next.runCenter = buildRunCenter(next);
  return next;
}

async function analyzeProject(project) {
  const analysis = await analyzeRequirements(project);
  const next = {
    ...project,
    ...analysis,
    status: '用例待生成'
  };
  next.activityLog = appendActivityLog(next, buildActivityEvent({
    phaseKey: 'analysis',
    stepLabel: '解析需求',
    title: '需求解析完成',
    detail: `识别 ${next.featureModules.length} 个功能模块、${next.acceptanceCriteria.length} 条验收项。`,
    tone: 'success',
    currentObject: next.featureModules[0]?.name || next.name,
    nextAction: '点击“生成用例”整理验证范围'
  }));
  next.runCenter = buildRunCenter(next);
  return next;
}

function designTestCases(project) {
  const testCases = generateTestCases(project);
  const next = {
    ...project,
    testCases,
    status: '用例待执行'
  };
  next.activityLog = appendActivityLog(next, buildActivityEvent({
    phaseKey: 'cases',
    stepLabel: '生成用例',
    title: '测试用例已生成',
    detail: `当前已整理 ${testCases.length} 条候选用例，可进入执行阶段。`,
    tone: 'success',
    currentObject: testCases[0]?.title || next.name,
    nextAction: '点击“执行评测”收集证据'
  }));
  next.runCenter = buildRunCenter(next);
  return next;
}

function executeProject(project) {
  const executions = runWebUiAssessment(project);
  const generatedCode = generateRegressionCode({ ...project, executions });
  const next = {
    ...project,
    executions,
    defects: executions.defects,
    generatedCode,
    status: '评测报告待生成'
  };
  next.activityLog = appendActivityLog(next, buildActivityEvent({
    phaseKey: 'execution',
    stepLabel: '执行评测',
    title: '执行阶段完成',
    detail: `已执行 ${executions.summary.total} 条用例，通过 ${executions.summary.passed} 条，待人工确认 ${executions.summary.manualReview} 条。`,
    tone: executions.summary.failed > 0 ? 'warning' : 'success',
    currentObject: executions.results[0]?.title || next.name,
    nextAction: executions.summary.manualReview > 0
      ? '先复核待人工确认项，再决定是否生成报告'
      : '点击“生成报告”汇总结论'
  }));
  next.runCenter = buildRunCenter(next);
  return next;
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

  const next = {
    ...project,
    report,
    status: '评测报告已生成'
  };
  next.activityLog = appendActivityLog(next, buildActivityEvent({
    phaseKey: 'report',
    stepLabel: '生成报告',
    title: '报告已生成',
    detail: summary.failed > 0
      ? `已形成报告，当前包含 ${summary.failed} 项功能缺陷待复核。`
      : '已形成可复核报告，当前执行范围内未发现明确功能缺陷。',
    tone: summary.failed > 0 ? 'warning' : 'success',
    currentObject: report.title,
    nextAction: summary.failed > 0 ? '复核缺陷并安排复测' : '复核报告并沉淀回归资产'
  }));
  next.runCenter = buildRunCenter(next);
  return next;
}

module.exports = {
  createProject,
  updateProject,
  hydrateProject,
  analyzeProject,
  designTestCases,
  executeProject,
  buildReport
};
