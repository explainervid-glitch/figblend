"""Settings: scene-level for the post-import controls, addon-level for Live Link.

Live Link's auto-start lives in addon preferences rather than the scene, because
it has to survive opening a different .blend -- which is exactly when it fires.
"""

import bpy

from . import builder


# The Thickness field stores nothing itself: it reads the active object and
# writes every selected one, so it always shows what is really there.

def _get_thickness(self):
    obj = bpy.context.view_layer.objects.active
    if obj is not None and obj.get("figblend_part") and obj.parent is not None:
        obj = obj.parent
    return builder.depth_of(obj) if builder.can_thicken(obj) else 0.0


def _set_thickness(self, value):
    for obj in builder.thickness_roots(bpy.context.selected_objects):
        builder.set_depth(obj, value)


class FigBlendSettings(bpy.types.PropertyGroup):
    thickness: bpy.props.FloatProperty(
        name="Thickness",
        description=(
            "Thickness of the selected elements, grown backwards from their "
            "front face. Shows the active one's; setting it changes them all. "
            "0 makes them flat. In the viewport, Alt+T drags it"
        ),
        min=0.0, soft_max=0.01, step=0.01, precision=5, unit="LENGTH",
        get=_get_thickness, set=_set_thickness,
    )
    shader_mode: bpy.props.FloatProperty(
        name="Shader Mode",
        description=(
            "0 = flat emission (self-lit UI), 1 = Principled BSDF (reacts to "
            "scene lighting). Values in between blend the two"
        ),
        default=0.0, min=0.0, max=1.0, subtype="FACTOR",
    )
    emission_strength: bpy.props.FloatProperty(
        name="Emission Strength",
        description="Brightness of the emissive half of the shader",
        default=1.0, min=0.0, soft_max=10.0,
    )


class FigBlendPreferences(bpy.types.AddonPreferences):
    # Must match the addon's module name, which is this package.
    bl_idname = __package__

    auto_start: bpy.props.BoolProperty(
        name="Start Live Link automatically",
        description=(
            "Begin listening whenever a saved .blend is opened, so the Figma "
            "plugin finds this instance without you pressing Start"
        ),
        default=False,
    )

    # A Live Link send has no import dialog, so its options live here instead.
    live_build_geometry: bpy.props.BoolProperty(
        name="Build 3D Geometry",
        description=(
            "Extrude and stack layers on bundles that arrive over Live Link. "
            "The import dialog's own setting cannot apply -- a Live Link send "
            "never opens one"
        ),
        default=False,
    )
    live_geometry_depth: bpy.props.FloatProperty(
        name="Thickness",
        description="Layer thickness, which also becomes the layer spacing",
        default=0.0005, min=0.00001, soft_max=0.02, precision=5, unit="LENGTH",
    )

    geo_node_group: bpy.props.StringProperty(
        name="Geo Node Group",
        description=(
            "Geometry node group used by Geo Mesh. Inputs are matched by name "
            "(Height, Roundness, Text, Font, Icon Object, ...)"
        ),
        default="Button Custom",
    )
    geo_library: bpy.props.StringProperty(
        name="Library",
        description=(
            "Optional .blend that holds the node group. Used when the open file "
            "does not have it, so Geo Mesh works in any file"
        ),
        default="", subtype="FILE_PATH",
    )

    check_updates: bpy.props.BoolProperty(
        name="Check for Updates",
        description=(
            "Once per session, ask GitHub whether a newer FigBlend has been "
            "released, and say so in the FigBlend panel. Nothing is downloaded "
            "until you choose to update. Skipped when Blender's online access "
            "is off"
        ),
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "auto_start")
        layout.label(
            text="An unsaved file is skipped: ./figAssets has nowhere to live.",
            icon="INFO")

        box = layout.box()
        box.label(text="Live Link imports", icon="IMPORT")
        box.prop(self, "live_build_geometry")
        row = box.row()
        row.enabled = self.live_build_geometry
        row.prop(self, "live_geometry_depth")

        box = layout.box()
        box.label(text="Geo Mesh", icon="GEOMETRY_NODES")
        box.prop(self, "geo_node_group")
        box.prop(self, "geo_library")

        from . import updater
        box = layout.box()
        box.label(text="Updates  -  FigBlend %s"
                       % updater.version_text(updater.installed_version()),
                  icon="PACKAGE")
        row = box.row(align=True)
        row.prop(self, "check_updates")
        row.operator("figblend.check_update", text="Check Now",
                     icon="FILE_REFRESH")
        info = updater.state()
        if info["message"]:
            box.label(text=info["message"],
                      icon="ERROR" if info["status"] == "error" else "INFO")
        if info["status"] == "available":
            box.operator("figblend.get_update", icon="IMPORT")
        source = updater.install_source()
        if source != "repository":
            # Without the repository, Blender has nowhere to pull an update
            # from. Say so here rather than leave the user wondering.
            box.label(text="Not installed from the FigBlend repository, so "
                           "updates are manual.", icon="INFO")
            box.label(text="Add the repository and install from it to "
                           "update in place.")


def preferences():
    """Addon preferences, or None when running outside the addon system."""
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None


classes = (FigBlendSettings, FigBlendPreferences)
