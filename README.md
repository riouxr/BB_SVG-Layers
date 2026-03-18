# BB SVG Layers — Blender Addon

A Blender 4.2+ extension that automates the full pipeline for converting imported SVG layers into game-ready 3D paper cutout meshes. It handles geometry processing, UV projection, material assignment from an asset library, and intelligent layer stacking — including multi-character scenes with automatic collection sorting.

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
| **Tiny Object Threshold** | Objects with surface area below this value (in Blender units²) are always placed in the frontmost layer. Use this to keep whiskers, thin lines and small details on top. Default: `500` |

---

### Buttons

#### Apply & Sort
Runs the full geometry pipeline on all objects in the active collection (or viewport selection), then automatically sorts them into sub-collections by name prefix.

Steps performed on each object:
1. **Rotate +90° on X** — flattens the SVG from Blender's import orientation
2. **Scale ×850** — converts SVG units to a usable world scale
3. **Convert curves to mesh** — handles 2D curves before transforms are applied
4. **Apply all transforms** — bakes location, rotation and scale
5. **Merge by Distance** — cleans up duplicate vertices from curve conversion
6. **UV projection from Y** — maps UVs onto a 1920×1920 px canvas (`U = X / 1920`, `V = Z / 1920`)
7. **Solidify modifier** — adds thickness of `1`
8. **Assign material** — appends and assigns the matching material from the **Paper** asset library by name prefix (e.g. object `Wes_body.002` → material `Wes_body`)

After processing, objects are moved into sub-collections under the active collection, grouped by their name prefix:
- `BG_` objects → **BG** collection
- Character objects (`Wes_`, `Dad_`, etc.) → one collection per character, named after the prefix
- `FG_` objects → **FG** collection

---

#### Auto Stack
Stacks all objects intelligently using a **greedy layer-packing algorithm**, processing each sub-collection independently and chaining Y offsets continuously between them.

**Algorithm:**
1. Sort objects by surface area — largest first
2. Objects below the **Tiny Object Threshold** are set aside and placed in the frontmost layer of their group
3. Each remaining object is placed in the earliest layer where it doesn't overlap anything already there (overlap detected using the **Separating Axis Theorem** on XZ-plane polygons, with a bounding box pre-check for speed)
4. Collections are processed in **outliner order** — reorder them in the outliner before running Auto Stack to control the BG → character → FG depth order
5. Y offset between every layer is `1` unit

> **Tip:** Run **Apply & Sort** first, reorder your collections in the outliner if needed, then run **Auto Stack**.

---

#### − / + Buttons
Move all selected objects along +Y or -Y by `1` unit. Useful for fine-tuning individual layers after Auto Stack.

#### Snap
Snaps all selected objects to the **highest Y value** among them — useful for aligning pieces that should be on the same layer.

#### Refresh
Re-assigns materials from the **Paper** asset library to all objects in the active collection or selection, without touching geometry. Useful after renaming objects or adding new materials to the library.

#### Override Single
Makes a **single-user copy** of the assigned material for each selected object, then creates a **library override** so it can be edited independently without affecting other objects. The overridden material is named `<prefix>_override` (e.g. `Wes_body_override`).

#### Override Same
Same as Override Single, but after creating the override it **reassigns it to every object in the scene** that was using the same original material. Use this when multiple objects share one material and you want them all to switch to the same editable override in one click.

---

## Asset Library Setup

Materials are fetched automatically from a Blender asset library whose `.blend` file path contains the word **Paper** (e.g. `PaperLibrary.blend`).

### Steps to set up

1. Create or locate your materials `.blend` file
2. Open it and mark each material as an asset: right-click the material in the **Asset Browser → Mark as Asset**
3. In Blender **Preferences → File Paths → Asset Libraries**, add the folder containing your `.blend` file

### Name Matching

The addon strips Blender's duplicate suffix from the object name to find the material:

| Object name | Material looked up |
|---|---|
| `Wes_body` | `Wes_body` |
| `Wes_body.001` | `Wes_body` |
| `BG_sky.014` | `BG_sky` |

If a material is not found, a warning appears in the Info bar.

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

1. Import your SVG into Blender
2. Select the collection containing all imported objects
3. Click **Apply & Sort** — geometry is processed and objects are sorted into sub-collections by prefix
4. Reorder collections in the outliner if needed (BG → characters → FG)
5. Click **Auto Stack** — objects are stacked by area within each collection, with Y offsets chaining between collections
6. Fine-tune with **− / +**, **Snap**, **Refresh**, **Override Single**, and **Override Same** as needed

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
- An asset library with a `.blend` file whose path contains `Paper` (for material assignment)

---

## License

GPL-2.0-or-later — see [Blender's extension licensing guidelines](https://extensions.blender.org/about/licenses/).
