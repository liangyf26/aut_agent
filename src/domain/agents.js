const { createLlmProvider } = require('./llmProviders');

const defaultProvider = createLlmProvider({ type: 'heuristic', name: 'local-heuristic' });

const moduleCatalog = [
  {
    key: 'project',
    name: '评测项目管理',
    aliases: ['项目', '评测', '人员', '周期', '环境', '账号'],
    featurePoints: ['创建评测项目', '维护被测系统信息', '管理测试范围与验收周期']
  },
  {
    key: 'requirements',
    name: '文档解析与需求理解',
    aliases: ['需求', '文档', '说明书', '验收', '解析', '提取'],
    featurePoints: ['上传或录入需求资料', '提取功能点与验收项', '建立需求追踪关系']
  },
  {
    key: 'case-design',
    name: '测试计划与用例生成',
    aliases: ['测试计划', '用例', '场景', '边界', '权限'],
    featurePoints: ['生成测试场景', '生成测试用例', '支持人工审核与调整']
  },
  {
    key: 'web-execution',
    name: 'Web UI 自动化执行',
    aliases: ['Web', '浏览器', '页面', '登录', '表单', '按钮', '截图', 'trace'],
    featurePoints: ['执行浏览器自动化步骤', '记录执行证据', '识别失败原因']
  },
  {
    key: 'reporting',
    name: '结果分析与评测报告',
    aliases: ['报告', '缺陷', '整改', '风险', '覆盖率', '结论'],
    featurePoints: ['汇总测试结论', '生成缺陷清单', '输出评测报告']
  }
];

function normalizeText(text = '') {
  return text.replace(/\r\n/g, '\n').trim();
}

function splitSentences(text) {
  return normalizeText(text)
    .split(/[\n。；;]+/)
    .map((line) => line.replace(/^[-*\d.、\s]+/, '').trim())
    .filter(Boolean);
}

function scoreModule(text, item) {
  return item.aliases.reduce((score, alias) => {
    return text.toLowerCase().includes(alias.toLowerCase()) ? score + 1 : score;
  }, 0);
}

function pickModules(text) {
  const scored = moduleCatalog
    .map((item) => ({ ...item, score: scoreModule(text, item) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score);

  const modules = scored.length > 0 ? scored : moduleCatalog.slice(0, 3);
  return modules.slice(0, 5).map((item, index) => ({
    id: `fm-${index + 1}`,
    name: item.name,
    summary: `围绕${item.name}形成可验证的验收范围。`,
    featurePoints: item.featurePoints.map((name, pointIndex) => ({
      id: `fp-${index + 1}-${pointIndex + 1}`,
      name,
      businessRules: inferBusinessRules(name),
      priority: pointIndex === 0 ? 'P0' : 'P1'
    }))
  }));
}

function inferBusinessRules(featurePoint) {
  if (featurePoint.includes('执行')) {
    return ['操作步骤应可追溯', '失败时应保留证据', '高风险操作需要人工确认'];
  }
  if (featurePoint.includes('报告') || featurePoint.includes('缺陷')) {
    return ['结论应能追溯到验收项', '缺陷需包含复现路径和整改建议'];
  }
  if (featurePoint.includes('上传') || featurePoint.includes('录入')) {
    return ['输入资料应保留来源', '解析结果应支持人工确认'];
  }
  return ['数据应完整保存', '关键字段应可审核', '异常输入应给出明确提示'];
}

function extractAcceptanceCriteria(text) {
  const criteriaLines = splitSentences(text).filter((line) => {
    return /(应|必须|需要|支持|允许|禁止|验收|输出|记录|生成|可追溯|可复核)/.test(line);
  });

  const base = criteriaLines.slice(0, 8).map((line, index) => ({
    id: `ac-${index + 1}`,
    description: line.length > 80 ? `${line.slice(0, 80)}...` : line,
    source: '需求资料'
  }));

  if (base.length > 0) {
    return base;
  }

  return [
    { id: 'ac-1', description: '平台应支持创建评测项目并维护被测系统信息。', source: '默认规则' },
    { id: 'ac-2', description: '平台应支持从需求资料生成测试场景和测试用例。', source: '默认规则' },
    { id: 'ac-3', description: '平台应记录执行证据并生成评测报告。', source: '默认规则' }
  ];
}

function createScenarios(featureModules) {
  return featureModules.flatMap((module, moduleIndex) => {
    return module.featurePoints.map((point, pointIndex) => ({
      id: `ts-${moduleIndex + 1}-${pointIndex + 1}`,
      moduleId: module.id,
      featurePointId: point.id,
      title: `${point.name}验收路径`,
      actor: point.name.includes('报告') ? '项目验收负责人' : '第三方测评工程师',
      path: ['准备前置数据', `执行${point.name}`, '核对页面反馈和持久化结果', '记录证据与结论'],
      risk: point.priority === 'P0' ? '高' : '中'
    }));
  });
}

async function analyzeRequirements(project) {
  const documentText = normalizeText(project.documentText || project.scope || '');
  const analysis = await defaultProvider.generateJson('requirements-analysis', project, () => {
    const featureModules = pickModules(documentText);
    const acceptanceCriteria = extractAcceptanceCriteria(documentText);
    const testScenarios = createScenarios(featureModules);

    return {
      featureModules,
      acceptanceCriteria,
      testScenarios
    };
  });

  return {
    ...analysis.result,
    analysisMeta: {
      provider: analysis.provider,
      generatedAt: analysis.generatedAt
    }
  };
}

function generateTestCases(project) {
  const modules = project.featureModules || [];
  const criteria = project.acceptanceCriteria || [];
  const criteriaIds = criteria.map((item) => item.id);

  const cases = modules.flatMap((module, moduleIndex) => {
    return module.featurePoints.flatMap((point, pointIndex) => {
      const baseId = `${moduleIndex + 1}-${pointIndex + 1}`;
      const linkedCriteria = criteriaIds.length > 0
        ? [criteriaIds[(moduleIndex + pointIndex) % criteriaIds.length]]
        : [];

      return [
        {
          id: `tc-${baseId}-happy`,
          title: `${point.name} - 正常流程`,
          moduleId: module.id,
          featurePointId: point.id,
          priority: point.priority,
          type: '正常流程',
          preconditions: ['测试账号已具备对应角色权限', '被测系统处于可访问状态'],
          steps: [
            `进入${project.sut?.baseUrl || '被测系统'}。`,
            `导航到${module.name}相关页面。`,
            `按业务规则完成${point.name}。`,
            '保存页面截图、关键响应和操作日志。'
          ],
          expectedResults: [
            `${point.name}可以完成。`,
            '页面反馈、数据状态和权限表现符合需求资料。',
            '执行证据可以追溯到验收项。'
          ],
          acceptanceCriteriaIds: linkedCriteria,
          enabled: true
        },
        {
          id: `tc-${baseId}-exception`,
          title: `${point.name} - 异常与边界校验`,
          moduleId: module.id,
          featurePointId: point.id,
          priority: point.priority === 'P0' ? 'P1' : 'P2',
          type: '异常流程',
          preconditions: ['准备无效、缺失或越权输入数据'],
          steps: [
            `进入${module.name}相关页面。`,
            `对${point.name}提交异常数据或越权操作。`,
            '观察系统提示、拦截逻辑和数据变化。'
          ],
          expectedResults: [
            '系统给出明确提示或阻断。',
            '未产生不符合验收项的数据副作用。',
            '失败或阻断原因被记录为执行证据。'
          ],
          acceptanceCriteriaIds: linkedCriteria,
          enabled: true
        }
      ];
    });
  });

  return cases.slice(0, 16);
}

module.exports = {
  analyzeRequirements,
  generateTestCases
};
