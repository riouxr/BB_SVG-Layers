import bpy
import math
import mathutils
import os
import re
import random
import hashlib
import xml.etree.ElementTree as ET


# ─────────────────────────────────────────────
#  Collection / object helpers
# ─────────────────────────────────────────────

def get_active_collection(context):
    layer_col = context.view_layer.active_layer_collection
    if layer_col:
        return layer_col.collection
    return None


def collect_from_collection(col):
    objects = []
    seen = set()
    def recurse(c):
        for obj in c.objects:
            if obj.name not in seen:
                objects.append(obj)
                seen.add(obj.name)
        for child in c.children:
            recurse(child)
    recurse(col)
    return objects


def gather_objects(context):
    col = get_active_collection(context)
    if col is not None and col != context.scene.collection:
        return collect_from_collection(col)
    return list(context.selected_objects)


# ─────────────────────────────────────────────
#  SVG color parsing
# ─────────────────────────────────────────────

CSS_COLORS = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000",
    "green": "#008000", "blue": "#0000ff", "yellow": "#ffff00",
    "cyan": "#00ffff", "magenta": "#ff00ff", "orange": "#ffa500",
    "pink": "#ffc0cb", "purple": "#800080", "brown": "#a52a2a",
    "gray": "#808080", "grey": "#808080", "lime": "#00ff00",
    "navy": "#000080", "teal": "#008080", "silver": "#c0c0c0",
}


def hex_to_linear(hex_color):
    """Convert a CSS hex / rgb() / named color string to a linear-space RGBA tuple."""
    hex_color = hex_color.strip()
    if hex_color.lower() in CSS_COLORS:
        hex_color = CSS_COLORS[hex_color.lower()]
    m = re.match(r'rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', hex_color)
    if m:
        r, g, b = int(m.group(1)) / 255.0, int(m.group(2)) / 255.0, int(m.group(3)) / 255.0
    else:
        hex_color = hex_color.lstrip("#")
        if len(hex_color) == 3:
            hex_color = "".join(c * 2 for c in hex_color)
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0

    def s2l(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return (s2l(r), s2l(g), s2l(b), 1.0)


def _fill_from_style(style):
    m = re.search(r'fill\s*:\s*(#[0-9a-fA-F]{3,6}|rgb\s*\([^)]+\)|[a-zA-Z]+)', style)
    if m:
        val = m.group(1)
        if val.lower() == "none":
            return None
        return val
    return None


def get_fill_color(element):
    style = element.get("style", "")
    if style:
        result = _fill_from_style(style)
        if result:
            return result
    fill = element.get("fill", "")
    if fill and fill.lower() not in ("", "none"):
        return fill
    return None


def collect_fill_from_ancestors(elem, root):
    parent_map = {c: p for p in root.iter() for c in p}
    node = elem
    while node in parent_map:
        node = parent_map[node]
        color = get_fill_color(node)
        if color:
            return color
    return None


def parse_svg_colors(svg_path):
    """
    Parse an SVG and return (prefix_color, svg_id_order).
      prefix_color  - dict  prefix -> CSS color string
      svg_id_order  - list of element IDs (those containing '.') in document order
    """
    tree = ET.parse(svg_path)
    root = tree.getroot()
    prefix_color = {}
    svg_id_order = []
    for elem in root.iter():
        elem_id = elem.get("id", "")
        if "." not in elem_id:
            continue
        svg_id_order.append(elem_id)
        prefix = elem_id.split(".")[0]
        if prefix not in prefix_color:
            color = get_fill_color(elem)
            if not color:
                color = collect_fill_from_ancestors(elem, root)
            if color:
                prefix_color[prefix] = color
            else:
                print(f"[SVG Layer] No fill for prefix={prefix!r} id={elem_id!r} — defaulting to #000000")
                prefix_color[prefix] = "#000000"
    return prefix_color, svg_id_order


# ─────────────────────────────────────────────
#  Material creation from Master
# ─────────────────────────────────────────────

def set_color_in_nodetree(node_tree, linear_color):
    """Inject a linear RGBA color into the first suitable node in a material node tree."""
    nodes = node_tree.nodes
    if "Color" in nodes:
        n = nodes["Color"]
        if n.outputs and hasattr(n.outputs[0], "default_value"):
            n.outputs[0].default_value = linear_color
            return
    for n in nodes:
        if n.type == "RGB":
            n.outputs[0].default_value = linear_color
            return
    for n in nodes:
        if n.type == "BSDF_PRINCIPLED":
            n.inputs["Base Color"].default_value = linear_color
            return
    for n in nodes:
        for inp in n.inputs:
            if inp.name == "Color" and inp.type == "RGBA":
                inp.default_value = linear_color
                return


def set_mapping_rotation_z(node_tree):
    """Randomise the Z rotation of the first Mapping node (texture variation)."""
    for n in node_tree.nodes:
        if n.type == "MAPPING":
            n.inputs["Rotation"].default_value[2] = math.radians(random.uniform(0, 360))
            return


def get_or_create_material(prefix, linear_color, master_mat):
    """Return an existing material named *prefix* or copy it from master_mat,
    then set its color."""
    if prefix in bpy.data.materials:
        mat = bpy.data.materials[prefix]
    else:
        mat = master_mat.copy()
        mat.name = prefix
    if mat.use_nodes:
        set_color_in_nodetree(mat.node_tree, linear_color)
        set_mapping_rotation_z(mat.node_tree)
    return mat


# ─────────────────────────────────────────────
#  Asset library export
# ─────────────────────────────────────────────

def get_user_library_path():
    prefs = bpy.context.preferences
    libs = prefs.filepaths.asset_libraries
    for lib in libs:
        if lib.name.lower() == "user library":
            return bpy.path.abspath(lib.path)
    if libs:
        return bpy.path.abspath(libs[0].path)
    return None


def _ensure_catalog(lib_root, catalog_name):
    cats_path = os.path.join(lib_root, "blender_assets.cats.txt")
    h = hashlib.md5(catalog_name.encode()).hexdigest()
    uid = f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
    entry = f"{uid}:{catalog_name}:{catalog_name}\n"
    existing = ""
    if os.path.exists(cats_path):
        with open(cats_path, "r", encoding="utf-8") as f:
            existing = f.read()
    if uid not in existing:
        with open(cats_path, "a", encoding="utf-8") as f:
            if not existing:
                f.write("VERSION 1\n")
            f.write(entry)
    return uid


def _load_master_from_library():
    """Try to load the 'Master' material from the Paper asset library if it
    is not already present in the current file.  Returns the material or None."""
    if bpy.data.materials.get("Master"):
        return bpy.data.materials["Master"]

    lib_root = get_user_library_path()
    if lib_root is None:
        return None

    # Walk every .blend inside the library looking for a material named Master
    for root, dirs, files in os.walk(lib_root):
        for fname in files:
            if not fname.lower().endswith(".blend"):
                continue
            blend_path = os.path.join(root, fname)
            # Peek at the material names without fully loading the file
            with bpy.data.libraries.load(blend_path, link=False) as (src, dst):
                if "Master" in src.materials:
                    dst.materials = ["Master"]
            # dst.materials is populated after the with-block closes
            mat = bpy.data.materials.get("Master")
            if mat:
                return mat

    return None


def mark_and_export_materials(materials, catalog_name="Paper"):
    """Mark materials as assets and write them into the Paper catalog in the User Library."""
    lib_root = get_user_library_path()
    if lib_root is None:
        return False, "No asset library configured in Preferences > File Paths > Asset Libraries."

    uid = _ensure_catalog(lib_root, catalog_name)

    for mat in materials:
        if not mat.asset_data:
            mat.asset_mark()
        if mat.asset_data is None:
            print(f"[SVG Layer] Warning: could not mark '{mat.name}' as asset, skipping.")
            continue
        mat.asset_data.catalog_id = uid
        mat.preview_ensure()

    bpy.ops.wm.previews_ensure()

    target_dir = os.path.join(lib_root, catalog_name)
    os.makedirs(target_dir, exist_ok=True)
    target_blend = os.path.join(target_dir, f"{catalog_name}.blend")

    bpy.data.libraries.write(
        target_blend,
        set(materials),
        fake_user=True,
        compress=False,
    )

    return True, target_blend


# ─────────────────────────────────────────────
#  Collection sorting helpers
# ─────────────────────────────────────────────

BG_PREFIX = "BG_"
FG_PREFIX = "FG_"


def get_object_prefix(obj):
    name = obj.name
    idx = name.find('_')
    if idx == -1:
        return name
    return name[:idx + 1]


def get_or_create_collection(parent_col, name):
    for child in parent_col.children:
        if child.name == name:
            return child
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    if col.name not in [c.name for c in parent_col.children]:
        parent_col.children.link(col)
    return col


def move_object_to_collection(obj, target_col):
    for col in list(obj.users_collection):
        try:
            col.objects.unlink(obj)
        except Exception:
            pass
    if obj.name not in target_col.objects:
        target_col.objects.link(obj)


def outliner_object_order(context, objects):
    vl_objects = list(context.view_layer.objects)
    order_map = {obj.name: i for i, obj in enumerate(vl_objects)}
    return sorted(objects, key=lambda o: order_map.get(o.name, 9999))


def group_objects_by_prefix(objects):
    bg_objs = []
    fg_objs = []
    char_groups = {}
    char_order = []
    for obj in objects:
        prefix = get_object_prefix(obj)
        if prefix == BG_PREFIX:
            bg_objs.append(obj)
        elif prefix == FG_PREFIX:
            fg_objs.append(obj)
        else:
            if prefix not in char_groups:
                char_groups[prefix] = []
                char_order.append(prefix)
            char_groups[prefix].append(obj)
    ordered_char_groups = [(p, char_groups[p]) for p in char_order]
    return bg_objs, ordered_char_groups, fg_objs


# ─────────────────────────────────────────────
#  Layer packing helpers
# ─────────────────────────────────────────────

def pack_layers(ordered_objects, step):
    """Stack SVG layers along world Y (front-ortho view_fwd).
    Delegates to pack_layers_view with preserve_order=True so the SVG
    document order is respected rather than re-sorting by depth.
    Uses face-polygon narrowphase overlap — same as Auto Stack — so
    concave shapes (holes, U-shapes, etc.) are handled correctly.
    """
    # SVG imports arrive as a front-view ortho scene: Y is depth,
    # XZ is the screen plane.
    view_fwd = mathutils.Vector((0.0, 1.0, 0.0))
    pack_layers_view(ordered_objects, step, view_fwd,
                     cam_pos=None, preserve_order=True)


def _vert_avg_depth(obj, view_fwd):
    """Average world-space vertex position projected onto view_fwd."""
    verts = obj.data.vertices
    if not verts:
        return 0.0
    total = sum((obj.matrix_world @ v.co).dot(view_fwd) for v in verts)
    return total / len(verts)


def _obj_view_info(obj, view_fwd, u, v_ax):
    """Return (depth, screen_bbox, screen_polys) for *obj*.

    depth        - vertex-average depth along view_fwd.
    screen_bbox  - (u_min, u_max, v_min, v_max) fast broadphase reject box.
    screen_polys - list of 2-D face polygons [(u,v), ...] for narrowphase.
                   Only actual face geometry — concave holes are represented
                   faithfully because we iterate faces, not the convex hull.
    """
    mesh = obj.data
    world_verts = [obj.matrix_world @ v.co for v in mesh.vertices]
    if not world_verts:
        return 0.0, (0.0, 0.0, 0.0, 0.0), []

    depths = [wv.dot(view_fwd) for wv in world_verts]
    us2d   = [wv.dot(u)        for wv in world_verts]
    vs2d   = [wv.dot(v_ax)     for wv in world_verts]

    screen_polys = [
        [(us2d[vi], vs2d[vi]) for vi in face.vertices]
        for face in mesh.polygons
    ]
    bbox = (min(us2d), max(us2d), min(vs2d), max(vs2d))
    return sum(depths) / len(depths), bbox, screen_polys


def _screen_bboxes_overlap(a, b):
    au0, au1, av0, av1 = a
    bu0, bu1, bv0, bv1 = b
    return au1 > bu0 and bu1 > au0 and av1 > bv0 and bv1 > av0


def _pt_in_poly_2d(pt, poly):
    """Ray-casting point-in-polygon test for a 2-D polygon."""
    x, y  = pt
    n     = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _segs_intersect_2d(p1, p2, p3, p4):
    """True if segment p1-p2 properly intersects segment p3-p4."""
    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    d1 = cross(p3, p4, p1)
    d2 = cross(p3, p4, p2)
    d3 = cross(p1, p2, p3)
    d4 = cross(p1, p2, p4)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and        ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    return False


def _polys_overlap_2d(pa, pb):
    """True if 2-D polygon pa and pb overlap (vertex-in-poly or edge cross)."""
    # Any vertex of pa inside pb?
    for pt in pa:
        if _pt_in_poly_2d(pt, pb):
            return True
    # Any vertex of pb inside pa?
    for pt in pb:
        if _pt_in_poly_2d(pt, pa):
            return True
    # Any edge pair crossing?
    na, nb = len(pa), len(pb)
    for i in range(na):
        for j in range(nb):
            if _segs_intersect_2d(pa[i], pa[(i+1)%na], pb[j], pb[(j+1)%nb]):
                return True
    return False


def _meshes_screen_overlap(bbox_a, polys_a, bbox_b, polys_b):
    """True if any face of mesh A overlaps any face of mesh B in screen space.
    Uses bounding-box broadphase to skip pairs that can't possibly overlap,
    then per-face polygon intersection for the narrowphase.  This correctly
    handles concave shapes — a U-shape's hole is not covered by any face,
    so objects sitting inside the hole are not flagged as overlapping.
    """
    # Broadphase: if overall bboxes don't touch, nothing overlaps.
    if not _screen_bboxes_overlap(bbox_a, bbox_b):
        return False
    # Narrowphase: face-level polygon intersection.
    for pa in polys_a:
        for pb in polys_b:
            if _polys_overlap_2d(pa, pb):
                return True
    return False


def pack_layers_view(objects, step, view_fwd, cam_pos=None, preserve_order=False):
    """Stack objects along an arbitrary view axis with face-polygon overlap detection.

    preserve_order=True  : use objects as-is (SVG document order from load_svg).
                           First object gets depth 0, others step back only on overlap.
    preserve_order=False : sort furthest-first by current vertex-average depth
                           (Auto Stack on selected).

    cam_pos=None  : ortho — translate obj.location along view_fwd.
    cam_pos=Vector: perspective — radially scale vertices from camera origin so
                    apparent size is preserved (same as +/- buttons).

    Overlap is tested face-by-face in 2-D screen space so concave shapes
    (U-shapes, holes) are handled correctly — the hole is not covered by any
    face, so objects sitting inside it are not flagged as overlapping.

    Returns the number of passes taken to converge.
    """
    if not objects:
        return 0

    ref = mathutils.Vector((0.0, 0.0, 1.0))
    if abs(view_fwd.dot(ref)) > 0.9:
        ref = mathutils.Vector((1.0, 0.0, 0.0))
    u    = view_fwd.cross(ref).normalized()
    v_ax = view_fwd.cross(u).normalized()

    max_passes = 20
    for iteration in range(max_passes):
        infos = []
        for obj in objects:
            depth, sbbox, spolys = _obj_view_info(obj, view_fwd, u, v_ax)
            infos.append({'obj': obj, 'depth': depth, 'sbbox': sbbox, 'spolys': spolys})

        if not preserve_order:
            infos.sort(key=lambda x: x['depth'], reverse=True)

        placed    = []  # list of (placed_depth, sbbox, spolys)
        any_moved = False
        base_depth = 0.0 if preserve_order else None

        for info in infos:
            obj           = info['obj']
            sbbox         = info['sbbox']
            spolys        = info['spolys']
            current_depth = info['depth']

            blocking = [d for d, bb, bp in placed
                        if _meshes_screen_overlap(sbbox, spolys, bb, bp)]

            if preserve_order:
                # SVG mode: first object anchors at 0, each overlapping layer
                # steps back by one step (negative Y = further from camera).
                if not placed:
                    target_depth = 0.0
                elif blocking:
                    target_depth = min(blocking) - step
                else:
                    target_depth = 0.0
            else:
                # Auto Stack mode: stay put unless too close to a blocker.
                target_depth = min(blocking) - step if blocking else current_depth

            delta = target_depth - current_depth

            if abs(delta) > 1e-4:
                any_moved = True
                if cam_pos is not None:
                    _move_obj_perspective(obj, cam_pos, view_fwd, delta)
                else:
                    _move_obj_ortho(obj, view_fwd, delta)
                depth_after = _vert_avg_depth(obj, view_fwd)
                print(f"  [pass {iteration+1}] {obj.name}: {current_depth:.3f} -> {depth_after:.3f}  target={target_depth:.3f}")

            placed.append((target_depth, sbbox, spolys))

        if not any_moved:
            print(f"[Auto Stack] converged in {iteration+1} pass(es)")
            return iteration + 1

    print(f"[Auto Stack] did not converge in {max_passes} passes")
    return max_passes


# ─────────────────────────────────────────────
#  SVG layer-order reader
# ─────────────────────────────────────────────

_INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"


def _svg_element_id(el):
    return el.get("id") or el.get("{%s}label" % _INKSCAPE_NS)


def read_svg_layer_order(filepath):
    """
    Parse an SVG and return element IDs in document order (bottom-of-stack first).
    Handles flat paths, Inkscape layer groups, and Affinity-style plain <g> groups.
    """
    try:
        tree = ET.parse(filepath)
    except Exception as e:
        print(f"SVG Layer: Could not parse '{filepath}': {e}")
        return []

    root = tree.getroot()

    def local(tag):
        return tag.split("}")[-1] if "}" in tag else tag

    ids = []

    def collect(element):
        for child in element:
            tag = local(child.tag)
            eid = _svg_element_id(child)
            if tag == "g":
                child_ids = []
                for grandchild in child:
                    gid = _svg_element_id(grandchild)
                    if gid:
                        child_ids.append(gid)
                    else:
                        collect(grandchild)
                if eid and not child_ids:
                    ids.append(eid)
                else:
                    ids.extend(child_ids)
                    for grandchild in child:
                        if local(grandchild.tag) == "g":
                            collect(grandchild)
            elif tag in ("path", "rect", "circle", "ellipse", "polygon",
                         "polyline", "line", "use", "image", "text"):
                if eid:
                    ids.append(eid)

    collect(root)
    return ids


def apply_svg_layer_order(objects, svg_id_order, step=3.0):
    order_map = {eid: i for i, eid in enumerate(svg_id_order)}
    matched = []
    for obj in objects:
        base_name = obj.name.split(".")[0]
        idx = order_map.get(base_name)
        if idx is not None:
            matched.append((idx, obj))
    matched.sort(key=lambda x: x[0])
    ordered = [obj for _, obj in matched]
    pack_layers(ordered, step)
    return len(ordered)


# ─────────────────────────────────────────────
#  Operator: Apply SVG Layers
# ─────────────────────────────────────────────

class SVG_OT_ApplyLayers(bpy.types.Operator):
    """Rotate, scale, convert curves, merge, UV-project, solidify, create materials
    from Master using SVG fill colors, export to Paper library, and sort into
    sub-collections by prefix (BG_, characters, FG_)."""
    bl_idname = "svg_layer.apply_layers"
    bl_label = "Apply & Sort"
    bl_options = {'REGISTER', 'UNDO'}

    # Supplied by SVG_OT_LoadSVG so colors can be read from the SVG.
    svg_filepath: bpy.props.StringProperty(default="", options={'SKIP_SAVE'})

    def execute(self, context):
        objects = gather_objects(context)

        if not objects:
            self.report({'WARNING'}, "No objects found.")
            return {'CANCELLED'}

        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        master_mat = _load_master_from_library()
        if master_mat is None:
            self.report({'ERROR'},
                "No 'Master' material found locally or in any asset library. "
                "Open the Paper library in the Asset Browser and append 'Master' manually.")
            return {'CANCELLED'}

        # Parse fill colors from SVG
        prefix_color_map = {}  # prefix -> linear RGBA tuple
        if self.svg_filepath:
            try:
                css_colors, _ = parse_svg_colors(self.svg_filepath)
                for prefix, hex_col in css_colors.items():
                    prefix_color_map[prefix] = hex_to_linear(hex_col)
            except Exception as exc:
                self.report({'ERROR'}, f"SVG color parse error: {exc}")
                return {'CANCELLED'}

        # ── Geometry processing ───────────────────────────────────────────
        processed = []

        for obj in objects:
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            context.view_layer.objects.active = obj

            obj.rotation_euler[0] += math.radians(90)
            obj.scale = (obj.scale[0] * 850, obj.scale[1] * 850, obj.scale[2] * 850)

            if obj.type == 'CURVE':
                bpy.ops.object.convert(target='MESH')
                obj = context.view_layer.objects.active

            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

            if obj.type == 'MESH':
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.remove_doubles(threshold=0.0001)
                bpy.ops.object.mode_set(mode='OBJECT')

            for m in [m for m in obj.modifiers if m.type == 'SOLIDIFY']:
                obj.modifiers.remove(m)
            solidify = obj.modifiers.new(name="Solidify", type='SOLIDIFY')
            solidify.thickness = 1
            solidify.offset = -1.0
            bpy.ops.object.modifier_apply(modifier="Solidify")

            if obj.type == 'MESH':
                mesh = obj.data
                back_vert_indices = set()
                for poly in mesh.polygons:
                    if poly.normal.y > 0.5:
                        back_vert_indices.update(poly.vertices)
                for v in mesh.vertices:
                    if v.index in back_vert_indices:
                        v.co.x -= 2
                        v.co.z += 2
                mesh.update()

            if obj.type == 'MESH':
                mesh = obj.data
                if not mesh.uv_layers:
                    mesh.uv_layers.new(name="UVMap")
                uv_layer = mesh.uv_layers.active
                canvas = 1920.0
                for poly in mesh.polygons:
                    for loop_idx in poly.loop_indices:
                        loop = mesh.loops[loop_idx]
                        vert = mesh.vertices[loop.vertex_index]
                        if poly.normal.y < -0.5:
                            uv_layer.data[loop_idx].uv = (vert.co.x / canvas, vert.co.z / canvas)
                        else:
                            uv_layer.data[loop_idx].uv = (0.0, 0.0)

            processed.append(obj)

        # ── Create materials from Master and assign ───────────────────────
        created_mats = []

        for obj in processed:
            if obj.type != 'MESH':
                continue
            prefix = obj.name.split('.')[0]
            linear = prefix_color_map.get(prefix, hex_to_linear("#000000"))
            mat = get_or_create_material(prefix, linear, master_mat)
            if obj.data.materials:
                obj.data.materials[0] = mat
            else:
                obj.data.materials.append(mat)
            if mat not in created_mats:
                created_mats.append(mat)

        # ── Export to Paper asset library ─────────────────────────────────
        if created_mats:
            ok, result = mark_and_export_materials(created_mats, catalog_name="Paper")
            if ok:
                lib_msg = f"{len(created_mats)} material(s) exported to {result}"
            else:
                self.report({'WARNING'}, f"Materials created but library export failed: {result}")
                lib_msg = "library export failed"
        else:
            lib_msg = "no materials created"

        # ── Sort into sub-collections by prefix ──────────────────────────
        processed = outliner_object_order(context, processed)
        parent_col = get_active_collection(context)
        if parent_col is None:
            parent_col = context.scene.collection

        bg_objs, char_groups, fg_objs = group_objects_by_prefix(processed)

        groups = []
        if bg_objs:
            groups.append(("BG", bg_objs))
        for prefix, objs in char_groups:
            groups.append((prefix.rstrip('_'), objs))
        if fg_objs:
            groups.append(("FG", fg_objs))

        for col_name, objs in groups:
            col = get_or_create_collection(parent_col, col_name)
            for obj in objs:
                move_object_to_collection(obj, col)

        bpy.ops.object.select_all(action='DESELECT')
        for obj in processed:
            try:
                obj.select_set(True)
            except ReferenceError:
                pass

        col_names = [g[0] for g in groups]
        self.report({'INFO'},
            f"Applied {len(processed)} object(s) -> collections: {', '.join(col_names)}. {lib_msg}")
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Move -/+
# ─────────────────────────────────────────────

def _get_rv3d(context):
    """Return the RegionView3D of the active 3D viewport, or None."""
    rv3d = getattr(context, "region_data", None)
    if rv3d is None:
        space = getattr(context, "space_data", None)
        if space is not None and space.type == 'VIEW_3D':
            rv3d = space.region_3d
    if rv3d is None:
        screen = getattr(context, "screen", None)
        if screen is not None:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    rv3d = area.spaces.active.region_3d
                    break
    return rv3d


def _get_view_forward_world(context):
    """Return a unit vector in world space pointing in the direction the viewer
    is currently looking, based on the active 3D viewport.
    Returns None if no 3D view can be located."""
    rv3d = _get_rv3d(context)
    if rv3d is None:
        return None
    # In view space, the camera looks along -Z.
    return (rv3d.view_rotation @ mathutils.Vector((0.0, 0.0, -1.0))).normalized()


def _is_orthographic(context):
    """Return True when the active viewport is in orthographic (or any non-perspective) mode."""
    rv3d = _get_rv3d(context)
    if rv3d is None:
        return False
    return rv3d.view_perspective == 'ORTHO'


def _dominant_ortho_axis(view_fwd_world):
    """Return the world-space unit vector of the axis most aligned with view_fwd_world.
    For a standard front-ortho view (looking along -Y) this returns (0, -1, 0)."""
    ax = abs(view_fwd_world.x)
    ay = abs(view_fwd_world.y)
    az = abs(view_fwd_world.z)
    if ax >= ay and ax >= az:
        return mathutils.Vector((1.0 if view_fwd_world.x >= 0 else -1.0, 0.0, 0.0))
    elif ay >= ax and ay >= az:
        return mathutils.Vector((0.0, 1.0 if view_fwd_world.y >= 0 else -1.0, 0.0))
    else:
        return mathutils.Vector((0.0, 0.0, 1.0 if view_fwd_world.z >= 0 else -1.0))


def _move_obj_ortho(obj, axis_world, delta):
    """Translate all vertices of obj by delta along axis_world (world space).
    The object's origin (location) is NOT moved — only vertex data changes."""
    # Convert the world-space translation into local object space
    local_delta = obj.matrix_world.to_3x3().inverted() @ (axis_world * delta)
    for v in obj.data.vertices:
        v.co += local_delta
    obj.data.update()


def _get_camera_world_location(context):
    """Return the world-space position of the active camera, or the viewport
    eye position as a fallback."""
    cam = context.scene.camera
    if cam is not None:
        return cam.matrix_world.to_translation()
    rv3d = _get_rv3d(context)
    if rv3d is not None:
        return rv3d.view_matrix.inverted().to_translation()
    return None


def _move_obj_perspective(obj, cam_world_pos, view_fwd_world, step):
    """Move obj along the view ray by *step* world units while preserving its
    apparent angular size as seen from cam_world_pos.

    Depth is measured as vertex-average position along the view axis — same
    metric used by Auto Stack and Snap — so the step is always exactly *step*
    world units regardless of object rotation or bounding-box shape.

    Factor = (obj_depth - cam_depth + step) / (obj_depth - cam_depth)
    Applied as a radial scale of all vertices from the camera origin in local
    space, so angular size is conserved.
    """
    mw     = obj.matrix_world
    mw_inv = mw.inverted()

    # Vertex-average depth in world space — no bounding box.
    obj_depth = _vert_avg_depth(obj, view_fwd_world)
    cam_depth = cam_world_pos.dot(view_fwd_world)
    depth_from_cam = obj_depth - cam_depth

    if abs(depth_from_cam) < 1e-4:
        return

    factor    = (depth_from_cam + step) / depth_from_cam
    cam_local = mw_inv @ cam_world_pos

    for v in obj.data.vertices:
        v.co = cam_local + (v.co - cam_local) * factor

    obj.data.update()


class SVG_OT_MoveForward(bpy.types.Operator):
    """Move selected objects away from the viewer.
    Orthographic view: pure translation along the view axis.
    Camera / perspective view: scale from world origin so apparent size is preserved."""
    bl_idname = "svg_layer.move_forward"
    bl_label = "Move Forward"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        selected = set(o.name for o in context.selected_objects)
        if context.scene.svg_layer_all_others:
            objects = [o for o in context.scene.objects if o.name not in selected]
        else:
            objects = list(context.selected_objects)
        if not objects:
            self.report({'WARNING'}, "No objects selected." if not context.scene.svg_layer_all_others else "No unselected objects found.")
            return {'CANCELLED'}
        view_fwd = _get_view_forward_world(context)
        if view_fwd is None:
            self.report({'WARNING'}, "No 3D viewport found — cannot determine view direction.")
            return {'CANCELLED'}
        step = context.scene.svg_layer_step
        ortho = _is_orthographic(context)
        if not ortho:
            cam_pos = _get_camera_world_location(context)
            if cam_pos is None:
                self.report({'WARNING'}, "Could not find camera position.")
                return {'CANCELLED'}
        for obj in objects:
            if obj.type != 'MESH':
                continue
            if ortho:
                axis = _dominant_ortho_axis(view_fwd)
                _move_obj_ortho(obj, axis, step)
            else:
                _move_obj_perspective(obj, cam_pos, view_fwd, step)
        return {'FINISHED'}


class SVG_OT_MoveBack(bpy.types.Operator):
    """Move selected objects toward the viewer.
    Orthographic view: pure translation along the view axis.
    Camera / perspective view: scale from world origin so apparent size is preserved."""
    bl_idname = "svg_layer.move_back"
    bl_label = "Move Back"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        selected = set(o.name for o in context.selected_objects)
        if context.scene.svg_layer_all_others:
            objects = [o for o in context.scene.objects if o.name not in selected]
        else:
            objects = list(context.selected_objects)
        if not objects:
            self.report({'WARNING'}, "No unselected objects found." if context.scene.svg_layer_all_others else "No objects selected.")
            return {'CANCELLED'}
        view_fwd = _get_view_forward_world(context)
        if view_fwd is None:
            self.report({'WARNING'}, "No 3D viewport found — cannot determine view direction.")
            return {'CANCELLED'}
        step = context.scene.svg_layer_step
        ortho = _is_orthographic(context)
        if not ortho:
            cam_pos = _get_camera_world_location(context)
            if cam_pos is None:
                self.report({'WARNING'}, "Could not find camera position.")
                return {'CANCELLED'}
        for obj in objects:
            if obj.type != 'MESH':
                continue
            if ortho:
                axis = _dominant_ortho_axis(view_fwd)
                _move_obj_ortho(obj, axis, -step)
            else:
                _move_obj_perspective(obj, cam_pos, view_fwd, -step)
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Snap
# ─────────────────────────────────────────────

def _snap_obj_to_world_y(obj, cam_world_pos, target_y):
    """Scale obj's vertices radially from cam_world_pos so the bbox-centre
    world Y lands exactly on target_y.  Apparent screen size is preserved."""
    mw = obj.matrix_world
    mw_inv = mw.inverted()

    cam_local = mw_inv @ cam_world_pos

    corners_local = [mathutils.Vector(c) for c in obj.bound_box]
    center_local = sum(corners_local, mathutils.Vector()) / 8.0
    center_world_y = (mw @ center_local).y

    denom = center_world_y - cam_world_pos.y
    if abs(denom) < 1e-4:
        return  # degenerate — camera coincides with mesh plane

    # Exact factor: new_center_y = cam_y + (center_y - cam_y) * factor = target_y
    factor = (target_y - cam_world_pos.y) / denom

    if abs(factor) < 1e-6:
        return

    for v in obj.data.vertices:
        v.co = cam_local + (v.co - cam_local) * factor

    obj.data.update()


class SVG_OT_SnapY(bpy.types.Operator):
    """Snap all selected objects to the shallowest (closest to camera) depth
    among them, measured as vertex-average position along the view axis.
    Ortho: plain translation. Perspective: radial scale from camera origin
    so apparent size is preserved. No bounding boxes used."""
    bl_idname = "svg_layer.snap_y"
    bl_label = "Snap"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = [o for o in context.selected_objects if o.type == 'MESH']
        if not objects:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        view_fwd = _get_view_forward_world(context)
        if view_fwd is None:
            self.report({'WARNING'}, "No 3D viewport found.")
            return {'CANCELLED'}

        cam_pos = None
        if not _is_orthographic(context):
            cam_pos = _get_camera_world_location(context)
            if cam_pos is None:
                self.report({'WARNING'}, "Could not find camera position.")
                return {'CANCELLED'}

        # Use the active (highlighted) object as the depth target.
        active = context.active_object
        if active is None or active.type != 'MESH' or active not in objects:
            self.report({'WARNING'}, "No active mesh object — highlight one to snap to.")
            return {'CANCELLED'}

        # Vertex-average depth along the view axis — same metric as Auto Stack.
        depths = {obj: _vert_avg_depth(obj, view_fwd) for obj in objects}
        target_depth = depths[active]

        for obj in objects:
            delta = target_depth - depths[obj]
            if abs(delta) < 1e-6:
                continue
            if cam_pos is not None:
                _move_obj_perspective(obj, cam_pos, view_fwd, delta)
            else:
                _move_obj_ortho(obj, view_fwd, delta)

        return {'FINISHED'}


class SVG_OT_SnapToZero(bpy.types.Operator):
    """Snap all selected objects so their bbox-centre sits at Y=0.
    Orthographic: pure Y translation.
    Camera / perspective: radial scale from the camera origin."""
    bl_idname = "svg_layer.snap_to_zero"
    bl_label = "Snap to 0"
    bl_options = {'REGISTER', 'UNDO'}

    @staticmethod
    def _mesh_world_y(obj):
        corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
        return sum(c.y for c in corners) / 8.0

    def execute(self, context):
        objects = [o for o in context.selected_objects if o.type == 'MESH']
        if not objects:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        if not _is_orthographic(context):
            cam_pos = _get_camera_world_location(context)
            if cam_pos is None:
                self.report({'WARNING'}, "Could not find camera position.")
                return {'CANCELLED'}
            for obj in objects:
                _snap_obj_to_world_y(obj, cam_pos, 0.0)
        else:
            for obj in objects:
                delta_y = -self._mesh_world_y(obj)
                if abs(delta_y) < 1e-6:
                    continue
                local_delta = obj.matrix_world.to_3x3().inverted() @ mathutils.Vector((0.0, delta_y, 0.0))
                for v in obj.data.vertices:
                    v.co += local_delta
                obj.data.update()

        self.report({'INFO'}, f"Snapped {len(objects)} object(s) to Y=0.")
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Override Material
# ─────────────────────────────────────────────

class SVG_OT_OverrideMaterial(bpy.types.Operator):
    """Copy each selected object's material and make it fully local —
    no library links remain."""
    bl_idname = "svg_layer.override_material"
    bl_label = "Override Single"
    bl_options = {'REGISTER', 'UNDO'}

    @staticmethod
    def _make_local_copy(mat, new_name):
        """Return a fully local copy of mat with a fully independent node tree."""
        new_mat = mat.copy()
        new_mat.name = new_name

        # Break direct library link
        if new_mat.library is not None:
            new_mat.make_local()

        # Break override_library relationship
        if getattr(new_mat, "override_library", None) is not None:
            try:
                new_mat.make_local()
            except Exception:
                pass

        if new_mat.use_nodes and new_mat.node_tree:
            nt = new_mat.node_tree

            # Break node tree library / override links
            if nt.library is not None:
                nt.make_local()
            if getattr(nt, "override_library", None) is not None:
                try:
                    nt.make_local()
                except Exception:
                    pass

            # Walk nodes and localise any linked images or node-groups
            for node in nt.nodes:
                if hasattr(node, "image") and node.image is not None:
                    if node.image.library is not None:
                        node.image = node.image.copy()
                        node.image.make_local()
                if hasattr(node, "node_tree") and node.node_tree is not None:
                    ng = node.node_tree
                    if ng.library is not None:
                        node.node_tree = ng.copy()
                        node.node_tree.make_local()
                    if getattr(ng, "override_library", None) is not None:
                        try:
                            ng.make_local()
                        except Exception:
                            pass

        return new_mat

    def execute(self, context):
        # Override Single works on selected objects only — never the whole collection.
        objects = [o for o in context.selected_objects if o.type == 'MESH']

        if not objects:
            self.report({'WARNING'}, "No mesh objects found.")
            return {'CANCELLED'}

        overridden = 0
        already_local = 0
        skipped = []

        for obj in objects:
            # Read from the slot so we get what the object actually displays,
            # regardless of DATA/OBJECT link mode.
            if not obj.material_slots or obj.material_slots[0].material is None:
                skipped.append(obj.name)
                continue

            slot = obj.material_slots[0]
            mat  = slot.material

            # Skip if the slot is already a unique local OBJECT-linked copy
            # whose node tree is also private (single user).
            nt = mat.node_tree if (mat.use_nodes and mat.node_tree) else None
            nt_is_private = (nt is None or nt.users == 1)
            if (slot.link == 'OBJECT'
                    and mat.library is None
                    and getattr(mat, 'override_library', None) is None
                    and mat.users == 1
                    and nt_is_private):
                already_local += 1
                continue

            new_name = obj.name.split('.')[0] + "_local"
            new_mat  = self._make_local_copy(mat, new_name)

            # Switch the slot to OBJECT link so the assignment is per-object
            # and does not touch the shared mesh data.
            obj.material_slots[0].link     = 'OBJECT'
            obj.material_slots[0].material = new_mat
            overridden += 1

        if skipped:
            self.report({'INFO'},
                f"Localised {overridden} material(s). Skipped (no material): {', '.join(skipped)}")
        else:
            self.report({'INFO'}, f"Localised {overridden} material(s).")

        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Override Same Material
# ─────────────────────────────────────────────

class SVG_OT_OverrideMaterialSame(bpy.types.Operator):
    """For each selected object, make a fully local copy of its material and
    assign that local copy to ALL objects in the scene sharing the same original."""
    bl_idname = "svg_layer.override_material_same"
    bl_label = "Override Same"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Step 1 — collect unique source materials from selected objects.
        source_objects = [o for o in context.selected_objects if o.type == 'MESH']

        if not source_objects:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        local_map = {}  # original mat  -> one shared local copy
        skipped   = []

        for obj in source_objects:
            if not obj.material_slots or obj.material_slots[0].material is None:
                skipped.append(obj.name)
                continue
            original_mat = obj.material_slots[0].material
            if original_mat in local_map:
                continue
            # Skip if this material is already a unique local copy
            if (original_mat.library is None
                    and getattr(original_mat, 'override_library', None) is None):
                local_map[original_mat] = original_mat  # already local — reuse as-is
                continue
            new_name = original_mat.name.split('.')[0] + "_local"
            new_mat  = SVG_OT_OverrideMaterial._make_local_copy(original_mat, new_name)
            local_map[original_mat] = new_mat

        if not local_map:
            self.report({'WARNING'}, "No materials to localise.")
            return {'CANCELLED'}

        # Step 2 — assign the shared local copy to every scene object that
        #          uses one of the source materials, using OBJECT-linked slots
        #          so we never write to shared mesh data.
        assigned = 0
        for scene_obj in context.scene.objects:
            if scene_obj.type != 'MESH':
                continue
            for slot in scene_obj.material_slots:
                if slot.material in local_map:
                    local_mat = local_map[slot.material]
                    if slot.material is local_mat:
                        continue  # already the local copy, nothing to do
                    slot.link     = 'OBJECT'
                    slot.material = local_mat
                    assigned += 1

        msg = f"Localised {len(local_map)} material(s), assigned to {assigned} slot(s)."
        if skipped:
            self.report({'INFO'}, msg + f" Skipped (no material): {', '.join(skipped)}")
        else:
            self.report({'INFO'}, msg)

        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Auto Stack
# ─────────────────────────────────────────────
#  Operator: Purge Unused Materials
# ─────────────────────────────────────────────

class SVG_OT_PurgeUnusedMaterials(bpy.types.Operator):
    """Delete every material not assigned to any object slot in the scene.
    Works even when Blender's built-in purge finds nothing, because it
    ignores the fake-user flag and checks real slot assignments directly."""
    bl_idname  = "svg_layer.purge_unused_materials"
    bl_label   = "Purge Unused Materials"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Collect every material that is actually displayed.
        # slot.material already resolves DATA vs OBJECT link, so this covers
        # both link types without picking up mesh-data ghost materials that
        # are stored in mesh.materials but overridden at the object level.
        used = set()
        for obj in bpy.data.objects:
            for slot in obj.material_slots:
                if slot.material is not None:
                    used.add(slot.material)

        removed = 0
        for mat in list(bpy.data.materials):
            if mat not in used:
                mat.use_fake_user = False   # clear fake user so remove() works
                bpy.data.materials.remove(mat)
                removed += 1

        self.report({'INFO'},
            f"Removed {removed} unused material(s). "
            f"{len(bpy.data.materials)} remaining.")
        return {'FINISHED'}


# ─────────────────────────────────────────────

class SVG_OT_AutoStack(bpy.types.Operator):
    """Assign Y positions based on SVG document order (bottom layer = Y 0,
    each successive layer adds one step)."""
    bl_idname = "svg_layer.auto_stack"
    bl_label = "Auto Stack"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        step = context.scene.svg_layer_step
        objects = [o for o in gather_objects(context) if o.type == 'MESH']
        if not objects:
            self.report({'WARNING'}, "No mesh objects found.")
            return {'CANCELLED'}
        objects.sort(key=lambda o: o.location.y)
        pack_layers(objects, step)
        self.report({'INFO'}, f"Auto Stack: {len(objects)} objects packed at step={step}.")
        return {'FINISHED'}


class SVG_OT_AutoStackSelected(bpy.types.Operator):
    """Re-stack selected mesh objects from the camera / viewport view direction.
    Objects are sorted by their depth along the view axis and separated by
    Layer Step wherever their screen-space vertex footprints overlap."""
    bl_idname = "svg_layer.auto_stack_selected"
    bl_label = "Auto Stack"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = [o for o in context.selected_objects if o.type == 'MESH']
        self.report({'INFO'}, f"Auto Stack: found {len(objects)} mesh object(s).")
        if not objects:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        view_fwd = _get_view_forward_world(context)
        self.report({'INFO'}, f"view_fwd={view_fwd}")
        if view_fwd is None:
            self.report({'WARNING'}, "No 3D viewport found.")
            return {'CANCELLED'}

        cam_pos = _get_camera_world_location(context)
        self.report({'INFO'}, f"cam_pos={cam_pos}, ortho={_is_orthographic(context)}")
        if cam_pos is None and not _is_orthographic(context):
            self.report({'WARNING'}, "Could not find camera position.")
            return {'CANCELLED'}

        step = context.scene.svg_layer_step
        passes = pack_layers_view(objects, step, view_fwd, cam_pos=cam_pos)

        self.report({'INFO'},
            f"Auto Stack: {len(objects)} object(s), {passes} pass(es), step={step:.2f}.")
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Load SVG (order-aware import)
# ─────────────────────────────────────────────

class SVG_OT_LoadSVG(bpy.types.Operator):
    """Import an SVG, create materials from Master using SVG fill colors,
    export them to the Paper asset library, assign Y-depth from document order,
    then apply geometry processing and sort into sub-collections."""
    bl_idname = "svg_layer.load_svg"
    bl_label = "Load SVG"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty(
        name="File Path",
        description="Path to the SVG file to import",
        subtype='FILE_PATH',
        default="",
    )
    filter_glob: bpy.props.StringProperty(default="*.svg", options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        filepath = bpy.path.abspath(self.filepath)

        if not os.path.isfile(filepath):
            self.report({'ERROR'}, f"File not found: {filepath}")
            return {'CANCELLED'}

        master_mat = _load_master_from_library()
        if master_mat is None:
            self.report({'ERROR'},
                "No 'Master' material found locally or in any asset library. "
                "Open the Paper library in the Asset Browser and append 'Master' manually.")
            return {'CANCELLED'}

        svg_order = read_svg_layer_order(filepath)
        if not svg_order:
            self.report({'WARNING'},
                "Could not read layer order from SVG. Importing without depth assignment.")

        before = set(obj.name for obj in bpy.data.objects)

        try:
            bpy.ops.import_curve.svg(filepath=filepath)
        except Exception as e:
            self.report({'ERROR'}, f"SVG import failed: {e}")
            return {'CANCELLED'}

        new_objects = [obj for obj in bpy.data.objects if obj.name not in before]

        if not new_objects:
            self.report({'WARNING'}, "Import produced no new objects.")
            return {'CANCELLED'}

        if svg_order:
            step = context.scene.svg_layer_step
            n = apply_svg_layer_order(new_objects, svg_order, step=step)
            order_msg = f"depth assigned to {n}/{len(new_objects)} objects from SVG order"
        else:
            order_msg = "no SVG order found — depth unchanged"

        # Activate the collection the SVG importer created
        new_col = None
        for obj in new_objects:
            if obj.users_collection:
                new_col = obj.users_collection[0]
                break

        if new_col is not None:
            def find_layer_collection(layer_col, target_col):
                if layer_col.collection == target_col:
                    return layer_col
                for child in layer_col.children:
                    result = find_layer_collection(child, target_col)
                    if result is not None:
                        return result
                return None
            lc = find_layer_collection(context.view_layer.layer_collection, new_col)
            if lc is not None:
                context.view_layer.active_layer_collection = lc

        # Pass the SVG path to apply_layers so it can read fill colors
        bpy.ops.svg_layer.apply_layers(svg_filepath=filepath)
        bpy.ops.svg_layer.auto_stack()

        # Random UV rotation on all newly imported mesh objects
        for obj in bpy.data.objects:
            if obj.name not in before and obj.type == 'MESH':
                _random_rotate_uvs(obj)

        self.report({'INFO'},
            f"Loaded {len(new_objects)} object(s) from '{os.path.basename(filepath)}' — {order_msg}.")
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Hole Boolean
# ─────────────────────────────────────────────

def _mesh_avg_y(obj):
    """Return the average world Y of an object's vertices."""
    verts = obj.data.vertices
    if not verts:
        return 0.0
    mw = obj.matrix_world
    return sum((mw @ v.co).y for v in verts) / len(verts)


class SVG_OT_HoleBoolean(bpy.types.Operator):
    """Boolean-subtract the active (highlighted) object from every other
    selected object.  Works on flat SVG layers: the cutter is snapped to
    Y=0 and given a deep solidify so it punches through any layer depth."""
    bl_idname = "svg_layer.hole_boolean"
    bl_label = "Hole"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        cutter = context.active_object
        if cutter is None or cutter.type not in {'MESH', 'CURVE'}:
            self.report({'WARNING'}, "Make the hole-shape object active (highlighted).")
            return {'CANCELLED'}

        targets = [o for o in context.selected_objects
                   if o.type == 'MESH' and o is not cutter]
        if not targets:
            self.report({'WARNING'}, "Select at least one other mesh as the target.")
            return {'CANCELLED'}

        if context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # ── Convert cutter if it's a curve ───────────────────────────────
        if cutter.type == 'CURVE':
            for spline in cutter.data.splines:
                spline.use_cyclic_u = True
            cutter.data.fill_mode = 'FULL'
            bpy.ops.object.select_all(action='DESELECT')
            cutter.select_set(True)
            context.view_layer.objects.active = cutter
            bpy.ops.object.convert(target='MESH')
            if len(cutter.data.polygons) == 0:
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.fill()
                bpy.ops.object.mode_set(mode='OBJECT')

        # ── Make cutter single-user and apply transforms ──────────────────
        if cutter.data.users > 1:
            cutter.data = cutter.data.copy()
        bpy.ops.object.select_all(action='DESELECT')
        cutter.select_set(True)
        context.view_layer.objects.active = cutter
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # ── Snap cutter centre to Y=0 ─────────────────────────────────────
        # Shift all vertices so the average world Y lands at 0, keeping the
        # cutter's X/Z shape exactly where it was drawn.
        avg_y = _mesh_avg_y(cutter)
        mw_inv = cutter.matrix_world.inverted()
        local_delta = mw_inv.to_3x3() @ mathutils.Vector((0.0, -avg_y, 0.0))
        for v in cutter.data.vertices:
            v.co += local_delta
        cutter.data.update()

        # ── Give cutter massive depth so it cuts through any layer ────────
        # offset=0 centres the slab at Y=0, extending ±100 units each way.
        sol = cutter.modifiers.new(name="HoleSolidify", type='SOLIDIFY')
        sol.thickness = 200.0
        sol.offset = 0.0
        bpy.ops.object.modifier_apply(modifier="HoleSolidify")

        # ── Boolean subtract cutter from every target ─────────────────────
        for target in targets:
            if target.data.users > 1:
                target.data = target.data.copy()
            bpy.ops.object.select_all(action='DESELECT')
            target.select_set(True)
            context.view_layer.objects.active = target

            bool_mod = target.modifiers.new(name="HoleBoolean", type='BOOLEAN')
            bool_mod.operation = 'DIFFERENCE'
            bool_mod.object = cutter
            bool_mod.solver = 'EXACT'
            bpy.ops.object.modifier_apply(modifier="HoleBoolean")

        # ── Remove the cutter ─────────────────────────────────────────────
        bpy.data.objects.remove(cutter, do_unlink=True)

        # Restore selection to targets
        bpy.ops.object.select_all(action='DESELECT')
        for target in targets:
            target.select_set(True)
        context.view_layer.objects.active = targets[0]

        self.report({'INFO'}, f"Hole: cut {len(targets)} object(s).")
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Manual
# ─────────────────────────────────────────────

class SVG_OT_ManualProcess(bpy.types.Operator):
    """Apply solidify, merge vertices, back-face offset and UV projection
    to selected mesh/curve objects, without touching materials or collections.
    Bezier and NURBS curves are converted to mesh first."""
    bl_idname = "svg_layer.manual_process"
    bl_label = "Manual"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = [o for o in context.selected_objects if o.type in {'MESH', 'CURVE'}]
        if not objects:
            self.report({'WARNING'}, "No mesh or curve objects selected.")
            return {'CANCELLED'}

        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # Convert any curve objects (bezier / nurbs) to mesh first.
        # obj reference stays valid after convert() — it mutates the object in-place.
        curves_converted = 0
        mesh_objects = []
        for obj in objects:
            if obj.type == 'CURVE':
                for spline in obj.data.splines:
                    spline.use_cyclic_u = True
                obj.data.fill_mode = 'FULL'
                bpy.ops.object.select_all(action='DESELECT')
                obj.select_set(True)
                context.view_layer.objects.active = obj
                bpy.ops.object.convert(target='MESH')
                # Safety net: if no faces were created, fill the edge loop in edit mode
                if len(obj.data.polygons) == 0:
                    bpy.ops.object.mode_set(mode='EDIT')
                    bpy.ops.mesh.select_all(action='SELECT')
                    bpy.ops.mesh.fill()
                    bpy.ops.object.mode_set(mode='OBJECT')
                curves_converted += 1
            mesh_objects.append(obj)  # already MESH or just converted
        objects = mesh_objects

        for obj in objects:
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            context.view_layer.objects.active = obj

            # Make mesh single-user before applying transforms (prevents multi-user error)
            if obj.data.users > 1:
                obj.data = obj.data.copy()

            # Apply all pending transforms so geometry operations work in real space
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

            # Merge by distance
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.remove_doubles(threshold=0.0001)
            bpy.ops.object.mode_set(mode='OBJECT')

            # Solidify
            for m in [m for m in obj.modifiers if m.type == 'SOLIDIFY']:
                obj.modifiers.remove(m)
            solidify = obj.modifiers.new(name="Solidify", type='SOLIDIFY')
            solidify.thickness = 1
            solidify.offset = -1.0
            bpy.ops.object.modifier_apply(modifier="Solidify")

            # Back-face offset
            mesh = obj.data
            back_vert_indices = set()
            for poly in mesh.polygons:
                if poly.normal.y > 0.5:
                    back_vert_indices.update(poly.vertices)
            for v in mesh.vertices:
                if v.index in back_vert_indices:
                    v.co.x -= 2
                    v.co.z += 2
            mesh.update()

            # UV projection
            if not mesh.uv_layers:
                mesh.uv_layers.new(name="UVMap")
            uv_layer = mesh.uv_layers.active
            canvas = 1920.0
            for poly in mesh.polygons:
                for loop_idx in poly.loop_indices:
                    loop = mesh.loops[loop_idx]
                    vert = mesh.vertices[loop.vertex_index]
                    if poly.normal.y < -0.5:
                        uv_layer.data[loop_idx].uv = (vert.co.x / canvas, vert.co.z / canvas)
                    else:
                        uv_layer.data[loop_idx].uv = (0.0, 0.0)

        bpy.ops.object.select_all(action='DESELECT')
        for obj in objects:
            obj.select_set(True)

        # Random UV rotation — each object gets a different angle
        for obj in objects:
            _random_rotate_uvs(obj)

        curve_note = f" ({curves_converted} curve(s) converted)" if curves_converted else ""
        self.report({'INFO'}, f"Manual: processed {len(objects)} object(s){curve_note}.")
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Randomize + Flatten Y
# ─────────────────────────────────────────────

class SVG_OT_RandomizeFlatten(bpy.types.Operator):
    """Randomize vertex positions on selected mesh objects, then flatten
    all vertices (across all selected objects) to their global average Y."""
    bl_idname = "svg_layer.randomize_flatten"
    bl_label = "Randomize"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = [o for o in context.selected_objects if o.type == 'MESH']
        if not objects:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        amount = context.scene.svg_layer_randomize_amount

        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # Randomize all objects first
        for obj in objects:
            rng = random.Random()  # seeded from system time — different every call
            mesh = obj.data
            for v in mesh.vertices:
                v.co.x += rng.uniform(-amount, amount)
                v.co.y += rng.uniform(-amount, amount)
                v.co.z += rng.uniform(-amount, amount)
            mesh.update()

        # Compute global average world Y across every vertex of every object
        total_y = 0.0
        total_count = 0
        for obj in objects:
            mw = obj.matrix_world
            for v in obj.data.vertices:
                total_y += (mw @ v.co).y
                total_count += 1

        if total_count == 0:
            return {'FINISHED'}

        global_avg_y = total_y / total_count

        # Flatten every vertex to that global average Y (in local space)
        for obj in objects:
            mw = obj.matrix_world
            mw_inv = mw.inverted()
            for v in obj.data.vertices:
                world_co = mw @ v.co
                world_co.y = global_avg_y
                v.co = mw_inv @ world_co
            obj.data.update()

        # Restore selection
        bpy.ops.object.select_all(action='DESELECT')
        for obj in objects:
            obj.select_set(True)

        self.report({'INFO'}, f"Randomize+Flatten: processed {len(objects)} object(s).")
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  UV random rotation helper + operator
# ─────────────────────────────────────────────

def _random_rotate_uvs(obj, rng=None):
    """Rotate all UVs of obj around their centroid by a random angle.
    Each call with a fresh rng (or None → system-seeded) gives a different result."""
    mesh = obj.data
    if not mesh.uv_layers:
        return
    if rng is None:
        rng = random.Random()

    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return

    # Collect all UV coords for this object
    uvs = [uv_layer.data[li].uv for li in range(len(uv_layer.data))]
    if not uvs:
        return

    # Centroid
    cx = sum(uv.x for uv in uvs) / len(uvs)
    cy = sum(uv.y for uv in uvs) / len(uvs)

    angle = rng.uniform(0, 2 * math.pi)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)

    for uv in uvs:
        dx = uv.x - cx
        dy = uv.y - cy
        uv.x = cx + cos_a * dx - sin_a * dy
        uv.y = cy + sin_a * dx + cos_a * dy


class SVG_OT_RandomUVR(bpy.types.Operator):
    """Project UVs from front-face X/Z (same as Manual), then randomly rotate
    the UVs of each selected mesh object independently."""
    bl_idname = "svg_layer.random_uv_r"
    bl_label = "Random UV R"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = [o for o in context.selected_objects if o.type == 'MESH']
        if not objects:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        canvas = 1920.0
        for obj in objects:
            mesh = obj.data
            # Ensure a UV layer exists
            if not mesh.uv_layers:
                mesh.uv_layers.new(name="UVMap")
            uv_layer = mesh.uv_layers.active
            if uv_layer is None:
                continue
            # Project UVs: front-facing polys get X/Z coords, back-facing get (0,0)
            for poly in mesh.polygons:
                for loop_idx in poly.loop_indices:
                    loop = mesh.loops[loop_idx]
                    vert = mesh.vertices[loop.vertex_index]
                    if poly.normal.y < -0.5:
                        uv_layer.data[loop_idx].uv = (vert.co.x / canvas, vert.co.z / canvas)
                    else:
                        uv_layer.data[loop_idx].uv = (0.0, 0.0)
            # Random rotation on top of the fresh projection
            _random_rotate_uvs(obj)

        self.report({'INFO'}, f"UV projected + random rotation applied to {len(objects)} object(s).")
        return {'FINISHED'}


class SVG_OT_StackUVs(bpy.types.Operator):
    """Project UVs on all selected mesh objects the same way Manual does
    (front-facing faces: X/Z coords; back-facing faces: 0,0) without
    any random rotation."""
    bl_idname = "svg_layer.stack_uvs"
    bl_label = "Stack UVs"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = [o for o in context.selected_objects if o.type == 'MESH']
        if not objects:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        canvas = 1920.0
        for obj in objects:
            mesh = obj.data
            if not mesh.uv_layers:
                mesh.uv_layers.new(name="UVMap")
            uv_layer = mesh.uv_layers.active
            if uv_layer is None:
                continue
            for poly in mesh.polygons:
                for loop_idx in poly.loop_indices:
                    loop = mesh.loops[loop_idx]
                    vert = mesh.vertices[loop.vertex_index]
                    if poly.normal.y < -0.5:
                        uv_layer.data[loop_idx].uv = (vert.co.x / canvas, vert.co.z / canvas)
                    else:
                        uv_layer.data[loop_idx].uv = (0.0, 0.0)

        self.report({'INFO'}, f"Stack UVs: projected {len(objects)} object(s).")
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Panel
# ─────────────────────────────────────────────

class SVG_PT_LayerPanel(bpy.types.Panel):
    bl_label = "BB SVG Layers"
    bl_idname = "SVG_PT_layer_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SVG Layer"

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Import", icon='IMPORT')
        box.prop(context.scene, "svg_layer_step", text="Layer Step")
        box.operator("svg_layer.load_svg", icon='FILE_IMAGE')
        box.separator()
        box.prop(context.scene, "svg_layer_all_others", text="All Others", toggle=True)
        row = box.row(align=True)
        row.operator("svg_layer.move_forward", text="-", icon='REMOVE')
        row.operator("svg_layer.move_back", text="+", icon='ADD')
        box.operator("svg_layer.snap_y", icon='SNAP_ON')
        box.operator("svg_layer.snap_to_zero", icon='SNAP_ON')
        box.separator()
        box.operator("svg_layer.hole_boolean", icon='MOD_BOOLEAN')
        box.operator("svg_layer.manual_process", icon='MODIFIER')
        box.operator("svg_layer.auto_stack_selected", icon='ALIGN_TOP')
        box.operator("svg_layer.revert", icon='LOOP_BACK')
        box.operator("svg_layer.random_uv_r", icon='UV')
        box.operator("svg_layer.stack_uvs", icon='UV_DATA')
        box.separator()
        box.prop(context.scene, "svg_layer_randomize_amount", text="Amount")
        box.operator("svg_layer.randomize_flatten", icon='RNDCURVE')
        box.separator()
        box.operator("svg_layer.override_material", icon='LIBRARY_DATA_OVERRIDE')
        box.operator("svg_layer.override_material_same", icon='LIBRARY_DATA_OVERRIDE')
        box.operator("svg_layer.purge_unused_materials", icon='TRASH')


# ─────────────────────────────────────────────
#  Registration
# ─────────────────────────────────────────────

class SVG_OT_Revert(bpy.types.Operator):
    """Remove solidify thickness and back faces from selected objects,
    leaving only the front-facing flat mesh as seen from the camera.
    Select front faces -> invert -> delete."""
    bl_idname = "svg_layer.revert"
    bl_label = "Revert"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        import bmesh

        objects = [o for o in context.selected_objects if o.type == 'MESH']
        if not objects:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        view_fwd = _get_view_forward_world(context)
        if view_fwd is None:
            self.report({'WARNING'}, "No 3D viewport found.")
            return {'CANCELLED'}

        self.report({'INFO'}, f"view_fwd={view_fwd.x:.2f},{view_fwd.y:.2f},{view_fwd.z:.2f}")

        # Ensure object mode before switching per-object.
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # A face is "front-facing" when its world normal points toward the camera,
        # i.e. dot(world_normal, view_fwd) < 0.
        # Threshold 0.1 gives tolerance for faces not perfectly perpendicular
        # to the view — side/thickness faces (dot ≈ 0) still get deleted.
        total_deleted = 0
        for obj in objects:
            context.view_layer.objects.active = obj

            bpy.ops.object.mode_set(mode='EDIT')
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            bm.verts.ensure_lookup_table()

            # Project every vertex onto the view axis.
            # Front faces have ALL their vertices at the minimum depth (closest
            # to camera). Back faces and solidify thickness side faces have at
            # least one vertex deeper.
            # This is robust against the back-face offset that Manual applies
            # (which distorts normals on side faces, making normal-based tests
            # unreliable).
            vert_depths = [(obj.matrix_world @ v.co).dot(view_fwd) for v in bm.verts]
            min_depth   = min(vert_depths)
            max_depth   = max(vert_depths)
            depth_range = max_depth - min_depth

            if depth_range < 1e-4:
                self.report({'WARNING'},
                    f"{obj.name}: mesh appears flat (depth range {depth_range:.4f}) — "
                    f"apply Manual first before using Revert.")
                bpy.ops.object.mode_set(mode='OBJECT')
                continue

            # Tolerance: 5 % of the depth range so minor floating-point
            # variation doesn't misclassify a front face.
            tol = depth_range * 0.05

            front_faces = []
            to_delete   = []
            for face in bm.faces:
                face_max_depth = max((obj.matrix_world @ v.co).dot(view_fwd)
                                     for v in face.verts)
                if face_max_depth <= min_depth + tol:
                    front_faces.append(face)
                else:
                    to_delete.append(face)

            self.report({'INFO'},
                f"{obj.name}: {len(bm.faces)} faces, "
                f"{len(front_faces)} front, "
                f"{len(to_delete)} to delete. "
                f"depth range [{min_depth:.3f}, {max_depth:.3f}]")

            bmesh.ops.delete(bm, geom=to_delete, context='FACES')
            bmesh.update_edit_mesh(obj.data)
            bpy.ops.object.mode_set(mode='OBJECT')
            total_deleted += len(to_delete)

        self.report({'INFO'}, f"Revert: deleted {total_deleted} face(s) across {len(objects)} object(s).")
        return {'FINISHED'}


classes = (
    SVG_OT_LoadSVG,
    SVG_OT_ApplyLayers,
    SVG_OT_MoveForward,
    SVG_OT_MoveBack,
    SVG_OT_SnapY,
    SVG_OT_SnapToZero,
    SVG_OT_OverrideMaterial,
    SVG_OT_OverrideMaterialSame,
    SVG_OT_PurgeUnusedMaterials,
    SVG_OT_AutoStack,
    SVG_OT_AutoStackSelected,
    SVG_OT_HoleBoolean,
    SVG_OT_ManualProcess,
    SVG_OT_Revert,
    SVG_OT_RandomizeFlatten,
    SVG_OT_RandomUVR,
    SVG_OT_StackUVs,
    SVG_PT_LayerPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.svg_layer_all_others = bpy.props.BoolProperty(
        name="All Others",
        description="When ON, - and + buttons affect all objects NOT in the current selection",
        default=False,
    )

    bpy.types.Scene.svg_layer_step = bpy.props.FloatProperty(
        name="Layer Step",
        description="Y distance between successive SVG layers",
        default=3.0,
        min=0.001,
        soft_max=20.0,
        step=10,
        precision=2,
    )

    bpy.types.Scene.svg_layer_randomize_amount = bpy.props.FloatProperty(
        name="Randomize Amount",
        description="Vertex randomize offset applied before Y-flatten",
        default=2.0,
        min=0.0,
        soft_max=50.0,
        step=10,
        precision=2,
    )


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.svg_layer_all_others
    del bpy.types.Scene.svg_layer_step
    del bpy.types.Scene.svg_layer_randomize_amount
