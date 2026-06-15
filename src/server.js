const http = require('http');
const fs = require('fs/promises');
const path = require('path');
const { URL } = require('url');
const storage = require('./storage');
const {
  createProject,
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
  '.svg': 'image/svg+xml',
  '.png': 'image/png'
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
    const projects = await storage.listProjects();
    sendJson(res, 200, { projects });
    return true;
  }

  if (req.method === 'POST' && pathname === '/api/projects') {
    const body = await readJson(req);
    const project = await storage.saveProject(createProject(body));
    sendJson(res, 201, { project });
    return true;
  }

  const projectParams = routeMatch(pathname, '/api/projects/:id');
  if (projectParams && req.method === 'GET') {
    const project = await getProjectOr404(res, projectParams.id);
    if (project) {
      sendJson(res, 200, { project });
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
