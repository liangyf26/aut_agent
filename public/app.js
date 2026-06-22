const state = {
  projects: [],
  currentProject: null,
  activeTab: 'analysis',
  showProjectForm: false,
  pendingAction: null,
  stage2Overview: null,
  selectedRunId: null,
  selectedSessionId: null
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
  const [projectsPayload, stage2Payload] = await Promise.all([
    api('/api/projects'),
    api('/api/stage2/overview').catch(() => ({ overview: null }))
  ]);

  state.projects = projectsPayload.projects;
  state.stage2Overview = stage2Payload.overview;

  if (state.currentProject?.id) {
    state.currentProject = state.projects.find((item) => item.id === state.currentProject.id) || null;
  }

  if (!state.currentProject && state.projects.length > 0) {
    state.currentProject = state.projects[0];
  }

  fillForm(state.currentProject);
  syncSelectedSession();
  syncSelectedRun();
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
  const runs = state.stage2Overview?.runSummaries || [];
  if (runs.length === 0) {
    state.selectedRunId = null;
    return;
  }

  if (!runs.some((item) => item.runId === state.selectedRunId)) {
    state.selectedRunId = runs[0].runId;
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

function render() {
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

function renderStage2Overview() {
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

stage2RunList.addEventListener('click', (event) => {
  const button = event.target.closest('[data-run-id]');
  if (button) {
    state.selectedRunId = button.dataset.runId;
    renderStage2Overview();
    renderStage2RunDetail();
  }
});

stage2SessionList.addEventListener('click', (event) => {
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

document.querySelector('#stage2RunDetail').addEventListener('click', (event) => {
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
