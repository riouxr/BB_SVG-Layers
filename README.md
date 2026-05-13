# BB SVG Layers — Blender Addon

A Blender 4.2+ extension that automates the full pipeline for converting imported SVG layers into game-ready 3D paper cutout meshes. It handles geometry processing, UV projection, material creation from a master material, automatic export to the asset library, and intelligent layer stacking — including multi-character scenes with automatic collection sorting.

---

## What It Does

This addon is designed for **paper cutout 3D scenes** where SVG layers become individual mesh pieces stacked along the depth axis, simulating physical depth between paper layers.

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
| **Layer Step** | Depth separation added between each overlapping layer during stacking. Used by both Auto Stack and Load SVG. Default: `1` |

---

### Buttons

#### Load SVG
Imports an SVG file and automatically runs the full pipeline in one step:

1. **Reads layer order** from the SVG XML before importing, so document order is preserved regardless of Blender's alphabetical import behaviour
2. **Imports the SVG** via Blender's built-in importer
3. **Selects the new collection** created by the importer
4. **Runs Apply & Sort** — full geometry pipeline on every object in the collection, then sorts them into sub-collections by name prefix:
   - `BG_` objects → **BG** collection
   - Character objects (`Wes_`, `Dad_`, etc.) → one collection per character
   - `FG_` objects → **FG** collection

   Steps performed on each object:
   - Rotate +90° on X, Scale ×850, Convert curves to mesh, Apply all transforms
   - Merge by Distance, Solidify (thickness `1`, applied), Offset back faces (`-2` X, `+2` Z)
   - UV projection from Y onto 1920×1920 px canvas; back/side faces pinned to `(0, 0)`
   - **Create material** by copying the **Master** material, injecting the fill color read from the SVG, and assigning it to the object
   - **Export all created materials** to the **Paper** catalog in the User asset library

5. **Runs Auto Stack** — stacks all objects using a greedy layer-packing algorithm:
   - Objects below the **Tiny Object Threshold** go to the frontmost layer of their group
   - Each remaining object is placed in the earliest layer where it doesn't overlap anything already there
   - Overlap is detected using **face-polygon intersection** in screen space (not bounding boxes), so concave shapes such as U-shapes or cutouts are handled correctly — objects sitting inside a hole are not flagged as overlapping
   - Collections are processed in outliner order — reorder them in the outliner before loading if needed
   - Y offset between every layer is controlled by the **Layer Step** slider

---

#### − / + Buttons
Move all selected objects forward or back along the active viewport's view axis by one **Layer Step**. In perspective view, objects are radially scaled from the camera origin so their apparent size is preserved. Useful for fine-tuning individual layers after Auto Stack.

#### Snap
Snaps all selected objects to the **highest Y value** among them — useful for aligning pieces that should be on the same layer.

#### Manual
Runs the full geometry pipeline on the **currently selected objects** without importing an SVG. Useful when bringing in meshes that weren't created via Load SVG, or for re-processing existing objects.

Steps performed on each selected object:
- Solidify (thickness `1`, applied), Offset back faces (`-2` X, `+2` Z)
- UV projection from Y; back/side faces pinned to `(0, 0)`
- Create or update material from the **Master** template

#### Auto Stack
Re-stacks the **currently selected objects** along the **active camera / viewport depth axis**. Unlike the Load SVG auto-stack (which uses SVG document order), this button sorts objects by their current depth from the camera and then separates overlapping ones by **Layer Step**.

- Depth is measured as the **vertex-average position** projected onto the view direction — no bounding-box inflation from rotated objects
- Overlap is tested by projecting each object's actual **face polygons** onto the 2-D view plane, correctly handling concave shapes
- In perspective view, objects are radially scaled from the camera origin so their screen size is preserved after stacking
- Non-overlapping objects are left at their current depth — only objects that actually collide in screen space are moved
- Runs multiple passes until no further moves are needed, resolving cascading overlaps in a single button press

> **Tip:** Point your camera at the scene before clicking — the stacking axis follows whatever view is active.

#### Revert
Removes the solidify thickness and back faces added by **Manual**, leaving only the original flat front-facing mesh. Useful when you want to return objects to their pre-Manual state for further editing or a different processing pass.

- Identifies front faces by **vertex depth**: a face is kept if all its vertices sit at the minimum depth along the view axis (closest to camera)
- Back faces and solidify side/thickness faces all have at least one vertex deeper and are deleted
- Operates on all selected mesh objects in a single pass
- Reports how many faces were removed per object in the header

> **Note:** Revert must be used **after Manual** — if the object is still flat (no solidify applied), a warning is shown and nothing is deleted.

---

#### Override Single
Makes a **single-user copy** of the assigned material for each selected object, then creates a **library override** so it can be edited independently without affecting other objects. The overridden material is named `<prefix>_override` (e.g. `Wes_body_override`).

#### Override Same
Same as Override Single, but after creating the override it **reassigns it to every object in the scene** that was using the same original material. Use this when multiple objects share one material and you want them all to switch to the same editable override in one click.

---

## Master Material Setup

Materials are created automatically at import time by copying a local material named **`Master`** and injecting the fill color read from the SVG file.

### Steps to set up

1. In your `.blend` file, create a material named exactly **`Master`**
2. Set up its node tree however you like — the addon will find the first `RGB` node, node named `Color`, Principled BSDF Base Color input, or any `RGBA` input named `Color`, and write the SVG fill color into it
3. If your Master material has a **Mapping** node, its Z rotation will be randomised on each copy for natural texture variation

All created materials are automatically marked as assets and written to a **Paper** catalog in your configured User asset library.

### Name Matching

The addon derives the material name from the object name by stripping Blender's duplicate suffix:

| Object name | Material created |
|---|---|
| `Wes_body` | `Wes_body` |
| `Wes_body.001` | `Wes_body` |
| `BG_sky.014` | `BG_sky` |

If a material named `<prefix>` already exists locally it is reused and its color updated rather than creating a duplicate.

---

## Naming Convention

For multi-character scenes, name your SVG layers with prefixes:

| Prefix | Goes into | Stacked |
|---|---|---|
| `BG_` | **BG** collection | Furthest back |
| `Wes_`, `Dad_`, etc. | Per-character collection | Middle, in outliner order |
| `FG_` | **FG** collection | Closest to camera |

---

## Typical Workflows

### Full automatic pipeline
1. Create a material named **`Master`** in your `.blend` file and set up its node tree
2. *(Optional)* Reorder collections in the outliner to set the desired BG → characters → FG depth order
3. Click **Load SVG** and select your file — geometry processing, material creation, collection sorting, and layer stacking all run automatically
4. Fine-tune with **− / +**, **Snap**, **Override Single**, and **Override Same** as needed

### Manual pipeline (selected objects)
1. Select the objects you want to process
2. Click **Manual** — applies solidify, UV projection, and material assignment
3. Point the camera at your scene, select the processed objects, and click **Auto Stack** — objects are separated along the camera depth axis
4. *(Optional)* Click **Revert** to strip the solidify back and return objects to flat meshes

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
- A local material named **`Master`** in the current `.blend` file (used as the template for all created materials)
- A configured asset library in **Preferences → File Paths → Asset Libraries** (for exporting the generated materials)

---

## License

GPL-2.0-or-later — see [Blender's extension licensing guidelines](https://extensions.blender.org/about/licenses/).
