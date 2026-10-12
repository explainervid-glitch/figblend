// Tiny isometric helper: draws flat UI layers as thin 3D slabs in SVG.
// World units: x right, y back, z up. Returns SVG markup strings.

const C = Math.cos(Math.PI / 6), S = 0.5;

function P(x, y, z) {
  return [(x - y) * C, (x + y) * S - z];
}
function poly(points, fill, extra = '') {
  return `<polygon points="${points.map(p => p.join(',')).join(' ')}" fill="${fill}" ${extra}/>`;
}
function shade(hex, f) {
  const n = parseInt(hex.slice(1), 16);
  const ch = (v) => Math.max(0, Math.min(255, Math.round(v * f)));
  return `rgb(${ch(n >> 16)},${ch((n >> 8) & 255)},${ch(n & 255)})`;
}

// A slab: footprint x..x+w, y..y+h, bottom at z, thickness t.
function slab({ x, y, w, h, z = 0, t = 6, color = '#ffffff', stroke = 'none', opacity = 1, topFill }) {
  const top = z + t;
  const topFace = [P(x, y, top), P(x + w, y, top), P(x + w, y + h, top), P(x, y + h, top)];
  const right = [P(x + w, y, top), P(x + w, y + h, top), P(x + w, y + h, z), P(x + w, y, z)];
  const front = [P(x, y + h, top), P(x + w, y + h, top), P(x + w, y + h, z), P(x, y + h, z)];
  const s = stroke === 'none' ? '' : `stroke="${stroke}" stroke-width="1.5" stroke-linejoin="round"`;
  return `<g opacity="${opacity}">`
    + poly(right, shade(color, 0.62), s)
    + poly(front, shade(color, 0.78), s)
    + poly(topFace, topFill || color, s)
    + `</g>`;
}

// Draw ordinary 2D SVG onto a horizontal plane at height z.
function onPlane(z, markup) {
  return `<g transform="matrix(${C},${S},${-C},${S},0,${-z})">${markup}</g>`;
}

// The card as one flat picture -- what Image mode produces.
function cardPicture() {
  const r = (x, y, w, h, fill, rx = 4) =>
    `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}"/>`;
  let m = r(0, 0, CARD_W, CARD_H, '#2a2a33', 10);
  for (const items of CARD_LAYERS.slice(1)) {
    for (const o of items) m += r(o.x, o.y, o.w, o.h, o.color);
  }
  return m;
}

// Overlap-aware stack, the rule Build 3D uses: each element rests on the
// highest earlier element whose footprint it overlaps, not on its layer count.
function stackedCard({ t = 16, gap = 3, texturize = null } = {}) {
  const placed = [];
  const out = [];
  const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  for (const items of CARD_LAYERS) {
    for (const o of items) {
      const th = o.t ? Math.max(o.t, t * 0.5) : t;
      let base = 0;
      for (const p of placed) if (overlaps(o, p)) base = Math.max(base, p.top + gap);
      const fill = texturize ? texturize(o) : undefined;
      out.push(slab({ ...o, t: th, z: base, topFill: fill }));
      placed.push({ ...o, top: base + th });
    }
  }
  return out.join('');
}

// Dashed guide from a point on one layer down to another.
function guide(x, y, z1, z2, color = 'rgba(255,255,255,.22)') {
  const a = P(x, y, z1), b = P(x, y, z2);
  return `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="${color}" stroke-width="2" stroke-dasharray="6 7"/>`;
}

// A see-through sheet the size of the card: one Figma layer, lifted out.
function sheet(w, h, z) {
  const face = [P(0, 0, z), P(w, 0, z), P(w, h, z), P(0, h, z)];
  return poly(face, 'rgba(255,255,255,.035)',
    'stroke="rgba(255,255,255,.28)" stroke-width="2" stroke-dasharray="10 8"');
}

// One UI card exploded into its layers, each on its own sheet, bottom to top:
// card, media, text, button. gap = height between sheets.
const CARD_W = 360, CARD_H = 250;
const CARD_LAYERS = [
  [{ x: 0, y: 0, w: CARD_W, h: CARD_H, t: 10, color: '#2a2a33' }],
  [{ x: 22, y: 22, w: 150, h: 110, color: '#a259ff' },
   { x: 196, y: 22, w: 60, h: 60, color: '#3d8bff' }],
  [{ x: 196, y: 98, w: 140, h: 14, t: 4, color: '#f2f2f5' },
   { x: 196, y: 120, w: 96, h: 12, t: 4, color: '#9a9aa6' },
   { x: 22, y: 152, w: 316, h: 14, t: 4, color: '#f2f2f5' },
   { x: 22, y: 174, w: 240, h: 12, t: 4, color: '#9a9aa6' }],
  [{ x: 22, y: 200, w: 130, h: 32, color: '#f5792a' },
   // a play badge on the photo: overlaps it, so Build 3D stacks it on top
   { x: 76, y: 56, w: 42, h: 42, color: '#f2f2f5' }],
];

function explodedCard({ gap = 150, t = 7, sheets = true } = {}) {
  const top = gap * (CARD_LAYERS.length - 1);
  const out = [guide(0, CARD_H, 0, top), guide(CARD_W, CARD_H, 0, top), guide(CARD_W, 0, 0, top)];
  CARD_LAYERS.forEach((items, level) => {
    const z = level * gap;
    if (sheets && level > 0) out.push(sheet(CARD_W, CARD_H, z));
    for (const o of items) out.push(slab({ t, ...o, z }));
  });
  return out.join('');
}

// The FigBlend mark: three stacked layers, purple to orange.
function mark(size = 128) {
  const u = size / 128;
  const s = (o) => slab({ ...o, x: o.x * u, y: o.y * u, w: o.w * u, h: o.h * u, z: o.z * u, t: o.t * u });
  // A small UI layout sits on the top layer -- an image block and two lines
  // of text -- so the mark reads as "a screen, in 3D", not a generic stack.
  const ui = '#fff4ea';
  return s({ x: 0, y: 0, w: 70, h: 70, z: 0, t: 9, color: '#a259ff' })
    + s({ x: 0, y: 0, w: 70, h: 70, z: 22, t: 9, color: '#3d8bff' })
    + s({ x: 0, y: 0, w: 70, h: 70, z: 44, t: 9, color: '#f5792a' })
    + s({ x: 9, y: 9, w: 24, h: 24, z: 53, t: 3, color: ui })
    + s({ x: 9, y: 41, w: 52, h: 7, z: 53, t: 3, color: ui })
    + s({ x: 9, y: 54, w: 34, h: 7, z: 53, t: 3, color: ui });
}
