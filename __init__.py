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

def xz_bbox(obj):
    mesh = obj.data
    mat = obj.matrix_world
    xs = [(mat @ mesh.vertices[v].co).x for v in range(len(mesh.vertices))]
    zs = [(mat @ mesh.vertices[v].co).z for v in range(len(mesh.vertices))]
    return min(xs), max(xs), min(zs), max(zs)


def xz_bboxes_overlap(a, b):
    ax0, ax1, az0, az1 = a
    bx0, bx1, bz0, bz1 = b
    return ax1 > bx0 and bx1 > ax0 and az1 > bz0 and bz1 > az0


def pack_layers(ordered_objects, step):
    """
    ordered_objects: back-to-front (index 0 = furthest back).
    Index 0 -> Y=0. Each next object goes more negative only when it
    overlaps something already placed.
    """
    if not ordered_objects:
        return
    bboxes = [xz_bbox(o) for o in ordered_objects]
    placed_y = [None] * len(ordered_objects)
    for i in range(len(ordered_objects)):
        obj = ordered_objects[i]
        bbox_i = bboxes[i]
        blocking_y = None
        for j in range(i):
            if xz_bboxes_overlap(bbox_i, bboxes[j]):
                y_j = placed_y[j]
                if blocking_y is None or y_j < blocking_y:
                    blocking_y = y_j
        obj.location.y = 0.0 if blocking_y is None else blocking_y - step
        placed_y[i] = obj.location.y
        print(f"SVG Layer: '{obj.name}' -> Y={obj.location.y:.3f}")


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

        master_mat = bpy.data.materials.get("Master")
        if master_mat is None:
            self.report({'ERROR'}, "No material named 'Master' found.")
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

    Strategy:
      1. Convert cam_world_pos into the object's local space.
      2. Temporarily shift all vertices so the pivot is at the camera.
      3. Scale vertices uniformly by (depth + step) / depth — pure radial
         scale around the camera, angular size is conserved.
      4. Shift vertices back to restore the real origin position.
    """
    mw = obj.matrix_world
    mw_inv = mw.inverted()

    # Camera in local object space (accounts for object location/rotation/scale).
    cam_local = mw_inv @ cam_world_pos

    # Step 1: shift so pivot is at the camera.
    for v in obj.data.vertices:
        v.co -= cam_local

    # Depth: signed distance from camera to mesh centre along the view ray.
    center_shifted = sum(
        (mathutils.Vector(c) for c in obj.bound_box),
        mathutils.Vector(),
    ) / 8.0 - cam_local
    # Only rotation+scale needed to get the world-space direction.
    center_world_dir = mw.to_3x3() @ center_shifted
    depth = center_world_dir.dot(view_fwd_world)

    if abs(depth) < 1e-4:
        for v in obj.data.vertices:
            v.co += cam_local
        obj.data.update()
        return

    factor = (depth + step) / depth

    # Step 2: radial scale around the camera pivot.
    for v in obj.data.vertices:
        v.co *= factor

    # Step 3: restore the real origin.
    for v in obj.data.vertices:
        v.co += cam_local

    obj.data.update()


class SVG_OT_MoveForward(bpy.types.Operator):
    """Move selected objects away from the viewer.
    Orthographic view: pure translation along the view axis.
    Camera / perspective view: scale from world origin so apparent size is preserved."""
    bl_idname = "svg_layer.move_forward"
    bl_label = "Move Forward"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = list(context.selected_objects)
        if not objects:
            self.report({'WARNING'}, "No objects selected.")
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
        objects = list(context.selected_objects)
        if not objects:
            self.report({'WARNING'}, "No objects selected.")
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

class SVG_OT_SnapY(bpy.types.Operator):
    """Snap all selected objects to the highest Y value among them,
    using world-space bounding box centre. Origin stays fixed (e.g. at camera)."""
    bl_idname = "svg_layer.snap_y"
    bl_label = "Snap"
    bl_options = {'REGISTER', 'UNDO'}

    @staticmethod
    def _mesh_world_y(obj):
        """Return the world-space Y of the object's bounding-box centre."""
        corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
        return sum(c.y for c in corners) / 8.0

    def execute(self, context):
        objects = [o for o in context.selected_objects if o.type == 'MESH']
        if not objects:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        mesh_ys = {obj: self._mesh_world_y(obj) for obj in objects}
        max_mesh_y = max(mesh_ys.values())

        for obj in objects:
            delta_y = max_mesh_y - mesh_ys[obj]
            if abs(delta_y) < 1e-6:
                continue
            # Convert world-space Y shift into object local space,
            # then apply directly to vertex data — origin stays untouched.
            local_delta = obj.matrix_world.to_3x3().inverted() @ mathutils.Vector((0.0, delta_y, 0.0))
            for v in obj.data.vertices:
                v.co += local_delta
            obj.data.update()

        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Override Material
# ─────────────────────────────────────────────

class SVG_OT_OverrideMaterial(bpy.types.Operator):
    """Make a single-user copy of each selected object's material so it can be
    edited independently."""
    bl_idname = "svg_layer.override_material"
    bl_label = "Override Single"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = gather_objects(context)
        objects = [o for o in objects if o.type == 'MESH']

        if not objects:
            self.report({'WARNING'}, "No mesh objects found.")
            return {'CANCELLED'}

        overridden = 0
        skipped = []

        for obj in objects:
            if not obj.data.materials or obj.data.materials[0] is None:
                skipped.append(obj.name)
                continue
            mat = obj.data.materials[0]
            new_mat = mat.copy()
            new_mat.name = obj.name.split('.')[0] + "_override"
            obj.data.materials[0] = new_mat
            if mat.library is not None:
                try:
                    override = mat.override_create(remap_local_usages=False)
                    if override:
                        new_mat = override
                        new_mat.name = obj.name.split('.')[0] + "_override"
                        obj.data.materials[0] = new_mat
                except Exception as e:
                    print(f"SVG Layer: Could not create library override for '{mat.name}': {e}")
            overridden += 1

        if skipped:
            self.report({'WARNING'},
                f"Overridden {overridden} material(s). No material on: {', '.join(skipped)}")
        else:
            self.report({'INFO'}, f"Overridden {overridden} material(s).")

        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Override Same Material
# ─────────────────────────────────────────────

class SVG_OT_OverrideMaterialSame(bpy.types.Operator):
    """For each selected object, make a single-user override and assign that override
    to ALL objects in the scene sharing the same original material."""
    bl_idname = "svg_layer.override_material_same"
    bl_label = "Override Same"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = gather_objects(context)
        objects = [o for o in objects if o.type == 'MESH']

        if not objects:
            self.report({'WARNING'}, "No mesh objects found.")
            return {'CANCELLED'}

        override_map = {}
        skipped = []

        for obj in objects:
            if not obj.data.materials or obj.data.materials[0] is None:
                skipped.append(obj.name)
                continue
            original_mat = obj.data.materials[0]
            if original_mat.name in override_map:
                continue
            new_mat = original_mat.copy()
            new_mat.name = original_mat.name.split('.')[0] + "_override"
            if original_mat.library is not None:
                try:
                    override = original_mat.override_create(remap_local_usages=False)
                    if override:
                        new_mat = override
                        new_mat.name = original_mat.name.split('.')[0] + "_override"
                except Exception as e:
                    print(f"SVG Layer: Could not create library override for '{original_mat.name}': {e}")
            override_map[original_mat.name] = (original_mat, new_mat)

        if not override_map:
            self.report({'WARNING'}, "No materials to override.")
            return {'CANCELLED'}

        assigned = 0
        for scene_obj in context.scene.objects:
            if scene_obj.type != 'MESH' or not scene_obj.data.materials:
                continue
            for slot_idx, mat in enumerate(scene_obj.data.materials):
                if mat is None:
                    continue
                if mat.name in override_map:
                    _, new_mat = override_map[mat.name]
                    scene_obj.data.materials[slot_idx] = new_mat
                    assigned += 1

        if skipped:
            self.report({'WARNING'},
                f"Created {len(override_map)} override(s), assigned to {assigned} slot(s). "
                f"No material on: {', '.join(skipped)}")
        else:
            self.report({'INFO'},
                f"Created {len(override_map)} override(s), assigned to {assigned} object slot(s).")

        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Auto Stack
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

        if bpy.data.materials.get("Master") is None:
            self.report({'ERROR'}, "No material named 'Master' found. "
                        "Please create a 'Master' material before importing.")
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

        self.report({'INFO'},
            f"Loaded {len(new_objects)} object(s) from '{os.path.basename(filepath)}' — {order_msg}.")
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Manual
# ─────────────────────────────────────────────

class SVG_OT_ManualProcess(bpy.types.Operator):
    """Apply solidify, merge vertices, back-face offset and UV projection
    to selected mesh objects, without touching materials or collections."""
    bl_idname = "svg_layer.manual_process"
    bl_label = "Manual"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = [o for o in context.selected_objects if o.type == 'MESH']
        if not objects:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        for obj in objects:
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            context.view_layer.objects.active = obj

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

        self.report({'INFO'}, f"Manual: processed {len(objects)} object(s).")
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
        row = box.row(align=True)
        row.operator("svg_layer.move_forward", text="-", icon='REMOVE')
        row.operator("svg_layer.move_back", text="+", icon='ADD')
        box.operator("svg_layer.snap_y", icon='SNAP_ON')
        box.operator("svg_layer.override_material", icon='LIBRARY_DATA_OVERRIDE')
        box.operator("svg_layer.override_material_same", icon='LIBRARY_DATA_OVERRIDE')
        box.separator()
        box.operator("svg_layer.manual_process", icon='MODIFIER')


# ─────────────────────────────────────────────
#  Registration
# ─────────────────────────────────────────────

classes = (
    SVG_OT_LoadSVG,
    SVG_OT_ApplyLayers,
    SVG_OT_MoveForward,
    SVG_OT_MoveBack,
    SVG_OT_SnapY,
    SVG_OT_OverrideMaterial,
    SVG_OT_OverrideMaterialSame,
    SVG_OT_AutoStack,
    SVG_OT_ManualProcess,
    SVG_PT_LayerPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.svg_layer_step = bpy.props.FloatProperty(
        name="Layer Step",
        description="Y distance between successive SVG layers",
        default=3.0,
        min=0.001,
        soft_max=20.0,
        step=10,
        precision=2,
    )


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.svg_layer_step
