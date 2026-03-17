# SVG Layer — Blender Addon

A Blender 4.2+ extension that automates the full pipeline for converting imported SVG layers into game-ready 3D meshes, stacked along the Y axis with materials auto-assigned from an asset library.

---

## What It Does

With a single button click, the addon processes every object in the active collection (or viewport selection) and runs the following steps on each one:

1. **Rotate +90° on X** — flattens the SVG from Blender's default import orientation onto the XZ plane
2. **Scale ×850** — converts SVG units to a usable world scale
3. **Convert curves to mesh** — handles 2D curves before transforms are applied (required by Blender)
4. **Apply all transforms** — bakes location, rotation and scale into the mesh
5. **Merge by Distance** — cleans up duplicate vertices left over from the curve conversion
6. **Y offset** — stacks each object along the -Y axis by a cumulative amount; the last object in the list is the front layer (no offset), and each previous object steps further back
7. **UV projection from Y** — assigns UVs by projecting from the Y axis onto a 1920×1920 px canvas (`U = X / 1920`, `V = Z / 1920`)
8. **Solidify modifier** — adds thickness of `0.003` to each mesh
9. **Material assignment** — appends and assigns the matching material from the **Paper** asset library, matched by the object's name prefix (e.g. object `Sky.002` → material `Sky`)

---

## Installation

1. Download `svg_layer.zip` from the [Releases](../../releases) page
2. In Blender: **Edit → Preferences → Add-ons**
3. Drag and drop `svg_layer.zip` into the Preferences window, or use the dropdown **▾ → Install from Disk**
4. Enable **SVG Layer** in the addon list

> Requires **Blender 4.2 or later** (uses the extension manifest format).

---

## Usage

### Panel Location
`3D Viewport → N-Panel (N key) → SVG Layer tab`

### Controls

| Control | Description |
|---|---|
| **Y Offset per Layer** | Distance between each layer along -Y. Default: `0.1` |
| **Apply SVG Layers** | Runs the full pipeline on the active collection or selected objects |

### Target Selection

The addon operates on objects in this priority order:

- **Active collection** — if a collection is active in the Outliner (highlighted), all objects inside it are processed, including sub-collections
- **Viewport selection** — fallback if no collection is active; uses whatever objects are selected in the 3D viewport

### Layer Order

Objects are processed in list order. The **last object** is the front layer and receives no Y offset. Each earlier object is pushed further back by one step:

```
obj 1  →  -(n-1) × offset   (furthest back)
obj 2  →  -(n-2) × offset
...
obj n  →  0                  (front, base)
```

---

## Asset Library Setup

Materials are fetched automatically from a Blender asset library named **Paper**.

### Steps to set up

1. Create or locate your materials `.blend` file
2. Open it and mark each material as an asset: right-click the material in the **Asset Browser → Mark as Asset**
3. In Blender **Preferences → File Paths → Asset Libraries**, add your library folder and name it exactly `Paper`

### Name Matching

The addon strips the Blender duplicate suffix from the object name to find the material:

| Object name | Material looked up |
|---|---|
| `Sky` | `Sky` |
| `Sky.001` | `Sky` |
| `Trees.042` | `Trees` |

If a material is not found in the library, a warning is printed to the Info bar and the system console.

---

## File Structure

```
svg_layer/
├── __init__.py            # Addon code
└── blender_manifest.toml  # Extension manifest (Blender 4.2+)
```

---

## Requirements

- Blender 4.2 or later
- An asset library named `Paper` registered in Blender Preferences (for material assignment)

---

## License

GPL-2.0-or-later — see [Blender's extension licensing guidelines](https://extensions.blender.org/about/licenses/).
