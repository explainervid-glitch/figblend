"""Settings: scene-level for the post-import controls, addon-level for Live Link.

Live Link's auto-start lives in addon preferences rather than the scene, because
it has to survive opening a different .blend -- which is exactly when it fires.
"""

import bpy


class FigBlendSettings(bpy.types.PropertyGroup):
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


def preferences():
    """Addon preferences, or None when running outside the addon system."""
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None


classes = (FigBlendSettings, FigBlendPreferences)
