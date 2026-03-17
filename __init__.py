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
    """
    Append material by name from the Paper library blend files.
    Returns the material or None.
    """
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
#  Operator: Apply SVG Layers
# ─────────────────────────────────────────────

class SVG_OT_ApplyLayers(bpy.types.Operator):
    """Rotate, scale, convert curves, merge, UV-project, solidify and assign material"""
    bl_idname = "svg_layer.apply_layers"
    bl_label = "Apply SVG Layers"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objects = gather_objects(context)

        if not objects:
            self.report({'WARNING'}, "No objects found.")
            return {'CANCELLED'}

        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        blend_files = find_library_blend_file(context)

        for obj in objects:
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            context.view_layer.objects.active = obj

            # 1. Rotate +90° RX
            obj.rotation_euler[0] += math.radians(90)

            # 2. Scale ×850
            obj.scale = (obj.scale[0] * 850,
                         obj.scale[1] * 850,
                         obj.scale[2] * 850)

            # 3. Convert curve → mesh BEFORE applying transforms
            if obj.type == 'CURVE':
                bpy.ops.object.convert(target='MESH')
                obj = context.view_layer.objects.active

            # 4. Apply all transforms
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

            # 5. Merge by distance
            if obj.type == 'MESH':
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.remove_doubles(threshold=0.0001)
                bpy.ops.object.mode_set(mode='OBJECT')

            # 6. UV projection from Y axis (1920×1920 canvas)
            if obj.type == 'MESH':
                apply_uv_projection_y(obj)

            # 7. Solidify modifier
            for m in [m for m in obj.modifiers if m.type == 'SOLIDIFY']:
                obj.modifiers.remove(m)
            solidify = obj.modifiers.new(name="Solidify", type='SOLIDIFY')
            solidify.thickness = 2
            solidify.offset = -1.0

            # 8. Assign material by prefix (e.g. "Sky.002" → "Sky")
            mat_name = obj.name.split('.')[0]
            mat = append_material_from_library(context, mat_name, blend_files)
            if mat is not None:
                if obj.data.materials:
                    obj.data.materials[0] = mat
                else:
                    obj.data.materials.append(mat)
            else:
                self.report({'WARNING'}, f"Material '{mat_name}' not found in '{ASSET_LIBRARY_NAME}' library.")

        # Restore selection
        bpy.ops.object.select_all(action='DESELECT')
        for obj in objects:
            try:
                obj.select_set(True)
            except ReferenceError:
                pass

        self.report({'INFO'}, f"SVG Layers applied to {len(objects)} object(s).")
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Operator: Offset
# ─────────────────────────────────────────────

class SVG_OT_ApplyOffset(bpy.types.Operator):
    """Apply cumulative -Y offset. Last object = front (no offset), first = furthest back."""
    bl_idname = "svg_layer.apply_offset"
    bl_label = "Offset"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        step = context.scene.svg_layer_offset
        objects = gather_objects(context)

        if not objects:
            self.report({'WARNING'}, "No objects found.")
            return {'CANCELLED'}

        for i, obj in enumerate(objects):
            obj.location.y -= i * step

        self.report({'INFO'}, f"Offset applied to {len(objects)} object(s).")
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
        box.operator("svg_layer.apply_layers", icon='SHADERFX')
        box.operator("svg_layer.apply_offset", icon='SORTBYEXT')
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
    SVG_OT_ApplyOffset,
    SVG_OT_MoveForward,
    SVG_OT_MoveBack,
    SVG_OT_SnapY,
    SVG_OT_RefreshMaterials,
    SVG_PT_LayerPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

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
