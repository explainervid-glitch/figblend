# Releasing FigBlend

A release is a git tag. Pushing `v0.3.0` makes GitHub Actions build everything
and publish it as a GitHub release with three files:

| File | What it is |
|---|---|
| `figblend-addon-0.3.0.zip` | The Blender extension |
| `figblend-plugin-0.3.0.zip` | The Figma plugin |
| `index.json` | The extension repository listing Blender reads to find updates |

Both halves check the **latest** release for updates, so publishing one is what
ships an update to everyone.

---

## One-time setup

### 1. Create the GitHub repository

Make it **public**. Blender and the plugin check for updates without logging
in, and GitHub hides a private repository's releases from anonymous requests,
so a private repository gets no updates at all.

### 2. Point the project at it

In the root `package.json`, replace the placeholder:

```json
"repository": {
  "type": "git",
  "url": "https://github.com/YOUR-NAME/YOUR-REPO"
}
```

Then stamp it into the addon and the plugin:

```bash
npm run bump
```

Until this is done, both update checks stay switched off, and a release build
refuses to run.

### 3. Push

```bash
git remote add origin https://github.com/YOUR-NAME/YOUR-REPO.git
```

```bash
git add -A
```

```bash
git commit -m "Set release repository"
```

```bash
git push -u origin main
```

Nothing else to configure. The workflow in `.github/workflows/release.yml`
asks for the one permission it needs (`contents: write`) itself.

---

## Every release

### 1. Set the version

```bash
npm run bump 0.3.0
```

This writes `0.3.0` into every file that carries a version: `package.json`,
`blender_manifest.toml`, the addon's `bl_info`, the plugin's `PLUGIN_VERSION`
and `figma-plugin/package.json`. Never edit those by hand — the build checks
they all agree and stops if one was missed.

Versions are `MAJOR.MINOR.PATCH`, and must go up: an update check only offers a
release that is newer than what is installed.

### 2. Check it builds

```bash
npm run package
```

Optional, but it catches a TypeScript error now instead of in CI.

### 3. Commit and tag

```bash
git commit -am "Release 0.3.0"
```

```bash
git tag v0.3.0
```

The tag is the version with a `v` in front. It has to match exactly, or the
workflow stops.

### 4. Push the commit and the tag

```bash
git push origin main v0.3.0
```

### 5. Watch it publish

Open the repository's **Actions** tab. The *Release* run takes about a minute.
When it is green, **Releases** shows `FigBlend 0.3.0` with the three files and
notes generated from the commits since the last release.

### 6. Confirm the update source

Open this in a browser — it should download the new `index.json`, with
`"version": "0.3.0"` inside:

```
https://github.com/YOUR-NAME/YOUR-REPO/releases/latest/download/index.json
```

Done. Blenders pick it up on their next start; the Figma plugin within a day.

---

## How people install and update

### Blender — once

1. **Edit → Preferences → System → Network**: tick **Allow Online Access**.
2. **Get Extensions** tab → **Repositories** (top right) → **+** →
   **Add Remote Repository**.
3. URL:
   ```
   https://github.com/YOUR-NAME/YOUR-REPO/releases/latest/download/index.json
   ```
   Tick **Check for Updates on Startup**. Leave *Requires Access Token* off.
4. Search **FigBlend** in Get Extensions → **Install**.

If FigBlend was installed before from a zip or copied into `scripts/addons`,
remove that copy first — two copies register the same operators and clash.

From then on Blender checks on every start. The FigBlend panel shows
**Update to 0.3.0** when there is one; it opens the Extensions page, where the
FigBlend entry has an **Update** button. Blender verifies the download against
the checksum in `index.json` before installing.

A copy installed with **Install from Disk** still gets told about updates, but
cannot apply them — Blender only updates extensions installed from a
repository. Its button opens the release page instead.

### Figma plugin

First time: unzip `figblend-plugin-<version>.zip` somewhere permanent, then in
the Figma desktop app **Plugins → Development → Import plugin from manifest** →
pick the `manifest.json` inside.

To update: when the plugin shows the blue version pill in its header, click it
for the release page, download the new plugin zip and **unzip it over the same
folder**. Figma reads the files fresh the next time the plugin runs; no
re-import.

A plugin loaded from disk cannot replace its own files — Figma only updates
plugins it distributes. For updates that install themselves, publish the
plugin through Figma: to the Community, or privately to your organisation on a
plan that supports it.

---

## When something goes wrong

**The workflow failed at "Build and package".** Read the step's error; it is
one of these, each saying what to run:

- tag does not match the version → you tagged without running `npm run bump`
- placeholder repository → one-time setup step 2 was skipped
- a file disagrees on the version → someone edited one by hand; `npm run bump`
- `tsc failed` → the plugin does not compile; `npm run package` shows why

Fix it, then move the tag to the fixed commit and push it again:

```bash
git tag -d v0.3.0
```

```bash
git push origin :refs/tags/v0.3.0
```

```bash
git tag v0.3.0
```

```bash
git push origin main v0.3.0
```

**The workflow failed at "Publish release" with "already exists".** A release
for that tag is already there. Delete it under **Releases** (not just the tag),
then push the tag again as above.

**Blender says there are no updates.** It checks on startup. To check now:
Get Extensions → the **⌄** menu just right of *Repositories* → **Refresh
Remote**, then **Install Available Updates** from the same menu. Also confirm
online access is on, and that FigBlend was installed *from the repository*.

**The FigBlend panel never shows an update.** Its check needs the repository to
be set (one-time step 2), online access on, and **Check for Updates** ticked in
FigBlend's add-on preferences. **Check Now** there shows the last result.

**The plugin never shows an update.** It checks at most once a day, so a release
may take up to a day to appear. It also stays silent while `GITHUB_REPO` is the
placeholder — that is set by `npm run bump`.
