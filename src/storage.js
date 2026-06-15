const fs = require('fs/promises');
const path = require('path');

const DATA_DIR = path.join(__dirname, '..', 'data');
const STORE_FILE = path.join(DATA_DIR, 'store.json');

const initialStore = {
  projects: []
};

async function ensureStore() {
  await fs.mkdir(DATA_DIR, { recursive: true });
  try {
    await fs.access(STORE_FILE);
  } catch {
    await fs.writeFile(STORE_FILE, JSON.stringify(initialStore, null, 2), 'utf8');
  }
}

async function readStore() {
  await ensureStore();
  const raw = await fs.readFile(STORE_FILE, 'utf8');
  return JSON.parse(raw);
}

async function writeStore(store) {
  await ensureStore();
  await fs.writeFile(STORE_FILE, JSON.stringify(store, null, 2), 'utf8');
}

async function listProjects() {
  const store = await readStore();
  return store.projects.sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));
}

async function getProject(id) {
  const store = await readStore();
  return store.projects.find((project) => project.id === id) || null;
}

async function saveProject(project) {
  const store = await readStore();
  const index = store.projects.findIndex((item) => item.id === project.id);
  const next = {
    ...project,
    updatedAt: new Date().toISOString()
  };

  if (index >= 0) {
    store.projects[index] = next;
  } else {
    store.projects.push(next);
  }

  await writeStore(store);
  return next;
}

async function removeProject(id) {
  const store = await readStore();
  const before = store.projects.length;
  store.projects = store.projects.filter((project) => project.id !== id);
  await writeStore(store);
  return store.projects.length !== before;
}

module.exports = {
  listProjects,
  getProject,
  saveProject,
  removeProject
};
