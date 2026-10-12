"""Sidebar panel: N > FigBlend."""

import bpy

from . import bridge, ops, props, updater


class FIGBLEND_PT_panel(bpy.types.Panel):
    bl_label = "FigBlend"
    bl_idname = "FIGBLEND_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FigBlend"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.figblend

        # Only when there is something to act on -- a panel that always reads
        # "up to date" is a line people learn to skip.
        update = updater.state()
        if update["status"] == "available":
            layout.operator(ops.FIGBLEND_OT_get_update.bl_idname,
                            text="Update to %s"
                                 % updater.version_text(update["latest"]),
                            icon="PACKAGE")

        layout.operator(ops.FIGBLEND_OT_import.bl_idname,
                        text="Import Bundle", icon="IMPORT")

        running = bridge.is_running()
        box = layout.box()
        row = box.row()
        row.alert = running
        row.operator(ops.FIGBLEND_OT_toggle_bridge.bl_idname,
                     text="Stop Live Link" if running else "Start Live Link",
                     icon="UNLINKED" if not running else "LINKED",
                     depress=running)
        prefs = props.preferences()
        if prefs is not None:
            row = box.row(align=True)
            row.prop(prefs, "auto_start", text="Auto-Start", toggle=True)
            row.prop(prefs, "live_build_geometry", text="Build 3D", toggle=True)
            sub = box.row()
            sub.enabled = prefs.live_build_geometry
            sub.prop(prefs, "live_geometry_depth", text="Thickness")

        if running:
            state = bridge.pairing_state()
            row = box.row(align=True)
            if state == "paired":
                row.label(text="localhost:%d paired" % (bridge._port or bridge.PORT),
                          icon="CHECKMARK")
            elif state == "open":
                row.label(text="Pairing open %ds" % bridge.pairing_seconds_left(),
                          icon="UNLOCKED")
            else:
                row.label(text="localhost:%d not paired"
                               % (bridge._port or bridge.PORT), icon="LOCKED")
            if state != "open":
                row.operator(ops.FIGBLEND_OT_repair_link.bl_idname,
                             text="", icon="FILE_REFRESH")
            last = bridge._last_result
            if last.get("message"):
                box.label(text=last["message"][:44],
                          icon="ERROR" if last.get("state") == "error" else "INFO")
        elif not bpy.data.filepath:
            box.label(text="Save the .blend to enable", icon="INFO")

        selected = len(context.selected_objects)

        box = layout.box()
        box.label(text="Geometry", icon="MOD_SOLIDIFY")
        row = box.row(align=True)
        row.enabled = selected > 0
        row.operator(ops.FIGBLEND_OT_build_geometry.bl_idname,
                     text="3D", icon="MESH_CUBE")
        row.operator(ops.FIGBLEND_OT_flatten_geometry.bl_idname,
                     text="Flatten", icon="MESH_PLANE")

        box = layout.box()
        box.label(text="Shading", icon="MATERIAL")
        box.prop(settings, "shader_mode", text="Emission / BSDF")
        row = box.row()
        row.enabled = selected > 0
        row.operator(ops.FIGBLEND_OT_apply_shader_mode.bl_idname)
        row = box.row()
        row.enabled = selected > 0
        row.operator(ops.FIGBLEND_OT_split_material.bl_idname, icon="DUPLICATE")

        box.separator()
        box.label(text="Layer sorting")
        row = box.row(align=True)
        row.operator(ops.FIGBLEND_OT_set_render_method.bl_idname,
                     text="Dithered").method = "DITHERED"
        row.operator(ops.FIGBLEND_OT_set_render_method.bl_idname,
                     text="Blended").method = "BLENDED"

        box = layout.box()
        box.label(text="Shadows", icon="LIGHT")
        row = box.row(align=True)
        row.enabled = selected > 0
        row.operator(ops.FIGBLEND_OT_set_shadow.bl_idname,
                     text="Off").enabled = False
        row.operator(ops.FIGBLEND_OT_set_shadow.bl_idname,
                     text="On").enabled = True

        if selected:
            box.label(text="%d object(s) selected" % selected)
        else:
            box.label(text="Select objects to edit", icon="INFO")

        box.operator(ops.FIGBLEND_OT_select_relight.bl_idname,
                     text="Select Shapes Needing Light", icon="OUTLINER_OB_LIGHT")

        box = layout.box()
        box.label(text="Fonts", icon="FONT_DATA")
        box.operator(ops.FIGBLEND_OT_rebuild_fonts.bl_idname, icon="FILE_REFRESH")


classes = (FIGBLEND_PT_panel,)
