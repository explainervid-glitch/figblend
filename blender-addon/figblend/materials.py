"""The shared UI shader.

Every imported object gets a material built from the same node layout, with one
Value node ("UI Shader Mode") mixing Emission against Principled BSDF. Flat,
self-lit UI is the default because that is what most product shots want; dialling
the value to 1.0 hands the surface over to scene lighting.

Keeping the layout identical across every material is what makes the "split off
a few objects and make them 3D" workflow a one-click operation.
"""

import os

import bpy

MODE_NODE = "UI Shader Mode"
ALPHA_NODE = "UI Alpha"
IMAGE_NODE = "UI Image"
MAPPING_NODE = "UI Mapping"


def srgb_to_linear(value):
    """Figma colours are sRGB; Blender's shader sockets want linear."""
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def linear_to_srgb(value):
    """The inverse -- Blender's SVG importer hands back linear diffuse colours."""
    if value <= 0.0031308:
        return value * 12.92
    return 1.055 * (value ** (1.0 / 2.4)) - 0.055


def _configure_transparency(mat):
    """Blender 4.2 replaced the old blend/shadow method properties.

    Dithered rather than blended, deliberately. Blended transparency renders in
    a forward pass that does not write depth, so surfaces are ordered by a
    per-object sort. A UI is dozens of planes stacked a fraction of a millimetre
    apart, and the moment the view leaves dead-on that sort flips -- a card
    draws over its own contents and elements appear to vanish.

    Dithered writes depth, so occlusion is resolved per pixel and holds from any
    angle. The cost is hashed alpha, which is grainy in the viewport at low
    sample counts but resolves in a render. Hard-edged UI alpha barely shows it.

    Guarded with hasattr so one addon works across 4.2 EEVEE Next and earlier.
    """
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "DITHERED"
    if hasattr(mat, "blend_method"):
        try:
            mat.blend_method = "HASHED"  # the pre-4.2 equivalent of dithered
        except TypeError:
            pass
    if hasattr(mat, "shadow_method"):
        try:
            mat.shadow_method = "NONE"
        except TypeError:
            pass
    mat.use_backface_culling = False


def create_material(name, image_path=None, color=(1.0, 1.0, 1.0, 1.0),
                    emission_strength=1.0, mode=0.0, uv_transform=None):
    """Build a UI material. `color` is sRGB RGBA straight from the bundle.

    `uv_transform` is (scale_u, scale_v, offset_u, offset_v). When given, the
    texture is looked up through Generated coordinates instead of the mesh UVs,
    which is how a masked shape samples the right window of its content image
    without any UV unwrapping.
    """
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()

    linear = tuple(srgb_to_linear(c) for c in color[:3])

    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (600, 0)

    mode_value = tree.nodes.new("ShaderNodeValue")
    mode_value.name = mode_value.label = MODE_NODE
    mode_value.outputs[0].default_value = mode
    mode_value.location = (-400, -300)

    emission = tree.nodes.new("ShaderNodeEmission")
    emission.location = (100, 150)
    emission.inputs["Color"].default_value = (*linear, 1.0)
    emission.inputs["Strength"].default_value = emission_strength

    principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (100, -150)
    principled.inputs["Base Color"].default_value = (*linear, 1.0)
    if "Roughness" in principled.inputs:
        principled.inputs["Roughness"].default_value = 0.5

    mix_mode = tree.nodes.new("ShaderNodeMixShader")
    mix_mode.location = (350, 0)
    tree.links.new(mode_value.outputs[0], mix_mode.inputs[0])
    tree.links.new(emission.outputs[0], mix_mode.inputs[1])
    tree.links.new(principled.outputs[0], mix_mode.inputs[2])

    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (350, 250)

    mix_alpha = tree.nodes.new("ShaderNodeMixShader")
    mix_alpha.location = (500, 120)
    tree.links.new(transparent.outputs[0], mix_alpha.inputs[1])
    tree.links.new(mix_mode.outputs[0], mix_alpha.inputs[2])
    tree.links.new(mix_alpha.outputs[0], output.inputs["Surface"])

    if image_path and os.path.exists(image_path):
        tex = tree.nodes.new("ShaderNodeTexImage")
        tex.name = tex.label = IMAGE_NODE
        tex.location = (-300, 100)
        tex.interpolation = "Cubic"  # UI is viewed close up; Linear shows stair-stepping
        tex.extension = "CLIP"
        try:
            tex.image = bpy.data.images.load(image_path, check_existing=True)
            tex.image.alpha_mode = "STRAIGHT"
        except RuntimeError:
            tex.image = None

        if tex.image and uv_transform is not None:
            scale_u, scale_v, offset_u, offset_v = uv_transform
            coord = tree.nodes.new("ShaderNodeTexCoord")
            coord.location = (-900, 100)
            mapping = tree.nodes.new("ShaderNodeMapping")
            mapping.name = mapping.label = MAPPING_NODE
            mapping.location = (-650, 100)
            mapping.inputs["Scale"].default_value = (scale_u, scale_v, 1.0)
            mapping.inputs["Location"].default_value = (offset_u, offset_v, 0.0)
            tree.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
            tree.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
            # Outside the content image there is nothing to show.
            tex.extension = "CLIP"

        if tex.image:
            tree.links.new(tex.outputs["Color"], emission.inputs["Color"])
            tree.links.new(tex.outputs["Color"], principled.inputs["Base Color"])
            tree.links.new(tex.outputs["Alpha"], mix_alpha.inputs[0])
        else:
            _constant_alpha(tree, mix_alpha, color[3])
    else:
        _constant_alpha(tree, mix_alpha, color[3])

    _configure_transparency(mat)
    return mat


def _constant_alpha(tree, mix_alpha, alpha):
    node = tree.nodes.new("ShaderNodeValue")
    node.name = node.label = ALPHA_NODE
    node.outputs[0].default_value = alpha
    node.location = (350, 400)
    tree.links.new(node.outputs[0], mix_alpha.inputs[0])


def get_mode_node(mat):
    if not mat or not mat.use_nodes:
        return None
    return mat.node_tree.nodes.get(MODE_NODE)


def set_mode(mat, mode):
    """0.0 = pure emission, 1.0 = pure Principled. Fractions are valid."""
    node = get_mode_node(mat)
    if node:
        node.outputs[0].default_value = mode
        return True
    return False


def is_ui_material(mat):
    return get_mode_node(mat) is not None


def set_render_method(mat, method):
    """`method` is 'DITHERED' or 'BLENDED'. See _configure_transparency."""
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = method
    if hasattr(mat, "blend_method"):
        try:
            mat.blend_method = "HASHED" if method == "DITHERED" else "BLEND"
        except TypeError:
            pass
