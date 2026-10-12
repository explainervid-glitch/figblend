// Stamps the project version and the GitHub repository into every file that
// carries a copy, so a release is one command instead of six edits that have
// to agree.
//
// The root package.json is the source of truth:
//   "version"          -> the version every piece ships as
//   "repository".url   -> where releases live, and where updates are checked
//
// Usage:
//   node tools/bump.mjs 0.3.0    set a new version, then stamp everything
//   node tools/bump.mjs          re-stamp only (after editing "repository")

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function fail(message) {
  console.error(`\n  error: ${message}\n`);
  process.exit(1);
}

export function readProject() {
  const file = path.join(ROOT, 'package.json');
  const pkg = JSON.parse(fs.readFileSync(file, 'utf8'));
  const url = typeof pkg.repository === 'string'
    ? pkg.repository : (pkg.repository && pkg.repository.url) || '';
  const match = /github\.com[/:]([\w.-]+)\/([\w.-]+?)(?:\.git)?\/?$/.exec(url);
  if (!match) fail(`"repository" in package.json is not a GitHub URL: ${url || '(empty)'}`);
  return { file, pkg, version: pkg.version, repo: `${match[1]}/${match[2]}` };
}

export function isPlaceholder(repo) {
  return repo === 'OWNER/REPO';
}

// Each target names exactly one line. A pattern that matches zero times or
// more than once means the file has drifted from what this script expects,
// which is worth stopping for rather than half-stamping.
function targets(version, repo) {
  const [major, minor, patch] = version.split('.');
  return [
    ['blender-addon/figblend/blender_manifest.toml', [
      [/^version = ".*"$/m, `version = "${version}"`],
      [/^website = ".*"$/m, `website = "https://github.com/${repo}"`],
    ]],
    ['blender-addon/figblend/__init__.py', [
      [/"version": \(\d+, \d+, \d+\)/, `"version": (${major}, ${minor}, ${patch})`],
    ]],
    ['blender-addon/figblend/updater.py', [
      [/^GITHUB_REPO = ".*"$/m, `GITHUB_REPO = "${repo}"`],
    ]],
    ['figma-plugin/src/code.ts', [
      [/^const PLUGIN_VERSION = '.*';$/m, `const PLUGIN_VERSION = '${version}';`],
      [/^const GITHUB_REPO: string = '.*';$/m, `const GITHUB_REPO: string = '${repo}';`],
    ]],
    ['figma-plugin/package.json', [
      [/^  "version": ".*",$/m, `  "version": "${version}",`],
    ]],
  ];
}

function stamp(version, repo) {
  const changed = [];
  for (const [relative, rules] of targets(version, repo)) {
    const file = path.join(ROOT, relative);
    const original = fs.readFileSync(file, 'utf8');
    // Matched as LF and written back in the file's own line endings: a
    // Windows checkout with autocrlf has CRLF, where `$` never matches before
    // the \r and every pattern would miss.
    const crlf = original.includes('\r\n');
    const before = crlf ? original.replace(/\r\n/g, '\n') : original;
    let after = before;
    for (const [pattern, replacement] of rules) {
      const global = new RegExp(pattern.source, pattern.flags + 'g');
      const hits = (after.match(global) || []).length;
      if (hits !== 1) {
        fail(`${relative}: expected one match for ${pattern}, found ${hits}`);
      }
      after = after.replace(pattern, replacement);
    }
    if (after !== before) {
      fs.writeFileSync(file, crlf ? after.replace(/\n/g, '\r\n') : after);
      changed.push(relative);
    }
  }
  return changed;
}

const isMain = process.argv[1]
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  const requested = process.argv[2];
  const project = readProject();

  if (requested) {
    if (!/^\d+\.\d+\.\d+$/.test(requested)) {
      fail(`version must be X.Y.Z, got "${requested}"`);
    }
    // Rewritten by hand rather than JSON.stringify, to leave the rest of the
    // file's formatting alone.
    const text = fs.readFileSync(project.file, 'utf8');
    const updated = text.replace(/^  "version": ".*",(\r?)$/m, `  "version": "${requested}",$1`);
    if (updated === text && project.version !== requested) {
      fail('could not find "version" in package.json');
    }
    fs.writeFileSync(project.file, updated);
    project.version = requested;
  }

  const changed = stamp(project.version, project.repo);
  console.log(`\n  version     ${project.version}`);
  console.log(`  repository  ${project.repo}${isPlaceholder(project.repo)
    ? '   (placeholder -- update checks stay off until this is set)' : ''}`);
  console.log(changed.length
    ? `  stamped     ${changed.join('\n              ')}\n`
    : '  stamped     nothing to change\n');
}
