# FigBlend — Figma Community listing

Copy each section into the matching field of Figma's publish dialog.

---

## Name

FigBlend

## Tagline

Send Figma frames to Blender with editable text and real 3D

## Category

**Design tools**

(If the dialog also offers an import/export or 3D category, that fits too —
Design tools is the safe default.)

## Description

FigBlend moves your Figma UI into Blender as a real scene, so you can light it,
animate it and put a camera on it — without rebuilding anything by hand.

Select a frame, press Send to Blender, and it arrives in your open .blend file
a few seconds later. No export, no file to find, no import dialog.

**What comes across**

- Text as real Blender text objects, with your fonts — still editable
- Shapes as curves, sharp at any camera distance
- Borders outlined into real geometry, so strokes survive
- Masks rebuilt as the mask shape, with the content as its texture
- Groups and frames as Blender collections, parented the way they were nested
- Positions, sizes and stacking order exactly as designed

**Three export modes**

- Vector — curves and editable text. Gradients and images come in as textures.
- Texturized — curves that wear a render of themselves, so gradients and
  shadows survive on real geometry.
- Image — every element a textured plane, text included. Nothing needs to be
  installed, and it looks exactly like Figma.

**Build 3D**

Give every layer real thickness in one step. Each layer grows backwards from
its own face and rests on whatever is directly beneath it, so cards, buttons
and labels stack like a physical model instead of floating apart.

**Getting started**

FigBlend has two halves. This plugin is one; the other is a free add-on for
Blender 4.2 or newer.

1. Install the FigBlend add-on in Blender — instructions at
   github.com/explainervid-glitch/figblend
2. Save your .blend, open the FigBlend tab in Blender's sidebar (N) and press
   Start Live Link.
3. Run this plugin, select a frame, press Send to Blender.

No Blender running? Press Export instead and import the .zip from Blender's
File menu.

**Privacy**

Your design never leaves your computer. Live Link talks only to Blender on your
own machine (localhost), and Blender must be paired with the plugin before it
accepts anything.

**Good to know**

- Editable text needs the same fonts installed on the machine running Blender.
  Missing fonts are reported, not silently swapped. Image mode avoids this.
- Variable fonts render at their default weight in Blender; install the static
  weights if a style looks wrong.
- Vector mode drops Figma drop shadows in favour of clean geometry — light the
  scene instead, or use Texturized to keep them.

Questions and bug reports: github.com/explainervid-glitch/figblend/issues

## Support contact

https://github.com/explainervid-glitch/figblend/issues

---

## Images in this folder

| File | Use for | Size |
|---|---|---|
| `icon-128.png` | Icon | 128 × 128 |
| `icon-512.png` | Same icon, larger, if Figma accepts it | 512 × 512 |
| `thumbnail-1920x1080.png` | Thumbnail / cover | 1920 × 1080 |
| `carousel-1.png` | Carousel 1 — Live Link | 1920 × 1080 |
| `carousel-2.png` | Carousel 2 — three export modes | 1920 × 1080 |
| `carousel-3.png` | Carousel 3 — Build 3D | 1920 × 1080 |
| `carousel-4.png` | Carousel 4 — hierarchy and editable text | 1920 × 1080 |
| `carousel-5.png` | Carousel 5 — getting started | 1920 × 1080 |

Sources are in `source/`. Edit the HTML, then re-render everything from this
folder with:

```bash
bash source/render.sh
```
