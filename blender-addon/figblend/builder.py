"""Turn a UI Bundle into Blender objects.

Text becomes real FONT objects, solid vector shapes become curves, everything
else becomes a textured plane. Parenting mirrors the Figma tree, so moving a
card moves its contents.

By default a frame's own background *is* the group object -- there is no
separate Empty. Frames with no background of their own leave no object behind
and their children attach to the nearest ancestor that does.
"""

import json
import math
import os
import zipfile

import bpy
from mathutils import Matrix, Vector

from . import fonts, materials

SUPPORTED_SCHEMA = "1.1.0"

ALIGN_X = {
    "LEFT": "LEFT",
    "CENTER": "CENTER",
    "RIGHT": "RIGHT",
    "JUSTIFIED": "LEFT",  # Blender's JUSTIFY needs a text box; LEFT is closer
}
ALIGN_Y = {
    "TOP": "TOP",
    "CENTER": "CENTER",
    "BOTTOM": "BOTTOM",
}


class ImportReport:
    def __init__(self):
        self.warnings = []
        self.objects = 0
        self.texts = 0
        self.images = 0
        self.vectors = 0
        self.masked = 0
        self.textured = 0
        self.extruded = 0
        self.collapsed = 0
        self.bad_bounds = 0
        # Collections from an earlier import of the same bundle, replaced.
        self.replaced = 0
        # Geo Meshes from that earlier import, carried over onto this one.
        self.geo_kept = 0
        self.empty_collections = 0
        # Shapes whose Figma effects (shadows, blurs) were discarded in favour
        # of real geometry. Tagged so they can be found and relit.
        self.effects_dropped = []
        # Fonts that were missing, substituted, or variable -- anything that
        # makes the imported text differ from the design.
        self.font_notes = set()

    def warn(self, message):
        self.warnings.append(message)


# --- bundle ------------------------------------------------------------------

def extract_bundle(filepath):
    """Unpack next to the .zip so asset paths stay valid across sessions."""
    base = os.path.splitext(filepath)[0] + "_assets"
    with zipfile.ZipFile(filepath) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ValueError("Bundle is corrupt: failed CRC on %s" % bad)
        for member in archive.namelist():
            # Refuse absolute paths and traversal before writing anything.
            target = os.path.normpath(os.path.join(base, member))
            if not target.startswith(os.path.normpath(base) + os.sep):
                raise ValueError("Bundle contains an unsafe path: %s" % member)
        archive.extractall(base)
    return base


def load_bundle(filepath):
    asset_dir = extract_bundle(filepath)
    manifest = os.path.join(asset_dir, "bundle.json")
    if not os.path.exists(manifest):
        raise ValueError("Bundle has no bundle.json -- is this a UI export?")
    with open(manifest, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    version = data.get("schema_version", "0")
    if version.split(".")[0] != SUPPORTED_SCHEMA.split(".")[0]:
        raise ValueError(
            "Bundle schema %s is not compatible with this addon (expects %s.x)"
            % (version, SUPPORTED_SCHEMA.split(".")[0]))
    return data, asset_dir


def import_bundle(filepath, options, report=None):
    report = report or ImportReport()
    data, asset_dir = load_bundle(filepath)

    px_per_meter = options.px_per_meter or data["export_settings"].get("px_per_meter", 1000)
    scale = 1.0 / float(px_per_meter)

    # Re-sending the same frame is the normal iteration loop, so the new import
    # takes the old one's place instead of piling up beside it. Keyed on the
    # bundle filename, which the plugin derives from the selection -- so a
    # different frame lands in its own collection and is left alone.
    source = os.path.basename(filepath).lower()
    held = []
    if getattr(options, "replace_existing", True):
        report.replaced, held = _remove_previous_import(source)

    name = data.get("document", {}).get("name") or "FigBlend"
    collection = bpy.data.collections.new(name)
    collection["figblend_source"] = source
    bpy.context.scene.collection.children.link(collection)

    # Building geometry on import ties spacing to thickness, so layers sit face
    # to face instead of interpenetrating. The gap keeps those faces from being
    # exactly coplanar -- touching faces at identical depth z-fight, which reads
    # as the layer behind leaking through.
    depth = getattr(options, "geometry_depth", 0.0)
    building = bool(getattr(options, "build_geometry", False)) and depth > 0.0
    gap_fraction = getattr(options, "layer_gap", 0.02)
    gap = depth * gap_fraction
    z_step = depth + gap if building else options.z_step

    ctx = {
        "scale": scale,
        "z_step": z_step,
        "build_depth": depth if building else 0.0,
        "stack": plan_stack(data, depth, gap) if building else None,
        "asset_dir": asset_dir,
        "collection": collection,
        "options": options,
        "report": report,
    }

    for root in data.get("roots", []):
        build_node(root, None, ctx)

    if held:
        from . import geo  # geo builds on this module, so not at the top
        report.geo_kept = geo.reattach(held, collection, report)

    report.empty_collections = _prune_empty_collections(collection)

    if options.pack_images:
        for image in bpy.data.images:
            if image.filepath and not image.packed_file:
                try:
                    image.pack()
                except RuntimeError:
                    pass

    return collection, report


# --- tree --------------------------------------------------------------------

LEAF_KINDS = ("TEXT", "IMAGE", "VECTOR", "MASKED")


def build_node(node, parent, ctx):
    kind = node.get("type")

    # A frame or group gets its own collection, so what was grouped in Figma
    # stays grouped in the outliner. This also preserves frames that MERGE mode
    # collapses: the object disappears, but the name and structure survive.
    previous = ctx["collection"]
    if kind not in LEAF_KINDS and ctx["options"].collection_mode == "GROUPS":
        ctx["collection"] = _new_collection(node.get("name", "Group"), previous)

    try:
        obj = _build_node_inner(node, parent, ctx)
    finally:
        ctx["collection"] = previous
    return obj


def _new_collection(name, parent_collection):
    collection = bpy.data.collections.new(name)
    parent_collection.children.link(collection)
    return collection


def _remove_previous_import(source):
    """Delete whatever an earlier import of the same bundle left behind.

    Objects go first, then their mesh/curve data and any UI material that just
    lost its last user -- otherwise every re-send leaks a full set of datablocks
    that only a save-and-reload would clear.

    Geo Meshes made on it are lifted out first rather than deleted; the caller
    hands them back to geo.reattach() once the new import is built.

    Returns the number of collections removed, and the held Geo Meshes.
    """
    stale = [c for c in bpy.data.collections
             if c.get("figblend_source") == source]
    if not stale:
        return 0, []

    from . import geo
    held = geo.hold(stale)

    orphans = []

    def strip(collection):
        for child in list(collection.children):
            strip(child)
        for obj in list(collection.objects):
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data is not None and data.users == 0:
                orphans.append(data)
        # Never remove a collection still linked somewhere we did not visit.
        bpy.data.collections.remove(collection)

    for collection in stale:
        try:
            collection.name  # a nested one may already be gone with its parent
        except ReferenceError:
            continue
        strip(collection)

    # Mesh and curve data first: they own the material slots, so a material only
    # drops to zero users once its data is gone.
    if orphans:
        bpy.data.batch_remove(orphans)

    spent = [m for m in bpy.data.materials
             if m.users == 0 and materials.is_ui_material(m)]
    if spent:
        bpy.data.batch_remove(spent)
    return len(stale), held


def _prune_empty_collections(collection):
    """Drop collections that ended up with nothing in them.

    A frame whose children were all invisible, or a group that only existed to
    hold a mask, would otherwise leave an empty entry in the outliner.
    """
    removed = 0
    for child in list(collection.children):
        removed += _prune_empty_collections(child)
        if not child.objects and not child.children:
            bpy.data.collections.remove(child)
            removed += 1
    return removed


def _build_node_inner(node, parent, ctx):
    kind = node.get("type")
    if kind == "TEXT":
        obj = build_text(node, ctx)
    elif kind in LEAF_KINDS:
        obj = build_graphic(node, ctx)
    else:
        obj = build_group(node, ctx)

    if obj is not None:
        # Before parenting: the child's parent-inverse is taken from the
        # parent's location, so the parent's Z must already be final.
        if ctx["build_depth"]:
            apply_thickness(obj, ctx["build_depth"])
            ctx["report"].extruded += 1

        obj["figblend_id"] = node.get("id", "")
        obj["figblend_type"] = kind
        obj["figblend_z_index"] = node.get("z_index", 0)
        ctx["report"].objects += 1

        if node.get("effects_dropped"):
            # Findable later with a custom-property search, so the shapes that
            # used to have a baked shadow can be relit deliberately.
            obj["figblend_effects_dropped"] = True
            ctx["report"].effects_dropped.append(obj.name)

        if parent is not None:
            obj.parent = parent
            # Computed directly rather than from matrix_world, which is only
            # correct after a depsgraph update we have not triggered yet.
            obj.matrix_parent_inverse = Matrix.Translation(-parent.location)
        child_parent = obj
    else:
        # Nothing was built for this node (a frame with no background of its
        # own), so its children attach to whatever it would have hung from.
        child_parent = parent

    for child in node.get("children", []):
        build_node(child, child_parent, ctx)

    return obj


def build_group(node, ctx):
    if ctx["options"].group_mode == "MERGE":
        # The backdrop is the group. Returns None when the frame has no
        # background, which collapses it out of the hierarchy.
        obj = build_graphic(node, ctx)
        if obj is None:
            ctx["report"].collapsed += 1
        return obj

    obj = bpy.data.objects.new(node.get("name", "Group"), None)
    obj.empty_display_type = "PLAIN_AXES"
    obj.empty_display_size = max(
        node["transform"]["width"], node["transform"]["height"]) * ctx["scale"] * 0.1
    obj.location = _location(node, ctx)
    # Rotation is deliberately not applied -- see SCHEMA.md.
    obj["figblend_rotation"] = node["transform"].get("rotation", 0.0)
    _link(obj, ctx)

    backdrop = build_graphic(node, ctx, name=node.get("name", "Group") + " BG")
    if backdrop is not None:
        # Its own id, so a Geo Mesh made from it finds it again on a re-send.
        backdrop["figblend_id"] = node.get("id", "") + ":bg"
        backdrop.parent = obj
        backdrop.matrix_parent_inverse = Matrix.Translation(-obj.location)

    return obj


# --- surfaces ----------------------------------------------------------------

def build_graphic(node, ctx, name=None):
    """Build whichever surface this node carries: a curve, a plane, or nothing."""
    name = name or node.get("name", "Element")
    if node.get("mask"):
        # Masked group: the mask is the silhouette, the content its texture.
        return build_textured_shape(node, ctx, name, node["mask"])
    if node.get("vector") and node.get("image"):
        # Texturized vector: the shape is geometry, the raster is its surface.
        return build_textured_shape(node, ctx, name, node["vector"])
    if node.get("vector"):
        return build_vector(node, ctx, name)
    if node.get("image"):
        return build_image(node, ctx, name)
    return None


def build_textured_shape(node, ctx, name, shape):
    """Curve geometry wearing a rasterized image, lined up with the design.

    Covers two cases that are the same underneath: a masked group, where the
    mask gives the silhouette and the masked layers give the picture; and a
    texturized vector, where the shape gives the silhouette and a render of that
    same node gives the picture.

    Real geometry rather than a transparent cutout means the edge stays clean
    under a close camera, and the shape can be extruded or bevelled.
    """
    content = node.get("image")
    centre, width, height, rotation = _surface_box(node, shape, ctx)
    shape_box = {"center": centre, "width": width, "height": height}

    parts = _import_svg(os.path.join(ctx["asset_dir"], shape["path"]), ctx)
    if not parts:
        return None

    obj = _assemble_svg(parts, name, width, height, ctx)
    obj.location = _place(centre, node, ctx)
    obj.rotation_euler.z = math.radians(rotation)

    # The SVG importer gives each path its own flat colour; the image replaces
    # all of them, across every path making up the silhouette.
    mat = materials.create_material(
        name,
        image_path=os.path.join(ctx["asset_dir"], content["path"]) if content else None,
        mode=ctx["options"].shader_mode,
        uv_transform=_texture_uv_transform(shape_box, (content or {}).get("bounds")),
    )
    for part in parts:
        part.data.materials.clear()
        part.data.materials.append(mat)
        _apply_visibility(part, ctx)

    if node.get("mask"):
        ctx["report"].masked += 1
    else:
        ctx["report"].textured += 1
    return obj


def _texture_uv_transform(shape_box, content_box):
    """Map the shape's Generated 0..1 space onto the image.

    Generated coordinates run 0..1 across the shape's own bounding box, so the
    image has to be scaled and shifted by the ratio between the two boxes. They
    differ whenever the render reaches past the shape -- a drop shadow, say.
    Y is inverted because Figma counts downwards and texture V does not.
    """
    if not content_box:
        return None

    mask_w = shape_box["width"]
    mask_h = shape_box["height"]
    content_w = content_box["width"]
    content_h = content_box["height"]
    if content_w <= 1e-9 or content_h <= 1e-9:
        return None

    mask_left = shape_box["center"]["x"] - mask_w / 2.0
    content_left = content_box["center"]["x"] - content_w / 2.0
    # "Bottom" in Figma's Y-down space is the larger coordinate.
    mask_bottom = shape_box["center"]["y"] + mask_h / 2.0
    content_bottom = content_box["center"]["y"] + content_h / 2.0

    return (
        mask_w / content_w,
        mask_h / content_h,
        (mask_left - content_left) / content_w,
        (content_bottom - mask_bottom) / content_h,
    )


def _surface_box(node, payload, ctx=None):
    """Where the surface actually sits.

    Exported assets cover the node's render bounds, which reach past the layout
    box when effects are involved, so `bounds` wins when the exporter supplied
    it. Render bounds are axis-aligned with the rotation already baked in.

    A surface always belongs to its node, so its bounds must overlap the node's
    own box -- a stroke or shadow grows the box, it does not move it somewhere
    else. Bounds that miss entirely are wrong, and trusting them scatters
    objects across the scene, so the node's own transform wins instead.
    """
    transform = node["transform"]
    bounds = (payload or {}).get("bounds")

    if bounds:
        if _boxes_overlap(bounds, transform):
            return bounds["center"], bounds["width"], bounds["height"], 0.0
        if ctx is not None:
            ctx["report"].warn(
                "Ignoring impossible bounds on '%s': centre (%.0f, %.0f) does "
                "not overlap the node at (%.0f, %.0f). Re-export with a current "
                "version of the plugin."
                % (node.get("name", "?"), bounds["center"]["x"],
                   bounds["center"]["y"], transform["center"]["x"],
                   transform["center"]["y"]))
            ctx["report"].bad_bounds += 1

    return (transform["center"], transform["width"], transform["height"],
            transform.get("rotation", 0.0))


def _boxes_overlap(a, b):
    return (abs(a["center"]["x"] - b["center"]["x"])
            <= (a["width"] + b["width"]) / 2.0
            and abs(a["center"]["y"] - b["center"]["y"])
            <= (a["height"] + b["height"]) / 2.0)


def build_image(node, ctx, name):
    payload = node["image"]
    centre, width, height, rotation = _surface_box(node, payload, ctx)

    obj = build_plane(name, width, height, ctx)
    obj.location = _place(centre, node, ctx)
    obj.rotation_euler.z = math.radians(rotation)

    mat = materials.create_material(
        name,
        image_path=os.path.join(ctx["asset_dir"], payload["path"]),
        mode=ctx["options"].shader_mode,
    )
    obj.data.materials.append(mat)
    _apply_visibility(obj, ctx)
    ctx["report"].images += 1
    return obj


def build_vector(node, ctx, name):
    payload = node["vector"]
    centre, width, height, rotation = _surface_box(node, payload, ctx)
    path = os.path.join(ctx["asset_dir"], payload["path"])

    parts = _import_svg(path, ctx)
    if not parts:
        return None

    obj = _assemble_svg(parts, name, width, height, ctx)
    obj.location = _place(centre, node, ctx)
    obj.rotation_euler.z = math.radians(rotation)

    for part in parts:
        _convert_svg_materials(part, name, ctx)
        _apply_visibility(part, ctx)
    ctx["report"].vectors += 1
    return obj


def _import_svg(path, ctx):
    """Import an SVG and return its curve objects, unmerged.

    Deliberately *not* joined. Blender fills a curve from all of its splines at
    once, so merging a fill path with an outlined stroke makes their windings
    cancel and the shape floods with one colour. Joining curves also drops every
    material but the active object's. Keeping the paths as separate objects
    sidesteps both; they are parented together in _assemble_svg.
    """
    if not os.path.exists(path):
        ctx["report"].warn("Missing SVG asset: %s" % os.path.basename(path))
        return None

    before_objects = set(bpy.data.objects.keys())
    before_collections = set(bpy.data.collections.keys())
    try:
        bpy.ops.import_curve.svg(filepath=path)
    except (RuntimeError, AttributeError) as err:
        ctx["report"].warn(
            "SVG import failed for %s (%s). Enable the 'Scalable Vector "
            "Graphics' addon in Preferences." % (os.path.basename(path), err))
        return None

    new = [bpy.data.objects[n]
           for n in set(bpy.data.objects.keys()) - before_objects]
    if not new:
        ctx["report"].warn("SVG produced no geometry: %s" % os.path.basename(path))
        return None

    # The importer drops everything into its own collection named after the
    # file; move it into ours and bin the leftover.
    for obj in new:
        ctx["collection"].objects.link(obj)
        for coll in list(obj.users_collection):
            if coll is not ctx["collection"]:
                coll.objects.unlink(obj)
    for name in set(bpy.data.collections.keys()) - before_collections:
        coll = bpy.data.collections.get(name)
        if coll is not None and not coll.objects and not coll.children:
            bpy.data.collections.remove(coll)

    bpy.context.view_layer.update()
    return new


def _assemble_svg(parts, name, width, height, ctx):
    """Scale an SVG's paths as one unit and parent them into a single object.

    Every path is baked into its own data so no object carries a scale --
    object scale multiplies through to children, which silently inflates the
    text inside a card. The first path becomes the node's object and the rest
    hang off it, so the whole shape moves together.
    """
    scale = ctx["scale"]
    minimum, maximum = _combined_bounds(parts)
    span_x = maximum[0] - minimum[0]
    span_y = maximum[1] - minimum[1]
    centre = [(maximum[i] + minimum[i]) / 2.0 for i in range(3)]

    factor_x = (width * scale) / span_x if span_x > 1e-9 else 1.0
    factor_y = (height * scale) / span_y if span_y > 1e-9 else 1.0

    # Centre on the group's middle, then scale -- applied to every path with the
    # same numbers so their relative layout survives.
    recentre = Matrix.Translation((-centre[0], -centre[1], -centre[2]))
    resize = Matrix.Diagonal((factor_x, factor_y, 1.0, 1.0))

    for part in parts:
        part.data.transform(resize @ recentre @ part.matrix_world)
        _normalize_radius(part.data)
        part.matrix_world = Matrix.Identity(4)
        # Generated coordinates must span the whole shape, not each path's own
        # slice of it, or a mask's texture lookup lands in the wrong place.
        _set_texspace(part, width * scale, height * scale)

    root = parts[0]
    root.name = name
    for part in parts[1:]:
        part.name = "%s Part" % name
        # Marked so thickness reaches every path of the shape, not just the
        # first, while only the root carries the Z shift.
        part["figblend_part"] = True
        part.parent = root
        # Geometry is already in final position, so the child adds nothing.
        part.matrix_parent_inverse = Matrix.Identity(4)
        part.location = (0.0, 0.0, 0.0)
    return root



def _normalize_radius(data):
    """Reset control-point radius to 1 after scaling the curve data.

    Curve.transform() scales each point's radius along with its position, and
    radius multiplies extrude and bevel. Left alone, a shape fitted by a factor
    of ~3 extrudes three times as thick as asked for.
    """
    for spline in getattr(data, "splines", []):
        points = spline.bezier_points if spline.type == "BEZIER" else spline.points
        for point in points:
            point.radius = 1.0


def _combined_bounds(objects):
    """World-space min/max corner across several objects."""
    lows = [float("inf")] * 3
    highs = [float("-inf")] * 3
    for obj in objects:
        for corner in obj.bound_box:
            point = obj.matrix_world @ Vector(corner)
            for axis in range(3):
                lows[axis] = min(lows[axis], point[axis])
                highs[axis] = max(highs[axis], point[axis])
    return lows, highs


def _set_texspace(obj, width, height):
    """Point Generated texture coordinates at the geometry we just built.

    Blender does not recompute a curve's texture space after its data has been
    transformed, even with use_auto_texspace on -- it keeps whatever the SVG
    importer left, so Generated coords collapse into a narrow band and any
    texture or gradient driven by them is wrong.

    The size is taken from the target dimensions rather than read back from
    bound_box, which is still stale immediately after data.transform().
    """
    data = obj.data
    if not hasattr(data, "texspace_location"):
        return
    data.use_auto_texspace = False
    # Origin was centred on the bounds, so the geometry straddles zero.
    data.texspace_location = (0.0, 0.0, 0.0)
    # texspace_size is a half-extent; a flat shape has no depth, and a zero
    # would divide by zero in the Generated lookup.
    data.texspace_size = (width / 2.0 or 1.0, height / 2.0 or 1.0, 1.0)


def _convert_svg_materials(obj, name, ctx):
    """Swap the importer's plain materials for UI materials of the same colour.

    Without this the curves would ignore the emission/BSDF control that every
    other imported object responds to.
    """
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or materials.is_ui_material(mat):
            continue
        linear = tuple(mat.diffuse_color)
        srgb = tuple(materials.linear_to_srgb(c) for c in linear[:3])
        slot.material = materials.create_material(
            "%s %s" % (name, mat.name),
            color=(srgb[0], srgb[1], srgb[2], linear[3] if len(linear) > 3 else 1.0),
            mode=ctx["options"].shader_mode,
        )


def build_plane(name, width, height, ctx):
    """A centre-origin quad with a full 0..1 UV square, linked into the scene.

    Linking happens here rather than at the call site: an unlinked object is
    invisible and gets dropped on save, and that failure is silent.
    """
    scale = ctx["scale"]
    half_w = width * scale / 2.0
    half_h = height * scale / 2.0

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(
        [(-half_w, -half_h, 0.0), (half_w, -half_h, 0.0),
         (half_w, half_h, 0.0), (-half_w, half_h, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()

    uv_layer = mesh.uv_layers.new(name="UVMap")
    for i, uv in enumerate([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]):
        uv_layer.data[i].uv = uv

    return _link(bpy.data.objects.new(name, mesh), ctx)


# --- text --------------------------------------------------------------------

def build_text(node, ctx):
    text = node["text"]
    scale = ctx["scale"]
    size = text.get("size", 16) or 16

    curve = bpy.data.curves.new(name=node.get("name", "Text"), type="FONT")
    curve.body = text.get("characters", "")

    font, note = fonts.load(
        text["font"]["family"], text["font"]["style"],
        report=ctx["report"].warn,
    )
    if font:
        curve.font = font
    if note:
        ctx["report"].font_notes.add(note)

    # Figma's size is the em; Blender's covers the font's whole bounding box,
    # which is bigger by a per-font factor -- 1.33 for Nunito. Set Figma's
    # number directly and every text comes out about a quarter too small.
    metrics = fonts.metrics(curve.font)
    em = size * scale
    curve.size = em * (metrics.size_factor if metrics else 1.0)

    curve.align_x = ALIGN_X.get(text.get("align_horizontal", "LEFT"), "LEFT")
    curve.align_y = ALIGN_Y.get(text.get("align_vertical", "TOP"), "TOP")

    # Blender adds (space_character - 1) * size / 2 between every pair of
    # characters, whatever the glyphs -- a true tracking value, so Figma's
    # pixels convert exactly once the size is Blender's own.
    letter_spacing = text.get("letter_spacing") or {}
    if letter_spacing.get("unit") == "PIXELS" and letter_spacing.get("value"):
        tracking = letter_spacing["value"] * scale
        curve.space_character = 1.0 + 2.0 * tracking / curve.size

    # Blender advances lines by space_line * size, its size again. Figma's Auto
    # line height is the font's own ascent + descent + gap.
    line_height = text.get("line_height") or {}
    line_pitch = None
    if line_height.get("unit") == "PIXELS" and line_height.get("value"):
        line_pitch = line_height["value"] * scale
    elif metrics is not None:
        line_pitch = (metrics.ascent - metrics.descent + metrics.line_gap) * em
    if line_pitch:
        curve.space_line = line_pitch / curve.size

    obj = bpy.data.objects.new(node.get("name", "Text"), curve)

    # Anchor is resolved by the exporter to match align_x/align_y exactly.
    anchor = text.get("anchor") or node["transform"]["center"]
    obj.location = _place(anchor, node, ctx)

    # A fixed line height wider than the font's own centres each line in its
    # box, as CSS does. Blender's TOP and BOTTOM sit the glyphs against the
    # edge instead, so move them in by that half-leading.
    if metrics is not None and line_height.get("unit") == "PIXELS" and line_pitch:
        half_leading = (line_pitch - (metrics.ascent - metrics.descent) * em) / 2.0
        if curve.align_y == "TOP":
            obj.location.y -= half_leading
        elif curve.align_y == "BOTTOM":
            obj.location.y += half_leading
    obj.rotation_euler.z = math.radians(node["transform"].get("rotation", 0.0))
    _link(obj, ctx)

    colour = text.get("color", {"r": 0, "g": 0, "b": 0, "a": 1})
    mat = materials.create_material(
        node.get("name", "Text"),
        color=(colour["r"], colour["g"], colour["b"], colour.get("a", 1.0)),
        mode=ctx["options"].shader_mode,
    )
    curve.materials.append(mat)

    _apply_visibility(obj, ctx)
    # Text almost never wants to catch or cast shadows -- that is the single
    # biggest cause of muddy-looking UI renders.
    if ctx["options"].text_no_shadow:
        obj.visible_shadow = False

    ctx["report"].texts += 1
    return obj


# --- thickness ---------------------------------------------------------------

CURVE_TYPES = {"CURVE", "FONT", "SURFACE"}

# Text is a curve too, but TextCurve.transform() is a no-op: the geometry is
# regenerated from the body string, so the back-shift that keeps a curve's front
# face on the origin never lands. Left on `extrude` alone, text straddles its
# origin and floats half the depth above whatever it sits on. Solidify grows
# backwards from the real front face, so text goes through that path instead.
SOLIDIFY_TYPES = {"FONT", "MESH"}


def thickness_targets(obj):
    """The object plus the extra SVG paths that make up the same shape."""
    return [obj] + [c for c in obj.children if c.get("figblend_part")]


def apply_thickness(obj, depth):
    """Give a flat imported object real depth, growing backwards only.

    `depth` is the total thickness. Blender's curve `extrude` is a half-depth
    straddling the curve plane, so the data is shifted back by that half: the
    front face ends up on the object origin and the body sits behind it. The
    origin therefore stays exactly where the flat layer was, which is what lets
    layers stack predictably and keeps the manipulator on the visible face
    rather than buried mid-slab.

    The object itself is never moved, so callers stay in charge of placement.
    Re-running is safe: each target remembers the depth already applied and only
    shifts by the difference.
    """
    for target in thickness_targets(obj):
        previous = target.get("figblend_depth", 0.0)
        if target.type in SOLIDIFY_TYPES:
            if target.type == "FONT":
                # A solidified font must not also be extruded, or the two stack.
                target.data.extrude = 0.0
            modifier = target.modifiers.get("FigBlend Solidify")
            if modifier is None:
                modifier = target.modifiers.new("FigBlend Solidify", "SOLIDIFY")
            modifier.thickness = depth
            modifier.offset = -1.0  # keeps the original face, grows behind it
            modifier.use_rim = True
        elif target.type in CURVE_TYPES:
            data = target.data
            data.extrude = depth / 2.0
            if data.dimensions == "2D" and data.fill_mode != "BOTH":
                data.fill_mode = "BOTH"  # otherwise the ends stay open
            data.transform(Matrix.Translation((0.0, 0.0, (previous - depth) / 2.0)))
        else:
            continue
        target["figblend_depth"] = depth


def clear_thickness(obj):
    """Undo apply_thickness, putting the geometry back on the origin."""
    for target in thickness_targets(obj):
        previous = target.get("figblend_depth", 0.0)
        if target.type in SOLIDIFY_TYPES:
            modifier = target.modifiers.get("FigBlend Solidify")
            if modifier:
                target.modifiers.remove(modifier)
            if target.type == "FONT":
                # Bundles built before text used Solidify left extrude behind.
                target.data.extrude = 0.0
                target.data.bevel_depth = 0.0
        elif target.type in CURVE_TYPES:
            target.data.extrude = 0.0
            target.data.bevel_depth = 0.0
            target.data.transform(Matrix.Translation((0.0, 0.0, previous / 2.0)))
        if "figblend_depth" in target:
            del target["figblend_depth"]

    # Older imports moved the object instead of the data.
    if "figblend_base_z" in obj:
        obj.location.z = obj["figblend_base_z"]
        del obj["figblend_base_z"]


# --- stacking ----------------------------------------------------------------

def _node_box(node):
    """Axis-aligned XY box in Figma pixels, or None."""
    transform = node.get("transform") or {}
    centre = transform.get("center")
    width = transform.get("width")
    height = transform.get("height")
    if not centre or not width or not height:
        return None
    return (centre["x"], centre["y"], width, height)


def _overlaps_xy(a, b):
    """True when two boxes share area. Touching edges do not count."""
    return (abs(a[0] - b[0]) < (a[2] + b[2]) / 2.0
            and abs(a[1] - b[1]) < (a[3] + b[3]) / 2.0)


def _has_surface(node):
    return bool(node.get("image") or node.get("vector")
                or node.get("mask") or node.get("text"))


def plan_stack(data, depth, gap):
    """Decide each node's front-face Z by resting it on what it covers.

    Layer order alone is not enough. A document can have fifty layers that never
    touch, and stacking those by index builds a tower of empty space. Worse, a
    layer drawn above another it does not overlap would be lifted by it.

    So walk bottom to top and, for each layer, look only at already-placed
    layers whose XY box it actually overlaps. The layer rests on the highest of
    those; if it covers nothing, it starts at the base.
    """
    nodes = []

    def collect(node):
        nodes.append(node)
        for child in node.get("children", []):
            collect(child)

    for root in data.get("roots", []):
        collect(root)
    nodes.sort(key=lambda n: n.get("z_index", 0))

    placed = []   # (box, front_z) of everything already resting somewhere
    fronts = {}
    for node in nodes:
        if not _has_surface(node):
            continue  # a frame with no surface of its own holds nothing up
        box = _node_box(node)
        if box is None:
            continue
        support = 0.0
        for other_box, other_front in placed:
            if other_front > support and _overlaps_xy(box, other_box):
                support = other_front
        back = support + gap if support > 0.0 else 0.0
        front = back + depth
        fronts[node.get("id")] = front
        placed.append((box, front))
    return fronts


def object_xy_box(obj):
    """World-space XY box of an object, measured from its evaluated mesh.

    bound_box is unreliable on imported curves -- it keeps whatever the SVG
    importer left -- so evaluate the real geometry instead.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    xs, ys = [], []
    for target in thickness_targets(obj):
        evaluated = target.evaluated_get(depsgraph)
        try:
            mesh = evaluated.to_mesh()
        except RuntimeError:
            continue
        for vertex in mesh.vertices:
            world = target.matrix_world @ vertex.co
            xs.append(world.x)
            ys.append(world.y)
        evaluated.to_mesh_clear()
    if not xs:
        return None
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0,
            max(xs) - min(xs), max(ys) - min(ys))


def restack_objects(objects, depth, gap):
    """Rest each object on the highest thing it actually overlaps.

    Same rule as the import planner, but measured from geometry rather than the
    bundle, so it works on a selection the user has already moved around.
    """
    ordered = sorted(objects, key=lambda o: o.get("figblend_z_index", 0))
    placed = []
    moved = 0
    for obj in ordered:
        box = object_xy_box(obj)
        if box is None:
            continue
        support = 0.0
        for other_box, other_front in placed:
            if other_front > support and _overlaps_xy(box, other_box):
                support = other_front
        front = (support + gap if support > 0.0 else 0.0) + depth
        obj.location.z = front
        placed.append((box, front))
        moved += 1
    return moved


# --- placement ---------------------------------------------------------------

def _link(obj, ctx):
    ctx["collection"].objects.link(obj)
    return obj


def _z(node, ctx):
    # When building geometry the planner has already worked out where each layer
    # rests; fall back to flat index spacing otherwise.
    stack = ctx.get("stack")
    if stack:
        front = stack.get(node.get("id"))
        if front is not None:
            return front
    return node.get("z_index", 0) * ctx["z_step"]


def _place(centre, node, ctx):
    """Figma is Y-down; Blender is Y-up. Z carries the stacking order."""
    scale = ctx["scale"]
    return (centre["x"] * scale, -centre["y"] * scale, _z(node, ctx))


def _location(node, ctx):
    return _place(node["transform"]["center"], node, ctx)


def _apply_visibility(obj, ctx):
    if ctx["options"].no_shadows:
        obj.visible_shadow = False
