import bpy

# Used only when this folder is copied into scripts/addons as a legacy add-on.
# Installed as an extension, Blender reads blender_manifest.toml instead and
# ignores this. version is stamped by `npm run bump` -- edit it there.
bl_info = {
    "name": "FigBlend",
    "description": "Import UI exported from Figma with editable text, hierarchy and shader controls",
    "author": "",
    "version": (0, 2, 0),
    "blender": (4, 2, 0),
    "location": "File > Import > FigBlend Bundle (.zip), and View3D > Sidebar > FigBlend",
    "category": "Import-Export",
}

# Guard on one of our own submodules, not on bpy: bpy is imported at module
# top level, so "bpy" in locals() is already true on the very first load.
if "fonts" in locals():
    import importlib
    importlib.reload(fonts)
    importlib.reload(materials)
    importlib.reload(builder)
    importlib.reload(props)   # bridge reads addon preferences from here
    importlib.reload(bridge)
    importlib.reload(updater)
    importlib.reload(ops)
    importlib.reload(ui)
else:
    from . import fonts, materials, builder, props, bridge, updater, ops, ui


def menu_func_import(self, context):
    self.layout.operator(ops.FIGBLEND_OT_import.bl_idname,
                         text="FigBlend Bundle (.zip)")


classes = props.classes + ops.classes + ui.classes


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.figblend = bpy.props.PointerProperty(type=props.FigBlendSettings)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bridge.register_handlers()
    updater.register()


def unregister():
    updater.unregister()
    # Leaving the socket bound would keep the port busy after the addon is
    # disabled, and the next enable could not rebind it.
    bridge.unregister_handlers()
    bridge.stop()
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.Scene, "figblend"):
        del bpy.types.Scene.figblend


if __name__ == "__main__":
    register()
