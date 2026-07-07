const fs = require('fs/promises');
const path = require('path');

const ROOT_DIR = path.join(__dirname, '..');
const DEFAULT_STAGE2_DIR = path.join(ROOT_DIR, 'artifacts', 'stage2');
const SAFE_ID_PATTERN = /^[A-Za-z0-9_-]{1,120}$/;

// menu/page/feature/execution_goal — the four goal-loop packages under
// prototype/stage2/app/. Each writes its own run directory kind under
// artifacts/stage2/{dirName}/<runId>/ and (as of this dashboard visibility
// fix) a run_summary.json with the same schema-shape (get_summary() +
// generated_at). anchorFile marks a directory as a real run even if
// run_summary.json is missing (e.g. an older run predating this fix).
const GOAL_KINDS = {
  menu: {
    dirName: 'menu_goal_runs',
    label: '菜单发现',
    anchorFile: 'menu_entries.json',
    artifactKeys: {
      run_summary: 'run_summary.json',
      menu_entries: 'menu_entries.json',
      menu_entries_raw: 'menu_entries_raw.json'
    }
  },
  page: {
    dirName: 'page_goal_runs',
    label: '页面发现',
    anchorFile: 'page_entries.json',
    artifactKeys: {
      run_summary: 'run_summary.json',
      page_entries: 'page_entries.json',
      exploration_log: 'page_exploration_log.jsonl',
      screenshots_index: 'screenshots_index.json'
    }
  },
  feature: {
    dirName: 'feature_goal_runs',
    label: '功能点发现',
    anchorFile: 'feature_points.json',
    artifactKeys: {
      run_summary: 'run_summary.json',
      feature_points: 'feature_points.json',
      generated_test_cases: 'generated_test_cases.json',
      discovery_review: 'discovery_review.json'
    }
  },
  execution: {
    dirName: 'execution_goal_runs',
    label: '执行',
    anchorFile: 'execution_results.json',
    artifactKeys: {
      run_summary: 'run_summary.json',
      execution_results: 'execution_results.json',
      action_log: 'action_log.jsonl',
      network_events: 'network_events.json',
      screenshots_index: 'screenshots_index.json',
      human_tasks: 'human_tasks.json',
      human_takeover: 'human_takeover.json',
      human_takeover_resolution: 'human_takeover_resolution.json',
      round_analysis: 'round_analysis.json',
      next_round_plan: 'next_round_plan.json',
      run_report_json: path.join('reports', 'run_report.json'),
      run_report_md: path.join('reports', 'run_report.md')
    }
  }
};

async function pathExists(targetPath) {
  try {
    await fs.access(targetPath);
    return true;
  } catch {
    return false;
  }
}

async function readJsonIfExists(filePath) {
  try {
    const raw = await fs.readFile(filePath, 'utf8');
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function getStage2Dir(options = {}) {
  return options.stage2Dir || DEFAULT_STAGE2_DIR;
}

function getKindDir(kind, options = {}) {
  const kindConfig = GOAL_KINDS[kind];
  if (!kindConfig) {
    return null;
  }
  return path.join(getStage2Dir(options), kindConfig.dirName);
}

function isWithinRoot(filePath, rootPath) {
  const resolvedRoot = path.resolve(rootPath);
  const resolvedPath = path.resolve(filePath);
  return resolvedPath === resolvedRoot || resolvedPath.startsWith(`${resolvedRoot}${path.sep}`);
}

async function listRunDirNames(kind, options = {}) {
  const kindDir = getKindDir(kind, options);
  if (!kindDir) {
    return [];
  }
  let entries;
  try {
    entries = await fs.readdir(kindDir, { withFileTypes: true });
  } catch {
    return [];
  }
  return entries
    .filter((entry) => entry.isDirectory() && SAFE_ID_PATTERN.test(entry.name))
    .map((entry) => entry.name)
    .sort((a, b) => b.localeCompare(a));
}

function buildArtifactHref(kind, runId, artifactKey) {
  return `/api/stage2/goal-loop/${encodeURIComponent(kind)}/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactKey)}`;
}

async function readGoalLoopRunDirectory(kind, runId, options = {}) {
  const kindConfig = GOAL_KINDS[kind];
  const kindDir = getKindDir(kind, options);
  if (!kindConfig || !kindDir) {
    return null;
  }

  const runDir = path.join(kindDir, runId);
  const anchorPath = path.join(runDir, kindConfig.anchorFile);
  if (!(await pathExists(anchorPath))) {
    return null;
  }

  const [summary, stat] = await Promise.all([
    readJsonIfExists(path.join(runDir, 'run_summary.json')),
    fs.stat(runDir).catch(() => null)
  ]);

  const availableArtifactKeys = [];
  await Promise.all(
    Object.entries(kindConfig.artifactKeys).map(async ([key, relativePath]) => {
      if (await pathExists(path.join(runDir, relativePath))) {
        availableArtifactKeys.push(key);
      }
    })
  );

  return {
    kind,
    kindLabel: kindConfig.label,
    runId,
    summaryMissing: !summary,
    generatedAt: summary?.generated_at || null,
    updatedAt: summary?.generated_at || stat?.mtime?.toISOString() || null,
    summary: summary || null,
    artifacts: availableArtifactKeys.sort().map((key) => ({
      key,
      fileName: path.basename(kindConfig.artifactKeys[key]),
      href: buildArtifactHref(kind, runId, key)
    }))
  };
}

async function listGoalLoopRuns(options = {}) {
  const kinds = Object.keys(GOAL_KINDS);
  const result = {};

  await Promise.all(
    kinds.map(async (kind) => {
      const runIds = await listRunDirNames(kind, options);
      const runs = (await Promise.all(runIds.map((runId) => readGoalLoopRunDirectory(kind, runId, options))))
        .filter(Boolean);
      result[kind] = runs;
    })
  );

  return result;
}

async function resolveGoalLoopRunArtifact(kind, runId, artifactKey, options = {}) {
  const kindConfig = GOAL_KINDS[kind];
  if (!kindConfig) {
    return null;
  }
  if (!SAFE_ID_PATTERN.test(String(runId || ''))) {
    return null;
  }
  const relativePath = kindConfig.artifactKeys[artifactKey];
  if (!relativePath) {
    return null;
  }

  const kindDir = getKindDir(kind, options);
  const runDir = path.join(kindDir, runId);
  const filePath = path.join(runDir, relativePath);

  if (!isWithinRoot(filePath, kindDir)) {
    return null;
  }
  if (!(await pathExists(filePath))) {
    return null;
  }

  return {
    key: artifactKey,
    kind,
    fileName: path.basename(filePath),
    href: buildArtifactHref(kind, runId, artifactKey),
    path: filePath
  };
}

module.exports = {
  GOAL_KINDS,
  listGoalLoopRuns,
  resolveGoalLoopRunArtifact
};
