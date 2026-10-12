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

Downloads: [latest release](https://github.com/explainervid-glitch/figblend/releases/latest).

### Blender addon (4.2+)

Install from the repository so Blender keeps it updated:

1. **Preferences → System → Network** → tick **Allow Online Access**.
2. **Get Extensions** → **Repositories** → **+** → **Add Remote Repository**.
3. URL:
   ```
   https://github.com/explainervid-glitch/figblend/releases/latest/download/index.json
   ```
   Tick **Check for Updates on Startup**.
4. Search **FigBlend** → **Install**.

### Figma plugin (desktop app)

1. Unzip `figblend-plugin-<version>.zip` somewhere permanent.
2. **Plugins → Development → Import plugin from manifest** → pick its
   `manifest.json`.

To update: click the blue version pill in the plugin header, download the new
zip, unzip it over the same folder.

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
