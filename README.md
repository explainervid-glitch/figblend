# FigBlend

Move Figma UI into Blender with editable text, intact hierarchy and shader
controls.

| Part | Folder |
|---|---|
| Figma plugin — exports your selection | [`figma-plugin/`](figma-plugin) |
| Blender addon — rebuilds it as a scene | [`blender-addon/`](blender-addon) |
| Bundle format both sides share | [`SCHEMA.md`](SCHEMA.md) |

---

## Install

You need **Blender 4.2 or newer** and the **Figma desktop app** (the browser
version can't load FigBlend).

### Blender

**Recommended — updates itself.** Do this once:

1. Open Blender → **Edit → Preferences**.
2. **System** tab → **Network** → tick **Allow Online Access**.
3. **Get Extensions** tab → click **Repositories** (top right) → **+** →
   **Add Remote Repository**.
4. Paste this URL:
   ```
   https://github.com/explainervid-glitch/figblend/releases/latest/download/index.json
   ```
5. Tick **Check for Updates on Startup**, then confirm the dialog.
6. Search **FigBlend** in Get Extensions → **Install**.
7. Close Preferences. In the 3D viewport press `N` — a **FigBlend** tab appears
   in the sidebar.

Updates: when one is out, the FigBlend tab shows **Update to x.y.z**. Click it,
then **Update** on the FigBlend entry.

**Alternative — from a file (no auto-update).**

1. Download `figblend-addon-<version>.zip` from the
   [latest release](https://github.com/explainervid-glitch/figblend/releases/latest).
2. **Edit → Preferences → Get Extensions** → **⌄** (top right) → **Install from
   Disk…** → pick the zip.

### Figma

1. Download `figblend-plugin-<version>.zip` from the
   [latest release](https://github.com/explainervid-glitch/figblend/releases/latest).
2. Unzip it into a folder you'll keep (for example `Documents/FigBlend`).
   Figma loads the plugin from there every time, so don't delete or move it.
3. Open the **Figma desktop app** and any design file.
4. Main menu → **Plugins → Development → Import plugin from manifest…**
5. Pick `manifest.json` inside the unzipped folder.
6. Run it: **Plugins → Development → FigBlend**.

Updates: when one is out, a blue version number appears at the top of the
plugin. Click it, download the new zip, and unzip it **over the same folder**,
replacing the files. No need to import again.

The gear at the top right opens Settings: the plugin's version, a **Blender
add-on** button that downloads the add-on of that same version, and the full
log of exports, sends and warnings.

### Connect them (first time)

1. In Blender, **save** your `.blend` file — assets are stored next to it.
2. In the FigBlend tab, click **Start Live Link**.
3. Open the FigBlend plugin in Figma within 5 minutes. The pill at the top shows
   your `.blend` name when connected.

If the plugin says it isn't paired, click the refresh icon next to the Live
Link status in Blender, then reopen the plugin.

---

## Use

1. In Blender, save the `.blend`, open the **FigBlend** sidebar tab (`N`),
   click **Start Live Link**.
2. In Figma, select a frame, run FigBlend, pick a mode, click **Send to
   Blender**.

No Live Link? Click **Export** instead, then **File → Import → FigBlend Bundle**
in Blender.

| Mode | Result |
|---|---|
| Vector | Curves and editable text. Gradients fall back to images |
| Texturized | Curves wearing a render of themselves. Keeps gradients and effects |
| Image | Every element a textured plane, text included |

---

## Development

### Requirements

Node.js 18+, Blender 4.2+, Figma desktop app.

### Setup

```bash
git clone https://github.com/explainervid-glitch/figblend.git
```

```bash
npm install --prefix figblend/figma-plugin
```

Link the addon into Blender so edits are live. Remove any installed FigBlend
first — two copies clash.

Windows (PowerShell, from the repo root):

```powershell
New-Item -ItemType Junction -Path "$env:APPDATA\Blender Foundation\Blender\5.2\scripts\addons\figblend" -Target "$PWD\blender-addon\figblend"
```

macOS:

```bash
ln -s "$PWD/blender-addon/figblend" "$HOME/Library/Application Support/Blender/5.2/scripts/addons/figblend"
```

Then enable **FigBlend** in **Preferences → Add-ons**. In Figma, import
`figma-plugin/manifest.json` once.

### Edit loop

| Change | Do |
|---|---|
| Addon `.py` | Blender: `F3` → **Reload Scripts** |
| Plugin `src/code.ts` | Keep `npm run watch --prefix figma-plugin` running, re-run plugin (`Ctrl+Alt+P`) |
| Plugin `src/ui.html` | Re-run plugin |

Logs: Blender **Window → Toggle System Console**; Figma **Plugins →
Development → Show/Hide console**.

Test without Figma: import any bundle in [`samples/`](samples).

### Before committing

```bash
npm run package
```

Compiles the plugin and builds `dist/`. Fails on a TypeScript error or a
version mismatch.

---

## Release

One version covers both the addon and the plugin. Never edit it by hand.

**1. Check the current version:**

```bash
npm run version:check
```

Shows the version, whether every file agrees, and whether it's already
released.

**2. Bump it** (must be higher than the last release):

```bash
npm run bump 0.2.1
```

**3. Commit:**

```bash
git commit -am "Release 0.2.1"
```

**4. Tag** (`v` + exact version):

```bash
git tag v0.2.1
```

**5. Push:**

```bash
git push origin main v0.2.1
```

GitHub Actions builds and publishes the release. Blender offers the update on
next start; the plugin within a day.

Troubleshooting and details: [RELEASING.md](RELEASING.md).
