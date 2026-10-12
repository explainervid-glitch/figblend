// Builds everything a release ships into dist/:
//
//   dist/figblend-addon-<version>.zip    a Blender extension: Install from
//                                        Disk, or served from the repository
//   dist/figblend-plugin-<version>.zip   unzip, then point Figma at the
//                                        manifest.json inside
//   dist/index.json                      the extension repository listing
//                                        Blender syncs to find updates
//
// Node rather than Python because this repo already needs Node for tsc, and
// rather than Compress-Archive because the archives need exact control over
// their internal paths -- Blender wants blender_manifest.toml at the root of an
// extension, and the Figma manifest refers to dist/code.js and src/ui.html by
// those names.
//
// Usage: node tools/package.mjs [--release] [--skip-build]
//                               [--addon-only] [--plugin-only]
//
// --release is what CI runs on a tag. It refuses to build unless the
// repository is configured, every version agrees, and the tag being released
// is that version -- the mistakes that otherwise only show up once users are
// pointed at a broken update.

import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { deflateRawSync } from 'node:zlib';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { isPlaceholder, readProject } from './bump.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DIST = path.join(ROOT, 'dist');
const PLUGIN_DIR = path.join(ROOT, 'figma-plugin');
const ADDON_DIR = path.join(ROOT, 'blender-addon');

const args = process.argv.slice(2);
const flag = (name) => args.includes(name);

function fail(message) {
  console.error(`\n  error: ${message}\n`);
  process.exit(1);
}

// --- zip writer --------------------------------------------------------------
// Deflated, with a correct central directory. No dependency worth adding for
// the handful of files involved.

function dosStamp(date) {
  const time = (date.getHours() << 11) | (date.getMinutes() << 5)
    | (date.getSeconds() >> 1);
  const day = ((date.getFullYear() - 1980) << 9) | ((date.getMonth() + 1) << 5)
    | date.getDate();
  return { time, day };
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
    table[i] = c >>> 0;
  }
  return table;
})();

function crc32(buffer) {
  let crc = 0xFFFFFFFF;
  for (let i = 0; i < buffer.length; i++) {
    crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ buffer[i]) & 0xFF];
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

function writeZip(target, entries) {
  const stamp = dosStamp(new Date());
  const chunks = [];
  const central = [];
  let offset = 0;

  for (const entry of entries) {
    // Zip paths are always forward-slashed, whatever the host platform.
    const name = Buffer.from(entry.name.split(path.sep).join('/'), 'utf8');
    const raw = entry.data;
    const deflated = deflateRawSync(raw, { level: 9 });
    // Storing is smaller than deflating for already-compressed or tiny files.
    const store = deflated.length >= raw.length;
    const body = store ? raw : deflated;
    const method = store ? 0 : 8;
    const crc = crc32(raw);

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);          // version needed
    local.writeUInt16LE(0x0800, 6);      // UTF-8 names
    local.writeUInt16LE(method, 8);
    local.writeUInt16LE(stamp.time, 10);
    local.writeUInt16LE(stamp.day, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(body.length, 18);
    local.writeUInt32LE(raw.length, 22);
    local.writeUInt16LE(name.length, 26);
    local.writeUInt16LE(0, 28);
    chunks.push(local, name, body);

    const header = Buffer.alloc(46);
    header.writeUInt32LE(0x02014b50, 0);
    header.writeUInt16LE(20, 4);         // version made by
    header.writeUInt16LE(20, 6);         // version needed
    header.writeUInt16LE(0x0800, 8);
    header.writeUInt16LE(method, 10);
    header.writeUInt16LE(stamp.time, 12);
    header.writeUInt16LE(stamp.day, 14);
    header.writeUInt32LE(crc, 16);
    header.writeUInt32LE(body.length, 20);
    header.writeUInt32LE(raw.length, 24);
    header.writeUInt16LE(name.length, 28);
    header.writeUInt32LE(0, 38);         // external attributes
    header.writeUInt32LE(offset, 42);
    central.push(header, name);

    offset += local.length + name.length + body.length;
  }

  const centralSize = central.reduce((sum, c) => sum + c.length, 0);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(offset, 16);

  const archive = Buffer.concat([...chunks, ...central, end]);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, archive);
  return archive.length;
}

// --- collecting files --------------------------------------------------------

const SKIP_DIRS = new Set(['__pycache__', '.git', 'node_modules']);

function collect(dir, prefix, entries = []) {
  for (const item of fs.readdirSync(dir, { withFileTypes: true }).sort(
    (a, b) => a.name.localeCompare(b.name))) {
    if (item.isDirectory()) {
      if (SKIP_DIRS.has(item.name)) continue;
      collect(path.join(dir, item.name), path.posix.join(prefix, item.name), entries);
    } else if (item.isFile()) {
      if (item.name.endsWith('.pyc')) continue;
      entries.push({
        name: path.posix.join(prefix, item.name),
        data: fs.readFileSync(path.join(dir, item.name)),
      });
    }
  }
  return entries;
}

// --- versions ----------------------------------------------------------------
// One project version, stamped into several files by `npm run bump`. Read back
// from every one of them here, so a hand edit that missed a copy fails the
// build instead of shipping a plugin that disagrees with its own release tag.

function readMatch(relative, pattern, what) {
  // LF-normalised, or $ would never match on a CRLF checkout.
  const source = fs.readFileSync(path.join(ROOT, relative), 'utf8')
    .replace(/\r\n/g, '\n');
  const match = source.match(pattern);
  if (!match) fail(`could not read ${what} from ${relative}`);
  return match.slice(1).join('.');
}

function stampedVersions() {
  return {
    'package.json': readProject().version,
    'blender_manifest.toml': readMatch('blender-addon/figblend/blender_manifest.toml',
      /^version = "(.*)"$/m, 'version'),
    '__init__.py bl_info': readMatch('blender-addon/figblend/__init__.py',
      /"version": \((\d+), (\d+), (\d+)\)/, 'bl_info version'),
    'code.ts': readMatch('figma-plugin/src/code.ts',
      /^const PLUGIN_VERSION = '(.*)';$/m, 'PLUGIN_VERSION'),
  };
}

function stampedRepos() {
  return {
    'updater.py': readMatch('blender-addon/figblend/updater.py',
      /^GITHUB_REPO = "(.*)"$/m, 'GITHUB_REPO'),
    'code.ts': readMatch('figma-plugin/src/code.ts',
      /^const GITHUB_REPO: string = '(.*)';$/m, 'GITHUB_REPO'),
  };
}

function checkConsistency() {
  const project = readProject();
  const drift = [];
  for (const [where, value] of Object.entries(stampedVersions())) {
    if (value !== project.version) drift.push(`${where} says ${value}`);
  }
  for (const [where, value] of Object.entries(stampedRepos())) {
    if (value !== project.repo) drift.push(`${where} points at ${value}`);
  }
  if (drift.length) {
    fail(`package.json is ${project.version} / ${project.repo}, but `
      + `${drift.join(', ')}. Run: npm run bump`);
  }
  return project;
}

// What CI is about to publish has to be what the sources say it is.
function checkRelease(project) {
  if (isPlaceholder(project.repo)) {
    fail('"repository" in package.json is still the OWNER/REPO placeholder');
  }
  const tag = process.env.GITHUB_REF_NAME;
  if (tag && tag !== `v${project.version}`) {
    fail(`releasing tag ${tag}, but the sources are v${project.version}. `
      + `Run npm run bump ${tag.replace(/^v/, '')}, commit, then tag again`);
  }
  const actualRepo = process.env.GITHUB_REPOSITORY;
  if (actualRepo && actualRepo.toLowerCase() !== project.repo.toLowerCase()) {
    fail(`building in ${actualRepo}, but package.json says ${project.repo} -- `
      + 'update checks would look in the wrong place');
  }
}

// --- extension repository ----------------------------------------------------
// blender_manifest.toml is read with a deliberately tiny parser: the file uses
// strings, arrays of strings and one table, all of which are valid JSON on
// the right of the `=`. Anything else is refused rather than guessed at.

function readManifest(file) {
  const result = {};
  let table = result;
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/);
  lines.forEach((raw, index) => {
    const line = raw.trim();
    if (!line || line.startsWith('#')) return;
    const section = /^\[([\w.-]+)\]$/.exec(line);
    if (section) {
      table = result[section[1]] = {};
      return;
    }
    const pair = /^([\w-]+)\s*=\s*(.+)$/.exec(line);
    let value;
    try {
      value = pair && JSON.parse(pair[2]);
    } catch (err) {
      value = undefined;
    }
    if (value === undefined || value === null) {
      fail(`blender_manifest.toml line ${index + 1} is beyond what this `
        + `script parses: ${line}`);
    }
    table[pair[1]] = value;
  });
  return result;
}

// The same shape Blender's own `extension server-generate` writes: the
// manifest's fields, plus where to download the archive and how to check it.
function writeIndex(addon, project, release) {
  const manifest = readManifest(path.join(ADDON_DIR, 'figblend', 'blender_manifest.toml'));
  const archive = fs.readFileSync(addon.target);
  const filename = path.basename(addon.target);

  // Absolute, at the tagged release, once there is a repository: the index
  // is fetched through /releases/latest/, and a relative link would follow
  // "latest" too -- so a sync that straddles a new release could pair this
  // listing's checksum with the next release's archive.
  const archiveUrl = isPlaceholder(project.repo) && !release
    ? `./${filename}`
    : `https://github.com/${project.repo}/releases/download/v${project.version}/${filename}`;

  const index = {
    version: 'v1',
    blocklist: [],
    data: [{
      ...manifest,
      archive_url: archiveUrl,
      archive_size: archive.length,
      archive_hash: 'sha256:' + createHash('sha256').update(archive).digest('hex'),
    }],
  };
  const target = path.join(DIST, 'index.json');
  fs.writeFileSync(target, JSON.stringify(index, null, 2) + '\n');
  return { target, bytes: fs.statSync(target).size, count: 1 };
}

// --- the two packages --------------------------------------------------------

function buildPlugin(version) {
  if (!flag('--skip-build')) {
    // The compiler is run through node against its own entry point, rather than
    // through npx: npx needs a shell on Windows, and spawning a .cmd without
    // one is refused outright by current Node.
    const tsc = path.join(PLUGIN_DIR, 'node_modules', 'typescript', 'bin', 'tsc');
    if (!fs.existsSync(tsc)) {
      fail('typescript is not installed -- run npm install in figma-plugin/');
    }
    const result = spawnSync(process.execPath, [tsc, '-p', 'tsconfig.json'],
                             { cwd: PLUGIN_DIR, stdio: 'inherit' });
    if (result.status !== 0) fail('tsc failed -- the plugin was not packaged');
  }

  // Exactly what the manifest refers to, at the paths it refers to. ui.html is
  // shipped from src/ because that is where "ui" points.
  const wanted = [
    ['manifest.json', 'manifest.json'],
    [path.join('dist', 'code.js'), 'dist/code.js'],
    [path.join('src', 'ui.html'), 'src/ui.html'],
  ];
  const entries = [];
  for (const [from, to] of wanted) {
    const source = path.join(PLUGIN_DIR, from);
    if (!fs.existsSync(source)) {
      fail(`${from} is missing -- run without --skip-build`);
    }
    entries.push({ name: to, data: fs.readFileSync(source) });
  }

  const target = path.join(DIST, `figblend-plugin-${version}.zip`);
  return { target, bytes: writeZip(target, entries), count: entries.length };
}

function buildAddon(version) {
  const source = path.join(ADDON_DIR, 'figblend');
  if (!fs.existsSync(source)) fail('blender-addon/figblend is missing');

  // Flat, manifest at the root: the layout Blender's own `extension build`
  // produces. Blender treats it as an extension -- and so as something its
  // extension system can update -- rather than as a legacy add-on.
  const entries = collect(source, '');
  for (const required of ['__init__.py', 'blender_manifest.toml']) {
    if (!entries.some((e) => e.name === required)) {
      fail(`the addon has no ${required} -- Blender would reject it`);
    }
  }

  const target = path.join(DIST, `figblend-addon-${version}.zip`);
  return { target, bytes: writeZip(target, entries), count: entries.length };
}

// --- run ---------------------------------------------------------------------

const kb = (bytes) => `${(bytes / 1024).toFixed(1)} KB`;
const release = flag('--release');

const project = checkConsistency();
if (release) checkRelease(project);

const built = [];
if (!flag('--plugin-only')) {
  const addon = buildAddon(project.version);
  built.push(['addon', addon]);
  built.push(['index', writeIndex(addon, project, release)]);
}
if (!flag('--addon-only')) built.push(['plugin', buildPlugin(project.version)]);

console.log('');
for (const [label, result] of built) {
  console.log(`  ${label.padEnd(7)} ${path.relative(ROOT, result.target)}`
    + `  (${result.count} file${result.count === 1 ? '' : 's'}, ${kb(result.bytes)})`);
}
console.log(`\n  version ${project.version}, repository ${project.repo}`
  + (isPlaceholder(project.repo)
    ? '\n  note: placeholder repository -- update checks are off in this build'
    : ''));
console.log('');
