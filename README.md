# BB SVG Layers — Blender Addon

A Blender 4.2+ extension that automates the full pipeline for converting imported SVG layers into game-ready 3D paper cutout meshes. It handles geometry processing, UV projection, material creation from a Master material using SVG fill colors, and intelligent layer stacking — including multi-character scenes with automatic collection sorting.

---

## What It Does

This addon is designed for **paper cutout 3D scenes** where SVG layers become individual mesh pieces stacked along the Y axis, simulating physical depth between paper layers.

---

## Installation

1. Download `bb_svg_layers.zip` from the [Releases](../../releases) page
2. In Blender: **Edit → Preferences → Add-ons**
3. Drag and drop the `.zip` into the Preferences window, or use **▾ → Install from Disk**
4. Enable **BB SVG Layers** in the addon list

> Requires **Blender 4.2 or later** (uses the extension manifest format).

---

## Panel Location

`3D Viewport → N-Panel (N key) → SVG Layer tab`

---

## Controls

### Slider

| Slider | Description |
|---|---|
| **Layer Step** | Y distance (in Blender units) between successive layers during stacking. Default: `3.0` |

---

### Buttons

#### Load SVG
The primary entry point. Opens a file picker, imports the selected SVG, and runs the full pipeline automatically:

1. Reads element order and fill colors from the SVG file
2. Imports curves via Blender's built-in SVG importer
3. Runs **Apply & Sort** — geometry processing and collection sorting (see below)
4. Runs **Auto Stack** — assigns Y depth from SVG document order

> **Requires** a material named **`Master`** to exist in the scene before loading. See [Master Material Setup](#master-material-setup).

---

#### Apply & Sort *(called automatically by Load SVG)*
Runs the full geometry pipeline on all objects in the active collection, then automatically sorts them into sub-collections by name prefix.

Steps performed on each object:
1. **Rotate +90° on X** — flattens the SVG from Blender's import orientation
2. **Scale ×850** — converts SVG units to a usable world scale
3. **Convert curves to mesh** — handles 2D curves before transforms are applied
4. **Apply all transforms** — bakes location, rotation and scale
5. **Merge by Distance** — cleans up duplicate vertices from curve conversion
6. **Solidify modifier** — adds thickness of `1`, then immediately applied
7. **Offset back faces** — vertices on the back face (identified by Y normal) are moved `-2` on X and `+2` on Z, giving the cutout a characteristic paper-craft lean
8. **UV projection from Y** — front faces are mapped onto a 1920×1920 px canvas (`U = X / 1920`, `V = Z / 1920`); back and side faces are pinned to `(0, 0)` to avoid atlas stretching
9. **Create and assign material** — for each object, a copy of the **Master** material is created (or reused if one already exists for that prefix), named after the object's prefix (e.g. `Wes_body`). Its color node is set to the fill color parsed directly from the SVG file. A random Z rotation is applied to the Mapping node for texture variation.

After processing, objects are moved into sub-collections under the active collection, grouped by their name prefix:
- `BG_` objects → **BG** collection
- Character objects (`Wes_`, `Dad_`, etc.) → one collection per character, named after the prefix
- `FG_` objects → **FG** collection

---

#### Auto Stack *(called automatically by Load SVG)*
Assigns Y positions to all mesh objects in the active collection based on SVG document order, using a **greedy overlap-packing algorithm**.

**Algorithm:**
1. Objects are ordered according to their position in the original SVG document (bottom of stack first)
2. Each object is placed at Y=0 unless it overlaps something already placed — in which case it steps forward by one **Layer Step**
3. Overlap is detected using bounding box intersection on the XZ plane

> **Tip:** If you need to re-run stacking after manual edits, you can call Auto Stack from the operator search (`F3`). To control the BG → character → FG depth order, reorder collections in the outliner before running.

---

#### Manual
Applies solidify, merge by distance, back-face offset, and UV projection to **selected mesh objects only**, without touching materials or collections. Use this for objects that were added or modified after the initial Load SVG run.

---

#### − / + Buttons
Move all selected mesh objects one **Layer Step** forward or back, relative to the current view:

- **Orthographic view** — translates along the dominant view axis
- **Perspective / Camera view** — scales vertices radially from the camera position, preserving the object's apparent on-screen size

> Vertex data is modified directly; object origins are not moved.

#### Snap
Snaps all selected objects to the **highest Y value** among them (measured from world-space bounding box centres). Useful for aligning pieces that should be on the same layer. Object origins are not moved.

#### Override Single
Makes a **single-user copy** of the assigned material for each selected object so it can be edited independently. The copy is named `<prefix>_override` (e.g. `Wes_body_override`).

#### Override Same
Same as Override Single, but after creating the override it **reassigns it to every object in the scene** that was using the same original material. Use this when multiple objects share one material and you want them all to switch to the same editable copy in one click.

---

## Master Material Setup

Materials are created as **copies of a material named `Master`** that must exist in your scene before importing. The addon reads each SVG element's fill color and injects it into the copy's color node (an RGB node named `Color`, a plain RGB node, or the Principled BSDF Base Color, searched in that order). A random Z rotation is also applied to the first Mapping node for texture variation.

### Steps to set up

1. Create a material and name it exactly **`Master`**
2. Set up your node graph — include an **RGB node named `Color`** (or any RGB node) to act as the color injection point
3. Add a **Mapping node** if you want per-object texture rotation randomization
4. Save it in your scene — the addon will copy it automatically for each prefix on import

### Name Matching

The addon strips Blender's duplicate suffix from the object name to determine the material name:

| Object name | Material created |
|---|---|
| `Wes_body` | `Wes_body` |
| `Wes_body.001` | `Wes_body` (reused) |
| `BG_sky.014` | `BG_sky` |

Created materials are also automatically exported to the **Paper** catalog in your configured User Library for asset browser access.

---

## Naming Convention

For multi-character scenes, name your SVG layers with prefixes:

| Prefix | Goes into | Stacked |
|---|---|---|
| `BG_` | **BG** collection | Furthest back |
| `Wes_`, `Dad_`, etc. | Per-character collection | Middle, in outliner order |
| `FG_` | **FG** collection | Closest to camera |

---

## Typical Workflow

1. Create a **`Master`** material in your scene
2. In the **SVG Layer** panel, set the **Layer Step** to taste
3. Click **Load SVG** and select your SVG file — geometry processing, material creation, collection sorting, and layer stacking all run automatically
4. Fine-tune with **− / +**, **Snap**, **Override Single**, and **Override Same** as needed
5. Use **Manual** to reprocess any mesh objects added or edited after the initial import

---

## File Structure

```
bb_svg_layers/
├── __init__.py            # Addon code
└── blender_manifest.toml  # Extension manifest (Blender 4.2+)
```

---

## Requirements

- Blender 4.2 or later
- A material named **`Master`** in the scene (for material creation)

---

## License

GPL-2.0-or-later — see [Blender's extension licensing guidelines](https://extensions.blender.org/about/licenses/).
