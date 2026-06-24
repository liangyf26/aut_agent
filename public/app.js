const STAGE2_ONBOARDING_FORM_KEY = 'stage2_new_system_onboarding_form';
const STAGE2_ONBOARDING_RESULTS_KEY = 'stage2_new_system_onboarding_results';
const STAGE2_LOCAL_RUNS_KEY = 'stage2_v3_local_runs';
const WORKSPACE_VIEW = window.location.pathname.replace(/\/+$/, '') === '/stage2' ? 'stage2' : 'stage1';

const stage2OnboardingDefaults = {
  systemName: '',
  systemKeyTemplate: '',
  homeUrl: '',
  cdpUrl: 'http://localhost:9222',
  targetTemplate: '',
  pageName: '',
  scenarioKind: 'query',
  model: '',
  captureSeconds: '20',
  runDir: ''
};

const stage2OnboardingSteps = [
  {
    id: 1,
    phase: 'map',
    mode: 'executable',
    operation: 'explore_system_map',
    title: '先探索系统地图',
    detail: '生成菜单/页面入口树和页面类型初分。',
    artifacts: ['navigation_tree.json', 'page_semantic_summary.json', 'page_entries.json']
  },
  {
    id: 2,
    phase: 'map',
    mode: 'artifact',
    title: '检查系统地图产物',
    detail: '确认导航树、页面类型初分和候选功能点是否可信。',
    artifacts: ['navigation_tree.json', 'feature_points.json', 'discovery_result.json']
  },
  {
    id: 3,
    phase: 'map',
    mode: 'manual',
    title: '确认首批目标',
    detail: '优先选择查询列表页、详情展示页或导航页。',
    artifacts: ['page_semantic_summary.json', 'feature_points.json']
  },
  {
    id: 4,
    phase: 'template',
    mode: 'executable',
    operation: 'routing_summary',
    title: '查看模型路由摘要',
    detail: '确认 discovery 和 verification 会走哪种策略。',
    artifacts: ['routing_summary.json', 'discovery_strategy.json']
  },
  {
    id: 5,
    phase: 'template',
    mode: 'executable',
    operation: 'bootstrap_template',
    title: '生成最小模板骨架',
    detail: '为目标页面固定模板目录结构。',
    artifacts: ['template.json', 'locator_hints.json', 'baseline.json', 'data_schema.json']
  },
  {
    id: 6,
    phase: 'template',
    mode: 'executable',
    operation: 'live_discovery',
    title: '执行首轮自主探索',
    detail: '补页面入口、候选功能点和稳定文本线索。',
    artifacts: ['page_entries.json', 'feature_points.json', 'discovery_review_queue.json']
  },
  {
    id: 7,
    phase: 'template',
    mode: 'executable',
    operation: 'capture_human_recording',
    title: '补人工录制线索',
    detail: '用于表格、弹窗和复杂交互页面的演示路径。',
    artifacts: ['recording_summary.json', 'candidate_template_review.json', 'key_screenshots.json']
  },
  {
    id: 8,
    phase: 'template',
    mode: 'executable',
    operation: 'template_revision_checklist',
    title: '生成模板修订清单',
    detail: '把 discovery 和录制结果转成可审阅清单。',
    artifacts: ['template_revision_checklist.json', 'template_revision_checklist.md']
  },
  {
    id: 9,
    phase: 'template',
    mode: 'manual',
    title: '按清单人工改模板',
    detail: '修订 template、locator hints、baseline 和 data schema。',
    artifacts: ['template.json', 'locator_hints.json', 'baseline.json', 'data_schema.json']
  },
  {
    id: 10,
    phase: 'validation',
    mode: 'executable',
    operation: 'validate_connected_template',
    title: '执行单模板连机验证',
    detail: '验证新系统单模板、单功能点最小闭环。',
    artifacts: ['validation_result.json', 'verification_result.json', 'network_events.json']
  },
  {
    id: 11,
    phase: 'validation',
    mode: 'artifact',
    title: '检查验证产物',
    detail: '根据状态、截图和网络事件判断失败原因。',
    artifacts: ['validation_result.json', 'verification_result.json', 'screenshots/']
  },
  {
    id: 12,
    phase: 'validation',
    mode: 'manual',
    title: '失败后修订并重跑',
    detail: '一次只改一类问题，再回到单模板连机验证。',
    artifacts: ['locator_hints.json', 'template_revision_checklist.md']
  },
  {
    id: 13,
    phase: 'validation',
    mode: 'executable',
    operation: 'resume_human_takeover',
    title: '需要人工补齐时处理',
    detail: '录制演示或生成恢复续跑入口。',
    artifacts: ['human_takeover.json', 'candidate_template_review.json']
  },
  {
    id: 14,
    phase: 'validation',
    mode: 'manual',
    title: '接入统一验证汇总',
    detail: '把新模板接入 validation matrix 和对应回归测试。',
    artifacts: ['validation_matrix.py', 'test_g4_validation_matrix.py']
  },
  {
    id: 15,
    phase: 'validation',
    mode: 'executable',
    operation: 'validation_matrix',
    title: '查看统一验证汇总',
    detail: '运行验证矩阵并确认新模板出现在汇总里。',
    artifacts: ['latest_validation_matrix.json', 'latest_validation_matrix.md']
  }
];

const state = {
  projects: [],
  currentProject: null,
  activeTab: 'analysis',
  activeStage2Tab: 'overview',
  showProjectForm: false,
  pendingAction: null,
  stage2Overview: null,
  stage2Runs: [],
  stage2RunDetails: {},
  stage2LocalRuns: loadStage2LocalRuns(),
  stage2RunsApiAvailable: null,
  stage2LastError: '',
  selectedRunId: null,
  selectedSessionId: null,
  onboardingOperationSessionId: null,
  onboardingForm: loadStage2OnboardingForm(),
  onboardingStepResults: loadStage2OnboardingStepResults()
};

const AUTO_REFRESH_MS = 15000;
const fields = ['name', 'client', 'vendor', 'sutName', 'sutBaseUrl', 'accountNotes', 'scope', 'documentText'];

const projectForm = document.querySelector('#projectForm');
const projectFormShell = document.querySelector('#projectFormShell');
const projectList = document.querySelector('#projectList');
const pageTitle = document.querySelector('#pageTitle');
const pageSubtitle = document.querySelector('#pageSubtitle');
const projectStatus = document.querySelector('#projectStatus');
const saveState = document.querySelector('#saveState');
const toggleProjectFormButton = document.querySelector('#toggleProjectFormButton');
const stage2SessionList = document.querySelector('#stage2SessionList');
const stage2RunList = document.querySelector('#stage2RunList');
const stage2RunForm = document.querySelector('#stage2RunForm');

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || '请求失败');
  }
  return payload;
}

function loadStage2OnboardingForm() {
  try {
    const saved = JSON.parse(localStorage.getItem(STAGE2_ONBOARDING_FORM_KEY) || '{}');
    return { ...stage2OnboardingDefaults, ...saved };
  } catch {
    return { ...stage2OnboardingDefaults };
  }
}

function saveStage2OnboardingForm() {
  localStorage.setItem(STAGE2_ONBOARDING_FORM_KEY, JSON.stringify(state.onboardingForm));
}

function loadStage2OnboardingStepResults() {
  try {
    const saved = JSON.parse(localStorage.getItem(STAGE2_ONBOARDING_RESULTS_KEY) || '{}');
    return saved && typeof saved === 'object' ? saved : {};
  } catch {
    return {};
  }
}

function saveStage2OnboardingStepResults() {
  localStorage.setItem(STAGE2_ONBOARDING_RESULTS_KEY, JSON.stringify(state.onboardingStepResults));
}

function loadStage2LocalRuns() {
  try {
    const saved = JSON.parse(localStorage.getItem(STAGE2_LOCAL_RUNS_KEY) || '[]');
    return Array.isArray(saved) ? saved : [];
  } catch {
    return [];
  }
}

function saveStage2LocalRuns() {
  localStorage.setItem(STAGE2_LOCAL_RUNS_KEY, JSON.stringify(state.stage2LocalRuns.slice(0, 8)));
}

function updateStage2OnboardingField(name, value) {
  state.onboardingForm[name] = value;
  saveStage2OnboardingForm();
  renderStage2Overview();
}

function getStage2OnboardingParameters() {
  const form = state.onboardingForm;
  const systemKeyTemplate = normalizeSystemMapTemplateBase(form.systemKeyTemplate.trim());
  return {
    systemName: form.systemName.trim(),
    systemKeyTemplate,
    homeUrl: form.homeUrl.trim(),
    targetName: form.systemName.trim(),
    systemKey: systemKeyTemplate,
    systemMapTemplate: systemKeyTemplate ? `${systemKeyTemplate}_system_map` : '',
    startUrl: form.homeUrl.trim(),
    pageUrl: form.homeUrl.trim(),
    cdpUrl: form.cdpUrl.trim(),
    targetTemplate: form.targetTemplate.trim(),
    pageName: form.pageName.trim(),
    scenarioKind: form.scenarioKind.trim(),
    model: form.model.trim(),
    captureSeconds: Number(form.captureSeconds) || 0,
    runDir: form.runDir.trim()
  };
}

function normalizeSystemMapTemplateBase(value) {
  const text = String(value || '').trim();
  if (!text) {
    return '';
  }
  return text.replace(/(?:_system_map)+$/i, '');
}

function getStage2OperationSessionId() {
  return state.onboardingOperationSessionId;
}

function operationArtifactHref(sessionId, artifactKey) {
  if (!sessionId || !artifactKey) {
    return null;
  }
  return `/api/stage2/operation/artifacts/${encodeURIComponent(sessionId)}/${encodeURIComponent(artifactKey)}`;
}

function getStage2StepParams(step) {
  const params = getStage2OnboardingParameters();
  const templateName = step.id === 1 ? params.systemMapTemplate : params.targetTemplate;
  return {
    ...params,
    templateName,
    pageUrl: step.id === 1 ? params.startUrl : params.pageUrl,
    recordingSession: params.systemKey ? `${params.systemKey}_recording` : '',
    recordingUrl: params.pageUrl || params.startUrl,
    operatorId: 'run_center',
    maxAttempts: 3,
    maxRounds: 1
  };
}

function requiredFieldsForStage2Step(step) {
  if (step.id === 1) {
    return ['systemName', 'systemKeyTemplate', 'homeUrl', 'cdpUrl'];
  }
  if (step.id === 5) {
    return ['targetTemplate', 'homeUrl', 'pageName', 'scenarioKind'];
  }
  if (step.id === 13) {
    return ['runDir', 'cdpUrl'];
  }
  if ([6, 7, 10].includes(step.id)) {
    return ['targetTemplate', 'cdpUrl'];
  }
  if ([4, 8].includes(step.id)) {
    return ['targetTemplate'];
  }
  if (step.id === 15) {
    return ['cdpUrl'];
  }
  return [];
}

function missingFieldsForStage2Step(step) {
  const parameters = getStage2OnboardingParameters();
  return requiredFieldsForStage2Step(step).filter((field) => !parameters[field]);
}

function getFormValue() {
  const data = new FormData(projectForm);
  const payload = Object.fromEntries(fields.map((field) => [field, data.get(field)?.trim() || '']));
  if (state.currentProject?.id) {
    payload.id = state.currentProject.id;
  }
  return payload;
}

function fillForm(project) {
  projectForm.elements.name.value = project?.name || '';
  projectForm.elements.client.value = project?.client || '';
  projectForm.elements.vendor.value = project?.vendor || '';
  projectForm.elements.sutName.value = project?.sut?.name || '';
  projectForm.elements.sutBaseUrl.value = project?.sut?.baseUrl || '';
  projectForm.elements.accountNotes.value = project?.sut?.accountNotes || '';
  projectForm.elements.scope.value = project?.scope || '';
  projectForm.elements.documentText.value = project?.documentText || '';
}

async function loadDashboardData() {
  const [projectsPayload, stage2Payload, stage2RunsPayload] = await Promise.all([
    api('/api/projects'),
    api('/api/stage2/overview').catch(() => ({ overview: null })),
    api('/api/stage2/v3/runs').catch((error) => ({ error }))
  ]);

  state.projects = projectsPayload.projects;
  state.stage2Overview = stage2Payload.overview;
  state.stage2RunsApiAvailable = !stage2RunsPayload.error;
  state.stage2LastError = stage2RunsPayload.error ? stage2RunsPayload.error.message : '';
  state.stage2Runs = normalizeStage2Runs(stage2RunsPayload, state.stage2Overview);

  if (state.currentProject?.id) {
    state.currentProject = state.projects.find((item) => item.id === state.currentProject.id) || null;
  }

  if (!state.currentProject && state.projects.length > 0) {
    state.currentProject = state.projects[0];
  }

  fillForm(state.currentProject);
  syncSelectedSession();
  syncSelectedRun();
  await loadSelectedStage2RunDetail();
  render();
}

function syncSelectedSession() {
  const sessions = state.stage2Overview?.sessionSummaries || [];
  if (sessions.length === 0) {
    state.selectedSessionId = null;
    return;
  }

  if (state.selectedRunId) {
    const matched = sessions.find((item) => (item.timeline || []).some((run) => run.runId === state.selectedRunId));
    if (matched) {
      state.selectedSessionId = matched.sessionId;
      return;
    }
  }

  if (!sessions.some((item) => item.sessionId === state.selectedSessionId)) {
    state.selectedSessionId = sessions[0].sessionId;
  }
}

function syncSelectedRun() {
  const runs = getStage2Runs();
  if (runs.length === 0) {
    state.selectedRunId = null;
    return;
  }

  if (!runs.some((item) => getRunId(item) === state.selectedRunId)) {
    state.selectedRunId = getRunId(runs[0]);
  }

  syncSelectedSession();
}

async function selectProject(id) {
  const payload = await api(`/api/projects/${id}`);
  state.currentProject = payload.project;
  state.showProjectForm = false;
  fillForm(state.currentProject);
  render();
}

async function saveProject(event) {
  event.preventDefault();
  saveState.textContent = '保存中';
  try {
    const payload = await api('/api/projects', {
      method: 'POST',
      body: JSON.stringify(getFormValue())
    });
    state.currentProject = payload.project;
    fillForm(state.currentProject);
    state.showProjectForm = false;
    saveState.textContent = '已保存';
    await loadDashboardData();
  } catch (error) {
    saveState.textContent = error.message;
  }
}

async function runAction(action) {
  if (!state.currentProject) {
    state.showProjectForm = true;
    saveState.textContent = '请先保存项目';
    render();
    return;
  }

  state.pendingAction = action;
  saveState.textContent = '处理中';
  render();

  try {
    const payload = await api(`/api/projects/${state.currentProject.id}/${action}`, { method: 'POST' });
    state.currentProject = payload.project;
    saveState.textContent = '已更新';
    await loadDashboardData();
  } catch (error) {
    saveState.textContent = error.message;
  } finally {
    state.pendingAction = null;
    render();
  }
}

async function runStage2RunAction(runId, action, body, successMessage) {
  state.pendingAction = action;
  saveState.textContent = '处理中';
  render();
  try {
    const payload = await api(`/api/stage2/runs/${encodeURIComponent(runId)}/${action}`, {
      method: 'POST',
      body: JSON.stringify(body || {})
    });
    state.stage2Overview = payload.overview;
    syncSelectedRun();
    saveState.textContent = successMessage;
    render();
  } catch (error) {
    saveState.textContent = error.message;
    render();
  } finally {
    state.pendingAction = null;
    render();
  }
}

async function createStage2Run(event) {
  event.preventDefault();
  const data = new FormData(stage2RunForm);
  const payload = {
    systemName: data.get('systemName')?.trim() || '',
    system_name: data.get('systemName')?.trim() || '',
    entryUrl: data.get('entryUrl')?.trim() || '',
    entry_url: data.get('entryUrl')?.trim() || '',
    cdpUrl: data.get('cdpUrl')?.trim() || '',
    cdp_url: data.get('cdpUrl')?.trim() || '',
    accountNotes: data.get('accountNotes')?.trim() || '',
    account_notes: data.get('accountNotes')?.trim() || '',
    scope: data.get('scope')?.trim() || '',
    maxPages: Number(data.get('maxPages')) || 30,
    max_pages: Number(data.get('maxPages')) || 30,
    maxRounds: Number(data.get('maxRounds')) || 2,
    max_rounds: Number(data.get('maxRounds')) || 2
  };
  if (!payload.systemName || !payload.entryUrl) {
    saveState.textContent = '请填写系统名称和首页 URL';
    return;
  }

  state.pendingAction = 'create-stage2-run';
  saveState.textContent = '正在创建 run';
  render();

  try {
    const result = await api('/api/stage2/v3/runs', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    const run = normalizeStage2Run(result.run || result);
    state.selectedRunId = getRunId(run);
    delete state.stage2RunDetails[state.selectedRunId];
    saveState.textContent = '已创建 run';
    await loadDashboardData();
  } catch (error) {
    const localRun = normalizeStage2Run({
      ...payload,
      runId: `draft_${Date.now()}`,
      status: 'draft',
      latestMessage: `v3 创建接口暂不可用：${error.message}`,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      counts: { pages: 0, features: 0, cases: 0, executed: 0, passed: 0, failed: 0, skipped: 0, humanTasks: 0 }
    }, 'local');
    state.stage2LocalRuns.unshift(localRun);
    saveStage2LocalRuns();
    state.stage2Runs = normalizeStage2Runs({}, state.stage2Overview);
    state.selectedRunId = getRunId(localRun);
    saveState.textContent = '后端接口未就绪，已创建本地草稿';
    render();
  } finally {
    state.pendingAction = null;
    render();
  }
}

async function selectStage2Run(runId) {
  state.selectedRunId = runId;
  syncSelectedSession();
  await loadSelectedStage2RunDetail();
  renderStage2Overview();
}

async function runStage2V3Action(runId, action) {
  if (!runId || runId.startsWith('draft_')) {
    saveState.textContent = '本地草稿需要等待后端 v3 API 接入后才能执行';
    return;
  }
  const successText = {
    start: '已触发自动评测',
    pause: '已请求暂停',
    resume: '已请求继续',
    stop: '已请求停止',
    'analyze-round': '已触发 AI 复盘',
    'continue-next-round': '已请求进入下一轮',
    'generate-report': '已请求生成报告'
  }[action] || '已提交操作';
  state.pendingAction = `stage2-${action}`;
  saveState.textContent = '处理中';
  render();
  try {
    const result = await api(`/api/stage2/v3/runs/${encodeURIComponent(runId)}/${action}`, {
      method: 'POST',
      body: JSON.stringify({ operatorId: 'run_center', source: 'stage2_v3_cockpit' })
    });
    delete state.stage2RunDetails[runId];
    if (result.overview) {
      state.stage2Overview = result.overview;
    }
    saveState.textContent = successText;
    await loadDashboardData();
  } catch (error) {
    saveState.textContent = error.message;
    render();
  } finally {
    state.pendingAction = null;
    render();
  }
}

async function completeStage2HumanTask(taskId) {
  const runId = state.selectedRunId;
  if (!runId || !taskId) {
    return;
  }
  state.pendingAction = `stage2-human-${taskId}`;
  saveState.textContent = '正在提交人工处理结果';
  render();
  try {
    await api(`/api/stage2/v3/runs/${encodeURIComponent(runId)}/save-human-task`, {
      method: 'POST',
      body: JSON.stringify({
        taskId,
        operatorId: 'run_center',
        result: { status: 'completed', note: 'Completed from v3 run cockpit.' }
      })
    });
    saveState.textContent = '已记录人工处理结果';
    delete state.stage2RunDetails[runId];
    await loadDashboardData();
  } catch (error) {
    saveState.textContent = error.message;
    render();
  } finally {
    state.pendingAction = null;
    render();
  }
}

async function runStage2OperationStep(stepId) {
  const step = stage2OnboardingSteps.find((item) => item.id === Number(stepId));
  if (!step || step.mode !== 'executable') {
    return;
  }

  const missingFields = missingFieldsForStage2Step(step);
  if (missingFields.length) {
    saveState.textContent = '请先补齐向导参数';
    state.onboardingStepResults[step.id] = {
      status: 'blocked',
      message: `缺少参数：${missingFields.join(', ')}`,
      updatedAt: new Date().toISOString()
    };
    saveStage2OnboardingStepResults();
    renderStage2Overview();
    return;
  }

  const actionKey = `stage2-onboarding-${step.id}`;
  state.pendingAction = actionKey;
  state.onboardingStepResults[step.id] = {
    status: 'running',
    message: '正在提交到运行中心',
    updatedAt: new Date().toISOString()
  };
  saveStage2OnboardingStepResults();
  saveState.textContent = '处理中';
  render();

  try {
    const payload = await api('/api/stage2/operation/run-step', {
      method: 'POST',
      body: JSON.stringify({
        source: 'run_center_new_system_onboarding',
        stepId: step.operation,
        sessionId: getStage2OperationSessionId(),
        params: getStage2StepParams(step)
      })
    });
    if (payload.overview) {
      state.stage2Overview = payload.overview;
      syncSelectedRun();
    }
    const result = payload.stepResult || payload.result || {};
    const commandResult = result.result || result;
    const session = result.session || null;
    if (session?.sessionId) {
      state.onboardingOperationSessionId = session.sessionId;
    }
    state.onboardingStepResults[step.id] = {
      status: commandResult.status || 'submitted',
      message: commandResult.error || commandResult.stderrPreview || commandResult.stdoutPreview || '已提交运行中心',
      runId: commandResult.parsedStdout?.run_dir || commandResult.runId || commandResult.run_id || null,
      artifactHref: result.session?.sessionId
        ? operationArtifactHref(result.session.sessionId, `command_result_${step.operation}_json`)
        : (commandResult.artifacts?.result?.href || result.artifactHref || result.artifact_href || null),
      stepArtifacts: Array.isArray(result.stepArtifacts) ? result.stepArtifacts : [],
      artifacts: session?.artifacts || result.artifacts || [],
      updatedAt: commandResult.finishedAt || commandResult.updatedAt || commandResult.updated_at || new Date().toISOString()
    };
    saveStage2OnboardingStepResults();
    saveState.textContent = '已提交步骤';
    render();
  } catch (error) {
    state.onboardingStepResults[step.id] = {
      status: 'failed',
      message: error.message,
      updatedAt: new Date().toISOString()
    };
    saveStage2OnboardingStepResults();
    saveState.textContent = error.message;
    render();
  } finally {
    state.pendingAction = null;
    render();
  }
}

async function runStage2EnvironmentCheck() {
  const actionKey = 'stage2-onboarding-environment';
  state.pendingAction = actionKey;
  state.onboardingStepResults.check_environment = {
    status: 'running',
    message: '正在检查 Python、Stage-2 入口和本机 CDP',
    updatedAt: new Date().toISOString()
  };
  saveStage2OnboardingStepResults();
  saveState.textContent = '检查环境中';
  render();

  try {
    const payload = await api('/api/stage2/operation/check-environment', {
      method: 'POST',
      body: JSON.stringify({
        source: 'run_center_new_system_onboarding',
        sessionId: getStage2OperationSessionId(),
        params: getStage2OnboardingParameters()
      })
    });
    if (payload.overview) {
      state.stage2Overview = payload.overview;
      syncSelectedRun();
    }
    const result = payload.result || {};
    const commandResult = result.result || result;
    const session = result.session || null;
    if (session?.sessionId) {
      state.onboardingOperationSessionId = session.sessionId;
    }
    const checkSummary = commandResult.parsedStdout?.checks
      ? Object.entries(commandResult.parsedStdout.checks)
        .map(([key, value]) => `${key}:${value.ok ? 'ok' : 'fail'}`)
        .join(' · ')
      : commandResult.stderrPreview;
    state.onboardingStepResults.check_environment = {
      status: commandResult.status || 'submitted',
      message: checkSummary || '环境检查已完成',
      artifactHref: session?.sessionId ? operationArtifactHref(session.sessionId, 'command_result_check_environment_json') : null,
      updatedAt: commandResult.finishedAt || new Date().toISOString()
    };
    saveStage2OnboardingStepResults();
    saveState.textContent = '环境检查完成';
    render();
  } catch (error) {
    state.onboardingStepResults.check_environment = {
      status: 'failed',
      message: error.message,
      updatedAt: new Date().toISOString()
    };
    saveStage2OnboardingStepResults();
    saveState.textContent = error.message;
    render();
  } finally {
    state.pendingAction = null;
    render();
  }
}

function confirmStage2OnboardingStep(stepId) {
  const step = stage2OnboardingSteps.find((item) => item.id === Number(stepId));
  if (!step || step.mode === 'executable') {
    return;
  }
  state.onboardingStepResults[step.id] = {
    status: 'confirmed',
    message: step.mode === 'artifact' ? '已查看产物并确认' : '已完成人工确认',
    updatedAt: new Date().toISOString()
  };
  saveStage2OnboardingStepResults();
  saveState.textContent = '已记录确认';
  renderStage2Overview();
}

function render() {
  renderWorkspaceChrome();
  renderProjectList();
  renderProjectHeader();
  renderPipeline();
  renderStageTrack();
  renderSummary();
  renderMetrics();
  renderProjectFormVisibility();
  renderEventList();
  renderStage2Overview();
  renderStage2RunDetail();
  renderTabs();
}

function renderWorkspaceChrome() {
  document.body.dataset.workspace = WORKSPACE_VIEW;
  document.querySelectorAll('[data-workspace-link]').forEach((link) => {
    link.classList.toggle('active', link.dataset.workspaceLink === WORKSPACE_VIEW);
  });
}

function renderProjectList() {
  document.querySelector('#projectListCount').textContent = state.projects.length;
  if (state.projects.length === 0) {
    projectList.innerHTML = '<div class="empty-state">暂无评测项目</div>';
    return;
  }

  projectList.innerHTML = state.projects.map((project) => {
    const runCenter = getProjectRunCenter(project);
    return `
      <button class="project-item ${state.currentProject?.id === project.id ? 'active' : ''}" data-project-id="${project.id}" type="button">
        <strong>${escapeHtml(project.name)}</strong>
        <span>${escapeHtml(project.status)} · ${escapeHtml(runCenter.currentPhaseLabel)}</span>
        <small>${escapeHtml(runCenter.nextAction)}</small>
      </button>
    `;
  }).join('');
}

function renderProjectHeader() {
  const project = state.currentProject;
  const runCenter = getProjectRunCenter(project);
  const eyebrow = document.querySelector('.eyebrow');
  if (WORKSPACE_VIEW === 'stage2') {
    if (eyebrow) {
      eyebrow.textContent = '第二阶段 Python 执行子系统';
    }
    pageTitle.textContent = '第二阶段运行中心';
    pageSubtitle.textContent = '新系统接入、验证矩阵、人工接管和运行产物在这里独立操作。';
    projectStatus.textContent = state.stage2Overview ? '已接入' : '待读取';
    projectStatus.className = `status-pill ${state.stage2Overview ? 'success' : 'warning'}`;
  } else if (eyebrow) {
    eyebrow.textContent = '第一阶段需求驱动评测';
  }

  if (WORKSPACE_VIEW === 'stage2') {
    document.title = '第二阶段运行中心 - aut_agent';
  } else {
    document.title = 'aut_agent 软件自动化评测平台';
  }

  if (WORKSPACE_VIEW === 'stage2') {
    document.querySelector('#currentPhaseLabel').textContent = runCenter.currentPhaseLabel;
    document.querySelector('#currentStepLabel').textContent = runCenter.currentStepLabel;
    document.querySelector('#currentObjectLabel').textContent = runCenter.currentObjectLabel || '未选择项目';
    document.querySelector('#currentRoundLabel').textContent = runCenter.roundLabel;
    document.querySelector('#nextActionLabel').textContent = runCenter.nextAction;
    document.querySelector('#lastUpdatedLabel').textContent = formatDate(project?.updatedAt || runCenter.latestEventAt, true);
    return;
  }

  pageTitle.textContent = project ? project.name : '自动化测试运行中心';
  pageSubtitle.textContent = project
    ? `${project.sut?.name || '待补充系统'} · ${runCenter.currentPhaseLabel} · ${runCenter.currentStepLabel}`
    : '把当前项目、阶段进度和最近运行放在同一块台面上。';

  projectStatus.textContent = project ? project.status : '待创建';
  projectStatus.className = `status-pill ${project ? toneClass(runCenter.statusTone) : ''}`.trim();

  document.querySelector('#currentPhaseLabel').textContent = runCenter.currentPhaseLabel;
  document.querySelector('#currentStepLabel').textContent = runCenter.currentStepLabel;
  document.querySelector('#currentObjectLabel').textContent = runCenter.currentObjectLabel || '未选择项目';
  document.querySelector('#currentRoundLabel').textContent = runCenter.roundLabel;
  document.querySelector('#nextActionLabel').textContent = runCenter.nextAction;
  document.querySelector('#lastUpdatedLabel').textContent = formatDate(project?.updatedAt || runCenter.latestEventAt, true);
}

function renderPipeline() {
  const project = state.currentProject;
  const runCenter = getProjectRunCenter(project);
  const recommendedAction = recommendedActionFor(runCenter.currentPhaseKey, runCenter.blockers);
  const completed = completedActions(project);

  document.querySelectorAll('.pipeline button').forEach((button) => {
    const action = button.dataset.action;
    const locked = isActionLocked(action, project);
    button.classList.toggle('locked', locked);
    button.classList.toggle('completed', completed.includes(action));
    button.classList.toggle('recommended', action === recommendedAction && !locked);
    button.disabled = locked || Boolean(state.pendingAction);
    button.textContent = state.pendingAction === action ? '处理中...' : actionLabel(action);
  });
}

function renderStageTrack() {
  const runCenter = getProjectRunCenter(state.currentProject);
  const track = document.querySelector('#stageTrack');
  track.innerHTML = runCenter.stageStates.map((stage, index) => `
    <article class="stage-item ${stage.state}">
      <span>阶段 ${index + 1}</span>
      <strong>${escapeHtml(stage.label)}</strong>
      <small>${escapeHtml(stageStateLabel(stage.state))}</small>
    </article>
  `).join('');
}

function renderSummary() {
  const project = state.currentProject;
  const runCenter = getProjectRunCenter(project);

  document.querySelector('#recommendedAction').textContent = runCenter.nextAction;
  document.querySelector('#systemLabel').textContent = project?.sut?.name || '待补充';
  document.querySelector('#environmentLabel').textContent = project?.sut?.environment || '测试环境';
  document.querySelector('#accountState').textContent = project?.sut?.accountNotes || '待补充';
  document.querySelector('#scopeSummary').textContent = project?.scope || '尚未填写评测范围';

  const blockerContainer = document.querySelector('#projectBlockerList');
  if (!runCenter.blockers.length) {
    blockerContainer.innerHTML = '<div class="empty-state">当前无明显阻塞项，可以继续推进下一步。</div>';
    return;
  }

  blockerContainer.innerHTML = `
    <div class="blocker-list">
      ${runCenter.blockers.map((item) => `
        <article class="blocker-item ${toneClass(item.tone)}">
          <strong>${escapeHtml(item.title)}</strong>
          <p>${escapeHtml(item.detail)}</p>
        </article>
      `).join('')}
    </div>
  `;
}

function renderMetrics() {
  const runCenter = getProjectRunCenter(state.currentProject);
  document.querySelector('#moduleCount').textContent = runCenter.summary.moduleCount;
  document.querySelector('#featurePointCount').textContent = runCenter.summary.featurePointCount;
  document.querySelector('#criteriaCount').textContent = runCenter.summary.criteriaCount;
  document.querySelector('#caseCount').textContent = runCenter.summary.caseCount;
  document.querySelector('#executedCount').textContent = runCenter.summary.executedCount;
  document.querySelector('#passRate').textContent = `${runCenter.summary.passRate}%`;
}

function renderProjectFormVisibility() {
  const open = state.showProjectForm;
  projectFormShell.classList.toggle('is-collapsed', !open);
  toggleProjectFormButton.textContent = open ? '收起项目设置' : '项目设置';
}

function renderEventList() {
  const project = state.currentProject;
  const events = (project?.activityLog || []).slice().reverse();
  const container = document.querySelector('#eventList');
  if (!events.length) {
    container.innerHTML = '<div class="empty-state">保存项目后，这里会显示项目级阶段事件和最近动作。</div>';
    return;
  }

  container.innerHTML = events.map((event) => `
    <article class="event-item">
      <header>
        <strong>${escapeHtml(event.title)}</strong>
        <time>${formatDate(event.at)}</time>
      </header>
      <p>${escapeHtml(event.phaseLabel)} · ${escapeHtml(event.stepLabel || '阶段更新')}</p>
      <p>${escapeHtml(event.detail || event.nextAction || '等待后续动作')}</p>
    </article>
  `).join('');
}

function getRunId(run) {
  return run?.run_id || run?.runId || run?.id || run?.manifest?.run_id || '';
}

function getRunStatus(run) {
  return run?.status || run?.overallStatus || run?.manifest?.status || run?.current_status?.status || 'unknown';
}

function normalizeStage2Runs(payload = {}, overview = null) {
  const v3Items = payload.runs || payload.items || payload.data || payload.overview?.runs || [];
  const normalized = Array.isArray(v3Items) ? v3Items.map((run) => normalizeStage2Run(run, 'v3')) : [];
  const overviewRuns = (overview?.runSummaries || []).map((run) => normalizeStage2Run(run, 'overview'));
  const localRuns = state.stage2LocalRuns.map((run) => normalizeStage2Run(run, 'local'));
  const deduped = new Map();

  [...localRuns, ...normalized, ...overviewRuns].forEach((run) => {
    const id = getRunId(run);
    if (!id || deduped.has(id)) {
      return;
    }
    deduped.set(id, run);
  });

  return Array.from(deduped.values()).sort((left, right) => {
    const leftTime = new Date(left.updatedAt || left.createdAt || left.started_at || 0).getTime();
    const rightTime = new Date(right.updatedAt || right.createdAt || right.started_at || 0).getTime();
    return rightTime - leftTime;
  });
}

function normalizeStage2Run(run = {}, source = 'v3') {
  const manifest = run.run_manifest || run.manifest || {};
  const stats = run.stats || run.summary || {};
  const runId = getRunId(run) || `stage2_${Date.now()}`;
  return {
    ...run,
    runId,
    source,
    systemName: run.systemName || run.system_name || manifest.system_name || run.templateName || '未命名系统',
    entryUrl: run.entryUrl || run.entry_url || manifest.entry_url || run.homeUrl || '',
    status: getRunStatus(run),
    currentPhase: run.currentPhase || run.current_phase || run.currentStatus?.phase || run.current_status?.phase || run.status || '',
    currentPhaseLabel: run.currentPhaseLabel || run.current_phase_label || stageLabel(run.currentPhase || run.current_phase || run.status || ''),
    currentStepLabel: run.currentStepLabel || run.current_step_label || run.current_status?.current_step || '',
    currentTargetLabel: run.currentTargetLabel || run.current_target_label || run.current_status?.current_target || '',
    nextAction: run.nextAction || run.next_action || run.current_status?.next_action || '',
    latestMessage: run.latestMessage || run.message || run.current_status?.message || '',
    createdAt: run.createdAt || run.created_at || manifest.created_at || run.started_at || '',
    updatedAt: run.updatedAt || run.updated_at || manifest.updated_at || run.finished_at || run.started_at || '',
    counts: {
      pages: Number(stats.pageEntries ?? stats.pages ?? run.pageCount ?? run.page_count ?? toArrayItems(run.pageEntries || run.page_entries).length ?? 0),
      features: Number(stats.featurePoints ?? stats.features ?? run.featureCount ?? run.feature_count ?? toArrayItems(run.featurePoints || run.feature_points).length ?? 0),
      cases: Number(stats.testCases ?? stats.cases ?? run.caseCount ?? run.case_count ?? toArrayItems(run.generatedTestCases || run.generated_test_cases).length ?? 0),
      executed: Number(stats.executed ?? stats.executedCount ?? stats.executionCount ?? run.executedCount ?? 0),
      passed: Number(stats.passed ?? stats.passedCount ?? stats.verificationSuccesses ?? run.passedCount ?? 0),
      failed: Number(stats.failed ?? stats.failedCount ?? run.failedCount ?? 0),
      skipped: Number(stats.skipped ?? stats.skippedCount ?? run.skippedCount ?? 0),
      humanTasks: Number(stats.humanTasks ?? stats.pendingHumanTasks ?? run.pendingHumanTaskCount ?? 0)
    }
  };
}

function getStage2Runs() {
  return state.stage2Runs || [];
}

function getSelectedStage2Run() {
  const detail = state.stage2RunDetails[state.selectedRunId];
  const run = getStage2Runs().find((item) => getRunId(item) === state.selectedRunId);
  if (!detail) {
    return run;
  }
  const detailRun = detail.run || detail;
  const linkArtifacts = detailRun.artifacts || run?.artifacts || {};
  const payloadArtifacts = detail.artifacts || {};
  return normalizeStage2Run({
    ...run,
    ...detailRun,
    artifacts: { ...linkArtifacts, ...payloadArtifacts },
    artifactLinks: linkArtifacts
  }, run?.source || 'detail');
}

async function loadSelectedStage2RunDetail() {
  if (!state.selectedRunId || state.stage2RunDetails[state.selectedRunId]) {
    return;
  }
  if (state.selectedRunId.startsWith('draft_')) {
    return;
  }
  try {
    const payload = await api(`/api/stage2/v3/runs/${encodeURIComponent(state.selectedRunId)}`);
    state.stage2RunDetails[state.selectedRunId] = payload;
  } catch {
    state.stage2RunDetails[state.selectedRunId] = null;
  }
}

function toArrayItems(value) {
  if (Array.isArray(value)) {
    return value;
  }
  if (Array.isArray(value?.items)) {
    return value.items;
  }
  if (Array.isArray(value?.results)) {
    return value.results;
  }
  return [];
}

function getRunArtifact(run, ...keys) {
  for (const key of keys) {
    if (run?.[key]) {
      return run[key];
    }
    if (run?.artifacts?.[key]) {
      return run.artifacts[key];
    }
  }
  return null;
}

function getRunPages(run) {
  return toArrayItems(getRunArtifact(run, 'pageEntries', 'page_entries', 'pages'));
}

function getRunFeatures(run) {
  return toArrayItems(getRunArtifact(run, 'featurePoints', 'feature_points', 'features'));
}

function getRunCases(run) {
  return toArrayItems(getRunArtifact(run, 'generatedTestCases', 'generated_test_cases', 'testCases', 'cases'));
}

function getRunExecutions(run) {
  return toArrayItems(getRunArtifact(run, 'executionResults', 'execution_results', 'executions', 'results'));
}

function getRunHumanTasks(run) {
  const direct = toArrayItems(getRunArtifact(run, 'humanTasks', 'human_tasks'));
  if (direct.length) {
    return direct;
  }
  const pending = run?.actionCenter?.controlLoop?.pendingHumanActions || run?.actionCenter?.pendingHumanActions || [];
  return pending.map((item, index) => ({
    task_id: item.actionId || `pending_${index + 1}`,
    task_type: item.type || item.stage || 'review_next_round_plan',
    title: item.title || item.reason || '待人工处理',
    reason: item.reason || item.expectedOutcome || '',
    status: item.status || 'pending'
  }));
}

function getRunRoundAnalysis(run) {
  return getRunArtifact(run, 'roundAnalysis', 'round_analysis') || run?.aiReview || run?.analysis || {};
}

function getRunNextRoundPlan(run) {
  return getRunArtifact(run, 'nextRoundPlan', 'next_round_plan') || run?.nextRound || run?.next_round || {};
}

function getRunReportLinks(run) {
  const artifacts = getRunArtifacts(run);
  return artifacts.filter((item) => /report/i.test(item.key || item.label || item.fileName || ''));
}

function getRunArtifacts(run) {
  const artifacts = [];
  const pushArtifact = (item, fallbackKey = '') => {
    if (!item) {
      return;
    }
    if (typeof item === 'string') {
      artifacts.push({ key: fallbackKey || item, label: fallbackKey || item, fileName: item, href: item });
      return;
    }
    artifacts.push({
      key: item.key || fallbackKey || item.label || item.fileName || item.path || item.href,
      label: item.label || fallbackKey || item.fileName || item.path || item.href || 'artifact',
      fileName: item.fileName || item.path || item.href || '',
      description: item.description || item.kind || '',
      href: item.href || item.url || item.path || '#'
    });
  };

  if (Array.isArray(run?.artifacts)) {
    run.artifacts.forEach((item) => pushArtifact(item));
  }
  Object.entries(run?.artifactLinks || {}).forEach(([key, value]) => pushArtifact(value, key));
  if (!run?.artifactLinks && run?.artifacts && !Array.isArray(run.artifacts)) {
    Object.entries(run.artifacts)
      .filter(([, value]) => value?.href)
      .forEach(([key, value]) => pushArtifact(value, key));
  }
  Object.entries(run?.artifact_paths || run?.artifactPaths || {}).forEach(([key, value]) => pushArtifact(value, key));
  (run?.actionCenter?.artifactGroups || []).forEach((group) => {
    (group.items || []).forEach((item) => pushArtifact(item, group.label));
  });
  return artifacts;
}

function stage2TabId(tab) {
  return `stage2${tab[0].toUpperCase()}${tab.slice(1)}Tab`;
}

function renderStage2Overview() {
  document.querySelectorAll('[data-stage2-tab]').forEach((button) => {
    button.classList.toggle('active', button.dataset.stage2Tab === state.activeStage2Tab);
  });
  document.querySelectorAll('.stage2-tab-body').forEach((body) => {
    body.classList.remove('active');
  });
  document.querySelector(`#${stage2TabId(state.activeStage2Tab)}`)?.classList.add('active');

  renderStage2V3Shell();
  renderStage2V3OverviewTab();
  renderStage2V3CollectionTab('pages');
  renderStage2V3CollectionTab('features');
  renderStage2V3CollectionTab('cases');
  renderStage2V3CollectionTab('execution');
  renderStage2V3AiTab();
  renderStage2V3HumanTab();
  renderStage2V3ReportTab();
  renderStage2V3ArtifactsTab();
}

function renderStage2V3Shell() {
  const runs = getStage2Runs();
  const run = getSelectedStage2Run();
  const summaryNode = document.querySelector('#stage2Summary');
  const runTitle = document.querySelector('#stage2RunTitle');
  const runSubtitle = document.querySelector('#stage2RunSubtitle');
  const runEyebrow = document.querySelector('#stage2RunEyebrow');
  const actions = document.querySelector('#stage2RunActions');
  const metrics = document.querySelector('#stage2MetricCards');
  const monitor = document.querySelector('#stage2MonitorStrip');
  const timeline = document.querySelector('#stage2Timeline');

  if (summaryNode) {
    const apiState = state.stage2RunsApiAvailable === false ? 'v3 API 未就绪，当前显示兼容数据和本地草稿。' : 'v3 API 已连接，按 run 汇总第二阶段产物。';
    summaryNode.textContent = runs.length ? `${runs.length} 个 run 可查询。${apiState}` : `暂无 run。${apiState}`;
  }

  if (!run) {
    runEyebrow.textContent = '等待创建';
    runTitle.textContent = '从一个 run 开始';
    runSubtitle.textContent = '填写左侧最少信息后创建 run，运行中心会承接发现、执行、AI 复盘、人工处理和报告查看。';
    actions.innerHTML = '<span class="status-pill warning">尚未选择</span>';
    metrics.innerHTML = renderStage2MetricCards(null);
    monitor.innerHTML = renderStage2Monitor(null);
    timeline.innerHTML = renderStage2Timeline(null);
  } else {
    runEyebrow.textContent = `${run.source === 'local' ? '本地草稿' : '当前 run'} · ${escapeHtml(getRunId(run))}`;
    runTitle.textContent = run.systemName || getRunId(run);
    runSubtitle.textContent = [run.entryUrl, run.currentPhaseLabel, run.latestMessage].filter(Boolean).join(' · ') || '等待运行中心写入状态。';
    actions.innerHTML = renderStage2RunActions(run);
    metrics.innerHTML = renderStage2MetricCards(run);
    monitor.innerHTML = renderStage2Monitor(run);
    timeline.innerHTML = renderStage2Timeline(run);
  }

  if (!stage2RunList) {
    return;
  }
  if (!runs.length) {
    stage2RunList.innerHTML = `
      <div class="stage2-empty">
        <strong>还没有 run</strong>
        <p>创建 run 后，页面入口、功能点、执行结果和报告都会归档到同一个运行对象下。</p>
      </div>
    `;
    return;
  }

  stage2RunList.innerHTML = runs.map((item) => {
    const id = getRunId(item);
    return `
      <button class="stage2-run-card ${id === state.selectedRunId ? 'active' : ''}" data-run-id="${escapeHtml(id)}" type="button">
        <span class="tag ${verdictClass(getRunStatus(item))}">${escapeHtml(statusLabel(getRunStatus(item)))}</span>
        <strong>${escapeHtml(item.systemName || id)}</strong>
        <small>${escapeHtml(id)}</small>
        <p>${escapeHtml(item.latestMessage || item.currentPhaseLabel || item.entryUrl || '暂无运行摘要')}</p>
        <div class="stage2-run-card-stats">
          <span>${escapeHtml(String(item.counts?.pages || 0))} 入口</span>
          <span>${escapeHtml(String(item.counts?.features || 0))} 功能点</span>
          <span>${escapeHtml(String(item.counts?.executed || 0))} 已执行</span>
        </div>
      </button>
    `;
  }).join('');
}

function renderStage2RunActions(run) {
  const id = getRunId(run);
  const disabled = state.pendingAction || run.source === 'local' ? 'disabled' : '';
  const localNote = run.source === 'local'
    ? '<span class="status-pill warning">等待后端接入</span>'
    : '';
  return `
    ${localNote}
    <button class="ghost-action compact-action" data-stage2-run-action="start" data-run-id="${escapeHtml(id)}" type="button" ${disabled}>开始自动评测</button>
    <button class="ghost-action compact-action" data-stage2-run-action="pause" data-run-id="${escapeHtml(id)}" type="button" ${disabled}>暂停</button>
    <button class="ghost-action compact-action" data-stage2-run-action="resume" data-run-id="${escapeHtml(id)}" type="button" ${disabled}>继续</button>
    <button class="ghost-action compact-action" data-stage2-run-action="analyze-round" data-run-id="${escapeHtml(id)}" type="button" ${disabled}>AI 复盘</button>
    <button class="ghost-action compact-action" data-stage2-run-action="continue-next-round" data-run-id="${escapeHtml(id)}" type="button" ${disabled}>进入下一轮</button>
    <button class="ghost-action compact-action" data-stage2-run-action="generate-report" data-run-id="${escapeHtml(id)}" type="button" ${disabled}>生成报告</button>
    <button class="ghost-action compact-action danger-action" data-stage2-run-action="stop" data-run-id="${escapeHtml(id)}" type="button" ${disabled}>停止</button>
  `;
}

function renderStage2MetricCards(run) {
  const counts = run?.counts || {};
  const executionTotal = Number(counts.executed || 0);
  const passRate = executionTotal ? `${Math.round((Number(counts.passed || 0) / executionTotal) * 100)}%` : '-';
  const items = [
    ['页面入口', counts.pages || 0, '自动发现范围'],
    ['功能点', counts.features || 0, '可测交互目标'],
    ['执行用例', counts.cases || 0, '按类型生成'],
    ['已执行', executionTotal || 0, `${counts.failed || 0} 失败 / ${counts.skipped || 0} 跳过`],
    ['通过率', passRate, '基础路径状态'],
    ['人工任务', counts.humanTasks || getRunHumanTasks(run || {}).length || 0, '通过界面处理']
  ];
  return items.map(([label, value, note]) => `
    <article class="stage2-meter">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(String(value))}</strong>
      <small>${escapeHtml(note)}</small>
    </article>
  `).join('');
}

function renderStage2Monitor(run) {
  const nextPlan = getRunNextRoundPlan(run || {});
  const fields = [
    ['当前阶段', run?.currentPhaseLabel || stageLabel(run?.currentPhase || '-')],
    ['当前步骤', run?.currentStepLabel || '-'],
    ['当前对象', run?.currentTargetLabel || '-'],
    ['下一步动作', run?.nextAction || nextPlan.next_round_goal || nextPlan.nextRoundGoal || '-'],
    ['阻塞原因', run?.blockedReason || run?.waitingReason || nextPlan.decision || '-']
  ];
  return fields.map(([label, value]) => `
    <article class="stage2-monitor-item">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(String(value || '-'))}</strong>
    </article>
  `).join('');
}

function renderStage2Timeline(run) {
  const phases = getStage2Timeline(run);
  if (!phases.length) {
    return `
      <div class="stage2-empty">
        <strong>等待第一条运行事件</strong>
        <p>v3 要求持续落盘进度事件。后端接入后，这里会显示 preflight、discovery、执行、AI 复盘和报告阶段。</p>
      </div>
    `;
  }
  return `
    <div class="stage2-timeline-line">
      ${phases.map((phase) => `
        <article class="stage2-timeline-step ${phase.current ? 'current' : ''} ${phase.status || ''}">
          <span>${escapeHtml(phase.label)}</span>
          <strong>${escapeHtml(statusLabel(phase.status || 'pending'))}</strong>
          <small>${escapeHtml(phase.message || phase.time || '')}</small>
        </article>
      `).join('')}
    </div>
  `;
}

function getStage2Timeline(run) {
  if (!run) {
    return [];
  }
  const explicit = run.phaseTimeline || run.phase_timeline || run.timeline || [];
  if (explicit.length) {
    return explicit.map((item) => ({
      label: item.label || stageLabel(item.phase || item.key || ''),
      status: item.status || 'pending',
      message: item.message || item.nextAction || item.next_action || '',
      time: item.updatedAt || item.updated_at || item.timestamp || '',
      current: item.key === run.currentPhase || item.phase === run.currentPhase
    }));
  }
  const phaseOrder = [
    ['preflight', '预检'],
    ['discovery', '自动发现'],
    ['feature_analysis', '功能点识别'],
    ['case_generation', '用例生成'],
    ['execution', '安全执行'],
    ['ai_analysis', 'AI 复盘'],
    ['reporting', '报告']
  ];
  const current = run.currentPhase || run.status || '';
  return phaseOrder.map(([key, label]) => ({
    label,
    status: key === current ? 'running' : (run.status === 'completed' ? 'completed' : 'pending'),
    message: key === current ? run.latestMessage || run.nextAction || '' : '',
    current: key === current
  }));
}

function renderStage2V3OverviewTab() {
  const container = document.querySelector('#stage2OverviewTab');
  const run = getSelectedStage2Run();
  if (!container) {
    return;
  }
  if (!run) {
    container.innerHTML = renderStage2Empty('运行中心等待 run', '左侧创建 run 后，运行中心会把发现、执行、AI 复盘、人工介入和报告入口串成一条主流程。');
    return;
  }
  const analysis = getRunRoundAnalysis(run);
  const nextPlan = getRunNextRoundPlan(run);
  container.innerHTML = `
    <section class="stage2-overview-grid">
      <article class="stage2-work-card">
        <header>
          <strong>自动化闭环</strong>
          <span class="tag ${verdictClass(getRunStatus(run))}">${escapeHtml(statusLabel(getRunStatus(run)))}</span>
        </header>
        <p>${escapeHtml(run.latestMessage || '等待 run 状态写入。')}</p>
        <div class="detail-list">
          ${[
            ['Run ID', getRunId(run)],
            ['系统', run.systemName],
            ['入口', run.entryUrl || '-'],
            ['创建时间', formatDate(run.createdAt)],
            ['更新时间', formatDate(run.updatedAt)]
          ].map(([label, value]) => `
            <div class="detail-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value || '-'))}</strong></div>
          `).join('')}
        </div>
      </article>
      <article class="stage2-work-card">
        <header>
          <strong>AI 复盘摘要</strong>
          <span class="tag">${escapeHtml(String(analysis.confidence ?? analysis.ai_confidence ?? '-'))}</span>
        </header>
        <p>${escapeHtml(analysis.summary || analysis.coverage_summary?.summary || analysis.failure_summary?.summary || '暂无 AI 复盘产物。')}</p>
        <div class="tag-row">
          <span class="tag">${escapeHtml(String((analysis.human_tasks || analysis.humanTasks || []).length || getRunHumanTasks(run).length))} 个人工项</span>
          <span class="tag">${escapeHtml(String((analysis.improvement_candidates || analysis.improvementCandidates || []).length || 0))} 个改进候选</span>
        </div>
      </article>
      <article class="stage2-work-card">
        <header>
          <strong>下一轮计划</strong>
          <span class="tag ${nextPlan.requires_human_approval || nextPlan.requiresHumanApproval ? 'manual' : 'passed'}">${escapeHtml(nextPlan.decision || statusLabel(nextPlan.status || '-'))}</span>
        </header>
        <p>${escapeHtml(nextPlan.next_round_goal || nextPlan.nextRoundGoal || nextPlan.reason || '暂无下一轮计划。')}</p>
        <div class="tag-row">
          <span class="tag">${escapeHtml(nextPlan.should_continue || nextPlan.shouldContinue ? '建议继续' : '未建议继续')}</span>
          <span class="tag">${escapeHtml(nextPlan.risk_level || nextPlan.riskLevel || '风险未知')}</span>
        </div>
      </article>
    </section>
  `;
}

function renderStage2V3CollectionTab(kind) {
  const container = document.querySelector(`#${stage2TabId(kind)}`);
  const run = getSelectedStage2Run();
  if (!container) {
    return;
  }
  const config = {
    pages: {
      title: '页面入口',
      empty: '尚未发现页面入口。启动 discovery 后会显示导航树、可达状态和截图证据。',
      rows: getRunPages(run || {}),
      columns: [
        ['名称', (item) => item.name || item.title || item.page_name || item.pageEntryId || item.page_entry_id],
        ['类型', (item) => item.page_type || item.pageType || item.type || '-'],
        ['状态', (item) => statusLabel(item.status || 'unknown')],
        ['URL', (item) => item.url || item.entry_url || '-']
      ]
    },
    features: {
      title: '功能点',
      empty: '尚未识别功能点。功能点应来自默认可见和轻量交互后显式出现的交互目标。',
      rows: getRunFeatures(run || {}),
      columns: [
        ['名称', (item) => item.name || item.title || item.feature_point_id || item.featurePointId],
        ['类型', (item) => item.feature_type || item.featureType || item.type || '-'],
        ['风险', (item) => item.risk_level || item.riskLevel || '-'],
        ['置信度', (item) => item.confidence ?? '-'],
        ['审核', (item) => statusLabel(item.review_status || item.reviewStatus || '-')]
      ]
    },
    cases: {
      title: '执行型测试用例',
      empty: '尚未生成执行型测试用例。v3 用例应从功能点类型生成，不要求用户填写模板文件。',
      rows: getRunCases(run || {}),
      columns: [
        ['标题', (item) => item.title || item.name || item.test_case_id || item.testCaseId],
        ['模板', (item) => item.type_template || item.typeTemplate || item.kind || '-'],
        ['风险策略', (item) => item.risk_policy || item.riskPolicy || '-'],
        ['需人工确认', (item) => item.requires_human_confirmation || item.requiresHumanConfirmation ? '是' : '否']
      ]
    },
    execution: {
      title: '执行结果',
      empty: '尚未执行安全用例。执行结果应包含动作日志、页面反馈、截图引用和未执行原因。',
      rows: getRunExecutions(run || {}),
      columns: [
        ['用例', (item) => item.title || item.test_case_id || item.testCaseId],
        ['状态', (item) => statusLabel(item.status || 'unknown')],
        ['判定', (item) => item.verdict || '-'],
        ['失败原因', (item) => item.failure_reason || item.failureReason || '-'],
        ['人工确认', (item) => item.manual_confirmation_required || item.manualConfirmationRequired ? '需要' : '否']
      ]
    }
  }[kind];
  container.innerHTML = renderStage2Table(config.title, config.rows, config.columns, config.empty);
}

function renderStage2V3AiTab() {
  const container = document.querySelector('#stage2AiTab');
  const run = getSelectedStage2Run();
  if (!container) {
    return;
  }
  if (!run) {
    container.innerHTML = renderStage2Empty('等待 AI 复盘', '每轮自动执行结束后，AI 应分析失败、证据质量和下一轮策略。');
    return;
  }
  const analysis = getRunRoundAnalysis(run);
  const nextPlan = getRunNextRoundPlan(run);
  container.innerHTML = `
    <section class="stage2-ai-grid">
      <article class="stage2-work-card">
        <header><strong>本轮分析</strong><span class="tag">${escapeHtml(String(analysis.round_id || analysis.roundId || '-'))}</span></header>
        ${renderStage2KeyValueList([
          ['覆盖摘要', analysis.coverage_summary?.summary || analysis.coverageSummary?.summary || analysis.summary || '-'],
          ['失败摘要', analysis.failure_summary?.summary || analysis.failureSummary?.summary || '-'],
          ['证据质量', analysis.evidence_quality?.summary || analysis.evidenceQuality?.summary || '-'],
          ['AI 置信度', analysis.confidence ?? analysis.ai_confidence ?? '-']
        ])}
      </article>
      <article class="stage2-work-card">
        <header><strong>下一轮计划</strong><span class="tag">${escapeHtml(nextPlan.decision || '-')}</span></header>
        ${renderStage2KeyValueList([
          ['是否继续', nextPlan.should_continue || nextPlan.shouldContinue ? '是' : '否'],
          ['下一轮目标', nextPlan.next_round_goal || nextPlan.nextRoundGoal || '-'],
          ['风险等级', nextPlan.risk_level || nextPlan.riskLevel || '-'],
          ['需要人工批准', nextPlan.requires_human_approval || nextPlan.requiresHumanApproval ? '是' : '否']
        ])}
      </article>
      <article class="stage2-work-card stage2-work-card-wide">
        <header><strong>改进与沉淀候选</strong><span class="tag">${escapeHtml(String((analysis.improvement_candidates || analysis.improvementCandidates || []).length || 0))}</span></header>
        ${renderStage2CompactList(analysis.improvement_candidates || analysis.improvementCandidates || [], '暂无改进候选。')}
      </article>
    </section>
  `;
}

function renderStage2V3HumanTab() {
  const container = document.querySelector('#stage2HumanTab');
  const run = getSelectedStage2Run();
  if (!container) {
    return;
  }
  const tasks = getRunHumanTasks(run || {});
  if (!tasks.length) {
    container.innerHTML = renderStage2Empty('当前没有待人工处理任务', '当系统需要选择优先页面、审核功能点、录制路径、更正测试数据或批准下一轮时，任务会出现在这里。');
    return;
  }
  container.innerHTML = `
    <section class="stage2-human-grid">
      ${tasks.map((task) => `
        <article class="stage2-task-card">
          <header>
            <strong>${escapeHtml(task.title || task.task_id || '待人工处理')}</strong>
            <span class="tag ${task.status === 'pending' ? 'manual' : 'passed'}">${escapeHtml(statusLabel(task.status || 'pending'))}</span>
          </header>
          <p>${escapeHtml(task.reason || task.task_type || '请在界面中完成处理，系统会生成结构化结果。')}</p>
          <div class="tag-row">
            <span class="tag">${escapeHtml(task.task_type || task.type || 'human_task')}</span>
            <span class="tag">${escapeHtml(task.task_id || task.actionId || '-')}</span>
          </div>
          <div class="inline-actions">
            <button class="ghost-action compact-action" data-stage2-human-task="${escapeHtml(task.task_id || task.actionId || '')}" type="button" ${state.pendingAction ? 'disabled' : ''}>标记完成</button>
          </div>
        </article>
      `).join('')}
    </section>
  `;
}

function renderStage2V3ReportTab() {
  const container = document.querySelector('#stage2ReportTab');
  const run = getSelectedStage2Run();
  if (!container) {
    return;
  }
  const links = getRunReportLinks(run || {});
  const report = run?.report || run?.runReport || getRunArtifact(run || {}, 'run_report', 'runReport') || {};
  container.innerHTML = `
    <section class="stage2-report-layout">
      <article class="stage2-work-card">
        <header><strong>总体测试报告</strong><span class="tag">${escapeHtml(links.length ? '可打开' : '待生成')}</span></header>
        <p>${escapeHtml(report.summary || report.overview || '报告应直接说明页面入口、功能点、执行结果、失败原因、人工确认项和证据索引。')}</p>
        <div class="inline-actions">
          <button class="ghost-action compact-action" data-stage2-run-action="generate-report" data-run-id="${escapeHtml(getRunId(run || {}))}" type="button" ${!run || state.pendingAction ? 'disabled' : ''}>生成报告</button>
        </div>
      </article>
      <article class="stage2-work-card stage2-work-card-wide">
        <header><strong>报告链接</strong><span class="tag">${escapeHtml(String(links.length))}</span></header>
        ${renderStage2ArtifactLinks(links, '暂无报告 artifact。')}
      </article>
    </section>
  `;
}

function renderStage2V3ArtifactsTab() {
  const container = document.querySelector('#stage2ArtifactsTab');
  const run = getSelectedStage2Run();
  if (!container) {
    return;
  }
  const artifacts = getRunArtifacts(run || {});
  container.innerHTML = `
    <section class="stage2-work-card">
      <header><strong>稳定产物入口</strong><span class="tag">${escapeHtml(String(artifacts.length))}</span></header>
      ${renderStage2ArtifactLinks(artifacts, '暂无 artifact 链接。后端应按白名单 key 暴露 run_manifest、page_entries、feature_points、execution_results、round_analysis、next_round_plan 和 report。')}
    </section>
  `;
}

function renderStage2Table(title, rows, columns, emptyText) {
  if (!rows.length) {
    return renderStage2Empty(title, emptyText);
  }
  return `
    <section class="stage2-table-card">
      <div class="stage2-section-head">
        <h3>${escapeHtml(title)}</h3>
        <span class="tag">${escapeHtml(String(rows.length))}</span>
      </div>
      <div class="stage2-table-wrap">
        <table class="stage2-data-table">
          <thead><tr>${columns.map(([label]) => `<th>${escapeHtml(label)}</th>`).join('')}</tr></thead>
          <tbody>
            ${rows.map((row) => `
              <tr>${columns.map(([, getter]) => `<td>${escapeHtml(String(getter(row) ?? '-'))}</td>`).join('')}</tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function renderStage2KeyValueList(items) {
  return `
    <div class="detail-list">
      ${items.map(([label, value]) => `
        <div class="detail-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value || '-'))}</strong></div>
      `).join('')}
    </div>
  `;
}

function renderStage2CompactList(items, emptyText) {
  if (!items.length) {
    return `<div class="stage2-empty"><p>${escapeHtml(emptyText)}</p></div>`;
  }
  return `
    <div class="stage2-compact-list">
      ${items.slice(0, 8).map((item) => `
        <article>
          <strong>${escapeHtml(item.title || item.name || item.candidate_id || item.id || '候选项')}</strong>
          <p>${escapeHtml(item.reason || item.summary || item.description || '')}</p>
        </article>
      `).join('')}
    </div>
  `;
}

function renderStage2ArtifactLinks(artifacts, emptyText) {
  if (!artifacts.length) {
    return renderStage2Empty('Artifacts', emptyText);
  }
  return `
    <div class="stage2-artifact-grid">
      ${artifacts.map((artifact) => `
        <a class="artifact-action" href="${escapeHtml(artifact.href || '#')}" target="_blank" rel="noreferrer">
          <span>${escapeHtml(artifact.label || artifact.key || 'artifact')}</span>
          <small>${escapeHtml(artifact.description || artifact.fileName || '')}</small>
          <strong>${escapeHtml(artifact.key || artifact.fileName || '')}</strong>
        </a>
      `).join('')}
    </div>
  `;
}

function renderStage2Empty(title, message) {
  return `
    <div class="stage2-empty">
      <strong>${escapeHtml(title)}</strong>
      <p>${escapeHtml(message)}</p>
    </div>
  `;
}

function renderStage2SummaryTab() {
  const overview = state.stage2Overview;
  const summaryNode = document.querySelector('#stage2Summary');
  const cards = document.querySelector('#stage2OverviewCards');

  if (!overview) {
    summaryNode.textContent = '第二阶段运行摘要暂不可用。';
    cards.innerHTML = '<div class="empty-state">尚未读取到第二阶段运行产物。</div>';
    stage2SessionList.innerHTML = '<div class="empty-state">暂无编排会话。</div>';
    stage2RunList.innerHTML = '<div class="empty-state">暂无 run 记录。</div>';
    return;
  }

  const validation = overview.latestValidationMatrix;
  const daily = overview.latestDailyReport;
  const freeze = overview.latestBaselineFreezeManifest;
  const humanLoop = overview.humanLoopSummary;

  summaryNode.textContent = formatStage2OverviewSummary(overview);

  const facts = [
    {
      label: '最近 Runs',
      value: overview.summary.runCount,
      note: daily ? `${daily.successfulRuns} 成功 / ${daily.failedRuns} 失败` : '按最近产物目录汇总'
    },
    {
      label: '待人工处理',
      value: overview.summary.waitingHumanCount,
      note: `${overview.summary.scheduledNextRoundCount} 个待续跑`
    },
    {
      label: '验证矩阵',
      value: validation ? `${validation.passedCount}/${validation.targetCount}` : '-',
      note: validation ? `${validation.executedCount} 已执行` : '尚未生成'
    },
    {
      label: '最近录制',
      value: humanLoop ? humanLoop.actionEventCount : 0,
      note: humanLoop
        ? `${humanLoop.keyScreenshotCount} 张关键截图${humanLoop.draftStepCount ? ` · ${humanLoop.draftStepCount} 步草稿` : ''}`
        : '暂无录制摘要'
    }
  ];

  const extraCards = [];

  if (humanLoop) {
    const mappingNote = humanLoop.candidateFieldMappingCount
      ? `${humanLoop.candidateFieldMappingCount} 个候选字段映射`
      : '尚未形成字段映射候选';
    const draftNote = [
      humanLoop.templateName ? `模板：${humanLoop.templateName}` : '',
      humanLoop.operatorId ? `操作人：${humanLoop.operatorId}` : '',
      humanLoop.taskDescription || ''
    ].filter(Boolean).join(' · ');
    extraCards.push(`
      <article class="overview-fact overview-fact-wide">
        <span>人工录制候选</span>
        <strong>${escapeHtml(humanLoop.draftVersion ? `${humanLoop.draftVersion} 草稿` : `${humanLoop.actionEventCount} 个动作事件`)}</strong>
        <p class="inline-note">${escapeHtml(draftNote || mappingNote)}</p>
        <div class="tag-row">
          <span class="tag">${escapeHtml(String(humanLoop.pageUrlCount || 0))} 个页面</span>
          <span class="tag">${escapeHtml(String(humanLoop.candidateLocatorCount || 0))} 个 locator</span>
          <span class="tag">${escapeHtml(String(humanLoop.candidateDataFieldCount || 0))} 个候选字段</span>
          ${humanLoop.mappedProjectFieldCount ? `<span class="tag passed">${escapeHtml(String(humanLoop.mappedProjectFieldCount))} 个已映射</span>` : ''}
          ${humanLoop.needsReviewCount ? `<span class="tag manual">${escapeHtml(String(humanLoop.needsReviewCount))} 个待确认</span>` : ''}
        </div>
        ${humanLoop.reviewFieldKeys?.length ? `<p class="inline-note">字段样本：${escapeHtml(humanLoop.reviewFieldKeys.join('，'))}</p>` : ''}
        ${humanLoop.warnings?.length ? `<p class="inline-note">${escapeHtml(humanLoop.warnings[0])}</p>` : ''}
        ${renderInlineArtifactLinks(humanLoop.artifacts, '暂无录制审阅产物。')}
      </article>
    `);
  }

  if (freeze) {
    const recommendedRun = freeze.recommendedPrimaryRun;
    const freezeHeadline = recommendedRun?.model || (freeze.freezeRecommended ? '建议冻结' : '暂不冻结');
    const freezeNote = recommendedRun
      ? `${recommendedRun.runId || '-'} · ${statusLabel(recommendedRun.status || '-')}${recommendedRun.elapsedMs ? ` · ${formatDuration(recommendedRun.elapsedMs)}` : ''}`
      : (freeze.selectionReason || '暂无推荐主运行');
    extraCards.push(`
      <article class="overview-fact overview-fact-wide">
        <span>当前冻结基线</span>
        <strong>${escapeHtml(freezeHeadline)}</strong>
        <p class="inline-note">${escapeHtml(freezeNote)}</p>
        <div class="tag-row">
          <span class="tag ${freeze.freezeRecommended ? 'passed' : 'manual'}">${escapeHtml(freeze.freezeRecommended ? '建议冻结' : '待审阅')}</span>
          <span class="tag">${escapeHtml(String(freeze.runCount || 0))} 次 run</span>
          <span class="tag">${escapeHtml(String(freeze.successfulRunCount || 0))} 次成功</span>
        </div>
        ${freeze.selectionReason ? `<p class="inline-note">${escapeHtml(freeze.selectionReason)}</p>` : ''}
        ${renderInlineArtifactLinks(recommendedRun?.artifacts || [], '暂无冻结基线产物。')}
      </article>
    `);
  }

  cards.innerHTML = [
    ...facts.map((fact) => `
    <article class="overview-fact">
      <span>${escapeHtml(fact.label)}</span>
      <strong>${escapeHtml(String(fact.value))}</strong>
      <p class="inline-note">${escapeHtml(fact.note)}</p>
    </article>
  `),
    ...extraCards
  ].join('');

  const sessions = overview.sessionSummaries || [];
  if (!sessions.length) {
    stage2SessionList.innerHTML = '<div class="empty-state">暂无编排会话摘要。</div>';
  } else {
    const selectedSession = sessions.find((item) => item.sessionId === state.selectedSessionId) || sessions[0];
    const sessionFacts = selectedSession ? [
      ['会话 ID', selectedSession.sessionId],
      ['模板', selectedSession.templateName || '-'],
      ['模型', selectedSession.modelName || '-'],
      ['Run 数', String(selectedSession.runCount || 0)],
      ['最新 Run', selectedSession.latestRunId || '-'],
      ['待人工处理', selectedSession.waitingHuman ? '是' : '否'],
      ['未解决 Run', selectedSession.unresolvedHumanRunId || '-']
    ] : [];
    const sessionTimelineMarkup = selectedSession?.timeline?.length ? `
      <div class="session-timeline-list">
        ${selectedSession.timeline.map((run) => `
          <button class="session-run-detail ${state.selectedRunId === run.runId ? 'active' : ''}" data-run-id="${run.runId}" type="button">
            <header>
              <strong>${escapeHtml(run.runId)}</strong>
              <span class="tag ${verdictClass(run.overallStatus || '')}">${escapeHtml(statusLabel(run.overallStatus || 'unknown'))}</span>
            </header>
            <p>${escapeHtml(roundLabel(run.orchestrationRound))} · ${escapeHtml(run.currentPhaseLabel || '-')}</p>
            <p>${escapeHtml(run.latestMessage || run.waitingReason || '暂无说明')}</p>
          </button>
        `).join('')}
      </div>
    ` : '<div class="empty-state">当前会话还没有 run timeline。</div>';
    stage2SessionList.innerHTML = `
      <div class="session-header">
        <h3>编排会话</h3>
        <span class="tag">${escapeHtml(String(sessions.length))}</span>
      </div>
      <div class="session-card-list">
        ${sessions.map((session) => `
          <article class="session-item ${state.selectedSessionId === session.sessionId ? 'active' : ''}">
            <header>
              <strong>${escapeHtml(session.templateName || session.sessionId)}</strong>
              <time>${formatDate(session.updatedAt)}</time>
            </header>
            <p>${escapeHtml(session.modelName || '未知模型')} · ${escapeHtml(session.projectName || '第二阶段原型')}</p>
            <div class="tag-row">
              <span class="tag">${escapeHtml(String(session.runCount || 0))} 次 run</span>
              <span class="tag ${session.waitingHuman ? 'manual' : ''}">${escapeHtml(session.waitingHuman ? '待人工处理' : statusLabel(session.latestRunStatus || 'unknown'))}</span>
              ${session.latestNextRoundStatus ? `<span class="tag">${escapeHtml(statusLabel(session.latestNextRoundStatus))}</span>` : ''}
              ${session.stats?.promotionCandidateTotal ? `<span class="tag">${escapeHtml(String(session.stats.promotionCandidateTotal))} 个候选沉淀</span>` : ''}
            </div>
            <p>${escapeHtml(session.latestMessage || '暂无会话摘要')}</p>
            <div class="inline-actions">
              <button class="ghost-action compact-action" data-session-id="${escapeHtml(session.sessionId)}" type="button">查看会话详情</button>
              ${session.latestResumeCommand ? `<button class="ghost-action compact-action" data-copy-command="${escapeHtml(session.latestResumeCommand)}" type="button">复制会话恢复命令</button>` : ''}
            </div>
            ${(session.timeline || []).length ? `
              <div class="session-run-strip">
                ${(session.timeline || []).slice(0, 4).map((run) => `
                  <button class="session-run-chip ${state.selectedRunId === run.runId ? 'active' : ''}" data-run-id="${run.runId}" type="button">
                    <span>${escapeHtml(roundLabel(run.orchestrationRound))}</span>
                    <strong>${escapeHtml(statusLabel(run.overallStatus || 'unknown'))}</strong>
                  </button>
                `).join('')}
              </div>
            ` : ''}
          </article>
        `).join('')}
      </div>
      ${selectedSession ? `
        <article class="session-detail-card">
          <header>
            <strong>会话详情</strong>
            <div class="tag-row">
              <span class="tag">${escapeHtml(selectedSession.templateName || selectedSession.sessionId)}</span>
              <span class="tag">${escapeHtml(statusLabel(selectedSession.latestRunStatus || 'unknown'))}</span>
              ${selectedSession.latestNextRoundStatus ? `<span class="tag">${escapeHtml(statusLabel(selectedSession.latestNextRoundStatus))}</span>` : ''}
            </div>
          </header>
          <div class="detail-list">
            ${sessionFacts.map(([label, value]) => `
              <div class="detail-item">
                <span>${escapeHtml(label)}</span>
                <strong>${escapeHtml(String(value || '-'))}</strong>
              </div>
            `).join('')}
          </div>
          ${selectedSession.latestResumeCommand ? `
            <div class="command-card">
              <span>会话级恢复命令</span>
              <code>${escapeHtml(selectedSession.latestResumeCommand)}</code>
              <div class="inline-actions">
                <button class="ghost-action compact-action" data-copy-command="${escapeHtml(selectedSession.latestResumeCommand)}" type="button">复制恢复命令</button>
              </div>
            </div>
          ` : ''}
          <section class="session-detail-section">
            <h4>会话时间线</h4>
            ${sessionTimelineMarkup}
          </section>
        </article>
      ` : ''}
    `;
  }

  if (!overview.runSummaries.length) {
    stage2RunList.innerHTML = '<div class="empty-state">暂无第二阶段 run 记录。</div>';
    return;
  }

  stage2RunList.innerHTML = overview.runSummaries.map((run) => `
    <button class="run-item ${state.selectedRunId === run.runId ? 'active' : ''}" data-run-id="${run.runId}" type="button">
      <header>
        <strong>${escapeHtml(run.templateName)}</strong>
        <time>${formatDate(run.updatedAt)}</time>
      </header>
      <p>${escapeHtml(run.modelName)} · ${escapeHtml(run.currentPhaseLabel)}${run.currentRoundLabel ? ` · ${escapeHtml(run.currentRoundLabel)}` : ''}</p>
      <p>${escapeHtml(run.latestMessage)}</p>
      <div class="tag-row">
        <span class="tag ${verdictClass(run.overallStatus)}">${escapeHtml(statusLabel(run.overallStatus))}</span>
        <span class="tag">${run.stats.pageEntries} 入口</span>
        <span class="tag">${run.stats.featurePoints} 功能点</span>
        <span class="tag">${run.stats.verificationSuccesses} 成功</span>
        ${run.stats.promotionCandidates ? `<span class="tag">${escapeHtml(String(run.stats.promotionCandidates))} 个候选沉淀</span>` : ''}
        ${run.nextRound.status ? `<span class="tag">${escapeHtml(statusLabel(run.nextRound.status))}</span>` : ''}
      </div>
    </button>
  `).join('');
}

function renderStage2OnboardingTab() {
  const container = document.querySelector('#stage2OnboardingTab');
  const form = state.onboardingForm;
  const environmentResult = state.onboardingStepResults.check_environment;
  const operationSessionId = getStage2OperationSessionId() || state.stage2Overview?.operationCenter?.currentSession?.sessionId;
  const checkingEnvironment = state.pendingAction === 'stage2-onboarding-environment';
  const phaseCounts = stage2OnboardingSteps.reduce((counts, step) => {
    const result = state.onboardingStepResults[step.id];
    if (result?.status === 'confirmed' || result?.status === 'submitted' || result?.status === 'passed' || result?.status === 'completed') {
      counts[step.phase] += 1;
    }
    return counts;
  }, { map: 0, template: 0, validation: 0 });
  const phaseMeta = [
    ['map', '系统地图', '先确认入口结构和页面类型', 3],
    ['template', '模板收敛', '把低风险页面收敛成可复用模板', 6],
    ['validation', '验证与汇总', '连机验证后接入统一汇总', 6]
  ];

  container.innerHTML = `
    <section class="onboarding-layout">
      <form class="onboarding-form" id="stage2OnboardingForm">
        <div class="onboarding-form-head">
          <div>
            <p class="section-kicker">新系统接入</p>
            <h3>三段式向导</h3>
            <p class="panel-note">先看系统地图，再收敛模板，最后做连机验证和统一汇总。</p>
          </div>
          <div class="inline-actions">
            <button class="ghost-action compact-action" data-onboarding-check-env type="button" ${checkingEnvironment ? 'disabled' : ''}>
              ${checkingEnvironment ? '检查中...' : '检查环境'}
            </button>
            <button class="ghost-action compact-action" data-onboarding-reset type="button">清空状态</button>
          </div>
        </div>
        <div class="onboarding-field-grid">
          ${renderOnboardingField('systemName', '系统名称', form.systemName, '公交业务系统')}
          ${renderOnboardingField('systemKeyTemplate', 'system key/template', form.systemKeyTemplate, 'bus')}
          ${renderOnboardingField('homeUrl', '首页 URL', form.homeUrl, 'https://example.com/home', 'url')}
          ${renderOnboardingField('cdpUrl', 'CDP URL', form.cdpUrl, 'http://localhost:9222', 'url')}
          ${renderOnboardingField('targetTemplate', '目标模板', form.targetTemplate, 'bus_station_query_reset')}
          ${renderOnboardingField('pageName', '页面名', form.pageName, '班线查询页')}
          <label>
            scenario kind
            <select name="scenarioKind">
              ${['query', 'detail', 'navigation', 'create', 'edit', 'generic'].map((value) => `
                <option value="${value}" ${form.scenarioKind === value ? 'selected' : ''}>${value}</option>
              `).join('')}
            </select>
          </label>
          ${renderOnboardingField('model', 'model', form.model, '可留空使用默认 profile')}
          ${renderOnboardingField('captureSeconds', 'capture seconds', form.captureSeconds, '20', 'number')}
          ${renderOnboardingField('runDir', '接管 run dir（可选）', form.runDir, 'artifacts/stage2/runs/<run_dir>')}
        </div>
        <div class="onboarding-session-strip">
          <span>Operation Session</span>
          <strong>${escapeHtml(operationSessionId || '尚未创建')}</strong>
          ${environmentResult ? `<em class="${verdictClass(environmentResult.status)}">${escapeHtml(statusLabel(environmentResult.status))}</em>` : ''}
          ${environmentResult?.artifactHref ? `<a class="inline-link compact-link" href="${escapeHtml(environmentResult.artifactHref)}" target="_blank" rel="noreferrer">环境检查结果</a>` : ''}
        </div>
      </form>

      <section class="onboarding-phase-strip" aria-label="接入阶段">
        ${phaseMeta.map(([key, title, note, total]) => `
          <article class="onboarding-phase ${key}">
            <span>${escapeHtml(title)}</span>
            <strong>${escapeHtml(String(phaseCounts[key]))}/${escapeHtml(String(total))}</strong>
            <p>${escapeHtml(note)}</p>
          </article>
        `).join('')}
      </section>

      <section class="onboarding-steps">
        ${phaseMeta.map(([key, title]) => `
          <div class="onboarding-step-group">
            <h3>${escapeHtml(title)}</h3>
            <div class="onboarding-step-list">
              ${stage2OnboardingSteps.filter((step) => step.phase === key).map(renderOnboardingStepCard).join('')}
            </div>
          </div>
        `).join('')}
      </section>
    </section>
  `;
}

function renderOnboardingField(name, label, value, placeholder, type = 'text') {
  return `
    <label>
      ${escapeHtml(label)}
      <input name="${escapeHtml(name)}" type="${escapeHtml(type)}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}">
    </label>
  `;
}

function renderOnboardingStepCard(step) {
  const result = state.onboardingStepResults[step.id] || {};
  const status = result.status || (step.mode === 'executable' ? 'ready' : 'manual');
  const missingFields = step.mode === 'executable' ? missingFieldsForStage2Step(step) : [];
  const running = state.pendingAction === `stage2-onboarding-${step.id}`;
  const buttonLabel = step.mode === 'executable'
    ? (running ? '执行中...' : '执行步骤')
    : (step.mode === 'artifact' ? '标记已查看' : '标记已确认');
  const modeLabel = {
    executable: '可执行',
    manual: '人工确认',
    artifact: '产物查看'
  }[step.mode];
  const artifactLinks = resolveOnboardingStepArtifactLinks(step, result);

  return `
    <article class="onboarding-step-card ${step.mode} ${status}">
      <header>
        <span class="step-number">${escapeHtml(String(step.id).padStart(2, '0'))}</span>
        <div>
          <strong>${escapeHtml(step.title)}</strong>
          <p>${escapeHtml(step.detail)}</p>
        </div>
      </header>
      <div class="tag-row">
        <span class="tag ${step.mode === 'executable' ? 'passed' : 'manual'}">${escapeHtml(modeLabel)}</span>
        <span class="tag ${verdictClass(status)}">${escapeHtml(statusLabel(status))}</span>
        ${missingFields.length ? `<span class="tag warning">缺少 ${escapeHtml(String(missingFields.length))} 项</span>` : ''}
      </div>
      ${renderArtifactChips(step.artifacts)}
      ${result.message ? `<p class="inline-note">${escapeHtml(result.message)}</p>` : ''}
      ${result.runId ? `<p class="inline-note">Run：${escapeHtml(result.runId)}</p>` : ''}
      ${renderInlineArtifactLinks(artifactLinks, '')}
      <div class="inline-actions">
        <button
          class="ghost-action compact-action"
          data-onboarding-step="${escapeHtml(String(step.id))}"
          data-onboarding-action="${step.mode === 'executable' ? 'run' : 'confirm'}"
          type="button"
          ${(running || (step.mode === 'executable' && missingFields.length)) ? 'disabled' : ''}
        >${escapeHtml(buttonLabel)}</button>
        ${result.artifactHref ? `<a class="inline-link compact-link" href="${escapeHtml(result.artifactHref)}" target="_blank" rel="noreferrer">步骤结果</a>` : ''}
      </div>
    </article>
  `;
}

function resolveOnboardingStepArtifactLinks(step, result = {}) {
  if (Array.isArray(result.stepArtifacts) && result.stepArtifacts.length) {
    const directLinks = result.stepArtifacts
      .filter((item) => item && item.href && item.label)
      .map((item) => ({
        label: item.label,
        href: item.href,
        fileName: item.label
      }));
    if (result.artifactHref) {
      directLinks.push({
        label: 'command_result.json',
        href: result.artifactHref,
        fileName: '步骤命令结果'
      });
    }
    return directLinks;
  }

  const sessionId = getStage2OperationSessionId() || state.stage2Overview?.operationCenter?.currentSession?.sessionId;
  if (!sessionId) {
    return [];
  }

  const artifactKeyMap = {
    1: [
      ['navigation_tree.json', 'systemMapTemplate_navigation_tree.json'],
      ['page_semantic_summary.json', 'systemMapTemplate_page_semantic_summary.json'],
      ['page_entries.json', 'systemMapTemplate_page_entries.json']
    ],
    6: [
      ['page_entries.json', 'targetTemplate_page_entries.json'],
      ['feature_points.json', 'targetTemplate_feature_points.json'],
      ['discovery_review_queue.json', 'targetTemplate_discovery_review_queue.json']
    ],
    8: [
      ['template_revision_checklist.json', 'checklist_output_dir_template_revision_checklist.json'],
      ['template_revision_checklist.md', 'checklist_output_dir_template_revision_checklist.md']
    ],
    10: [
      ['validation_result.json', 'run_dir_validation_result.json'],
      ['verification_result.json', 'run_dir_verification_result.json'],
      ['network_events.json', 'run_dir_network_events.json']
    ],
    15: [
      ['latest_validation_matrix.json', 'latest_validation_matrix.json'],
      ['latest_validation_matrix.md', 'latest_validation_matrix.md']
    ]
  };

  const candidates = artifactKeyMap[step.id] || [];
  const links = candidates
    .map(([label, artifactKey]) => {
      const href = operationArtifactHref(sessionId, artifactKey);
      if (!href) {
        return null;
      }
      return {
        label,
        href,
        fileName: label
      };
    })
    .filter(Boolean);

  if (result.artifactHref) {
    links.push({
      label: 'command_result.json',
      href: result.artifactHref,
      fileName: '步骤命令结果'
    });
  }

  return links;
}

function renderArtifactChips(artifacts = []) {
  if (!artifacts.length) {
    return '';
  }
  return `
    <div class="artifact-chip-row">
      ${artifacts.map((artifact) => `<span>${escapeHtml(artifact)}</span>`).join('')}
    </div>
  `;
}

function renderStage2MatrixTab() {
  const container = document.querySelector('#stage2MatrixTab');
  const matrix = state.stage2Overview?.latestValidationMatrix;
  if (!matrix) {
    container.innerHTML = `
      <div class="stage2-guidance-empty">
        <strong>验证矩阵尚未生成</strong>
        <p>新系统模板稳定后，在“新系统接入”的第 15 步运行统一验证汇总。</p>
      </div>
    `;
    return;
  }

  const facts = [
    ['目标数', matrix.targetCount],
    ['已执行', matrix.executedCount],
    ['通过', matrix.passedCount],
    ['失败', matrix.failedCount],
    ['跳过', matrix.skippedCount]
  ];
  container.innerHTML = `
    <section class="stage2-tab-section">
      <div class="stage2-overview compact-overview">
        ${facts.map(([label, value]) => `
          <article class="overview-fact">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(String(value ?? '-'))}</strong>
          </article>
        `).join('')}
      </div>
      ${renderInlineArtifactLinks(matrix.artifacts || [], '暂无验证矩阵产物链接。')}
    </section>
  `;
}

function renderStage2HumanTab() {
  const container = document.querySelector('#stage2HumanTab');
  const overview = state.stage2Overview;
  const waitingSessions = (overview?.sessionSummaries || []).filter((session) => session.waitingHuman);
  const waitingRuns = (overview?.runSummaries || []).filter((run) => run.humanTakeover?.status && run.humanTakeover.status !== 'none');
  if (!overview || (!waitingSessions.length && !waitingRuns.length)) {
    container.innerHTML = `
      <div class="stage2-guidance-empty">
        <strong>当前没有待人工接管项</strong>
        <p>如果新系统需要登录、切页面或补前置数据，可在向导第 13 步生成处理入口。</p>
      </div>
    `;
    return;
  }

  container.innerHTML = `
    <section class="human-tab-grid">
      ${waitingRuns.map((run) => `
        <article class="human-task-row">
          <header>
            <strong>${escapeHtml(run.templateName || run.runId)}</strong>
            <span class="tag manual">${escapeHtml(statusLabel(run.humanTakeover.status || 'needs_review'))}</span>
          </header>
          <p>${escapeHtml(run.waitingReason || run.humanTakeover.waitingReason || run.latestMessage || '等待人工处理')}</p>
          <div class="inline-actions">
            <button class="ghost-action compact-action" data-run-id="${escapeHtml(run.runId)}" type="button">查看 Run</button>
            ${run.actionCenter?.resumeCommand ? `<button class="ghost-action compact-action" data-copy-command="${escapeHtml(run.actionCenter.resumeCommand)}" type="button">复制恢复命令</button>` : ''}
          </div>
        </article>
      `).join('')}
    </section>
  `;
}

function renderStage2RunDetail() {
  const container = document.querySelector('#stage2RunDetail');
  const empty = document.querySelector('#stage2RunEmpty');
  const run = (state.stage2Overview?.runSummaries || []).find((item) => item.runId === state.selectedRunId);

  if (!run) {
    empty.style.display = 'block';
    container.style.display = 'none';
    container.innerHTML = '';
    return;
  }

  empty.style.display = 'none';
  container.style.display = 'grid';

  const phaseTimeline = run.phaseTimeline.length ? run.phaseTimeline.map((phase) => `
    <article class="timeline-item ${phase.status === 'completed' ? 'completed' : ''} ${phase.status === 'failed' ? 'failed' : ''} ${phase.key === run.currentPhase ? 'current' : ''}">
      <header>
        <strong>${escapeHtml(phase.label)}</strong>
        <time>${formatDate(phase.updatedAt)}</time>
      </header>
      <p>${escapeHtml(phase.message || phase.nextAction || '暂无阶段说明')}</p>
      <div class="tag-row">
        <span class="tag ${verdictClass(phase.status)}">${escapeHtml(statusLabel(phase.status))}</span>
        ${phase.currentRoundLabel ? `<span class="tag">${escapeHtml(phase.currentRoundLabel)}</span>` : ''}
        ${phase.lastStepLabel ? `<span class="tag">${escapeHtml(phase.lastStepLabel)}</span>` : ''}
      </div>
    </article>
  `).join('') : '<div class="empty-state">暂无阶段时间线。</div>';

  const recentEvents = run.recentEvents.length ? run.recentEvents.slice().reverse().map((event) => `
    <article class="event-item">
      <header>
        <strong>${escapeHtml(event.step_label || event.phase || '事件')}</strong>
        <time>${formatDate(event.timestamp)}</time>
      </header>
      <p>${escapeHtml(event.message || '')}</p>
      <div class="tag-row">
        <span class="tag ${verdictClass(event.status)}">${escapeHtml(statusLabel(event.status))}</span>
        ${event.target_label ? `<span class="tag">${escapeHtml(event.target_label)}</span>` : ''}
      </div>
    </article>
  `).join('') : '<div class="empty-state">暂无近期事件。</div>';

  const detailFacts = [
    ['运行 ID', run.runId],
    ['模型', run.modelName],
    ['当前阶段', run.currentPhaseLabel],
    ['当前步骤', run.currentStepLabel || '-'],
    ['当前对象', run.currentTargetLabel || run.currentObjectLabel || '-'],
    ['下一步', run.nextAction || '-'],
    ['下一轮决策', statusLabel(run.nextRound.status || '-')],
    ['人工接管', statusLabel(run.humanTakeover.status || 'none')]
  ];
  const actionCenter = run.actionCenter || {
    artifactGroups: [],
    resumeCommand: null,
    pendingActionCount: 0,
    scheduledActionCount: 0,
    controlLoop: null
  };
  const promotionReview = run.promotionReview || {
    summary: null,
    candidateCount: 0,
    topCandidateTitles: [],
    approvalNotes: [],
    evidenceRequirements: []
  };
  const controlLoop = actionCenter.controlLoop || {
    nextRound: {},
    stopConditions: {},
    retryPlan: {},
    scheduledActions: [],
    pendingHumanActions: [],
    humanTakeover: { status: 'none', notes: [] }
  };
  const promotionCandidatesArtifact = findActionArtifact(actionCenter, 'promotion_candidates_json');
  const baselineSnapshotArtifact = findActionArtifact(actionCenter, 'baseline_snapshot_json');
  const runtimeDataArtifact = findActionArtifact(actionCenter, 'runtime_data_json');
  const actionGroups = actionCenter.artifactGroups.length ? actionCenter.artifactGroups.map((group) => `
    <article class="action-group">
      <header>
        <strong>${escapeHtml(group.label)}</strong>
        <span class="tag">${escapeHtml(String(group.items.length))}</span>
      </header>
      <div class="artifact-grid">
        ${group.items.map((item) => `
          <a class="artifact-action" href="${escapeHtml(item.href)}" target="_blank" rel="noreferrer">
            <span>${escapeHtml(item.label)}</span>
            <small>${escapeHtml(item.description || artifactKindLabel(item.kind))}</small>
            <strong>${escapeHtml(item.fileName)}</strong>
          </a>
        `).join('')}
      </div>
    </article>
  `).join('') : '<div class="empty-state">当前 run 暂无可直接打开的 artifacts。</div>';
  const nextRoundFacts = [
    ['调度状态', statusLabel(controlLoop.nextRound.status || '-')],
    ['自动续跑', autoContinueLabel(controlLoop.nextRound.shouldStart)],
    ['目标阶段', stageLabel(controlLoop.nextRound.targetStage || '-')],
    ['下一轮', roundLabel(controlLoop.nextRound.nextRound)],
    ['停止判断', statusLabel(controlLoop.stopConditions.status || '-')]
  ];
  const nextRoundNotes = [
    controlLoop.nextRound.reason ? `原因：${localizeReason(controlLoop.nextRound.reason)}` : '',
    ...(controlLoop.nextRound.notes || []).map((note) => localizeReason(note)),
    ...(controlLoop.stopConditions.reason ? [`停止原因：${localizeReason(controlLoop.stopConditions.reason)}`] : []),
    ...(controlLoop.stopConditions.notes || []).map((note) => localizeReason(note))
  ].filter(Boolean);
  const humanFacts = [
    ['状态', statusLabel(controlLoop.humanTakeover.status || 'none')],
    ['目标阶段', stageLabel(controlLoop.humanTakeover.targetStage || '-')],
    ['待处理动作', String(actionCenter.pendingActionCount || 0)],
    ['恢复命令', actionCenter.resumeCommand ? '已生成' : '未生成'],
    ['人工处理记录', statusLabel(run.humanTakeover.resolutionStatus || 'none')]
  ];
  const humanNotes = [
    controlLoop.humanTakeover.waitingReason ? `等待原因：${localizeReason(controlLoop.humanTakeover.waitingReason)}` : '',
    ...((controlLoop.humanTakeover.notes || []).map((note) => localizeReason(note)))
  ].filter(Boolean);
  const scheduledActionsMarkup = renderControlActionList(
    controlLoop.scheduledActions || [],
    '当前没有待续跑动作。'
  );
  const pendingHumanActionsMarkup = renderControlActionList(
    controlLoop.pendingHumanActions || [],
    controlLoop.humanTakeover.status === 'needs_review'
      ? '当前 run 需要人工复核，待人工处理动作尚未单独列出。'
      : '当前没有待人工处理动作。'
  );
  const promotionFacts = [
    ['候选数量', String(promotionReview.candidateCount || run.stats.promotionCandidates || 0)],
    ['审阅状态', statusLabel(promotionReview.reviewStatus || '-')],
    ['可冻结基线', String(promotionReview.baselineFreezeCandidateCount || 0)],
    ['待补跟进', String(promotionReview.deferredCandidateCount || 0)],
    ['证据要求', promotionReview.evidenceRequirements?.length ? `${promotionReview.evidenceRequirements.length} 条` : '-']
  ];
  const promotionNotes = [
    promotionReview.summary || '',
    ...(promotionReview.approvalNotes || []),
    ...(promotionReview.evidenceRequirements || []),
    ...Object.entries(promotionReview.reviewStatusBreakdown || {}).map(([status, count]) => `${statusLabel(status)}：${count}`),
    ...Object.entries(promotionReview.promotionRecommendationBreakdown || {}).map(([name, count]) => `${name}：${count}`)
  ].filter(Boolean);
  const promotionArtifacts = [
    promotionCandidatesArtifact,
    baselineSnapshotArtifact,
    runtimeDataArtifact
  ].filter(Boolean);

  container.innerHTML = `
    <section class="run-detail-section">
      <h3>运行摘要</h3>
      <div class="detail-list">
        ${detailFacts.map(([label, value]) => `
          <div class="detail-item">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(String(value || '-'))}</strong>
          </div>
        `).join('')}
      </div>
      <div class="tag-row">
        <span class="tag ${verdictClass(run.overallStatus)}">${escapeHtml(statusLabel(run.overallStatus))}</span>
        <span class="tag">${formatDuration(run.elapsedMs)}</span>
        <span class="tag">${run.stats.pageEntries} 入口</span>
        <span class="tag">${run.stats.featurePoints} 功能点</span>
        <span class="tag">${run.stats.promotionCandidates} 候选沉淀</span>
        ${actionCenter.scheduledActionCount ? `<span class="tag warning">${escapeHtml(String(actionCenter.scheduledActionCount))} 个待续跑动作</span>` : ''}
        ${actionCenter.pendingActionCount ? `<span class="tag manual">${escapeHtml(String(actionCenter.pendingActionCount))} 个待处理动作</span>` : ''}
      </div>
      ${run.blockedReason ? `<p class="panel-note">阻塞/判停说明：${escapeHtml(localizeReason(run.blockedReason))}</p>` : ''}
      ${run.waitingReason ? `<p class="panel-note">等待原因：${escapeHtml(localizeReason(run.waitingReason))}</p>` : ''}
    </section>

    <section class="run-detail-section">
      <h3>控制闭环</h3>
      <div class="control-grid">
        <article class="control-block">
          <header>
            <strong>下一轮调度</strong>
            <span class="tag ${verdictClass(controlLoop.nextRound.status || '')}">${escapeHtml(statusLabel(controlLoop.nextRound.status || '-'))}</span>
          </header>
          <div class="control-fact-list">
            ${renderControlFacts(nextRoundFacts)}
          </div>
          ${controlLoop.retryPlan.goal ? `<p class="panel-note">目标：${escapeHtml(controlLoop.retryPlan.goal)}</p>` : ''}
          ${renderControlNotes(nextRoundNotes, '暂无额外调度说明。')}
        </article>

        <article class="control-block">
          <header>
            <strong>人工接管</strong>
            <span class="tag ${verdictClass(controlLoop.humanTakeover.status || '')}">${escapeHtml(statusLabel(controlLoop.humanTakeover.status || 'none'))}</span>
          </header>
          <div class="control-fact-list">
            ${renderControlFacts(humanFacts)}
          </div>
          ${renderControlNotes(humanNotes, '当前没有额外人工接管说明。')}
        </article>

        <article class="control-block control-block-wide">
          <header>
            <strong>待续跑动作</strong>
            <div class="tag-row">
              ${controlLoop.retryPlan.status ? `<span class="tag">${escapeHtml(statusLabel(controlLoop.retryPlan.status))}</span>` : ''}
              <span class="tag">${escapeHtml(String(actionCenter.scheduledActionCount || 0))}</span>
            </div>
          </header>
          ${controlLoop.retryPlan.notes?.length ? renderControlNotes(controlLoop.retryPlan.notes.map((note) => localizeReason(note)), '') : ''}
          ${scheduledActionsMarkup}
        </article>

        ${(actionCenter.pendingActionCount || controlLoop.humanTakeover.status !== 'none') ? `
          <article class="control-block control-block-wide">
            <header>
              <strong>待人工处理动作</strong>
              <span class="tag manual">${escapeHtml(String(actionCenter.pendingActionCount || 0))}</span>
            </header>
            ${pendingHumanActionsMarkup}
          </article>
        ` : ''}
      </div>
    </section>

    <section class="run-detail-section">
      <h3>沉淀候选</h3>
      <div class="control-grid">
        <article class="control-block">
          <header>
            <strong>候选摘要</strong>
            <span class="tag">${escapeHtml(String(promotionReview.candidateCount || run.stats.promotionCandidates || 0))}</span>
          </header>
          <div class="control-fact-list">
            ${renderControlFacts(promotionFacts)}
          </div>
          ${renderControlNotes(promotionNotes.map((note) => localizeReason(note)), '当前没有额外沉淀说明。')}
        </article>

        <article class="control-block control-block-wide">
          <header>
            <strong>候选条目与审阅入口</strong>
            <div class="tag-row">
              ${promotionReview.readyCandidateCount ? `<span class="tag passed">${escapeHtml(String(promotionReview.readyCandidateCount))} 个可审阅</span>` : ''}
              ${promotionReview.manualReviewRequired ? `<span class="tag manual">需人工审阅</span>` : ''}
              ${(promotionReview.topCandidateTitles || []).slice(0, 3).map((title) => `<span class="tag">${escapeHtml(title)}</span>`).join('')}
            </div>
          </header>
          ${promotionArtifacts.length ? renderInlineArtifactLinks(promotionArtifacts, '') : '<div class="empty-state">当前 run 还没有沉淀候选 artifacts。</div>'}
        </article>
      </div>
    </section>

    <section class="run-detail-section">
      <h3>动作区</h3>
      ${actionCenter.resumeCommand ? `
        <div class="command-card">
          <span>恢复命令</span>
          <code>${escapeHtml(actionCenter.resumeCommand)}</code>
          <div class="inline-actions">
            <button class="ghost-action compact-action" data-copy-command="${escapeHtml(actionCenter.resumeCommand)}" type="button">复制恢复命令</button>
          </div>
        </div>
      ` : '<p class="panel-note">当前 run 没有恢复命令，可直接查看下方 artifacts。</p>'}
      ${(run.humanTakeover.status !== 'none' || run.humanTakeover.resolutionStatus) ? `
        <div class="inline-actions">
          <button
            class="ghost-action compact-action"
            data-stage2-action="mark-human-takeover-resolved"
            data-run-id="${escapeHtml(run.runId)}"
            type="button"
            ${state.pendingAction ? 'disabled' : ''}
          >
            标记人工处理完成
          </button>
          ${(run.humanTakeover.readyToResume !== false && actionCenter.resumeCommand) ? `
            <button
              class="ghost-action compact-action"
              data-stage2-action="resume-human-takeover"
              data-run-id="${escapeHtml(run.runId)}"
              type="button"
              ${state.pendingAction ? 'disabled' : ''}
            >
              从运行中心续跑
            </button>
          ` : ''}
        </div>
      ` : ''}
      ${(run.humanTakeover.resolutionStatus === 'resolved') ? `
        <p class="panel-note">
          已记录人工处理完成${run.humanTakeover.resolutionOperator ? `，操作人：${escapeHtml(run.humanTakeover.resolutionOperator)}` : ''}。
          这只表示人工已处理/已确认可继续，不等价于系统问题已被自动判定解决。
        </p>
      ` : ''}
      <div class="action-group-list">${actionGroups}</div>
    </section>

    <section class="run-detail-section">
      <h3>阶段时间线</h3>
      <div class="timeline-list">${phaseTimeline}</div>
    </section>

    <section class="run-detail-section">
      <h3>近期事件</h3>
      <div class="event-list">${recentEvents}</div>
    </section>

    <section class="run-detail-section">
      <h3>运行备注</h3>
      <div class="detail-list">
        ${(run.notes.length ? run.notes : ['暂无额外备注']).map((note) => `
          <div class="detail-item">
            <span>说明</span>
            <strong>${escapeHtml(note)}</strong>
          </div>
        `).join('')}
      </div>
    </section>
  `;
}

function renderControlFacts(facts) {
  return facts
    .filter(([, value]) => value && value !== '-')
    .map(([label, value]) => `
      <div class="control-fact">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(String(value))}</strong>
      </div>
    `).join('') || '<div class="empty-state">暂无控制信息。</div>';
}

function renderControlNotes(notes, emptyText) {
  if (!notes.length) {
    return emptyText ? `<p class="panel-note">${escapeHtml(emptyText)}</p>` : '';
  }

  return `
    <ul class="control-note-list">
      ${notes.map((note) => `<li>${escapeHtml(note)}</li>`).join('')}
    </ul>
  `;
}

function renderControlActionList(actions, emptyText) {
  if (!actions.length) {
    return `<div class="empty-state">${escapeHtml(emptyText)}</div>`;
  }

  return `
    <div class="control-action-list">
      ${actions.map((action) => `
        <article class="control-action-item">
          <header>
            <strong>${escapeHtml(action.title || action.actionId || '未命名动作')}</strong>
            <div class="tag-row">
              ${action.priority ? `<span class="tag">${escapeHtml(action.priority)}</span>` : ''}
              ${action.stage ? `<span class="tag">${escapeHtml(stageLabel(action.stage))}</span>` : ''}
              ${action.owner ? `<span class="tag">${escapeHtml(action.owner)}</span>` : ''}
              ${action.strategy ? `<span class="tag">${escapeHtml(action.strategy)}</span>` : ''}
              ${action.retryMode ? `<span class="tag">${escapeHtml(action.retryMode)}</span>` : ''}
            </div>
          </header>
          ${action.reason ? `<p>${escapeHtml(localizeReason(action.reason))}</p>` : ''}
          ${action.expectedOutcome ? `<p class="inline-note">期望：${escapeHtml(localizeReason(action.expectedOutcome))}</p>` : ''}
          ${action.actionId || action.clusterId ? `
            <div class="control-meta-row">
              ${action.actionId ? `<span>动作 ID：${escapeHtml(action.actionId)}</span>` : ''}
              ${action.clusterId ? `<span>失败簇：${escapeHtml(action.clusterId)}</span>` : ''}
            </div>
          ` : ''}
          ${action.notes?.length ? renderControlNotes(action.notes.map((note) => localizeReason(note)), '') : ''}
        </article>
      `).join('')}
    </div>
  `;
}

function renderInlineArtifactLinks(artifacts, emptyText = '') {
  if (!artifacts.length) {
    return emptyText ? `<div class="empty-state">${escapeHtml(emptyText)}</div>` : '';
  }

  return `
    <div class="inline-link-list">
      ${artifacts.map((artifact) => `
        <a class="inline-link" href="${escapeHtml(artifact.href)}" target="_blank" rel="noreferrer">
          <span>${escapeHtml(artifact.label)}</span>
          <small>${escapeHtml(artifact.fileName || artifact.description || '')}</small>
        </a>
      `).join('')}
    </div>
  `;
}

function findActionArtifact(actionCenter, key) {
  for (const group of actionCenter?.artifactGroups || []) {
    const matched = (group.items || []).find((item) => item.key === key);
    if (matched) {
      return matched;
    }
  }
  return null;
}

function renderTabs() {
  document.querySelectorAll('.tabs button').forEach((button) => {
    button.classList.toggle('active', button.dataset.tab === state.activeTab);
  });

  document.querySelectorAll('.tab-body').forEach((tab) => {
    tab.classList.remove('active');
  });
  document.querySelector(`#${state.activeTab}Tab`).classList.add('active');

  renderAnalysis();
  renderCases();
  renderExecution();
  renderReport();
  renderCode();
}

function renderAnalysis() {
  const project = state.currentProject;
  const container = document.querySelector('#analysisTab');
  if (!project?.featureModules?.length) {
    container.innerHTML = '<div class="empty-state">保存项目后点击“解析需求”，这里会显示功能模块、功能点和验收项。</div>';
    return;
  }

  container.innerHTML = `
    <div class="section-list">
      ${project.featureModules.map((module) => `
        <article class="module-item">
          <h3>${escapeHtml(module.name)}</h3>
          <p class="muted">${escapeHtml(module.summary)}</p>
          <ul>
            ${module.featurePoints.map((point) => `<li>${escapeHtml(point.name)} <span class="tag">${escapeHtml(point.priority)}</span></li>`).join('')}
          </ul>
        </article>
      `).join('')}
      <article class="module-item">
        <h3>验收项</h3>
        <ul>
          ${project.acceptanceCriteria.map((item) => `<li>${escapeHtml(item.description)}</li>`).join('')}
        </ul>
      </article>
    </div>
  `;
}

function renderCases() {
  const project = state.currentProject;
  const container = document.querySelector('#casesTab');
  if (!project?.testCases?.length) {
    container.innerHTML = '<div class="empty-state">解析需求后点击“生成用例”，这里会显示可审核的测试用例。</div>';
    return;
  }

  container.innerHTML = `<div class="case-list">
    ${project.testCases.map((testCase) => `
      <article class="case-item">
        <h3>${escapeHtml(testCase.title)}</h3>
        <div class="tag-row">
          <span class="tag">${escapeHtml(testCase.priority)}</span>
          <span class="tag">${escapeHtml(testCase.type)}</span>
          <span class="tag">${escapeHtml(testCase.acceptanceCriteriaIds.join(', ') || '未绑定验收项')}</span>
        </div>
        <ol>
          ${testCase.steps.map((step) => `<li>${escapeHtml(step)}</li>`).join('')}
        </ol>
      </article>
    `).join('')}
  </div>`;
}

function renderExecution() {
  const project = state.currentProject;
  const container = document.querySelector('#executionTab');
  if (!project?.executions?.results?.length) {
    container.innerHTML = '<div class="empty-state">生成用例后点击“执行评测”，这里会显示测试结论和执行证据摘要。</div>';
    return;
  }

  container.innerHTML = `<div class="execution-list">
    ${project.executions.results.map((result) => `
      <article class="result-item">
        <h3>${escapeHtml(result.title)}</h3>
        <div class="tag-row">
          <span class="tag ${verdictClass(result.verdict)}">${escapeHtml(result.verdict)}</span>
          <span class="tag">${escapeHtml(String(result.durationMs))} ms</span>
          <span class="tag">${escapeHtml(result.evidence.trace)}</span>
        </div>
        <p class="muted">${escapeHtml(result.evidence.agentRationale)}</p>
      </article>
    `).join('')}
  </div>`;
}

function renderReport() {
  const project = state.currentProject;
  const container = document.querySelector('#reportTab');
  if (!project?.report) {
    container.innerHTML = '<div class="empty-state">执行评测后点击“生成报告”，这里会显示评测结论、覆盖率和缺陷清单。</div>';
    return;
  }

  container.innerHTML = `
    <div class="report-block">
      <article class="module-item">
        <h3>${escapeHtml(project.report.title)}</h3>
        <p>${escapeHtml(project.report.conclusion)}</p>
        <div class="tag-row">
          <span class="tag">执行 ${escapeHtml(String(project.report.coverage.executedCases))}</span>
          <span class="tag passed">通过 ${escapeHtml(String(project.report.verdictSummary.passed))}</span>
          <span class="tag failed">缺陷 ${escapeHtml(String(project.report.verdictSummary.failed))}</span>
          <span class="tag manual">人工确认 ${escapeHtml(String(project.report.verdictSummary.manualReview))}</span>
        </div>
      </article>
      <article class="module-item">
        <h3>风险说明</h3>
        <ul>${project.report.riskNotes.map((note) => `<li>${escapeHtml(note)}</li>`).join('')}</ul>
      </article>
      ${project.defects.length ? `
        <article class="defect-item">
          <h3>缺陷清单</h3>
          ${project.defects.map((defect) => `
            <div>
              <strong>${escapeHtml(defect.title)}</strong>
              <div class="tag-row">
                <span class="tag failed">${escapeHtml(defect.severity)}</span>
                <span class="tag">${escapeHtml(defect.status)}</span>
              </div>
              <ol>${defect.reproductionSteps.map((step) => `<li>${escapeHtml(step)}</li>`).join('')}</ol>
              <p class="muted">${escapeHtml(defect.suggestion)}</p>
            </div>
          `).join('')}
        </article>
      ` : ''}
    </div>
  `;
}

function renderCode() {
  const project = state.currentProject;
  document.querySelector('#codeTab').textContent = project?.generatedCode || '// 执行评测后，这里会显示沉淀的自动化测试代码。';
}

function getProjectRunCenter(project) {
  if (!project) {
    return {
      currentPhaseKey: 'setup',
      currentPhaseLabel: '项目资料',
      currentStepLabel: '先保存项目资料并建立评测上下文。',
      currentObjectLabel: '未选择项目',
      roundLabel: '初始化轮',
      nextAction: '新建或选择项目开始',
      latestEventAt: '',
      statusTone: 'info',
      blockers: [],
      stageStates: [
        { key: 'setup', label: '项目资料', state: 'current' },
        { key: 'analysis', label: '需求解析', state: 'pending' },
        { key: 'cases', label: '用例设计', state: 'pending' },
        { key: 'execution', label: '执行验证', state: 'pending' },
        { key: 'report', label: '报告汇总', state: 'pending' }
      ],
      summary: {
        moduleCount: 0,
        featurePointCount: 0,
        criteriaCount: 0,
        caseCount: 0,
        executedCount: 0,
        passRate: 0
      }
    };
  }

  if (project.runCenter) {
    return project.runCenter;
  }

  const moduleCount = project.featureModules?.length || 0;
  const featurePointCount = (project.featureModules || []).reduce((total, item) => total + item.featurePoints.length, 0);
  const caseCount = project.testCases?.length || 0;
  const executedCount = project.executions?.summary?.total || 0;
  const passRate = project.executions?.summary?.passRate || 0;
  let currentPhaseKey = 'setup';
  if (project.report) currentPhaseKey = 'report';
  else if (project.executions?.results?.length) currentPhaseKey = 'execution';
  else if (project.testCases?.length) currentPhaseKey = 'cases';
  else if (project.featureModules?.length) currentPhaseKey = 'analysis';

  const phaseLabels = {
    setup: '项目资料',
    analysis: '需求解析',
    cases: '用例设计',
    execution: '执行验证',
    report: '报告汇总'
  };

  const steps = {
    setup: '维护项目资料与被测系统上下文',
    analysis: '解析需求资料并提取验收范围',
    cases: '生成可审核的测试用例',
    execution: '执行评测并收集证据',
    report: '汇总执行结果并生成报告'
  };

  const stageOrder = ['setup', 'analysis', 'cases', 'execution', 'report'];
  const currentIndex = stageOrder.indexOf(currentPhaseKey);
  return {
    currentPhaseKey,
    currentPhaseLabel: phaseLabels[currentPhaseKey],
    currentStepLabel: steps[currentPhaseKey],
    currentObjectLabel: project.sut?.name || project.name,
    roundLabel: executedCount > 0 ? '验证第 1 轮' : '初始化轮',
    nextAction: '继续当前流程',
    latestEventAt: project.updatedAt,
    statusTone: 'info',
    blockers: [],
    stageStates: stageOrder.map((key, index) => ({
      key,
      label: phaseLabels[key],
      state: index < currentIndex ? 'completed' : (index === currentIndex ? 'current' : 'pending')
    })),
    summary: {
      moduleCount,
      featurePointCount,
      criteriaCount: project.acceptanceCriteria?.length || 0,
      caseCount,
      executedCount,
      passRate
    }
  };
}

function recommendedActionFor(currentPhaseKey, blockers = []) {
  if (blockers.some((item) => item.title === '存在待人工确认项')) {
    return null;
  }
  const mapping = {
    setup: 'analyze',
    analysis: 'generate-cases',
    cases: 'run',
    execution: 'report'
  };
  return mapping[currentPhaseKey] || null;
}

function completedActions(project) {
  if (!project) {
    return [];
  }
  const actions = [];
  if (project.featureModules?.length) actions.push('analyze');
  if (project.testCases?.length) actions.push('generate-cases');
  if (project.executions?.results?.length) actions.push('run');
  if (project.report) actions.push('report');
  return actions;
}

function isActionLocked(action, project) {
  if (!project) {
    return true;
  }
  if (action === 'analyze') return false;
  if (action === 'generate-cases') return !(project.featureModules?.length);
  if (action === 'run') return !(project.testCases?.length);
  if (action === 'report') return !(project.executions?.results?.length);
  return false;
}

function actionLabel(action) {
  const labels = {
    analyze: '解析需求',
    'generate-cases': '生成用例',
    run: '执行评测',
    report: '生成报告'
  };
  return labels[action] || action;
}

function stageStateLabel(state) {
  const labels = {
    pending: '待开始',
    current: '进行中',
    completed: '已完成'
  };
  return labels[state] || state;
}

function statusLabel(value = '') {
  const labels = {
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    stopped: '已停止',
    scheduled: '待续跑',
    planned: '已计划',
    waiting_human: '待人工处理',
    skipped: '已跳过',
    partial: '部分完成',
    passed: '通过',
    needs_review: '待复核',
    ready_for_review: '可审阅',
    needs_evidence: '待补证据',
    needs_followup_validation: '待补验证',
    warning: '注意',
    none: '无',
    unknown: '未知',
    ready: '待执行',
    manual: '待确认',
    submitted: '已提交',
    confirmed: '已确认',
    blocked: '待补参数',
    '-': '-'
  };
  return labels[value] || value || '未知';
}

function toneClass(value = '') {
  if (!value) return '';
  if (value.includes('success') || value.includes('passed')) return 'success';
  if (value.includes('warning')) return 'warning';
  if (value.includes('manual')) return 'manual';
  if (value.includes('failed') || value.includes('error')) return 'failed';
  return '';
}

function verdictClass(value = '') {
  if (value.includes('通过') || value.includes('completed') || value.includes('passed') || value.includes('success')) return 'passed';
  if (value.includes('失败') || value.includes('failed')) return 'failed';
  if (value.includes('partial')) return 'warning';
  if (value.includes('warning')) return 'warning';
  if (value.includes('manual') || value.includes('review') || value.includes('stopped')) return 'manual';
  return '';
}

function artifactKindLabel(value = '') {
  const labels = {
    text: '文本产物',
    image: '截图证据',
    document: '文档附件',
    file: '文件产物'
  };
  return labels[value] || '文件产物';
}

function formatStage2OverviewSummary(overview) {
  const daily = overview.latestDailyReport;
  const freeze = overview.latestBaselineFreezeManifest;
  const validation = overview.latestValidationMatrix;

  if (daily) {
    const modelCount = Array.isArray(daily.modelsCovered) ? daily.modelsCovered.length : 0;
    const modelSummary = modelCount ? `，覆盖 ${modelCount} 个模型` : '';
    const watchSummary = daily.watchItems?.length ? `，${daily.watchItems.length} 项待关注` : '';
    const freezeSummary = freeze?.freezeRecommended && freeze?.recommendedPrimaryRun?.model
      ? `，当前推荐冻结模型为 ${freeze.recommendedPrimaryRun.model}`
      : '';
    return `最近日报已汇总 ${daily.runCount} 次 run，成功 ${daily.successfulRuns} 次，失败 ${daily.failedRuns} 次${modelSummary}${watchSummary}${freezeSummary}。`;
  }

  if (validation) {
    return `最近验证矩阵状态为${statusLabel(validation.status)}，已执行 ${validation.executedCount}/${validation.targetCount}，通过 ${validation.passedCount}。`;
  }

  return '最近 run 摘要已接入。';
}

function localizeReason(value = '') {
  const reasonMap = {
    'Next round scheduling stopped because no_improvement was triggered.': '未检测到继续改进收益，已停止下一轮调度。',
    'Next round scheduling stopped because goal_completed was triggered.': '目标已完成，已停止下一轮调度。',
    'Stop decision requires manual review before scheduling the next round.': '需要人工复核后再决定是否进入下一轮。',
    'Next round scheduling stopped because resource_budget_exhausted was triggered.': '已触发资源预算上限，停止下一轮调度。',
    'Failure cluster scheduled for the next round.': '该失败簇已被纳入下一轮处理。',
    'Retry plan was derived from the latest run report, status snapshot, and attempt outcomes.': '重试计划由最近一次运行报告、状态快照和尝试结果推导得出。'
  };

  return reasonMap[value] || value;
}

function autoContinueLabel(value) {
  if (value === true) {
    return '是';
  }
  if (value === false) {
    return '否';
  }
  return '待复核';
}

function roundLabel(value) {
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  return `第 ${value} 轮`;
}

function stageLabel(value = '') {
  const labels = {
    preflight: '预检',
    discovery: '发现',
    verification: '验证',
    reporting: '报告',
    retry: '重试',
    submit: '提交',
    ready: '待执行',
    draft: '草稿',
    manual: '待确认',
    draft: '草稿',
    waiting_human: '待人工处理',
    planned: '已计划',
    feature_analysis: '功能点识别',
    case_generation: '用例生成',
    execution: '安全执行',
    ai_analysis: 'AI 复盘',
    submitted: '已提交',
    confirmed: '已确认',
    running: '执行中',
    blocked: '待补参数',
    '-': '-'
  };
  return labels[value] || value || '-';
}

function formatDuration(ms) {
  if (!ms && ms !== 0) {
    return '-';
  }
  if (ms < 1000) {
    return `${ms} ms`;
  }
  return `${(ms / 1000).toFixed(1)} s`;
}

function formatDate(value, includeYear = false) {
  if (!value) {
    return '-';
  }
  return new Date(value).toLocaleString('zh-CN', includeYear ? {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  } : {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
}

function escapeHtml(value = '') {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

async function copyText(value, successMessage) {
  try {
    await navigator.clipboard.writeText(value);
    saveState.textContent = successMessage;
  } catch {
    saveState.textContent = '复制失败，请手动复制';
  }
}

projectForm.addEventListener('submit', saveProject);

stage2RunForm?.addEventListener('submit', createStage2Run);

document.querySelector('#stage2Cockpit')?.addEventListener('click', (event) => {
  const refreshButton = event.target.closest('[data-stage2-action="refresh-runs"]');
  if (refreshButton) {
    loadDashboardData().catch((error) => {
      saveState.textContent = error.message;
    });
    return;
  }

  const runActionButton = event.target.closest('[data-stage2-run-action][data-run-id]');
  if (runActionButton) {
    runStage2V3Action(runActionButton.dataset.runId, runActionButton.dataset.stage2RunAction);
    return;
  }

  const humanTaskButton = event.target.closest('[data-stage2-human-task]');
  if (humanTaskButton) {
    completeStage2HumanTask(humanTaskButton.dataset.stage2HumanTask);
    return;
  }

  const copyButton = event.target.closest('[data-copy-command]');
  if (copyButton) {
    copyText(copyButton.dataset.copyCommand, '命令已复制');
  }
});

document.querySelector('#newProjectButton').addEventListener('click', () => {
  state.currentProject = null;
  state.showProjectForm = true;
  fillForm(null);
  saveState.textContent = '未保存';
  render();
});

toggleProjectFormButton.addEventListener('click', () => {
  state.showProjectForm = !state.showProjectForm;
  renderProjectFormVisibility();
});

projectList.addEventListener('click', (event) => {
  const button = event.target.closest('[data-project-id]');
  if (button) {
    selectProject(button.dataset.projectId).catch((error) => {
      saveState.textContent = error.message;
    });
  }
});

document.querySelector('.pipeline').addEventListener('click', (event) => {
  const button = event.target.closest('[data-action]');
  if (button) {
    runAction(button.dataset.action);
  }
});

document.querySelector('.tabs').addEventListener('click', (event) => {
  const button = event.target.closest('[data-tab]');
  if (button) {
    state.activeTab = button.dataset.tab;
    renderTabs();
  }
});

document.querySelector('#stage2Tabs')?.addEventListener('click', (event) => {
  const button = event.target.closest('[data-stage2-tab]');
  if (!button) {
    return;
  }
  state.activeStage2Tab = button.dataset.stage2Tab;
  renderStage2Overview();
});

document.querySelector('#stage2OnboardingTab')?.addEventListener('input', (event) => {
  const field = event.target.closest('[name]');
  if (!field || !(field.name in state.onboardingForm)) {
    return;
  }
  state.onboardingForm[field.name] = field.value;
  saveStage2OnboardingForm();
});

document.querySelector('#stage2OnboardingTab')?.addEventListener('change', (event) => {
  const field = event.target.closest('[name]');
  if (!field || !(field.name in state.onboardingForm)) {
    return;
  }
  updateStage2OnboardingField(field.name, field.value);
});

document.querySelector('#stage2OnboardingTab')?.addEventListener('click', (event) => {
  const resetButton = event.target.closest('[data-onboarding-reset]');
  if (resetButton) {
    state.onboardingForm = { ...stage2OnboardingDefaults };
    state.onboardingStepResults = {};
    state.onboardingOperationSessionId = null;
    saveStage2OnboardingForm();
    saveStage2OnboardingStepResults();
    saveState.textContent = '向导状态已清空';
    renderStage2Overview();
    return;
  }

  const checkEnvironmentButton = event.target.closest('[data-onboarding-check-env]');
  if (checkEnvironmentButton) {
    runStage2EnvironmentCheck();
    return;
  }

  const stepButton = event.target.closest('[data-onboarding-step][data-onboarding-action]');
  if (!stepButton) {
    return;
  }
  if (stepButton.dataset.onboardingAction === 'run') {
    runStage2OperationStep(stepButton.dataset.onboardingStep);
    return;
  }
  confirmStage2OnboardingStep(stepButton.dataset.onboardingStep);
});

document.querySelector('#stage2HumanTab')?.addEventListener('click', (event) => {
  const copyButton = event.target.closest('[data-copy-command]');
  if (copyButton) {
    copyText(copyButton.dataset.copyCommand, '恢复命令已复制');
    return;
  }
  const runButton = event.target.closest('[data-run-id]');
  if (runButton) {
    state.selectedRunId = runButton.dataset.runId;
    syncSelectedSession();
    renderStage2Overview();
    renderStage2RunDetail();
  }
});

stage2RunList?.addEventListener('click', (event) => {
  const button = event.target.closest('[data-run-id]');
  if (button) {
    selectStage2Run(button.dataset.runId).catch((error) => {
      saveState.textContent = error.message;
    });
  }
});

stage2SessionList?.addEventListener('click', (event) => {
  const copyButton = event.target.closest('[data-copy-command]');
  if (copyButton) {
    copyText(copyButton.dataset.copyCommand, '恢复命令已复制');
    return;
  }

  const sessionButton = event.target.closest('[data-session-id]');
  if (sessionButton) {
    state.selectedSessionId = sessionButton.dataset.sessionId;
    renderStage2Overview();
    renderStage2RunDetail();
    return;
  }
  const button = event.target.closest('[data-run-id]');
  if (button) {
    state.selectedRunId = button.dataset.runId;
    syncSelectedSession();
    renderStage2Overview();
    renderStage2RunDetail();
  }
});

document.querySelector('#stage2RunDetail')?.addEventListener('click', (event) => {
  const copyButton = event.target.closest('[data-copy-command]');
  if (copyButton) {
    copyText(copyButton.dataset.copyCommand, '恢复命令已复制');
    return;
  }

  const actionButton = event.target.closest('[data-stage2-action][data-run-id]');
  if (!actionButton) {
    return;
  }
  const { runId } = actionButton.dataset;
  const action = actionButton.dataset.stage2Action;
  const run = (state.stage2Overview?.runSummaries || []).find((item) => item.runId === runId);
  if (!run) {
    return;
  }
  if (action === 'mark-human-takeover-resolved') {
    runStage2RunAction(
      runId,
      action,
      {
        operatorId: 'run_center',
        note: 'Marked as resolved from run center.',
        readyToResume: true,
        handledActionIds: []
      },
      '已记录人工处理完成'
    );
    return;
  }
  if (action === 'resume-human-takeover') {
    runStage2RunAction(
      runId,
      action,
      {
        operatorId: 'run_center',
        note: 'Resumed from run center.'
      },
      '已触发恢复续跑'
    );
  }
});

function autoRefreshDashboard() {
  if (document.hidden || state.pendingAction || state.showProjectForm) {
    return;
  }

  loadDashboardData().catch((error) => {
    saveState.textContent = error.message;
  });
}

loadDashboardData().catch((error) => {
  saveState.textContent = error.message;
});

window.setInterval(autoRefreshDashboard, AUTO_REFRESH_MS);
