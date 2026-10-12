// Where the version stands: what the sources say, whether every copy agrees,
// and whether this version has already been released.
//
// The release question is the useful one. A version that is already out has
// to be bumped before the next release, or the tag push is refused and no one
// is offered an update.
//
// Usage: node tools/version.mjs [--offline]
// Exits 1 when the copies disagree, so it can gate a commit hook or CI.

import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { isPlaceholder, readProject, stampedRepos, stampedVersions } from './bump.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const offline = process.argv.includes('--offline');

const project = readProject();
const tag = `v${project.version}`;
const pad = (text, width) => String(text).padEnd(width);

console.log(`\n  FigBlend ${project.version}   ${project.repo}\n`);

let drift = 0;
const rows = [
  ...Object.entries(stampedVersions()).map(([where, value]) => [where, value, project.version]),
  ...Object.entries(stampedRepos()).map(([where, value]) => [where, value, project.repo]),
];
for (const [where, value, expected] of rows) {
  const ok = value === expected;
  if (!ok) drift += 1;
  console.log(`  ${pad(where, 28)}${pad(value, 32)}${ok ? 'ok' : 'MISMATCH'}`);
}

// --- release state -----------------------------------------------------------

function git(...args) {
  const result = spawnSync('git', args, { cwd: ROOT, encoding: 'utf8' });
  return result.status === 0 ? result.stdout.trim() : null;
}

const localTag = git('tag', '--list', tag);
const latestTag = git('describe', '--tags', '--abbrev=0', '--match', 'v[0-9]*');

async function latestRelease() {
  if (offline || isPlaceholder(project.repo)) return { skipped: true };
  try {
    const res = await fetch(`https://api.github.com/repos/${project.repo}/releases/latest`, {
      headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'FigBlend-version-check' },
      signal: AbortSignal.timeout(6000),
    });
    if (!res.ok) {
      await res.body?.cancel();  // release the socket rather than leave it open
      return res.status === 404 ? { tag: null } : { error: `GitHub answered ${res.status}` };
    }
    return { tag: (await res.json()).tag_name };
  } catch (err) {
    return { error: 'GitHub unreachable' };
  }
}

const release = await latestRelease();

console.log('');
console.log(`  ${pad('git tag ' + tag, 28)}${localTag ? 'exists' : 'not created yet'}`);
console.log(`  ${pad('latest tag in this repo', 28)}${latestTag || 'none'}`);
console.log(`  ${pad('latest GitHub release', 28)}${
  release.skipped ? (offline ? 'skipped (--offline)' : 'skipped (placeholder repository)')
    : release.error ? release.error
      : release.tag || 'none published yet'}`);

// --- what to do next ---------------------------------------------------------

const [major, minor, patch] = project.version.split('.').map(Number);
const next = `${major}.${minor}.${patch + 1}`;
let advice;

if (drift) {
  advice = `${drift} file(s) disagree with package.json. Run: npm run bump`;
} else if (release.tag === tag) {
  advice = `${project.version} is released. Before the next release: npm run bump ${next}`;
} else if (localTag && !release.skipped && !release.error) {
  advice = `${tag} is tagged but not published. If the tag is pushed, check the Actions tab;`
    + ` otherwise: git push origin main ${tag}`;
} else if (localTag) {
  advice = `${tag} is tagged. Make sure it is pushed: git push origin main ${tag}`;
} else {
  advice = `${project.version} is not released yet. To release it:`
    + ` git commit -am "Release ${project.version}", then git tag ${tag}`;
}
console.log(`\n  ${advice}\n`);

// exitCode, not exit(): on Windows, exiting while fetch still holds its socket
// trips a libuv assertion and the process dies with 127 instead of our code.
process.exitCode = drift ? 1 : 0;
