import bpy

# Used only when this folder is copied into scripts/addons as a legacy add-on.
# Installed as an extension, Blender reads blender_manifest.toml instead and
# ignores this. version is stamped by `npm run bump` -- edit it there.
bl_info = {
    "name": "FigBlend",
    "description": "Import UI exported from Figma with editable text, hierarchy and shader controls",
    "author": "",
    "version": (0, 2, 4),
    "blender": (4, 2, 0),
    "location": "File > Import > FigBlend Bundle (.zip), and View3D > Sidebar > FigBlend",
    "category": "Import-Export",
}

# On a reload (Reload Scripts, re-enabling, or an extension update in a running
# Blender) every submodule from the previous load is still cached in
# sys.modules, and `from . import x` would hand back that stale copy without
# running the new code. So each cached one is re-executed first, in dependency
# order. Checked in sys.modules rather than in this module's namespace: when
# Blender re-imports the package from scratch the namespace is empty but the
# cache is not. A module that is new in this version is not cached yet and is
# simply imported below.
import importlib
import sys

_SUBMODULES = ("fonts", "materials", "builder", "geo", "props", "bridge",
               "updater", "ops", "ui")
for _name in _SUBMODULES:
    _cached = sys.modules.get("%s.%s" % (__name__, _name))
    if _cached is not None:
        importlib.reload(_cached)

from . import fonts, materials, builder, geo, props, bridge, updater, ops, ui


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
