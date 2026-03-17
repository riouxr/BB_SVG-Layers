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
    """
    Project UVs from the Y axis.
    U = X / 1920,  V = Z / 1920  →  fits a 1920×1920 px canvas in [0, 1].
    """
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
    """Recursively find all .blend files under a library root path."""
    blend_files = []
    for root, dirs, files in os.walk(library_path):
        for f in files:
            if f.endswith(".blend"):
                blend_files.append(os.path.join(root, f))
    return blend_files


ASSET_LIBRARY_NAME = "Paper"


def append_material_from_libraries(mat_name):
    """
    Search the 'Paper' asset library for a material named mat_name.
    If found, append it into the current file and return the material.
    If it already exists in bpy.data.materials, return it immediately.
    """
    # Already loaded — no need to append again
    existing = bpy.data.materials.get(mat_name)
    if existing is not None:
        return existing

    asset_libs = bpy.context.preferences.filepaths.asset_libraries
    paper_lib = next((lib for lib in asset_libs if lib.name == ASSET_LIBRARY_NAME), None)

    if paper_lib is None:
        print(f"SVG Layer: Asset library '{ASSET_LIBRARY_NAME}' not found in Preferences.")
        return None

    lib_path = bpy.path.abspath(paper_lib.path)
    if not os.path.isdir(lib_path):
        print(f"SVG Layer: Library path does not exist: {lib_path}")
        return None

    for blend_path in find_blend_files_in_library(lib_path):
        with bpy.data.libraries.load(blend_path, assets_only=True) as (data_from, data_to):
            if mat_name in data_from.materials:
                data_to.materials = [mat_name]

        mat = bpy.data.materials.get(mat_name)
        if mat is not None:
            return mat

    return None


# ─────────────────────────────────────────────
#  Main Operator
# ─────────────────────────────────────────────

class SVG_OT_ApplyLayers(bpy.types.Operator):
    """Rotate, scale, convert, merge, offset, UV-project, solidify and assign asset material"""
    bl_idname = "svg_layer.apply_layers"
    bl_label = "Apply SVG Layers"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        step = context.scene.svg_layer_offset
        objects = gather_objects(context)

        if not objects:
            self.report({'WARNING'}, "No objects found.")
            return {'CANCELLED'}

        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        n = len(objects)

        for i, obj in enumerate(objects):

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

            # 6. Cumulative -Y offset (last object = base / no offset)
            obj.location.y -= i * step

            # 7. UV projection from Y axis (1920×1920 canvas)
            if obj.type == 'MESH':
                apply_uv_projection_y(obj)

            # 8. Solidify modifier
            for m in [m for m in obj.modifiers if m.type == 'SOLIDIFY']:
                obj.modifiers.remove(m)
            solidify = obj.modifiers.new(name="Solidify", type='SOLIDIFY')
            solidify.thickness = 0.003
            solidify.offset = -1.0

            # 9. Assign material — search asset libraries by prefix name
            #    e.g. "Layer_01.003" → looks for material "Layer_01"
            mat_name = obj.name.split('.')[0]
            mat = append_material_from_libraries(mat_name)
            if mat is not None:
                if obj.data.materials:
                    obj.data.materials[0] = mat
                else:
                    obj.data.materials.append(mat)
            else:
                self.report({'WARNING'},
                    f"Material '{mat_name}' not found in any asset library.")

        # Restore selection
        bpy.ops.object.select_all(action='DESELECT')
        for obj in objects:
            try:
                obj.select_set(True)
            except ReferenceError:
                pass

        self.report({'INFO'}, f"SVG Layers applied to {n} object(s).")
        return {'FINISHED'}


# ─────────────────────────────────────────────
#  Panel
# ─────────────────────────────────────────────

class SVG_PT_LayerPanel(bpy.types.Panel):
    bl_label = "BB SVG Layer"
    bl_idname = "SVG_PT_layer_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SVG Layer"

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.prop(context.scene, "svg_layer_offset", text="Y Offset per Layer", slider=True)
        box.operator("svg_layer.apply_layers", icon='SHADERFX')


# ─────────────────────────────────────────────
#  Registration
# ─────────────────────────────────────────────

classes = (
    SVG_OT_ApplyLayers,
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
