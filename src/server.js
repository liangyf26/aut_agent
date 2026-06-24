const http = require('http');
const fs = require('fs/promises');
const path = require('path');
const { URL } = require('url');
const storage = require('./storage');
const {
  loadStage2Overview,
  resolveHumanLoopArtifact,
  resolveStage2RunArtifact
} = require('./stage2Dashboard');
const {
  markHumanTakeoverResolved,
  resumeHumanTakeover
} = require('./stage2Actions');
const {
  checkOperationEnvironment,
  loadOperationCenter,
  resolveOperationArtifact,
  runOperationStep
} = require('./stage2OperationCenter');
const {
  analyzeV3Run,
  continueNextRound,
  createV3Run,
  generateV3Report,
  getV3Run,
  listV3Runs,
  resolveV3RunArtifact,
  saveHumanTaskResult,
  setV3RunLifecycleStatus,
  startV3Run
} = require('./stage2V3RunCenter');
const {
  createProject,
  updateProject,
  hydrateProject,
  analyzeProject,
  designTestCases,
  executeProject,
  buildReport
} = require('./domain/assessment');

const PORT = Number(process.env.PORT || 4173);
const PUBLIC_DIR = path.join(__dirname, '..', 'public');

const mimeTypes = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.jsonl': 'text/plain; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.bmp': 'image/bmp',
  '.pdf': 'application/pdf'
};

function sendJson(res, statusCode, payload) {
  res.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store'
  });
  res.end(JSON.stringify(payload));
}

function sendError(res, statusCode, message) {
  sendJson(res, statusCode, { error: message });
}

function sendOperationError(res, error) {
  sendError(res, error.statusCode || 500, error.message || 'Stage-2 operation failed.');
}

function sendStage2V3Error(res, error) {
  sendError(res, error.statusCode || 500, error.message || 'Stage-2 v3 run operation failed.');
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(chunk);
  }
  const raw = Buffer.concat(chunks).toString('utf8');
  return raw ? JSON.parse(raw) : {};
}

async function getProjectOr404(res, id) {
  const project = await storage.getProject(id);
  if (!project) {
    sendError(res, 404, 'Assessment project not found.');
    return null;
  }
  return project;
}

function routeMatch(pathname, pattern) {
  const pathParts = pathname.split('/').filter(Boolean);
  const patternParts = pattern.split('/').filter(Boolean);
  if (pathParts.length !== patternParts.length) {
    return null;
  }

  const params = {};
  for (let index = 0; index < patternParts.length; index += 1) {
    const patternPart = patternParts[index];
    const pathPart = pathParts[index];
    if (patternPart.startsWith(':')) {
      params[patternPart.slice(1)] = pathPart;
    } else if (patternPart !== pathPart) {
      return null;
    }
  }

  return params;
}

async function handleApi(req, res, pathname) {
  if (req.method === 'GET' && pathname === '/api/health') {
    sendJson(res, 200, { ok: true, service: 'aut_agent', at: new Date().toISOString() });
    return true;
  }

  if (req.method === 'GET' && pathname === '/api/projects') {
    const projects = (await storage.listProjects()).map(hydrateProject);
    sendJson(res, 200, { projects });
    return true;
  }

  if (req.method === 'GET' && pathname === '/api/stage2/overview') {
    const overview = await loadStage2Overview();
    sendJson(res, 200, { overview });
    return true;
  }

  if (req.method === 'GET' && pathname === '/api/stage2/operation/state') {
    try {
      const operationCenter = await loadOperationCenter();
      sendJson(res, 200, { operationCenter });
    } catch (error) {
      sendOperationError(res, error);
    }
    return true;
  }

  if (req.method === 'GET' && pathname === '/api/stage2/operation/sessions') {
    try {
      const operationCenter = await loadOperationCenter();
      sendJson(res, 200, { sessions: operationCenter.sessions, operationCenter });
    } catch (error) {
      sendOperationError(res, error);
    }
    return true;
  }

  if (req.method === 'POST' && pathname === '/api/stage2/operation/check-environment') {
    try {
      const body = await readJson(req);
      const result = await checkOperationEnvironment(body);
      const overview = await loadStage2Overview();
      sendJson(res, 200, { result, overview });
    } catch (error) {
      sendOperationError(res, error);
    }
    return true;
  }

  if (req.method === 'POST' && pathname === '/api/stage2/operation/run-step') {
    try {
      const body = await readJson(req);
      const result = await runOperationStep(body);
      const overview = await loadStage2Overview();
      sendJson(res, 200, { result, overview });
    } catch (error) {
      sendOperationError(res, error);
    }
    return true;
  }

  if (req.method === 'GET' && pathname === '/api/stage2/v3/runs') {
    try {
      sendJson(res, 200, await listV3Runs());
    } catch (error) {
      sendStage2V3Error(res, error);
    }
    return true;
  }

  if (req.method === 'POST' && pathname === '/api/stage2/v3/runs') {
    try {
      const body = await readJson(req);
      sendJson(res, 201, await createV3Run(body));
    } catch (error) {
      sendStage2V3Error(res, error);
    }
    return true;
  }

  const stage2V3ArtifactParams = routeMatch(pathname, '/api/stage2/v3/runs/:runId/artifacts/:artifactKey');
  if (stage2V3ArtifactParams && req.method === 'GET') {
    try {
      const artifact = await resolveV3RunArtifact(
        stage2V3ArtifactParams.runId,
        stage2V3ArtifactParams.artifactKey
      );
      if (!artifact) {
        sendError(res, 404, 'Stage-2 v3 artifact not found.');
        return true;
      }
      const content = await fs.readFile(artifact.path);
      res.writeHead(200, {
        'Content-Type': mimeTypes[path.extname(artifact.path).toLowerCase()] || 'application/octet-stream',
        'Cache-Control': 'no-store',
        'Content-Disposition': `inline; filename="${encodeURIComponent(artifact.fileName)}"`
      });
      res.end(content);
    } catch (error) {
      sendStage2V3Error(res, error);
    }
    return true;
  }

  const stage2V3RunParams = routeMatch(pathname, '/api/stage2/v3/runs/:runId');
  if (stage2V3RunParams && req.method === 'GET') {
    try {
      sendJson(res, 200, await getV3Run(stage2V3RunParams.runId));
    } catch (error) {
      sendStage2V3Error(res, error);
    }
    return true;
  }

  const stage2V3RunActionParams = routeMatch(pathname, '/api/stage2/v3/runs/:runId/:action');
  if (stage2V3RunActionParams && req.method === 'POST') {
    try {
      const body = await readJson(req);
      const { runId, action } = stage2V3RunActionParams;
      const handlers = {
        start: () => startV3Run(runId, body),
        pause: () => setV3RunLifecycleStatus(runId, 'pause', body),
        resume: () => setV3RunLifecycleStatus(runId, 'resume', body),
        stop: () => setV3RunLifecycleStatus(runId, 'stop', body),
        'save-human-task': () => saveHumanTaskResult(runId, body),
        'analyze-round': () => analyzeV3Run(runId),
        'continue-next-round': () => continueNextRound(runId, body),
        'generate-report': () => generateV3Report(runId)
      };
      const handler = handlers[action];
      if (!handler) {
        sendError(res, 404, 'Unknown stage-2 v3 run action.');
        return true;
      }
      sendJson(res, 200, await handler());
    } catch (error) {
      sendStage2V3Error(res, error);
    }
    return true;
  }

  const stage2RunActionParams = routeMatch(pathname, '/api/stage2/runs/:runId/:action');
  if (stage2RunActionParams && req.method === 'POST') {
    const body = await readJson(req);
    let result;

    if (stage2RunActionParams.action === 'mark-human-takeover-resolved') {
      result = await markHumanTakeoverResolved({
        runId: stage2RunActionParams.runId,
        operatorId: body.operatorId,
        note: body.note,
        readyToResume: body.readyToResume,
        handledActionIds: body.handledActionIds
      });
    } else if (stage2RunActionParams.action === 'resume-human-takeover') {
      result = await resumeHumanTakeover({
        runId: stage2RunActionParams.runId,
        cdpUrl: body.cdpUrl,
        maxAttempts: body.maxAttempts,
        maxRounds: body.maxRounds,
        operatorId: body.operatorId,
        note: body.note
      });
    } else {
      sendError(res, 404, 'Unknown stage-2 run action.');
      return true;
    }

    const overview = await loadStage2Overview();
    sendJson(res, 200, { result, overview });
    return true;
  }

  const humanLoopArtifactParams = routeMatch(pathname, '/api/stage2/human-loop/:sessionId/artifacts/:artifactKey');
  if (humanLoopArtifactParams && req.method === 'GET') {
    const artifact = await resolveHumanLoopArtifact(humanLoopArtifactParams.sessionId, humanLoopArtifactParams.artifactKey);
    if (!artifact) {
      sendError(res, 404, 'Human-loop artifact not found.');
      return true;
    }

    try {
      const content = await fs.readFile(artifact.path);
      res.writeHead(200, {
        'Content-Type': mimeTypes[path.extname(artifact.path).toLowerCase()] || 'application/octet-stream',
        'Cache-Control': 'no-store',
        'Content-Disposition': `inline; filename="${encodeURIComponent(artifact.fileName)}"`
      });
      res.end(content);
    } catch {
      sendError(res, 404, 'Human-loop artifact file is unavailable.');
    }
    return true;
  }

  const operationArtifactParams = routeMatch(pathname, '/api/stage2/operation/artifacts/:sessionId/:artifactKey');
  if (operationArtifactParams && req.method === 'GET') {
    const artifact = await resolveOperationArtifact(
      operationArtifactParams.sessionId,
      operationArtifactParams.artifactKey
    );
    if (!artifact) {
      sendError(res, 404, 'Operation artifact not found.');
      return true;
    }

    try {
      const content = await fs.readFile(artifact.path);
      res.writeHead(200, {
        'Content-Type': mimeTypes[path.extname(artifact.path).toLowerCase()] || 'application/octet-stream',
        'Cache-Control': 'no-store',
        'Content-Disposition': `inline; filename="${encodeURIComponent(path.basename(artifact.path))}"`
      });
      res.end(content);
    } catch {
      sendError(res, 404, 'Operation artifact file is unavailable.');
    }
    return true;
  }

  const stage2ArtifactParams = routeMatch(pathname, '/api/stage2/runs/:runId/artifacts/:artifactKey');
  if (stage2ArtifactParams && req.method === 'GET') {
    const artifact = await resolveStage2RunArtifact(stage2ArtifactParams.runId, stage2ArtifactParams.artifactKey);
    if (!artifact) {
      sendError(res, 404, 'Stage-2 artifact not found.');
      return true;
    }

    try {
      const content = await fs.readFile(artifact.path);
      res.writeHead(200, {
        'Content-Type': mimeTypes[path.extname(artifact.path).toLowerCase()] || 'application/octet-stream',
        'Cache-Control': 'no-store',
        'Content-Disposition': `inline; filename="${encodeURIComponent(artifact.fileName)}"`
      });
      res.end(content);
    } catch {
      sendError(res, 404, 'Stage-2 artifact file is unavailable.');
    }
    return true;
  }

  if (req.method === 'POST' && pathname === '/api/projects') {
    const body = await readJson(req);
    const project = body.id
      ? await getProjectOr404(res, body.id)
      : null;
    if (body.id && !project) {
      return true;
    }
    const nextProject = project ? updateProject(project, body) : createProject(body);
    const saved = await storage.saveProject(nextProject);
    sendJson(res, project ? 200 : 201, { project: saved });
    return true;
  }

  const projectParams = routeMatch(pathname, '/api/projects/:id');
  if (projectParams && req.method === 'GET') {
    const project = await getProjectOr404(res, projectParams.id);
    if (project) {
      sendJson(res, 200, { project: hydrateProject(project) });
    }
    return true;
  }

  if (projectParams && req.method === 'DELETE') {
    const removed = await storage.removeProject(projectParams.id);
    sendJson(res, removed ? 200 : 404, { removed });
    return true;
  }

  const actionParams = routeMatch(pathname, '/api/projects/:id/:action');
  if (actionParams && req.method === 'POST') {
    const project = await getProjectOr404(res, actionParams.id);
    if (!project) {
      return true;
    }

    const actionHandlers = {
      analyze: analyzeProject,
      'generate-cases': designTestCases,
      run: executeProject,
      report: buildReport
    };

    const handler = actionHandlers[actionParams.action];
    if (!handler) {
      sendError(res, 404, 'Unknown project action.');
      return true;
    }

    const updated = await storage.saveProject(await handler(project));
    sendJson(res, 200, { project: updated });
    return true;
  }

  return false;
}

async function serveStatic(res, pathname) {
  const safePath = pathname === '/' ? '/index.html' : pathname;
  const requestedPath = path.normalize(decodeURIComponent(safePath)).replace(/^(\.\.[/\\])+/, '');
  const filePath = path.join(PUBLIC_DIR, requestedPath);

  if (!filePath.startsWith(PUBLIC_DIR)) {
    sendError(res, 403, 'Forbidden.');
    return;
  }

  try {
    const content = await fs.readFile(filePath);
    const ext = path.extname(filePath);
    res.writeHead(200, {
      'Content-Type': mimeTypes[ext] || 'application/octet-stream',
      'Cache-Control': 'no-store'
    });
    res.end(content);
  } catch {
    const fallback = await fs.readFile(path.join(PUBLIC_DIR, 'index.html'));
    res.writeHead(200, {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-store'
    });
    res.end(fallback);
  }
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);
    if (url.pathname.startsWith('/api/')) {
      const handled = await handleApi(req, res, url.pathname);
      if (!handled) {
        sendError(res, 404, 'API route not found.');
      }
      return;
    }

    await serveStatic(res, url.pathname);
  } catch (error) {
    sendError(res, 500, error.message);
  }
});

server.listen(PORT, () => {
  console.log(`aut_agent platform is running at http://localhost:${PORT}`);
});
