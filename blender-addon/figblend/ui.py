"""Sidebar panel: N > FigBlend.

Laid out by how often each control is used. Live Link sits at the top, always
open. Tools that act on the selection get their own subpanel. The occasional
scene-wide settings live in a subpanel that starts closed. Labels are short
and the detail lives in the tooltips.
"""

import bpy

from . import bridge, ops, props, updater


def _live_link_status():
    """One short line saying what Live Link is doing, plus an icon.

    None when there is nothing worth saying.
    """
    if not bridge.is_running():
        if not bpy.data.filepath:
            return "Save the .blend first", "ERROR"
        return None

    last = bridge._last_result
    if last.get("state") == "error":
        return last["message"], "ERROR"

    state = bridge.pairing_state()
    if state == "open":
        return "Waiting for Figma  %ds" % bridge.pairing_seconds_left(), "TIME"
    if state == "closed":
        return "Not paired", "LOCKED"
    if last.get("message"):
        return last["message"], "CHECKMARK"
    return "Connected  :%d" % (bridge._port or bridge.PORT), "CHECKMARK"


def _labeled_row(layout, text):
    """A label on the left, aligned buttons on the right -- the same shape as
    Blender's own property rows, which a row heading only gives to properties."""
    split = layout.split(factor=0.38, align=True)
    split.label(text=text)
    return split.row(align=True)


class FIGBLEND_PT_panel(bpy.types.Panel):
    bl_label = "FigBlend"
    bl_idname = "FIGBLEND_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FigBlend"

    def draw_header_preset(self, context):
        # Importing a file is the fallback when Live Link is not used, so it
        # gets a header button rather than a row of its own.
        self.layout.operator(ops.FIGBLEND_OT_import.bl_idname,
                             text="", icon="IMPORT", emboss=False)

    def draw(self, context):
        layout = self.layout

        # Shown only when there is an update. A line that always reads "up to
        # date" is one people learn to skip.
        update = updater.state()
        if update["status"] == "available":
            layout.operator(ops.FIGBLEND_OT_get_update.bl_idname,
                            text="Update to %s"
                                 % updater.version_text(update["latest"]),
                            icon="PACKAGE")

        running = bridge.is_running()
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator(ops.FIGBLEND_OT_toggle_bridge.bl_idname,
                     text="Stop Live Link" if running else "Start Live Link",
                     icon="LINKED" if running else "UNLINKED",
                     depress=running)
        if running and bridge.pairing_state() != "open":
            row.operator(ops.FIGBLEND_OT_repair_link.bl_idname,
                         text="", icon="FILE_REFRESH")

        status = _live_link_status()
        if status:
            text, icon = status
            layout.label(text=text, icon=icon)

        prefs = props.preferences()
        if prefs is None:
            return
        row = layout.row(align=True)
        row.prop(prefs, "auto_start", text="Auto-Start", toggle=True)
        row.prop(prefs, "live_build_geometry", text="Build 3D", toggle=True)
        if prefs.live_build_geometry:
            layout.prop(prefs, "live_geometry_depth", text="Thickness")


class FIGBLEND_PT_selection(bpy.types.Panel):
    bl_label = "Selection"
    bl_idname = "FIGBLEND_PT_selection"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FigBlend"
    bl_parent_id = "FIGBLEND_PT_panel"

    def draw_header_preset(self, context):
        count = len(context.selected_objects)
        if count:
            self.layout.label(text=str(count))

    def draw(self, context):
        layout = self.layout
        settings = context.scene.figblend

        if not context.selected_objects:
            layout.label(text="Select objects to edit", icon="RESTRICT_SELECT_OFF")
            return

        row = _labeled_row(layout, "Geometry")
        row.operator(ops.FIGBLEND_OT_build_geometry.bl_idname,
                     text="3D", icon="MESH_CUBE")
        row.operator(ops.FIGBLEND_OT_flatten_geometry.bl_idname,
                     text="Flat", icon="MESH_PLANE")

        row = _labeled_row(layout, "Shadows")
        row.operator(ops.FIGBLEND_OT_set_shadow.bl_idname,
                     text="Off").enabled = False
        row.operator(ops.FIGBLEND_OT_set_shadow.bl_idname,
                     text="On").enabled = True

        row = _labeled_row(layout, "Lighting")
        row.prop(settings, "shader_mode", text="", slider=True)
        row.operator(ops.FIGBLEND_OT_apply_shader_mode.bl_idname,
                     text="", icon="CHECKMARK")
        row.operator(ops.FIGBLEND_OT_split_material.bl_idname,
                     text="", icon="DUPLICATE")


class FIGBLEND_PT_scene(bpy.types.Panel):
    bl_label = "Scene"
    bl_idname = "FIGBLEND_PT_scene"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FigBlend"
    bl_parent_id = "FIGBLEND_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        row = _labeled_row(layout, "Transparency")
        row.operator(ops.FIGBLEND_OT_set_render_method.bl_idname,
                     text="Dithered").method = "DITHERED"
        row.operator(ops.FIGBLEND_OT_set_render_method.bl_idname,
                     text="Blended").method = "BLENDED"

        col = layout.column(align=True)
        col.operator(ops.FIGBLEND_OT_select_relight.bl_idname,
                     text="Select Shapes Needing Light", icon="LIGHT")
        col.operator(ops.FIGBLEND_OT_rebuild_fonts.bl_idname,
                     text="Rescan Fonts", icon="FONT_DATA")


classes = (FIGBLEND_PT_panel, FIGBLEND_PT_selection, FIGBLEND_PT_scene)
