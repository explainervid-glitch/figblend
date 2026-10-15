# UI Bundle Schema v1.1.0

The contract between the Figma plugin (writer) and the Blender addon (reader).
Both sides are just readers/writers of this format — nothing else is shared.

## Bundle layout

A `.zip` produced by the Figma plugin:

```
my-export.zip
├── bundle.json          # the manifest described below
└── assets/
    ├── 1_23.svg         # solid vector shapes, named <figma-id, ':' → '_'>
    ├── 1_47.png         # everything else, rasterized
    └── 1_12_bg.png      # a frame's own backdrop
```

## Conventions

These three decisions are load-bearing. Change them and every scene built with
the addon shifts.

### Units

`px_per_meter` (default **1000**) converts Figma pixels to Blender metres.
A 1440 px wide frame lands as a 1.44 m object. Chosen so a typical desktop UI is
a couple of metres across — comfortable for default camera clipping, lamp
falloff, and Blender's grid.

### Axes

Figma is Y-down, origin top-left. The UI is rebuilt flat in Blender's **XY
plane, facing +Z** (so a default camera on +Z looking down -Z sees it upright):

```
blender_x = figma_x * S
blender_y = -figma_y * S          # Y flips
blender_z = z_index * z_step      # stacking, see below
```

where `S = 1 / px_per_meter`.

The bundle carries a node's **centre**, already resolved into absolute page
space, because Blender mesh planes are centre-origin anyway:

```
location = (center.x * S, -center.y * S, z)
```

Centres come from Figma's `absoluteTransform`, not `absoluteBoundingBox`. The
bounding box is axis-aligned, so it reports the wrong size for anything rotated;
the matrix gives the true centre and the true size (`node.width/height`) for any
transform.

### Z-ordering

Figma is a flat painter's-algorithm stack — there is no depth, only draw order.
In `children`, **index 0 is the bottom** of the z-order and the last child is on
top (matching Figma's render order).

The exporter walks the tree depth-first in child order and stamps every node
with a monotonically increasing `z_index`. The importer multiplies it by
`z_step` (default 0.0001 m). This guarantees draw order is preserved and nothing
z-fights. The user can dial `z_step` up for a deliberately layered, parallax look
or down to near-flat.

Note that `z_index` counts *every* node, not tree depth, so total depth grows
with the size of the design — a 500-layer frame spans 500 × `z_step`. That is
why the step is small by default.

### Sending part of a frame

Positions are measured from the top-left of the **top frame** each selected node
belongs to (the frame sitting directly on the page or in a section), not from
the selection itself. Likewise a nested node's `z_index` starts at the place it
would have had if its whole top frame were sent. So an element sent on its own
lands exactly where it sat in an earlier send of the full frame.

Each root carries `frame`, the id of that top frame. With Build 3D on, the
importer lets layers already in the scene from the same frame hold up the new
ones, so a re-sent element rests on its backdrop rather than sinking into it.

## `bundle.json`

```jsonc
{
  "schema_version": "1.0.0",
  "generator": { "name": "figblend", "version": "0.1.0" },
  "document": {
    "name": "Dashboard",
    "exported_at": "2026-08-06T10:00:00.000Z"
  },
  "export_settings": {
    "px_per_meter": 1000,
    "raster_scale": 2,          // exportAsync SCALE constraint used for assets
    "vector_mode": true,        // solid shapes exported as SVG
    "image_texture": false,     // everything rasterized, text included
    "mode": "vector"            // vector | texvector | image
  },
  "roots": [ /* Node, plus "frame": "1:2" -- id of its top frame */ ]
}
```

### Export options

There is one export path — the walk described in this document — with two flags
that change what the leaves become.

**`vector_mode`** (default on) sends solid-coloured shapes down the `VECTOR`
route so they rebuild as curves. Off, they rasterize.

**`image_texture`** (default off) bakes *every* leaf to a plane with an image
texture, `TEXT` included. The tree, parenting and positions are unchanged — a
card still moves its contents — but nothing is editable and no `TEXT` or
`VECTOR` nodes are emitted at all. It forces `vector_mode` off, since curves and
baked planes are mutually exclusive.

The trade is editability against fidelity: baked text renders exactly as
designed, with the real font, ligatures and per-character styling, and needs no
font installed on the machine doing the render. See the text approximations at
the bottom of this document for what the editable route gives up.

The importer needs no special handling for either flag. Both produce ordinary
bundles; the settings are recorded so a reader can tell what it is looking at.

## `Node`

```jsonc
{
  "id": "1:23",              // Figma node id, unique within the bundle
  "name": "Card",            // becomes the Blender object name
  "type": "GROUP",           // GROUP | TEXT | IMAGE | VECTOR | MASKED
  "z_index": 7,
  "visible": true,
  "opacity": 1.0,
  "transform": {
    "center": { "x": 184, "y": 190 },  // px, absolute within the export root
    "width": 320, "height": 180,       // untransformed size
    "rotation": 0                      // degrees CCW, absolute (nesting resolved)
  },

  "effects_dropped": false,   // Figma effects discarded, see Vector below

  "image":  { /* Image  */ }, // on IMAGE, a GROUP's backdrop, or MASKED content
  "vector": { /* Vector */ }, // on VECTOR, or a GROUP's vector backdrop
  "mask":   { /* Vector */ }, // on MASKED only -- the clipping shape
  "text":   { /* Text   */ }, // on TEXT only
  "children": [ /* Node */ ]
}
```

A node normally carries one surface, `image` or `vector`. It carries **both** in
two cases:

- `MASKED` — `mask` is the silhouette, `image` is the masked content.
- `texvector` mode — `vector` is the silhouette, `image` is a render of that
  same node.

Both go through one importer path. The image is mapped onto the curve through
the shape's Generated coordinates, scaled and offset from the ratio between the
shape's box and `image.bounds`. The two differ whenever the render reaches past
the shape, which is what a drop shadow does.

`GROUP` is a container. It carries a surface when the frame has its own visible
fill, stroke or effects. By default the importer makes that surface *be* the
group object, so there is no extra Empty; a frame with no surface of its own
leaves no object and its children attach to the nearest ancestor that has one.
(The `Empty per Frame` import option restores a strict one-Empty-per-frame tree.)

### `Image`

```jsonc
{
  "path": "assets/1_23.png",
  "scale": 2,                // the raster scale it was exported at
  "width_px": 640,           // pixel dimensions of the file itself
  "height_px": 360,
  "bounds": { /* Bounds */ } // optional, see below
}
```

### `Vector`

An SVG that Blender rebuilds as curves. Emitted for nodes whose fills and
strokes are all solid colours. Gradients still rasterize — Blender's SVG
importer ignores them, and importing a shape that quietly lost its gradient is
worse than a raster that looks right.

**Strokes are outlined, not dropped.** The importer reads `fill` and nothing
else, so a `stroke` attribute would vanish, colour and width together. The
exporter runs Figma's `outlineStroke()` first, turning the border into a real
filled path. Fill and border are different colours, so they stay separate paths
in the SVG — the importer keeps them as separate curve objects parented
together, because *joining* them would merge their fill regions and flood the
shape with one colour.

**Effects are the deliberate exception.** A node with a drop shadow, inner
shadow or blur still comes through as geometry, and the effect is discarded —
a shadow baked into a texture is dead weight in a 3D scene, where real lights do
it better and correctly for the camera angle. Those nodes are flagged
`effects_dropped: true`, and the importer tags the object with a
`figblend_effects_dropped` custom property so they can be found and lit
deliberately.

```jsonc
{
  "path": "assets/1_23.svg",
  "bounds": { /* Bounds */ } // optional, see below
}
```

The importer measures what actually arrives and scales it to the node's size
rather than relying on the importer's 90 DPI convention, then bakes that scale
into the curve data — object scale would otherwise multiply through to every
child.

### `MASKED`

A group where one child has `isMask` set — a photo in a circle, most often.
Figma masks apply to a node's *subsequent siblings*, meaning the layers stacked
above it.

The node carries **two** surfaces: `mask` is the clipping shape, and `image` is
the masked content exported unclipped at full extent. The importer builds the
mask as curve geometry and hangs the content on it as a texture, so the
silhouette is real geometry rather than a transparent cutout in a PNG — a
circular avatar gets a genuinely circular edge that survives a close camera, and
can be extruded or bevelled.

`transform` describes the **mask**, since that is what becomes the object.
`image.bounds` describes the content, and the importer derives the texture
mapping from the ratio between the two:

```
scale_u  = mask.width  / content.width
scale_v  = mask.height / content.height
offset_u = (mask.left   - content.left)   / content.width
offset_v = (content.bottom - mask.bottom) / content.height
```

Those feed a Mapping node driven by Generated coordinates, so no UV unwrapping
is involved. Note the importer must also fix the curve's **texture space** after
transforming the data — Blender does not recompute it, and stale texture space
silently squashes Generated coords into a narrow band.

Masks that are not plain filled shapes — image masks, luminance masks, nested
masks — fall back to `IMAGE`, with Figma flattening the whole group. That is
always visually correct, just no longer geometry.

### `Bounds`

Present on a **backdrop** surface. Which box it holds depends on how the surface
was exported, and getting this wrong distorts the shape:

- **Rasterized** — the frame's *render* bounds, which include effects. A drop
  shadow reaches past the frame, and the plane has to match the PNG or the
  shadow gets squashed.
- **Vector** — the frame's *layout* box, which excludes effects. The shadow is
  not coming, so sizing to render bounds would stretch the shape out to cover
  space nothing will occupy.

Either way the box is axis-aligned with rotation already accounted for, so a
node using `bounds` is placed unrotated.

```jsonc
{ "center": { "x": 184, "y": 190 }, "width": 340, "height": 200 }
```

### `Text`

Everything needed to rebuild a real Blender text object.

```jsonc
{
  "characters": "Monthly revenue",
  "font": { "family": "Inter", "style": "Semi Bold" },
  "size": 16,                                  // px
  "letter_spacing": { "unit": "PIXELS", "value": 0.2 },
  "line_height": { "unit": "PIXELS", "value": 24 },   // or AUTO
  "align_horizontal": "LEFT",                  // LEFT|CENTER|RIGHT|JUSTIFIED
  "align_vertical": "TOP",                     // TOP|CENTER|BOTTOM
  "auto_resize": "NONE",                       // NONE|WIDTH_AND_HEIGHT|HEIGHT|TRUNCATE
  "case": "ORIGINAL",
  "decoration": "NONE",
  "color": { "r": 0.1, "g": 0.1, "b": 0.12, "a": 1.0 },
  "anchor": { "x": 24, "y": 100 },             // see below
  "segments": [ /* TextSegment */ ]
}
```

`anchor` is the exact point the text box hangs from, resolved by the exporter
from `align_horizontal`/`align_vertical` (left-top corner for LEFT/TOP, the
centre for CENTER/CENTER, and so on). The importer sets Blender's
`align_x`/`align_y` to match and places the object there — so text lands where
the designer put it without the importer knowing anything about Figma's box
model.

The top-level properties describe the dominant style. `segments` carries the
styled runs from `getStyledTextSegments()` so mixed styling inside one text box
(a bold word mid-sentence) is not lost.

> **v1 limitation.** The importer renders the dominant style only; `segments` is
> exported and preserved in the bundle but not yet applied. Blender text objects
> have no per-character font support without splitting into multiple objects.

```jsonc
// TextSegment
{
  "start": 0, "end": 7,
  "characters": "Monthly",
  "font": { "family": "Inter", "style": "Bold" },
  "size": 16,
  "color": { "r": 0, "g": 0, "b": 0, "a": 1 }
}
```

## Known approximations

Recorded here so they are decisions, not bugs:

- **Letter spacing.** Blender has no absolute tracking, only `space_character`
  (a multiplier). Approximated as `1 + letter_spacing_px / font_size_px`.
- **Line height.** Mapped to `space_line` as `line_height_px / font_size_px`.
  Blender's default line advance is not exactly 1 em, so long paragraphs drift.
- **Percent units.** `letter_spacing`/`line_height` in `PERCENT` are converted to
  pixels against `size` at export time, so the importer only ever sees pixels
  or `AUTO`.
- **Justified text** falls back to left alignment.
- **Rotation** is absolute and applied to leaf nodes only. `GROUP` Empties are
  created unrotated, because their children already carry absolute transforms —
  applying it to both would rotate the contents twice. Rotating the Empty after
  import behaves normally, which is what matters for animation.
- **Skew** is discarded. Only rotation is extracted from `absoluteTransform`;
  a skewed node keeps its position and size but loses the shear.
- **Ring fills need matching contours.** Blender fills a hole correctly when a
  ring's outer and inner edges are both rounded or both sharp. A rounded outer
  edge paired with a sharp inner one fills solid instead. Outlining a stroke
  produces concentric edges of the same kind, so this does not arise in
  practice — but it will bite anyone hand-writing test SVGs.
