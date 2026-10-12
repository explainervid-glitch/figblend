/// <reference types="@figma/plugin-typings" />

// Walks the current selection and emits a UI Bundle (see ../../SCHEMA.md).
// Text stays as text, containers stay as containers, everything else is
// rasterized through exportAsync.
//
// Positions come from absoluteTransform rather than absoluteBoundingBox: the
// bounding box is axis-aligned, so it is the wrong size for anything rotated,
// and node.rotation is parent-relative, which would double-apply under nesting.

const SCHEMA_VERSION = '1.1.0';
// Both stamped by `npm run bump` -- edit them there, not here.
const PLUGIN_VERSION = '0.2.1';
// Typed as string on purpose. Left to inference, a stamped repository name gets
// its own literal type, and comparing that with the placeholder below is a
// compile error -- so the plugin would build only while still unconfigured.
const GITHUB_REPO: string = 'explainervid-glitch/figblend';

const REPO_PLACEHOLDER = 'OWNER/REPO';

const CONTAINER_TYPES = [
  'FRAME', 'GROUP', 'COMPONENT', 'COMPONENT_SET', 'INSTANCE', 'SECTION',
];

type ContainerNode =
  | FrameNode | GroupNode | ComponentNode | ComponentSetNode
  | InstanceNode | SectionNode;

// Shapes Blender's SVG importer can rebuild as real curves.
const VECTOR_TYPES = [
  'VECTOR', 'RECTANGLE', 'ELLIPSE', 'POLYGON', 'STAR', 'LINE',
  'BOOLEAN_OPERATION',
];

interface Ctx {
  rasterScale: number;
  vectorMode: boolean;
  imageTexture: boolean;
  texturize: boolean;
  originX: number;
  originY: number;
  zCounter: number;
  effectsDropped: number;
  maskedFlattened: number;
  strokesOutlined: number;
  assets: { path: string; bytes: Uint8Array }[];
  warnings: string[];
}

// The height is a first guess; the UI reports its real content height once it
// has laid out, and on every change after that.
const UI_WIDTH = 280;
figma.showUI(__html__, { width: UI_WIDTH, height: 150, themeColors: true });

// Live Link tokens, kept per Blender process. clientStorage lives in the main
// thread, so the UI iframe asks for them through these two messages -- its own
// origin is null, which makes localStorage unavailable there.
const TOKEN_PREFIX = 'bridge-token:';

figma.ui.onmessage = async (msg) => {
  if (msg.type === 'cancel') {
    figma.closePlugin();
    return;
  }
  if (msg.type === 'token-get') {
    const value = await figma.clientStorage.getAsync(TOKEN_PREFIX + msg.key);
    figma.ui.postMessage({ type: 'token', key: msg.key, value: value || null });
    return;
  }
  if (msg.type === 'token-set') {
    if (msg.value) {
      await figma.clientStorage.setAsync(TOKEN_PREFIX + msg.key, msg.value);
    } else {
      await figma.clientStorage.deleteAsync(TOKEN_PREFIX + msg.key);
    }
    return;
  }
  if (msg.type === 'resize') {
    // Clamped so a bad measurement can never shrink the window to nothing or
    // grow it past the screen.
    const height = Math.max(100, Math.min(600, Math.round(Number(msg.height) || 0)));
    figma.ui.resize(UI_WIDTH, height);
    return;
  }
  if (msg.type === 'ui-ready') {
    uiReady = true;
    announceUpdate();
    return;
  }
  if (msg.type === 'open-release') {
    // The URL is built here, never passed in, so the UI can only ever open
    // this project's own release page.
    figma.openExternal(releaseUrl());
    return;
  }
  if (msg.type === 'export') {
    try {
      await runExport(msg.pxPerMeter, msg.rasterScale, msg.mode);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      figma.ui.postMessage({ type: 'error', message });
      figma.notify(`Export failed: ${message}`, { error: true });
    }
  }
};

// --- update check ------------------------------------------------------------
// A plugin loaded from disk cannot replace its own files; only one published
// through Figma updates itself. So this only looks: at most once a day it asks
// GitHub for the latest release, and when that is newer the UI offers the
// release page. Unzipping the new release over this folder is the update --
// Figma reads the files fresh on the next run.

const UPDATE_CHECK_KEY = 'update-check';
const UPDATE_INTERVAL_MS = 24 * 60 * 60 * 1000;

interface UpdateCheck { checkedAt: number; latest: string | null; }

function updatesConfigured(): boolean {
  return !!GITHUB_REPO && GITHUB_REPO !== REPO_PLACEHOLDER;
}

function releaseUrl(): string {
  return `https://github.com/${GITHUB_REPO}/releases/latest`;
}

// 'v1.2.3' -> [1, 2, 3]. A pre-release suffix is ignored: GitHub's "latest
// release" never points at a pre-release, so there is nothing to rank it by.
function parseVersion(text: string): number[] | null {
  const match = /^\s*v?(\d+)\.(\d+)\.(\d+)/.exec(text || '');
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

function isNewer(candidate: number[], current: number[]): boolean {
  for (let i = 0; i < 3; i++) {
    if (candidate[i] !== current[i]) return candidate[i] > current[i];
  }
  return false;
}

async function checkForUpdate(): Promise<void> {
  if (!updatesConfigured()) return;
  const current = parseVersion(PLUGIN_VERSION);
  if (!current) return;

  let cached = await figma.clientStorage.getAsync(UPDATE_CHECK_KEY) as
    UpdateCheck | undefined;

  if (!cached || Date.now() - cached.checkedAt > UPDATE_INTERVAL_MS) {
    try {
      const res = await fetch(
        `https://api.github.com/repos/${GITHUB_REPO}/releases/latest`,
        { headers: { Accept: 'application/vnd.github+json' } });
      // A 404 means nothing is released yet; a 403 is the rate limit. Either
      // way, keep whatever was known and ask again tomorrow rather than on
      // every run.
      const body = res.ok ? await res.json() : null;
      cached = {
        checkedAt: Date.now(),
        latest: body && typeof body.tag_name === 'string'
          ? body.tag_name
          : (cached ? cached.latest : null),
      };
      await figma.clientStorage.setAsync(UPDATE_CHECK_KEY, cached);
    } catch (err) {
      return; // offline -- try again next run
    }
  }

  const latest = cached && cached.latest ? parseVersion(cached.latest) : null;
  if (latest && isNewer(latest, current)) {
    pendingUpdate = latest.join('.');
    announceUpdate();
  }
}

// With a fresh cache the check finishes almost at once, possibly before the UI
// iframe has loaded and can hear anything. So the answer is held until the UI
// says it is ready, and sent from whichever side arrives second.
let pendingUpdate: string | null = null;
let uiReady = false;

function announceUpdate() {
  if (uiReady && pendingUpdate) {
    figma.ui.postMessage({ type: 'update', version: pendingUpdate });
  }
}

checkForUpdate().catch(() => { /* never let an update check break the plugin */ });

async function runExport(
  pxPerMeter: number, rasterScale: number, mode: string,
) {
  const selection = figma.currentPage.selection;
  if (selection.length === 0) {
    figma.ui.postMessage({ type: 'error', message: 'Select a frame or component first.' });
    return;
  }

  // Everything is positioned relative to the top-left of the whole selection,
  // so a multi-frame export keeps its relative layout.
  let originX = Infinity;
  let originY = Infinity;
  for (const node of selection) {
    const box = boundsOf(node);
    if (!box) continue;
    originX = Math.min(originX, box.x);
    originY = Math.min(originY, box.y);
  }
  if (!isFinite(originX) || !isFinite(originY)) {
    figma.ui.postMessage({ type: 'error', message: 'Selection has no visible bounds.' });
    return;
  }

  // Three ways out, each a different trade of editability against fidelity:
  //   vector    -- curves and real text, flat colours only
  //   texvector -- curve geometry wearing a render of itself, so gradients and
  //                effects survive while the silhouette stays geometry
  //   image     -- everything baked to planes, text included
  const texturize = mode === 'texvector';
  const imageTexture = mode === 'image';

  const ctx: Ctx = {
    rasterScale,
    // Curves are pointless when every element is being baked to a plane.
    vectorMode: !imageTexture,
    imageTexture,
    texturize,
    originX, originY,
    zCounter: 0, effectsDropped: 0, maskedFlattened: 0, strokesOutlined: 0,
    assets: [], warnings: [],
  };

  const roots = [];
  for (let i = 0; i < selection.length; i++) {
    figma.ui.postMessage({
      type: 'progress',
      message: `Walking ${selection[i].name}…`,
      value: i / selection.length,
    });
    const node = await walk(selection[i], ctx);
    if (node) roots.push(node);
  }

  const bundle = {
    schema_version: SCHEMA_VERSION,
    generator: { name: 'figblend', version: PLUGIN_VERSION },
    document: { name: figma.root.name, exported_at: new Date().toISOString() },
    export_settings: {
      px_per_meter: pxPerMeter,
      raster_scale: rasterScale,
      vector_mode: ctx.vectorMode,
      image_texture: imageTexture,
      mode,
    },
    roots,
  };

  figma.ui.postMessage({
    type: 'bundle',
    filename: sanitizeFilename(selection[0].name) + '.zip',
    manifest: JSON.stringify(bundle, null, 2),
    assets: ctx.assets,
    warnings: ctx.warnings,
    stats: {
      nodes: ctx.zCounter,
      assets: ctx.assets.length,
      effectsDropped: ctx.effectsDropped,
      strokesOutlined: ctx.strokesOutlined,
    },
  });
}

async function walk(node: SceneNode, ctx: Ctx): Promise<any | null> {
  if (!node.visible) return null;

  const width = 'width' in node ? node.width : 0;
  const height = 'height' in node ? node.height : 0;
  if (width === 0 || height === 0) return null;

  const zIndex = ctx.zCounter++;
  const centre = localToAbsolute(node, width / 2, height / 2, ctx);

  const base = {
    id: node.id,
    name: node.name,
    z_index: zIndex,
    visible: true,
    opacity: 'opacity' in node ? node.opacity : 1,
    transform: {
      center: centre,
      width,
      height,
      rotation: absoluteRotation(node),
    },
  };

  if (node.type === 'TEXT') {
    // Image-texture mode bakes text too. It stops being editable, but it also
    // stops depending on the artist having the right font installed -- and it
    // renders exactly as designed, ligatures and all.
    if (!ctx.imageTexture) {
      return Object.assign({}, base, { type: 'TEXT', text: extractText(node, ctx) });
    }
  }

  if (CONTAINER_TYPES.indexOf(node.type) !== -1) {
    const container = node as ContainerNode;

    // A mask hides everything stacked above it. Walking the children one by one
    // would export the masked content at full size -- the classic "photo in a
    // circle comes out square" failure.
    if ('children' in container) {
      const maskIndex = container.children.findIndex(
        (child) => child.visible && 'isMask' in child && child.isMask === true);
      if (maskIndex !== -1) {
        const masked = await exportMasked(container, maskIndex, ctx, base);
        if (masked) return masked;
      }
    }
    // Children carry absolute transforms, so the Empty must not re-apply the
    // container's rotation -- that would rotate everything twice. The value is
    // kept in the bundle for reference only.
    const result: any = Object.assign({}, base, { type: 'GROUP', children: [] });

    // A frame's own fill/stroke/effects become its backdrop. Exported by
    // cloning and stripping the children, because exportAsync always includes
    // descendants.
    if (hasOwnPaint(container)) {
      const backdrop = await exportBackdrop(container, ctx);
      if (backdrop) Object.assign(result, backdrop);
    }

    if ('children' in container) {
      for (const child of container.children) {
        const built = await walk(child, ctx);
        if (built) result.children.push(built);
      }
    }
    return result;
  }

  // Solid-filled vector shapes come through as real curves; Blender rebuilds
  // them resolution-independently, so icons stay crisp under a close camera.
  if (isVectorEligible(node, ctx)) {
    const vector = await exportVector(node, ctx);
    if (vector) {
      // Texturized: ship a render of the node alongside its outline. The shape
      // stays geometry, while gradients, effects and anything else the SVG
      // route would lose survive in the texture.
      const texture = ctx.texturize ? await exportRaster(node, ctx) : null;

      const dropped = hasVisibleEffects(node) && !texture;
      if (dropped) {
        ctx.warnings.push(
          `Dropped effects on "${node.name}" -- imported as geometry.`);
        ctx.effectsDropped += 1;
      }
      const built: any = Object.assign({}, base, {
        type: 'VECTOR',
        transform: vectorTransform(node, ctx, base),
        effects_dropped: dropped,
        vector,
      });
      if (texture) built.image = texture;
      return built;
    }
  }

  // Everything else -- gradients, images, and anything in image-texture mode.
  const image = await exportRaster(node, ctx);
  if (!image) return null;

  return Object.assign({}, base, {
    type: 'IMAGE',
    transform: rasterTransform(node, ctx, base),
    image,
  });
}

// Vectors size to absoluteBoundingBox, which excludes effects, because the
// importer throws the effects away. Sizing to render bounds would stretch the
// shape out to cover a shadow that is not going to be there.
//
// The box is axis-aligned with rotation already accounted for, and the SVG's
// own geometry is rotated to match, so the object carries no rotation itself.
function vectorTransform(node: SceneNode, ctx: Ctx, base: any) {
  const box = boundsOf(node);
  if (!box) return base.transform;
  return {
    center: {
      x: box.x + box.width / 2 - ctx.originX,
      y: box.y + box.height / 2 - ctx.originY,
    },
    width: box.width,
    height: box.height,
    rotation: 0,
  };
}

// A PNG covers the node's *render* bounds -- which include effects and already
// have the rotation baked in -- so the surface that carries it is axis-aligned
// and sized to those bounds, not to the layout box.
function rasterTransform(node: SceneNode, ctx: Ctx, base: any) {
  const render = ('absoluteRenderBounds' in node && node.absoluteRenderBounds)
    || boundsOf(node);
  if (!render) return base.transform;
  return {
    center: {
      x: render.x + render.width / 2 - ctx.originX,
      y: render.y + render.height / 2 - ctx.originY,
    },
    width: render.width,
    height: render.height,
    rotation: 0,
  };
}

// --- geometry ----------------------------------------------------------------

// absoluteTransform is [[m00, m01, m02], [m10, m11, m12]] mapping the node's
// local space (Y-down, origin at its top-left) into absolute page space.
function localToAbsolute(node: SceneNode, lx: number, ly: number, ctx: Ctx) {
  if (!('absoluteTransform' in node)) {
    const box = boundsOf(node);
    return {
      x: (box ? box.x + lx : 0) - ctx.originX,
      y: (box ? box.y + ly : 0) - ctx.originY,
    };
  }
  const m = node.absoluteTransform;
  return {
    x: m[0][0] * lx + m[0][1] * ly + m[0][2] - ctx.originX,
    y: m[1][0] * lx + m[1][1] * ly + m[1][2] - ctx.originY,
  };
}

// Figma's own definition: atan2(-m10, m00), in degrees, counterclockwise.
// Taken from the absolute matrix so nesting is already accounted for.
function absoluteRotation(node: SceneNode): number {
  if (!('absoluteTransform' in node)) return 0;
  const m = node.absoluteTransform;
  return (Math.atan2(-m[1][0], m[0][0]) * 180) / Math.PI;
}

// --- text --------------------------------------------------------------------

function extractText(node: TextNode, ctx: Ctx) {
  const size = typeof node.fontSize === 'number' ? node.fontSize : 16;

  const segments = node.getStyledTextSegments([
    'fontName', 'fontSize', 'fills', 'letterSpacing', 'lineHeight',
    'textCase', 'textDecoration',
  ]);

  // The longest run wins as the object-level style.
  let dominant = segments[0];
  for (const seg of segments) {
    if (!dominant || seg.end - seg.start > dominant.end - dominant.start) dominant = seg;
  }

  const domSize = dominant ? dominant.fontSize : size;
  const domFont = dominant ? dominant.fontName : { family: 'Inter', style: 'Regular' };

  return {
    characters: node.characters,
    font: { family: domFont.family, style: domFont.style },
    size: domSize,
    letter_spacing: normalizeSpacing(
      dominant ? dominant.letterSpacing : { value: 0, unit: 'PIXELS' }, domSize),
    line_height: normalizeSpacing(
      dominant ? dominant.lineHeight : { unit: 'AUTO' }, domSize),
    align_horizontal: node.textAlignHorizontal,
    align_vertical: node.textAlignVertical,
    auto_resize: node.textAutoResize,
    case: dominant ? dominant.textCase : 'ORIGINAL',
    decoration: dominant ? dominant.textDecoration : 'NONE',
    color: solidColorOf(dominant ? dominant.fills : node.fills),
    // The exact point the text box is anchored from, resolved here so the
    // importer only has to match Blender's align_x/align_y to it.
    anchor: textAnchor(node, ctx),
    segments: segments.map((seg) => ({
      start: seg.start,
      end: seg.end,
      characters: node.characters.slice(seg.start, seg.end),
      font: { family: seg.fontName.family, style: seg.fontName.style },
      size: seg.fontSize,
      color: solidColorOf(seg.fills),
    })),
  };
}

function textAnchor(node: TextNode, ctx: Ctx) {
  let lx = 0;
  if (node.textAlignHorizontal === 'CENTER') lx = node.width / 2;
  else if (node.textAlignHorizontal === 'RIGHT') lx = node.width;

  let ly = 0;
  if (node.textAlignVertical === 'CENTER') ly = node.height / 2;
  else if (node.textAlignVertical === 'BOTTOM') ly = node.height;

  return localToAbsolute(node, lx, ly, ctx);
}

// Figma reports spacing in PIXELS or PERCENT (of font size). The bundle only
// ever carries pixels or AUTO, so the importer has one less case to handle.
function normalizeSpacing(
  spacing: LetterSpacing | LineHeight,
  fontSize: number,
): { unit: string; value?: number } {
  if (spacing.unit === 'AUTO') return { unit: 'AUTO' };
  if (spacing.unit === 'PERCENT') {
    return { unit: 'PIXELS', value: (spacing.value / 100) * fontSize };
  }
  return { unit: 'PIXELS', value: spacing.value };
}

function solidColorOf(fills: readonly Paint[] | PluginAPI['mixed']) {
  const fallback = { r: 0, g: 0, b: 0, a: 1 };
  if (!Array.isArray(fills)) return fallback;
  for (const paint of fills) {
    if (paint.visible === false) continue;
    if (paint.type !== 'SOLID') continue;
    return {
      r: paint.color.r,
      g: paint.color.g,
      b: paint.color.b,
      a: paint.opacity === undefined ? 1 : paint.opacity,
    };
  }
  return fallback;
}

// --- rasterizing -------------------------------------------------------------

function hasOwnPaint(node: SceneNode): boolean {
  const fills = 'fills' in node ? node.fills : null;
  const hasFill = Array.isArray(fills) &&
    fills.some((p) => p.visible !== false && p.opacity !== 0);

  const strokes = 'strokes' in node ? node.strokes : null;
  const hasStroke = Array.isArray(strokes) && strokes.some((p) => p.visible !== false);

  const effects = 'effects' in node ? node.effects : null;
  const hasEffect = Array.isArray(effects) && effects.some((e) => e.visible !== false);

  return hasFill || hasStroke || hasEffect;
}

// Blender's SVG importer handles filled paths, and ignores strokes and most
// gradients. So a node only goes down the vector route when it is nothing but
// solid fills -- anything else would silently lose detail, which is worse than
// a raster that looks right.
//
// Effects are the deliberate exception: a drop shadow baked into a texture is
// dead weight in a 3D scene, where real lights do the job better. Shapes with
// effects still come through as geometry, and the effect is dropped.
function isVectorEligible(node: SceneNode, ctx: Ctx): boolean {
  if (!ctx.vectorMode) return false;
  if (VECTOR_TYPES.indexOf(node.type) === -1) return false;

  const fills = visiblePaints(node, 'fills');
  const strokes = visiblePaints(node, 'strokes');

  // Nothing visible to draw.
  if (fills.length === 0 && strokes.length === 0) return false;

  // Texturized mode gets its appearance from a render, so a gradient is no
  // reason to give up the geometry.
  if (ctx.texturize) return true;

  // Strokes are fine -- they get outlined into filled paths before export.
  // Gradients are not: the importer would drop them silently.
  if (fills.some((p) => p.type !== 'SOLID')) return false;
  if (strokes.some((p) => p.type !== 'SOLID')) return false;

  return true;
}

function visiblePaints(node: SceneNode, key: 'fills' | 'strokes'): Paint[] {
  const paints = key in node ? (node as any)[key] : null;
  if (!Array.isArray(paints)) return [];
  return paints.filter((p: Paint) => p.visible !== false && p.opacity !== 0);
}

function hasVisibleEffects(node: SceneNode): boolean {
  const effects = 'effects' in node ? node.effects : null;
  return Array.isArray(effects) && effects.some((e) => e.visible !== false);
}

function hasOnlySolidFills(node: SceneNode): boolean {
  const visible = visiblePaints(node, 'fills');
  if (visible.length === 0) return false;
  return visible.every((p) => p.type === 'SOLID');
}

// A masked group becomes the mask's *shape* carrying the masked content as its
// texture. The silhouette ends up as real geometry rather than a transparent
// cutout in a PNG -- so a circular avatar has a genuinely circular edge that
// stays clean under a close camera, and can be extruded or bevelled.
//
// Falls back to letting Figma flatten the whole group when the mask is not a
// plain filled shape (image masks, luminance masks, nested masks).
async function exportMasked(
  container: ContainerNode, maskIndex: number, ctx: Ctx, base: any,
) {
  const maskNode = container.children[maskIndex];
  const hasContent = container.children
    .slice(maskIndex + 1)
    .some((child) => child.visible);
  if (!hasContent) return null;

  if (ctx.vectorMode && isVectorEligible(maskNode, ctx)) {
    const mask = await exportVector(maskNode, ctx, '_mask', false);
    const content = await exportMaskedContent(container, maskIndex, ctx);
    if (mask && content) {
      return Object.assign({}, base, {
        type: 'MASKED',
        // The object is the mask shape, so it takes the mask's box.
        transform: vectorTransform(maskNode, ctx, base),
        effects_dropped: hasVisibleEffects(maskNode),
        mask,
        image: content,
      });
    }
    ctx.warnings.push(
      `Falling back to a flattened raster for masked group "${container.name}".`);
  }

  // Figma's own export honours the mask, so this is always visually right.
  const flat = await exportRaster(container, ctx);
  if (!flat) return null;
  ctx.maskedFlattened += 1;
  return Object.assign({}, base, {
    type: 'IMAGE',
    transform: rasterTransform(container, ctx, base),
    image: flat,
  });
}

// The masked content on its own, unclipped. Drop the mask and everything below
// it from a clone -- what is left is exactly the layers the mask applied to,
// and with the mask gone they export at full extent.
async function exportMaskedContent(
  container: ContainerNode, maskIndex: number, ctx: Ctx,
) {
  let clone: SceneNode | null = null;
  try {
    const target = cloneDetached(container);
    clone = target;
    const shift = cloneShift(container, target);
    if (!('children' in target)) return null;

    const children = target.children.slice();
    for (let i = 0; i <= maskIndex && i < children.length; i++) {
      children[i].remove();
    }

    const bytes = await target.exportAsync({
      format: 'PNG',
      constraint: { type: 'SCALE', value: ctx.rasterScale },
    });
    const path = `assets/${sanitizeId(container.id)}_content.png`;
    ctx.assets.push({ path, bytes });

    const box = unshift(
      ('absoluteRenderBounds' in target && target.absoluteRenderBounds)
      || boundsOf(target), shift) || boundsOf(container);
    return {
      path,
      scale: ctx.rasterScale,
      width_px: box ? Math.round(box.width * ctx.rasterScale) : 0,
      height_px: box ? Math.round(box.height * ctx.rasterScale) : 0,
      bounds: toBounds(box, ctx),
    };
  } catch (err) {
    ctx.warnings.push(`Could not export masked content of "${container.name}": ${err}`);
    return null;
  } finally {
    if (clone) clone.remove();
  }
}

// Clone a container so its children can be edited. Instances refuse child
// removal until they are detached.
function cloneDetached(node: ContainerNode): SceneNode {
  const clone = node.clone();
  if (clone.type === 'INSTANCE') return clone.detachInstance();
  return clone;
}

// clone() parents the duplicate under figma.currentPage, not under the original's
// parent. A nested node keeps its parent-relative x/y, so re-rooting it to the
// page shifts its absolute position by the whole parent chain -- often thousands
// of pixels. Any measurement taken from a clone has to be shifted back.
interface CloneShift { dx: number; dy: number; }

function cloneShift(original: SceneNode, clone: SceneNode): CloneShift {
  const a = boundsOf(original);
  const b = boundsOf(clone);
  if (!a || !b) return { dx: 0, dy: 0 };
  return { dx: b.x - a.x, dy: b.y - a.y };
}

function unshift(box: Rect | null, shift: CloneShift): Rect | null {
  if (!box) return null;
  return {
    x: box.x - shift.dx,
    y: box.y - shift.dy,
    width: box.width,
    height: box.height,
  };
}

function toBounds(box: Rect | null, ctx: Ctx) {
  if (!box) return null;
  return {
    center: {
      x: box.x + box.width / 2 - ctx.originX,
      y: box.y + box.height / 2 - ctx.originY,
    },
    width: box.width,
    height: box.height,
  };
}

async function exportVector(
  node: SceneNode, ctx: Ctx, suffix = '', outlineStrokes = true,
) {
  if (outlineStrokes && visiblePaints(node, 'strokes').length > 0) {
    const outlined = await exportOutlined(node, ctx, suffix);
    if (outlined) return outlined;
    // Outlining failed; the plain export at least keeps the fill.
  }
  try {
    const bytes = await node.exportAsync({ format: 'SVG' });
    const path = `assets/${sanitizeId(node.id)}${suffix}.svg`;
    ctx.assets.push({ path, bytes });
    return { path };
  } catch (err) {
    ctx.warnings.push(`Could not export "${node.name}" as SVG: ${err}`);
    return null;
  }
}

// Blender's SVG importer reads `fill` and nothing else -- a `stroke` attribute
// is dropped colour, width and all. Figma's outlineStroke() converts a stroke
// into a real filled path, which imports perfectly, so borders and lines stay
// geometry instead of falling back to a flat raster.
//
// The fill and the outlined stroke are different colours, so they stay separate
// paths. Blender joins them into one object with two material slots.
async function exportOutlined(node: SceneNode, ctx: Ctx, suffix: string) {
  let disposable: SceneNode | null = null;
  try {
    const clone = node.clone() as SceneNode & { strokes: readonly Paint[] };
    disposable = clone;
    // Measured before anything is mutated, while clone and original still
    // describe the same shape.
    const shift = cloneShift(node, clone);

    if (!('outlineStroke' in clone) || typeof (clone as any).outlineStroke !== 'function') {
      return null;
    }
    const outlined: VectorNode | null = (clone as any).outlineStroke();
    if (!outlined) return null;

    // The original stroke would now be drawn twice. The importer ignores stroke
    // attributes anyway, but leaving it makes the SVG lie about its bounds.
    (clone as any).strokes = [];

    const parent = clone.parent || figma.currentPage;
    const group = figma.group([clone, outlined], parent as any);
    disposable = group;

    const bytes = await group.exportAsync({ format: 'SVG' });
    const path = `assets/${sanitizeId(node.id)}${suffix}.svg`;
    ctx.assets.push({ path, bytes });

    // An outlined stroke sits outside the fill's box, so the surface has to be
    // sized to the combined shape or the importer scales it down to fit.
    const bounds = toBounds(unshift(group.absoluteBoundingBox, shift), ctx);
    ctx.strokesOutlined += 1;
    return { path, bounds };
  } catch (err) {
    ctx.warnings.push(`Could not outline the stroke on "${node.name}": ${err}`);
    return null;
  } finally {
    // Removing the group removes the clone and the outlined path with it.
    if (disposable) disposable.remove();
  }
}

async function exportRaster(node: SceneNode, ctx: Ctx) {
  try {
    const bytes = await node.exportAsync({
      format: 'PNG',
      constraint: { type: 'SCALE', value: ctx.rasterScale },
    });
    const path = `assets/${sanitizeId(node.id)}.png`;
    ctx.assets.push({ path, bytes });
    const box = ('absoluteRenderBounds' in node && node.absoluteRenderBounds) || boundsOf(node);
    return {
      path,
      scale: ctx.rasterScale,
      width_px: box ? Math.round(box.width * ctx.rasterScale) : 0,
      height_px: box ? Math.round(box.height * ctx.rasterScale) : 0,
      // Needed whenever this raster is a texture rather than the surface
      // itself: the render reaches past the shape when effects are involved,
      // and the importer lines the two up from these boxes.
      bounds: toBounds(box, ctx),
    };
  } catch (err) {
    ctx.warnings.push(`Could not rasterize "${node.name}": ${err}`);
    return null;
  }
}

// exportAsync has no "background only" mode, so clone the frame, drop its
// children, export that, and bin the clone.
async function exportBackdrop(node: ContainerNode, ctx: Ctx) {
  let clone: SceneNode | null = null;
  try {
    // An instance's children cannot be removed while it is still an instance,
    // so detach the clone first. Instances are the common case in real files.
    let target: SceneNode = node.clone();
    clone = target;
    // Measured before any mutation -- clone() re-roots to the page, so every
    // measurement taken from the clone is offset from the original.
    const shift = cloneShift(node, target);
    if (target.type === 'INSTANCE') {
      target = target.detachInstance();
      clone = target;
    }

    if ('children' in target) {
      for (const child of target.children.slice()) child.remove();
    }

    // A filled frame -- including rounded corners -- becomes a curve, so cards
    // stay sharp however close the camera gets. Cards are also where drop
    // shadows usually live; the shadow goes, the geometry stays.
    if (ctx.vectorMode && (ctx.texturize || isBackdropVectorEligible(target))) {
      const dropped = hasVisibleEffects(target) && !ctx.texturize;
      if (dropped) {
        ctx.warnings.push(
          `Dropped effects on "${node.name}" background -- imported as geometry.`);
        ctx.effectsDropped += 1;
      }

      // A bordered card: outline the frame's stroke into a filled path so the
      // border survives, since the importer ignores stroke attributes. We are
      // already holding a clone, so it is safe to mutate.
      let exportTarget: SceneNode = target;
      let outlinedStroke = false;
      if (visiblePaints(target, 'strokes').length > 0
          && typeof (target as any).outlineStroke === 'function') {
        const outlined: VectorNode | null = (target as any).outlineStroke();
        if (outlined) {
          (target as any).strokes = [];
          exportTarget = figma.group([target, outlined], target.parent as any);
          clone = exportTarget; // so the finally block bins the whole group
          outlinedStroke = true;
          ctx.strokesOutlined += 1;
        }
      }

      const bytes = await exportTarget.exportAsync({ format: 'SVG' });
      const path = `assets/${sanitizeId(node.id)}_bg.svg`;
      ctx.assets.push({ path, bytes });

      // Sized to the layout box, not render bounds: the shadow is not coming.
      // An outlined stroke does sit outside that box, so use the real extent.
      const box = outlinedStroke
        ? unshift((exportTarget as any).absoluteBoundingBox, shift)
        : boundsOf(node);
      const result: any = {
        vector: { path, bounds: toBounds(box, ctx) },
        effects_dropped: dropped,
      };

      // Texturized: render the same stripped backdrop and hand it over as the
      // curve's texture, so gradients and shadows on the card survive.
      if (ctx.texturize) {
        const raster = await exportTarget.exportAsync({
          format: 'PNG',
          constraint: { type: 'SCALE', value: ctx.rasterScale },
        });
        const rasterPath = `assets/${sanitizeId(node.id)}_bg.png`;
        ctx.assets.push({ path: rasterPath, bytes: raster });
        const rasterBox = unshift(
          ('absoluteRenderBounds' in exportTarget && exportTarget.absoluteRenderBounds)
          || boundsOf(exportTarget), shift) || box;
        result.image = {
          path: rasterPath,
          scale: ctx.rasterScale,
          width_px: rasterBox ? Math.round(rasterBox.width * ctx.rasterScale) : 0,
          height_px: rasterBox ? Math.round(rasterBox.height * ctx.rasterScale) : 0,
          bounds: toBounds(rasterBox, ctx),
        };
      }
      return result;
    }

    // The stripped clone's render bounds are the true extent of a rasterized
    // backdrop -- a drop shadow reaches past the frame box, and the surface has
    // to match or the shadow gets squashed.
    const box = unshift(
      ('absoluteRenderBounds' in target && target.absoluteRenderBounds)
      || boundsOf(target), shift) || boundsOf(node);

    const bytes = await target.exportAsync({
      format: 'PNG',
      constraint: { type: 'SCALE', value: ctx.rasterScale },
    });
    const path = `assets/${sanitizeId(node.id)}_bg.png`;
    ctx.assets.push({ path, bytes });
    return {
      image: {
        path,
        scale: ctx.rasterScale,
        width_px: box ? Math.round(box.width * ctx.rasterScale) : 0,
        height_px: box ? Math.round(box.height * ctx.rasterScale) : 0,
        bounds: toBounds(box, ctx),
      },
    };
  } catch (err) {
    ctx.warnings.push(`Could not export backdrop for "${node.name}": ${err}`);
    return null;
  } finally {
    if (clone) clone.remove();
  }
}

// Same rule as isVectorEligible, applied to the stripped backdrop clone.
function isBackdropVectorEligible(node: SceneNode): boolean {
  const fills = visiblePaints(node, 'fills');
  const strokes = visiblePaints(node, 'strokes');
  if (fills.length === 0 && strokes.length === 0) return false;
  if (fills.some((p) => p.type !== 'SOLID')) return false;
  if (strokes.some((p) => p.type !== 'SOLID')) return false;
  return true;
}

function boundsOf(node: SceneNode) {
  return 'absoluteBoundingBox' in node ? node.absoluteBoundingBox : null;
}

function sanitizeId(id: string): string {
  return id.replace(/[^a-zA-Z0-9]/g, '_');
}

function sanitizeFilename(name: string): string {
  return name.replace(/[^a-zA-Z0-9-_]+/g, '-').replace(/^-+|-+$/g, '').toLowerCase() || 'ui-export';
}
