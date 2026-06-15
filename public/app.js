const state = {
  projects: [],
  currentProject: null,
  activeTab: 'analysis'
};

const projectForm = document.querySelector('#projectForm');
const projectList = document.querySelector('#projectList');
const pageTitle = document.querySelector('#pageTitle');
const projectStatus = document.querySelector('#projectStatus');
const saveState = document.querySelector('#saveState');

const fields = ['name', 'client', 'vendor', 'sutName', 'sutBaseUrl', 'accountNotes', 'scope', 'documentText'];

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

function getFormValue() {
  const data = new FormData(projectForm);
  return Object.fromEntries(fields.map((field) => [field, data.get(field)?.trim() || '']));
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

async function loadProjects() {
  const payload = await api('/api/projects');
  state.projects = payload.projects;
  if (!state.currentProject && state.projects.length > 0) {
    state.currentProject = state.projects[0];
    fillForm(state.currentProject);
  }
  render();
}

async function selectProject(id) {
  const payload = await api(`/api/projects/${id}`);
  state.currentProject = payload.project;
  fillForm(state.currentProject);
  render();
}

async function saveProject(event) {
  event.preventDefault();
  saveState.textContent = '保存中';
  const payload = await api('/api/projects', {
    method: 'POST',
    body: JSON.stringify(getFormValue())
  });
  state.currentProject = payload.project;
  fillForm(state.currentProject);
  saveState.textContent = '已保存';
  await loadProjects();
}

async function runAction(action) {
  if (!state.currentProject) {
    saveState.textContent = '请先保存项目';
    return;
  }
  saveState.textContent = '处理中';
  const payload = await api(`/api/projects/${state.currentProject.id}/${action}`, { method: 'POST' });
  state.currentProject = payload.project;
  state.projects = state.projects.map((project) => project.id === payload.project.id ? payload.project : project);
  saveState.textContent = '已更新';
  render();
}

function render() {
  renderProjectList();
  renderHeader();
  renderMetrics();
  renderTabs();
}

function renderProjectList() {
  if (state.projects.length === 0) {
    projectList.innerHTML = '<div class="empty-state">暂无评测项目</div>';
    return;
  }

  projectList.innerHTML = state.projects.map((project) => `
    <button class="project-item ${state.currentProject?.id === project.id ? 'active' : ''}" data-project-id="${project.id}" type="button">
      <strong>${escapeHtml(project.name)}</strong>
      <span>${escapeHtml(project.status)} · ${formatDate(project.updatedAt)}</span>
    </button>
  `).join('');
}

function renderHeader() {
  const project = state.currentProject;
  pageTitle.textContent = project ? project.name : '评测项目工作台';
  projectStatus.textContent = project ? project.status : '待创建';
}

function renderMetrics() {
  const project = state.currentProject;
  const featurePoints = project?.featureModules?.reduce((total, item) => total + item.featurePoints.length, 0) || 0;
  document.querySelector('#moduleCount').textContent = project?.featureModules?.length || 0;
  document.querySelector('#criteriaCount').textContent = project?.acceptanceCriteria?.length || 0;
  document.querySelector('#caseCount').textContent = project?.testCases?.length || 0;
  document.querySelector('#passRate').textContent = `${project?.executions?.summary?.passRate || 0}%`;
  document.querySelector('#moduleCount').title = `${featurePoints} 个功能点`;
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
            ${module.featurePoints.map((point) => `<li>${escapeHtml(point.name)} <span class="tag">${point.priority}</span></li>`).join('')}
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
          <span class="tag">${testCase.priority}</span>
          <span class="tag">${escapeHtml(testCase.type)}</span>
          <span class="tag">${testCase.acceptanceCriteriaIds.join(', ') || '未绑定验收项'}</span>
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
          <span class="tag">${result.durationMs} ms</span>
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
          <span class="tag">执行 ${project.report.coverage.executedCases}</span>
          <span class="tag passed">通过 ${project.report.verdictSummary.passed}</span>
          <span class="tag failed">缺陷 ${project.report.verdictSummary.failed}</span>
          <span class="tag manual">人工确认 ${project.report.verdictSummary.manualReview}</span>
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
              <div class="tag-row"><span class="tag failed">${escapeHtml(defect.severity)}</span><span class="tag">${escapeHtml(defect.status)}</span></div>
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

function verdictClass(verdict) {
  if (verdict.includes('通过')) return 'passed';
  if (verdict.includes('失败')) return 'failed';
  if (verdict.includes('环境')) return 'warning';
  return 'manual';
}

function formatDate(value) {
  return new Date(value).toLocaleString('zh-CN', {
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

projectForm.addEventListener('submit', saveProject);

document.querySelector('#newProjectButton').addEventListener('click', () => {
  state.currentProject = null;
  fillForm(null);
  saveState.textContent = '未保存';
  render();
});

projectList.addEventListener('click', (event) => {
  const button = event.target.closest('[data-project-id]');
  if (button) {
    selectProject(button.dataset.projectId);
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

loadProjects().catch((error) => {
  saveState.textContent = error.message;
});
