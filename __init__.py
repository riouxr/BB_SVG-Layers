import bpy
import math
import mathutils
import os


# ─────────────────────────────────────────────
#  Helpers
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


def apply_uv_projection_y(obj):
    """U = X / 1920, V = Z / 1920 — projects from Y axis onto 1920x1920 canvas."""
    mesh = obj.data
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    uv_layer = mesh.uv_layers.active
    canvas = 1920.0
    for poly in mesh.polygons:
        for loop_idx in poly.loop_indices:
            loop = mesh.loops[loop_idx]
            vert = mesh.vertices[loop.vertex_index]
            uv_layer.data[loop_idx].uv = (vert.co.x / canvas, vert.co.z / canvas)


def find_blend_files_in_library(library_path):
    blend_files = []
    for root, dirs, files in os.walk(library_path):
        for f in files:
            if f.endswith(".blend"):
                blend_files.append(os.path.join(root, f))
    return blend_files


ASSET_LIBRARY_NAME = "Paper"


def find_library_blend_file(context):
    """Find ONLY .blend files whose path contains ASSET_LIBRARY_NAME."""
    asset_libs = context.preferences.filepaths.asset_libraries
    result = []
    for lib in asset_libs:
        lib_root = os.path.normpath(bpy.path.abspath(lib.path))
        for blend_path in find_blend_files_in_library(lib_root):
            if ASSET_LIBRARY_NAME.lower() in blend_path.lower():
                result.append(blend_path)
    return result


def append_material_from_library(context, mat_name, blend_files):
    existing = bpy.data.materials.get(mat_name)
    if existing is not None and not existing.name.startswith('SVGMat'):
        return existing

    for blend_path in blend_files:
        try:
            with bpy.data.libraries.load(blend_path, assets_only=True) as (data_from, data_to):
                if mat_name not in data_from.materials:
                    continue
                names_before = set(m.name for m in bpy.data.materials)
                data_to.materials = [mat_name]

            mat = bpy.data.materials.get(mat_name)
            if mat is not None:
                return mat

            names_after = set(m.name for m in bpy.data.materials)
            for new_name in (names_after - names_before):
                if new_name.split('.')[0] == mat_name:
                    mat = bpy.data.materials.get(new_name)
                    if mat:
                        mat.name = mat_name
                        return mat
        except Exception as e:
            print(f"SVG Layer: Error reading {blend_path}: {e}")

    return None


# ─────────────────────────────────────────────
#  Collection sorting helpers
# ─────────────────────────────────────────────

BG_PREFIX = "BG_"
FG_PREFIX = "FG_"


def get_object_prefix(obj):
    """Return the prefix group of an object (everything up to and including first '_')."""
    name = obj.name
    idx = name.find('_')
    if idx == -1:
        return name  # no underscore, use full name as group
    return name[:idx + 1]  # e.g. "Wes_", "BG_", "FG_"


def get_or_create_collection(parent_col, name):
    """Get existing child collection by name or create it under parent_col."""
    # Check if already a direct child
    for child in parent_col.children:
        if child.name == name:
            return child
    # Check if exists in bpy.data but not linked yet
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    # Link under parent if not already there
    if col.name not in [c.name for c in parent_col.children]:
        parent_col.children.link(col)
    return col


def move_object_to_collection(obj, target_col):
    """Remove obj from all current collections and link to target_col."""
    # Unlink from every collection currently holding this object
    for col in list(obj.users_collection):
        try:
            col.objects.unlink(obj)
        except Exception:
            pass
    # Link to target
    if obj.name not in target_col.objects:
        target_col.objects.link(obj)


def outliner_object_order(context, objects):
    """
    Return objects sorted by their order in the outliner (view layer object list).
    Blender doesn't expose outliner order directly, so we use the order objects
    appear in the view layer's depsgraph, which matches the outliner top-to-bottom.
    """
    vl_objects = list(context.view_layer.objects)
    order_map = {obj.name: i for i, obj in enumerate(vl_objects)}
    return sorted(objects, key=lambda o: order_map.get(o.name, 9999))


def group_objects_by_prefix(objects):
    """
    Return (bg_objs, character_groups, fg_objs) where:
      - bg_objs: list of objects with BG_ prefix
      - character_groups: dict of {prefix: [objs]} for all other non-FG prefixes,
                          ordered by first appearance in the input list
      - fg_objs: list of objects with FG_ prefix
    """
    bg_objs = []
    fg_objs = []
    char_groups = {}   # prefix -> list, preserving insertion order
    char_order = []    # track prefix insertion order

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

    # Build ordered list of character groups preserving outliner order
    ordered_char_groups = [(p, char_groups[p]) for p in char_order]
    return bg_objs, ordered_char_groups, fg_objs


# ─────────────────────────────────────────────
#  Auto Stack helpers
# ─────────────────────────────────────────────

def mesh_area(obj):
    mesh = obj.data
    area = 0.0
    mat = obj.matrix_world
    for poly in mesh.polygons:
        verts = [mat @ mesh.vertices[i].co for i in poly.vertices]
        if len(verts) >= 3:
            for i in range(1, len(verts) - 1):
                a = verts[i] - verts[0]
                b = verts[i + 1] - verts[0]
                area += a.cross(b).length * 0.5
    return area


def get_xz_polygons(obj):
    mesh = obj.data
    mat = obj.matrix_world
    polys = []
    for poly in mesh.polygons:
        pts = []
        for vi in poly.vertices:
            co = mat @ mesh.vertices[vi].co
            pts.append((co.x, co.z))
        polys.append(pts)
    return polys


def xz_polygons_overlap(polys_a, polys_b):
    def axes(poly):
        axs = []
        n = len(poly)
        for i in range(n):
            p1 = poly[i]
            p2 = poly[(i + 1) % n]
            edge = (p2[0] - p1[0], p2[1] - p1[1])
            axs.append((-edge[1], edge[0]))
        return axs

    def project(poly, axis):
        dots = [p[0] * axis[0] + p[1] * axis[1] for p in poly]
        return min(dots), max(dots)

    def intervals_overlap(a, b):
        return a[0] <= b[1] and b[0] <= a[1]

    def sat_overlap(pa, pb):
        if len(pa) < 2 or len(pb) < 2:
            return False
        for axis in axes(pa) + axes(pb):
            length = math.sqrt(axis[0]**2 + axis[1]**2)
            if length < 1e-10:
                continue
            axis = (axis[0] / length, axis[1] / length)
            if not intervals_overlap(project(pa, axis), project(pb, axis)):
                return False
        return True

    for pa in polys_a:
        for pb in polys_b:
            if len(pa) >= 2 and len(pb) >= 2:
                if sat_overlap(pa, pb):
                    return True
    return False


def objects_overlap_xz(obj_a, obj_b):
    def xz_bbox(obj):
        mesh = obj.data
        mat = obj.matrix_world
        xs = [(mat @ mesh.vertices[v].co).x for v in range(len(mesh.vertices))]
        zs = [(mat @ mesh.vertices[v].co).z for v in range(len(mesh.vertices))]
        return min(xs), max(xs), min(zs), max(zs)

    ax0, ax1, az0, az1 = xz_bbox(obj_a)
    bx0, bx1, bz0, bz1 = xz_bbox(obj_b)

    if ax1 < bx0 or bx1 < ax0 or az1 < bz0 or bz1 < az0:
        return False

    polys_a = get_xz_polygons(obj_a)
    polys_b = get_xz_polygons(obj_b)
    return xz_polygons_overlap(polys_a, polys_b)


def auto_stack_group(objects, step, threshold, y_start):
    """
    Run the greedy layer-packing algorithm on a group of objects.
    y_start: the Y value for layer 0 of this group.
    Returns the Y value of the frontmost layer used (for chaining groups).
    """
    mesh_objs = [o for o in objects if o.type == 'MESH']
    if not mesh_objs:
        return y_start

    normal_objs = [o for o in mesh_objs if mesh_area(o) >= threshold]
    tiny_objs   = [o for o in mesh_objs if mesh_area(o) < threshold]

    normal_objs.sort(key=lambda o: mesh_area(o), reverse=True)

    layers = []
    for obj in normal_objs:
        placed = False
        for layer_idx, layer_objs in enumerate(layers):
            if not any(objects_overlap_xz(obj, other) for other in layer_objs):
                layer_objs.append(obj)
                obj.location.y = y_start - layer_idx * step
                placed = True
                break
        if not placed:
            layers.append([obj])
            obj.location.y = y_start - (len(layers) - 1) * step

    # Tiny objects go one step in front of the last normal layer
    front_y = y_start - len(layers) * step if layers else y_start
    for obj in tiny_objs:
        obj.location.y = front_y

    # Return the frontmost Y used by this group
    total_layers = len(layers) + (1 if tiny_objs else 0)
    return y_start - (total_layers - 1) * step


# ─────────────────────────────────────────────
#  Operator: Apply SVG Layers
# ─────────────────────────────────────────────

class SVG_OT_ApplyLayers(bpy.types.Operator):
    """Rotate, scale, convert curves, merge, UV-project, solidify, assign material
    and sort into sub-collections by prefix (BG_, characters, FG_)."""
    bl_idname = "svg_layer.apply_layers"
    bl_label = "Apply & Sort"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = gather_objects(context)

        if not objects:
            self.report({'WARNING'}, "No objects found.")
            return {'CANCELLED'}

        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        blend_files = find_library_blend_file(context)
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
                apply_uv_projection_y(obj)

            for m in [m for m in obj.modifiers if m.type == 'SOLIDIFY']:
                obj.modifiers.remove(m)
            solidify = obj.modifiers.new(name="Solidify", type='SOLIDIFY')
            solidify.thickness = 0.003
            solidify.offset = -1.0

            mat_name = obj.name.split('.')[0]
            mat = append_material_from_library(context, mat_name, blend_files)
            if mat is not None:
                if obj.data.materials:
                    obj.data.materials[0] = mat
                else:
                    obj.data.materials.append(mat)
            else:
                self.report({'WARNING'}, f"Material '{mat_name}' not found in '{ASSET_LIBRARY_NAME}' library.")

            processed.append(obj)

        # ── Sort into sub-collections by prefix ──
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
        self.report({'INFO'}, f"Applied {len(processed)} object(s) → collections: {', '.join(col_names)}")
        return {'FINISHED'}






# ─────────────────────────────────────────────
#  Operator: Move -/+
# ─────────────────────────────────────────────

class SVG_OT_MoveForward(bpy.types.Operator):
    """Move selected objects in +Y by the offset amount"""
    bl_idname = "svg_layer.move_forward"
    bl_label = "Move Forward"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = list(context.selected_objects)
        if not objects:
            self.report({'WARNING'}, "No objects selected.")
            return {'CANCELLED'}
        step = context.scene.svg_layer_offset
        for obj in objects:
            obj.location.y += step
        return {'FINISHED'}


class SVG_OT_MoveBack(bpy.types.Operator):
    """Move selected objects in -Y by the offset amount"""
    bl_idname = "svg_layer.move_back"
    bl_label = "Move Back"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = list(context.selected_objects)
        if not objects:
            self.report({'WARNING'}, "No objects selected.")
            return {'CANCELLED'}
        step = context.scene.svg_layer_offset
        for obj in objects:
            obj.location.y -= step
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Snap
# ─────────────────────────────────────────────

class SVG_OT_SnapY(bpy.types.Operator):
    """Snap all selected objects to the highest Y value among them"""
    bl_idname = "svg_layer.snap_y"
    bl_label = "Snap"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = list(context.selected_objects)
        if not objects:
            self.report({'WARNING'}, "No objects selected.")
            return {'CANCELLED'}
        max_y = max(obj.location.y for obj in objects)
        for obj in objects:
            obj.location.y = max_y
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Refresh Materials
# ─────────────────────────────────────────────

class SVG_OT_RefreshMaterials(bpy.types.Operator):
    """Re-assign materials from the Paper asset library to all objects in the collection or selection"""
    bl_idname = "svg_layer.refresh_materials"
    bl_label = "Refresh"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = gather_objects(context)

        if not objects:
            self.report({'WARNING'}, "No objects found.")
            return {'CANCELLED'}

        blend_files = find_library_blend_file(context)
        assigned = 0
        missing = []

        for obj in objects:
            if obj.type != 'MESH':
                continue
            mat_name = obj.name.split('.')[0]
            mat = append_material_from_library(context, mat_name, blend_files)
            if mat is not None:
                if obj.data.materials:
                    obj.data.materials[0] = mat
                else:
                    obj.data.materials.append(mat)
                assigned += 1
            else:
                missing.append(mat_name)

        if missing:
            self.report({'WARNING'}, f"Missing materials: {', '.join(set(missing))}")
        else:
            self.report({'INFO'}, f"Materials refreshed on {assigned} object(s).")

        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Auto Stack
# ─────────────────────────────────────────────

class SVG_OT_AutoStack(bpy.types.Operator):
    """Stack each sub-collection independently by area, chaining Y offsets
    between collections in outliner order. BG furthest back, FG in front."""
    bl_idname = "svg_layer.auto_stack"
    bl_label = "Auto Stack"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        step = context.scene.svg_layer_offset
        threshold = context.scene.svg_layer_area_threshold

        parent_col = get_active_collection(context)
        if parent_col is None:
            parent_col = context.scene.collection

        # Get sub-collections in outliner order
        # view_layer.layer_collection gives the outliner tree
        def find_layer_collection(layer_col, target_col):
            if layer_col.collection == target_col:
                return layer_col
            for child in layer_col.children:
                result = find_layer_collection(child, target_col)
                if result:
                    return result
            return None

        parent_lc = find_layer_collection(
            context.view_layer.layer_collection, parent_col
        )

        if parent_lc is None or not parent_lc.children:
            # No sub-collections — fall back to stacking all objects flat
            objects = [o for o in gather_objects(context) if o.type == 'MESH']
            if not objects:
                self.report({'WARNING'}, "No mesh objects found.")
                return {'CANCELLED'}
            auto_stack_group(objects, step, threshold, y_start=0.0)
            self.report({'INFO'}, f"Auto Stack: {len(objects)} objects (no sub-collections found).")
            return {'FINISHED'}

        # Process each sub-collection in outliner order, chaining Y
        y_cursor = 0.0
        total_objects = 0
        total_cols = 0

        for child_lc in parent_lc.children:
            col = child_lc.collection
            # Get only direct objects of this collection (not nested)
            objects = [o for o in col.objects if o.type == 'MESH']
            if not objects:
                continue

            front_y = auto_stack_group(objects, step, threshold, y_start=y_cursor)
            # Next collection starts one step beyond this one's front
            y_cursor = front_y - step
            total_objects += len(objects)
            total_cols += 1

        self.report({'INFO'}, f"Auto Stack: {total_objects} objects across {total_cols} collection(s).")
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Panel
# ─────────────────────────────────────────────

class SVG_PT_LayerPanel(bpy.types.Panel):
    bl_label = "SVG Layer"
    bl_idname = "SVG_PT_layer_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SVG Layer"

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.prop(context.scene, "svg_layer_offset", text="Y Offset per Layer", slider=True)
        box.prop(context.scene, "svg_layer_area_threshold", text="Tiny Object Threshold", slider=True)
        box.operator("svg_layer.apply_layers", icon='SHADERFX')
        box.operator("svg_layer.auto_stack", icon='SORTSIZE')
        row = box.row(align=True)
        row.operator("svg_layer.move_forward", text="-", icon='REMOVE')
        row.operator("svg_layer.move_back", text="+", icon='ADD')
        box.operator("svg_layer.snap_y", icon='SNAP_ON')
        box.operator("svg_layer.refresh_materials", icon='MATERIAL')


# ─────────────────────────────────────────────
#  Registration
# ─────────────────────────────────────────────

classes = (
    SVG_OT_ApplyLayers,
    SVG_OT_MoveForward,
    SVG_OT_MoveBack,
    SVG_OT_SnapY,
    SVG_OT_RefreshMaterials,
    SVG_OT_AutoStack,
    SVG_PT_LayerPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.svg_layer_area_threshold = bpy.props.FloatProperty(
        name="Tiny Object Threshold",
        description="Objects with area below this value are always placed in the frontmost layer",
        default=500.0,
        min=0.0,
        max=100000.0,
        step=100,
        precision=1,
        soft_min=0.0,
        soft_max=10000.0,
    )

    bpy.types.Scene.svg_layer_offset = bpy.props.FloatProperty(
        name="Y Offset",
        description="Per-layer distance along -Y (last object = base, no offset)",
        default=0.1,
        min=0.0,
        max=100.0,
        step=1,
        precision=3,
        soft_min=0.0,
        soft_max=10.0,
    )


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.svg_layer_offset
    del bpy.types.Scene.svg_layer_area_threshold
