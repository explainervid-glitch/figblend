# Figma UI → Blender

Move Figma UI into Blender with editable text, intact hierarchy, and shader
controls — instead of rebuilding it by hand.

Two halves that share one file format:

| Piece | What it is | Where |
|---|---|---|
| Figma plugin | Walks your selection, emits a `.zip` bundle | [`figma-plugin/`](figma-plugin) |
| Blender addon | Imports the bundle, rebuilds the scene | [`blender-addon/`](blender-addon) |
| The contract | Versioned JSON both sides agree on | [`SCHEMA.md`](SCHEMA.md) |

Everything else follows from the schema. Neither side knows anything about the
other beyond it.

## Status

**v0.1 — imports end to end in Blender 5.2 LTS.**

Verified:

- Full import into Blender 5.2: hierarchy, parenting, text objects, image
  planes, materials, and the sidebar controls.
- Every object lands at its expected world position (Y-flip, px→m, Z stacking).
- Text rebuilt as real FONT objects with correct font, size, alignment,
  letter spacing and line height.
- Font resolution against a real Windows collection: 177 families / 516 faces
  indexed in 0.6 s, correct on `Arial Bold Italic`, `Segoe UI Semi Bold`,
  `Roboto Medium`, `Calibri Light`; substitutions reported rather than silent.
- Sidebar controls: split material, emission↔BSDF blend (isolated to the split
  material), shadow toggles.
- Figma plugin typechecks and builds clean (`tsc`).
- ZIP writer output reads correctly in Python's `zipfile` with valid CRCs.
- Bundle validation: schema gating, missing manifest, corrupt zip, and zip-slip
  path traversal all rejected.

Not yet verified: **a real Figma export.** The importer has only been fed the
synthetic bundle in [`samples/`](samples). The exporter's node walk, rasterizing
and backdrop-cloning have not run against a live Figma document.

## Test it without Figma

[`samples/make_test_bundle.py`](samples/make_test_bundle.py) generates a bundle
that exercises every code path — nested groups, backdrops, transparency,
LEFT/TOP and CENTER/CENTER text, negative letter spacing, an explicit line
height, a rotated leaf, and a deliberately missing font weight.

```bash
python samples/make_test_bundle.py
```

Then import `samples/test_bundle.zip` in Blender. You should get 11 objects, and
a warning that `Inter Semi Bold` was substituted — that warning is correct
behaviour, not a failure.

It also writes `samples/test_bundle_flat.zip`, the flat-mode equivalent: one
plane, one texture, no warnings.

## Install

### Blender addon

Blender 4.2 LTS or newer.

1. Zip the inner `figblend/` folder (the one containing `__init__.py`).
2. Preferences → Add-ons → Install from Disk → pick the zip → enable it.

You get **File → Import → Figma UI Bundle (.zip)** and a **Figma UI** tab in the
3D viewport sidebar (`N`).

### Figma plugin

Needs the **Figma desktop app** — plugins in development can only be loaded from
disk there, not in the browser.

```bash
cd figma-plugin
npm install
npm run build
```

Then in the desktop app: menu → Plugins → Development → Import plugin from
manifest → pick `figma-plugin/manifest.json`.

Re-run `npm run build` after editing `src/code.ts` (or `npm run watch` to
rebuild on save), then re-run the plugin in Figma to pick up changes. Editing
`src/ui.html` needs no build step.

## Use it

1. Select a frame or component set in Figma.
2. Run the plugin, pick an export mode, set pixels-per-metre and raster quality,
   hit **Export**. A `.zip` downloads.
3. In Blender, **File → Import → Figma UI Bundle**, pick the zip.

### Live Link — skip the file entirely

With Live Link on, the plugin detects Blender and sends the export straight in.
No download, no File → Import.

1. Save your `.blend` (assets are written beside it, so it needs a home).
2. Sidebar → **Figma UI** → **Start Live Link**.
3. The plugin's header pill turns green and reads **Blender**; the button
   becomes **Send to Blender**.

Assets land in **`./figAssets`** next to the `.blend`, and image paths are
rewritten blend-relative (`//figAssets/…`) so the folder and the file stay
portable together.

The pill also reports problems: **Offline** means no Blender is listening, and
**Unsaved** means Blender is there but the `.blend` has not been saved, so
there is nowhere to put `figAssets`. Click the pill to re-check. If a send ever
fails, the plugin falls back to downloading the zip rather than losing the work.

**Scope of the connection:** the server binds `127.0.0.1` only, is off until you
start it, and stops when you disable the addon. Nothing leaves the machine — the
plugin manifest allows exactly `localhost:8787` and nothing else. It does mean
any local process could post a bundle while it is running, so leave it off when
you are not using it.

### Export options

The UI always comes across structured — hierarchy, parenting and positions
intact. One three-way choice decides what the individual elements become.

| Mode | Geometry | Appearance | Editable |
|---|---|---|---|
| **Vector** | real curves | flat colours from the SVG | yes — curves and text |
| **Texturized** | real curves | a render of the node, mapped on | no, but the shape is |
| **Image** | flat planes | a render of the node | no |

**Vector** keeps everything editable, but only solid fills survive — gradients
and effects force a shape back to a plane.

**Texturized** is the middle ground: the silhouette is still curve geometry, so
it stays sharp at any distance and can be extruded or bevelled, while gradients,
shadows and anything else the plain vector route loses survive in the texture.
Gradients no longer disqualify a shape from being geometry. Text stays editable.

**Image** bakes everything including text, so no fonts need installing and it
matches Figma exactly. Reach for it when the UI is background dressing.

Raster quality means something different per mode: in Vector it only affects the
few rasterized leftovers; in Texturized and Image it is the resolution of every
surface, so raise it for close-ups.

**Texturized pairs with Build 3D Geometry** on the Blender side. Because the
shapes arrive as curves, the importer can extrude them and stack the layers
flush — each layer's back face landing exactly on the one below, in Figma's
layer order. That turns a flat design into a solid, relightable model in one
import.

The importer unpacks assets to `<bundle-name>_assets/` next to the zip, so keep
the two together — or leave **Pack Images** on and the PNGs are embedded in the
`.blend`.

### Import options

| Option | Does what |
|---|---|
| Pixels per Metre | Overrides the bundle's scale. 1000 makes a 1440 px frame 1.44 m wide |
| Layer Spacing | Gap between stacking levels, default 0.1 mm. Every layer gets a step, so depth accumulates with design size — raise for parallax, drop toward 0 for flat. Ignored when Build 3D Geometry is on |
| Build 3D Geometry | Extrudes every element on import and stacks the layers flush in Figma's layer order. Meant for **Texturized** exports, where the shapes are curves |
| Thickness | Total depth of each layer when building geometry. It also becomes the layer spacing, so the stack has no gaps and no overlap |
| Collections | **Collection per Group** (default) gives every frame and group its own nested collection, mirroring the Figma tree in the outliner. Collections that end up empty are pruned. **One Collection** puts everything in a single collection instead |
| Groups | **Merge Into Background** (default) makes a frame's background the group object itself, and collapses frames that have none. **Empty per Frame** keeps the Figma tree exactly, with more objects |
| Shader Mode | 0 = flat emission (self-lit UI), 1 = Principled BSDF (reacts to lights) |
| Disable Shadows on Text | On by default — text shadowing its own background is the usual cause of muddy UI renders |
| Pack Images | Embed PNGs so the `.blend` survives being moved |

### After import

The sidebar panel works on the current selection:

- **Shading** — drag the emission/BSDF blend and apply it. **Split Off Material**
  first if you only want a few objects to change; imported objects share
  materials by design.
- **Layer sorting** — **Dithered** (the default) or **Blended** transparency for
  every imported material. If elements vanish or draw in the wrong order when
  you orbit away from a straight-on view, you are on Blended; switch to
  Dithered. See below.
- **Shadows** — flip shadow casting on or off per selection, and
  **Select Shapes Needing Light** grabs every shape whose Figma shadow was
  discarded on the way in.
- **Fonts** — **Rescan Installed Fonts** after installing a font the importer
  reported as missing.

## How it decides what to keep

- **Text** stays a real Blender FONT object — editable, re-fontable, animatable.
- **Strokes and borders** are outlined into filled paths before export, so
  outlined buttons, dividers and lines come through as geometry instead of flat
  images. Blender's SVG importer only reads fills, so a raw stroke would vanish.
- **Solid shapes** export as SVG and rebuild as real curves —
  resolution-independent, so cards and icons stay sharp however close the camera
  gets, and they can be extruded or bevelled like any other curve. Shadows and
  blurs on those shapes are dropped rather than forcing a raster: real lights
  do that job better in 3D.
- **Masked groups** — a photo in a circle, say — come through as the mask shape
  in geometry with the photo mapped onto it. The silhouette is real curve
  geometry, not a transparent cutout, so it stays clean close up.
- **Frames, groups, components** become parents. A frame's own background *is*
  the group object, so moving a card moves its contents without an extra Empty
  in between.
- **Everything else** — gradients, images, strokes, and anything with effects —
  is rasterized to PNG at your chosen scale.

Those middle two lines are the product decision. Rasterize everything and this
is just the flat texture importer that already exists; rasterize nothing and you
are writing a Figma rendering engine in shader nodes. The line sits at: text,
structure, and shapes that are nothing but solid fills.

## Why transparency is Dithered, not Blended

A UI is dozens of planes stacked a fraction of a millimetre apart. EEVEE's
*blended* transparency renders in a forward pass that does **not write depth**,
so those surfaces are ordered by a single per-object sort. Dead-on that sort
happens to be right; the moment you orbit, it flips — and a card draws over its
own contents, so text and icons appear to vanish.

*Dithered* writes depth, so occlusion is resolved per pixel and holds from any
angle. The cost is hashed alpha: grainy in the viewport at low sample counts,
but it resolves in a render, and hard-edged UI alpha barely shows it.

Blended is still one click away in the sidebar if you need genuinely coloured
transparency — tinted glass over the UI, say — and can live with the sorting.

## Known limitations

- **Frame overflow clipping is not applied.** `clipsContent` on a frame is
  ignored, so a child bleeding past its frame stays visible instead of being
  cut off. Layer masks (`isMask`) *are* handled — this is the separate case of
  a frame cropping its own children.
- **Complex masks flatten.** Image, luminance and nested masks fall back to a
  single flattened raster of the group. Correct-looking, but no longer geometry
  and no longer editable.
- **Figma effects are discarded on vector shapes.** A card with a drop shadow
  imports as clean geometry with no shadow — deliberately. Baked 2D shadows
  fight the camera in a 3D scene. Use **Select Shapes Needing Light** in the
  sidebar to grab everything that lost one and light it properly.
- **Gradients force a raster.** Blender's SVG importer ignores them, so a
  gradient-filled shape falls back to PNG rather than importing as a curve that
  quietly lost its gradient.
- **An outlined shape becomes two objects.** The fill and the border are
  different colours, so they stay separate curves parented together. Moving the
  parent moves both; joining them would merge their fill regions and flood the
  shape with one colour.
- **Variable fonts.** Blender loads only a variable font's default instance, so
  a Semi Bold request renders at Regular. Inter ships this way, which makes it
  common rather than an edge case. The importer detects it and warns; installing
  the static weight fixes it.
- **Mixed styling inside one text box** is exported (`segments`) but not applied
  — Blender text objects have no per-character font support without splitting
  into separate objects.
- **Text metrics are approximate.** Letter spacing and line height are mapped to
  Blender's multipliers; long paragraphs drift. See the bottom of
  [`SCHEMA.md`](SCHEMA.md).
- **Auto-layout** is baked into positions, not reconstructed.
- **Skew** is discarded; only rotation survives.
- Justified text falls back to left-aligned.

## Next

- Run a real import and fix what breaks.
- Calibrate text metrics against a reference frame.
- Corner radii and solid fills as geometry rather than raster, so cards stay
  crisp under close-up camera moves.
