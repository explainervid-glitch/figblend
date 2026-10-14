"""Operators: import, plus the shader and shadow controls."""

import zipfile

import bpy
from bpy_extras.io_utils import ImportHelper

from . import bridge, builder, fonts, geo, materials, props, updater


class FIGBLEND_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = "figblend.import_bundle"
    bl_label = "Import FigBlend Bundle"
    bl_description = "Import a .zip exported by the FigBlend plugin"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".zip"
    filter_glob: bpy.props.StringProperty(default="*.zip", options={"HIDDEN"})

    px_per_meter: bpy.props.IntProperty(
        name="Pixels per Metre",
        description="Override the value stored in the bundle. 0 keeps the bundle's own setting",
        default=0, min=0, soft_max=4000,
    )
    z_step: bpy.props.FloatProperty(
        name="Layer Spacing",
        description=(
            "Distance in metres between stacking levels. Every layer in the "
            "design gets one step, so a busy frame accumulates depth quickly -- "
            "raise this for deliberate parallax, drop it towards 0 for flat"
        ),
        default=0.0001, min=0.0, soft_max=0.01, precision=6, unit="LENGTH",
    )
    build_geometry: bpy.props.BoolProperty(
        name="Build 3D Geometry",
        description=(
            "Extrude every element on import and stack the layers flush, back "
            "face to front face, in Figma's layer order. Meant for Texturized "
            "exports, where the shapes are curves"
        ),
        default=False,
    )
    geometry_depth: bpy.props.FloatProperty(
        name="Thickness",
        description=(
            "Total thickness of each layer, grown backwards from the layer's "
            "own plane. It also drives the layer spacing, so the stack sits "
            "face to face with no overlap"
        ),
        default=0.0005, min=0.00001, soft_max=0.02, precision=5, unit="LENGTH",
    )
    layer_gap: bpy.props.FloatProperty(
        name="Seam Gap",
        description=(
            "Clearance between stacked layers, as a fraction of thickness. "
            "Exactly touching faces sit at the same depth and z-fight, which "
            "looks like the layer behind leaking through"
        ),
        default=0.02, min=0.0, max=0.5, subtype="FACTOR",
    )
    collection_mode: bpy.props.EnumProperty(
        name="Collections",
        description="How the Figma group structure maps onto the outliner",
        items=[
            ("GROUPS", "Collection per Group",
             "Every frame and group becomes its own nested collection, so what "
             "was grouped in Figma stays grouped in Blender"),
            ("SINGLE", "One Collection",
             "Everything lands in a single collection. Parenting still mirrors "
             "the Figma tree"),
        ],
        default="GROUPS",
    )
    group_mode: bpy.props.EnumProperty(
        name="Groups",
        description="What to create for frames and groups",
        items=[
            ("MERGE", "Merge Into Background",
             "The frame's own background is the group object. Frames with no "
             "background are collapsed and their children move up a level"),
            ("EMPTY", "Empty per Frame",
             "Every frame becomes an Empty with its background as a child. "
             "Keeps the Figma tree exactly, at the cost of more objects"),
        ],
        default="MERGE",
    )
    shader_mode: bpy.props.FloatProperty(
        name="Shader Mode",
        description="0 = flat emission, 1 = Principled BSDF",
        default=0.0, min=0.0, max=1.0, subtype="FACTOR",
    )
    no_shadows: bpy.props.BoolProperty(
        name="Disable Shadows Everywhere",
        description="Stop every imported object from casting shadows",
        default=False,
    )
    text_no_shadow: bpy.props.BoolProperty(
        name="Disable Shadows on Text",
        description="Text casting shadows onto its own background is the usual cause of muddy UI renders",
        default=True,
    )
    pack_images: bpy.props.BoolProperty(
        name="Pack Images",
        description="Embed the PNGs in the .blend so the file survives being moved",
        default=True,
    )
    replace_existing: bpy.props.BoolProperty(
        name="Replace Previous Import",
        description=(
            "Delete an earlier import of this same bundle before building, so "
            "re-exporting a frame updates it in place instead of stacking a "
            "second copy on top. Any edits made to that earlier import go with "
            "it. Turn this off to keep both"
        ),
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True

        col = layout.column(heading="Scale")
        col.prop(self, "px_per_meter")
        sub = col.row()
        sub.enabled = not self.build_geometry  # thickness drives spacing instead
        sub.prop(self, "z_step")

        col = layout.column(heading="Geometry")
        col.prop(self, "build_geometry")
        sub = col.column()
        sub.enabled = self.build_geometry
        sub.prop(self, "geometry_depth")
        sub.prop(self, "layer_gap")

        layout.prop(self, "collection_mode")
        layout.prop(self, "group_mode")

        col = layout.column(heading="Shading")
        col.prop(self, "shader_mode")
        col.prop(self, "text_no_shadow")
        col.prop(self, "no_shadows")

        layout.prop(self, "replace_existing")
        layout.prop(self, "pack_images")

    def execute(self, context):
        try:
            collection, report = builder.import_bundle(self.filepath, self)
        except (ValueError, zipfile.BadZipFile) as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}
        except OSError as err:
            self.report({"ERROR"}, "Could not read bundle: %s" % err)
            return {"CANCELLED"}

        summary = "Imported %d objects (%d text, %d vector, %d image)" % (
            report.objects, report.texts, report.vectors, report.images)
        if report.masked:
            summary += ", %d masked shape(s)" % report.masked
        if report.textured:
            summary += ", %d texturized vector(s)" % report.textured
        if report.extruded:
            summary += ", %d extruded" % report.extruded
        if report.replaced:
            summary += ", replaced %d previous import(s)" % report.replaced
        if report.geo_kept:
            summary += ", kept %d Geo Mesh(es)" % report.geo_kept
        if report.collapsed:
            summary += ", %d empty frame(s) collapsed" % report.collapsed
        if report.bad_bounds:
            summary += (", %d bad bound(s) ignored -- re-export with a current "
                        "plugin" % report.bad_bounds)
        if report.effects_dropped:
            summary += (", effects dropped on %d shape(s)"
                        % len(report.effects_dropped))

        if report.font_notes:
            notes = "; ".join(sorted(report.font_notes))
            self.report({"WARNING"}, "%s. Font issues: %s" % (summary, notes))
        else:
            self.report({"INFO"}, summary)

        for warning in report.warnings[:10]:
            print("[FigBlend] %s" % warning)

        return {"FINISHED"}


class FIGBLEND_OT_split_material(bpy.types.Operator):
    bl_idname = "figblend.split_material"
    bl_label = "Split Off Material"
    bl_description = (
        "Give the selected objects their own copy of their material, so changing "
        "them does not affect the rest of the imported UI"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        count = 0
        for obj in context.selected_objects:
            if not obj.data or not hasattr(obj.data, "materials"):
                continue
            for slot in obj.material_slots:
                if slot.material and materials.is_ui_material(slot.material):
                    slot.material = slot.material.copy()
                    count += 1
        if not count:
            self.report({"WARNING"}, "No imported UI materials in the selection")
            return {"CANCELLED"}
        self.report({"INFO"}, "Split %d material(s)" % count)
        return {"FINISHED"}


class FIGBLEND_OT_apply_shader_mode(bpy.types.Operator):
    bl_idname = "figblend.apply_shader_mode"
    bl_label = "Apply to Selected"
    bl_description = "Set the emission/Principled blend on the selected objects' materials"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        settings = context.scene.figblend
        seen = set()
        count = 0
        for obj in context.selected_objects:
            if not obj.data or not hasattr(obj.data, "materials"):
                continue
            for slot in obj.material_slots:
                mat = slot.material
                if not mat or mat.name in seen:
                    continue
                seen.add(mat.name)
                if materials.set_mode(mat, settings.shader_mode):
                    count += 1
        if not count:
            self.report({"WARNING"}, "No imported UI materials in the selection")
            return {"CANCELLED"}
        self.report({"INFO"}, "Updated %d material(s)" % count)
        return {"FINISHED"}


class FIGBLEND_OT_set_shadow(bpy.types.Operator):
    bl_idname = "figblend.set_shadow"
    bl_label = "Set Shadow Visibility"
    bl_options = {"REGISTER", "UNDO"}

    enabled: bpy.props.BoolProperty(name="Cast Shadows", default=False)

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    @classmethod
    def description(cls, context, properties):
        if properties.enabled:
            return "Let the selected objects cast shadows again"
        return "Stop the selected objects casting shadows"

    def execute(self, context):
        for obj in context.selected_objects:
            obj.visible_shadow = self.enabled
        state = "on" if self.enabled else "off"
        self.report({"INFO"}, "Shadows %s for %d object(s)"
                    % (state, len(context.selected_objects)))
        return {"FINISHED"}


class FIGBLEND_OT_set_render_method(bpy.types.Operator):
    bl_idname = "figblend.set_render_method"
    bl_label = "Set Transparency Method"
    bl_options = {"REGISTER", "UNDO"}

    method: bpy.props.EnumProperty(
        name="Method",
        items=[
            ("DITHERED", "Dithered", ""),
            ("BLENDED", "Blended", ""),
        ],
        default="DITHERED",
    )

    @classmethod
    def description(cls, context, properties):
        if properties.method == "DITHERED":
            return ("Dithered transparency. Writes depth, so stacked UI layers "
                    "stay correctly ordered from every angle. Use this if "
                    "elements vanish when you orbit the view")
        return ("Blended transparency. Smoother alpha, but does not write depth "
                "-- stacked layers can draw in the wrong order once the view "
                "leaves dead-on")

    def execute(self, context):
        count = 0
        for mat in bpy.data.materials:
            if materials.is_ui_material(mat):
                materials.set_render_method(mat, self.method)
                count += 1
        if not count:
            self.report({"WARNING"}, "No imported UI materials in this file")
            return {"CANCELLED"}
        self.report({"INFO"}, "%s on %d material(s)" % (self.method.title(), count))
        return {"FINISHED"}


class FIGBLEND_OT_build_geometry(bpy.types.Operator):
    bl_idname = "figblend.build_geometry"
    bl_label = "Build 3D Geometry"
    bl_description = (
        "Give the selected objects real thickness. Curves and text are "
        "extruded; image planes get a Solidify modifier"
    )
    bl_options = {"REGISTER", "UNDO"}

    depth: bpy.props.FloatProperty(
        name="Depth",
        description="Total thickness, not the half-depth Blender's curve extrude uses",
        default=0.002, min=0.0, soft_max=0.05, precision=4, unit="LENGTH",
    )
    bevel: bpy.props.FloatProperty(
        name="Bevel",
        description="Rounds the extruded edge. Keep it well under half the depth",
        default=0.0, min=0.0, soft_max=0.01, precision=4, unit="LENGTH",
    )
    bevel_resolution: bpy.props.IntProperty(
        name="Bevel Resolution", default=2, min=0, max=16,
    )
    restack: bpy.props.BoolProperty(
        name="Restack Layers",
        description=(
            "Re-space the selected objects by their Figma layer order so the "
            "new thickness has somewhere to go. Without this, extruding into "
            "the 0.1 mm import spacing drives every layer through its neighbours"
        ),
        default=True,
    )
    layer_gap: bpy.props.FloatProperty(
        name="Seam Gap",
        description=(
            "Clearance between stacked layers, as a fraction of thickness. "
            "Exactly touching faces sit at the same depth and z-fight"
        ),
        default=0.02, min=0.0, max=0.5, subtype="FACTOR",
    )

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        curves = meshes = skipped = 0

        # Skip objects whose shape-root is also selected: apply_thickness already
        # reaches their extra SVG paths, and doing it twice shifts them twice.
        selected = set(context.selected_objects)
        roots = [o for o in context.selected_objects
                 if not (o.get("figblend_part") and o.parent in selected)]

        # Spread the layers out first. Imported spacing is a fraction of a
        # millimetre, so extruding into it would bury every layer in the one
        # behind. The gap keeps the touching faces off the same plane.
        if self.restack:
            builder.restack_objects(roots, self.depth,
                                    self.depth * self.layer_gap)

        for obj in roots:
            if obj.type in builder.CURVE_TYPES:
                builder.apply_thickness(obj, self.depth)
                for target in builder.thickness_targets(obj):
                    # Not text: bevel_depth grows both ways from the curve
                    # plane, which would push text back off its front face.
                    if (target.type in builder.CURVE_TYPES
                            and target.type not in builder.SOLIDIFY_TYPES):
                        target.data.bevel_depth = self.bevel
                        target.data.bevel_resolution = self.bevel_resolution
                curves += 1
            elif obj.type == "MESH":
                builder.apply_thickness(obj, self.depth)
                meshes += 1
            else:
                skipped += 1

        if not curves and not meshes:
            self.report({"WARNING"}, "Nothing extrudable selected")
            return {"CANCELLED"}

        message = "Built %d curve(s), %d mesh(es)" % (curves, meshes)
        if skipped:
            message += ", skipped %d" % skipped
        self.report({"INFO"}, message)
        return {"FINISHED"}


class FIGBLEND_OT_flatten_geometry(bpy.types.Operator):
    bl_idname = "figblend.flatten_geometry"
    bl_label = "Flatten"
    bl_description = (
        "Undo Build 3D Geometry on the selected objects. A Geo Mesh is removed "
        "and the element it replaced comes back"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        selected = set(context.selected_objects)
        count = restored = 0
        for obj in list(context.selected_objects):
            if obj.get(geo.GEO_KEY):
                geo.restore(obj)
                restored += 1
                continue
            if obj.get("figblend_part") and obj.parent in selected:
                continue
            if obj.type in builder.CURVE_TYPES or obj.type == "MESH":
                builder.clear_thickness(obj)
                count += 1
        message = "Flattened %d object(s)" % count
        if restored:
            message += ", restored %d from Geo Mesh" % restored
        self.report({"INFO"}, message)
        return {"FINISHED"}


class FIGBLEND_OT_drag_thickness(bpy.types.Operator):
    bl_idname = "figblend.drag_thickness"
    bl_label = "Thickness"
    bl_description = (
        "Drag left and right in the viewport to set the thickness of the "
        "selected elements. Shift for fine steps, Ctrl to snap to 0.1 mm, "
        "or type a value in millimetres. Click or Enter to keep it"
    )
    bl_options = {"REGISTER", "UNDO", "GRAB_CURSOR_X", "BLOCKING"}

    thickness: bpy.props.FloatProperty(
        name="Thickness",
        description="Thickness of every selected element, grown backwards from its front face",
        default=0.0005, min=0.0, soft_max=0.01, step=0.01, precision=5, unit="LENGTH",
    )

    # Screen pixels to double or halve the thickness. Doubling rather than
    # adding keeps the drag equally easy at 0.1 mm and at 10 mm.
    PIXELS_PER_DOUBLING = 150.0
    SNAP = 0.0001

    @classmethod
    def poll(cls, context):
        if context.mode != "OBJECT":
            cls.poll_message_set("Switch to Object Mode")
            return False
        if not builder.thickness_roots(context.selected_objects):
            cls.poll_message_set("Select imported shapes, text, images or Geo Meshes")
            return False
        return True

    def execute(self, context):
        roots = builder.thickness_roots(context.selected_objects)
        for obj in roots:
            builder.set_depth(obj, self.thickness)
        self.report({"INFO"}, "Thickness %s on %d element(s)"
                    % (self._text(self.thickness), len(roots)))
        return {"FINISHED"}

    def invoke(self, context, event):
        self._roots = builder.thickness_roots(context.selected_objects)
        self._before = [(obj, builder.depth_of(obj)) for obj in self._roots]
        active = context.view_layer.objects.active
        active = active.parent if active and active.get("figblend_part") and active.parent else active
        start = builder.depth_of(active) if active in self._roots else self._before[0][1]
        if start <= 0.0:
            # Nothing to scale from: begin at the Live Link thickness.
            prefs = props.preferences()
            start = prefs.live_geometry_depth if prefs else 0.0005
        self._start = start
        self._x = event.mouse_x
        self._typed = ""
        self._apply(context, start)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            for obj, depth in self._before:
                builder.set_depth(obj, depth)
            self._finish(context)
            return {"CANCELLED"}
        if event.type in {"LEFTMOUSE", "RET", "NUMPAD_ENTER", "SPACE"} and event.value == "PRESS":
            self._finish(context)
            return {"FINISHED"}

        if event.value == "PRESS" and event.ascii and event.ascii in "0123456789.,":
            self._typed += "." if event.ascii == "," else event.ascii
        elif event.type == "BACK_SPACE" and event.value == "PRESS":
            self._typed = self._typed[:-1]
        elif event.type not in {"MOUSEMOVE", "LEFT_SHIFT", "RIGHT_SHIFT",
                                "LEFT_CTRL", "RIGHT_CTRL"}:
            return {"RUNNING_MODAL"}

        if self._typed:
            try:
                value = float(self._typed) / 1000.0  # typed in millimetres
            except ValueError:
                return {"RUNNING_MODAL"}
        else:
            pixels = event.mouse_x - self._x
            if event.shift:
                pixels /= 10.0
            value = self._start * 2.0 ** (pixels / self.PIXELS_PER_DOUBLING)
            if event.ctrl:
                value = max(self.SNAP, round(value / self.SNAP) * self.SNAP)
        self._apply(context, value)
        return {"RUNNING_MODAL"}

    def _apply(self, context, value):
        self.thickness = value
        for obj in self._roots:
            builder.set_depth(obj, value)
        if context.area:
            context.area.header_text_set(
                "Thickness  %s%s   ·   %d element(s)"
                % (self._text(value), "  (typed, mm)" if self._typed else "", len(self._roots)))
        context.workspace.status_text_set(
            "Click / Enter: keep    Right-click / Esc: cancel    "
            "Shift: fine    Ctrl: snap 0.1 mm    Type: value in mm")

    def _finish(self, context):
        if context.area:
            context.area.header_text_set(None)
        context.workspace.status_text_set(None)

    @staticmethod
    def _text(value):
        return bpy.utils.units.to_string("METRIC", "LENGTH", value, precision=3)


class FIGBLEND_OT_make_geo(bpy.types.Operator):
    bl_idname = "figblend.make_geo"
    bl_label = "Geo Mesh"
    bl_description = (
        "Rebuild the selected elements as dense meshes from a geometry node "
        "group, so Wave, Simple Deform and other deformers can animate them. "
        "A button's own text and icon move onto it. Flat undoes it"
    )
    bl_options = {"REGISTER", "UNDO"}

    height: bpy.props.FloatProperty(
        name="Height",
        description="Thickness of the rebuilt mesh. 0 uses the Build 3D depth, or a size-based default",
        default=0.0, min=0.0, soft_max=0.1, precision=4, unit="LENGTH",
    )
    density: bpy.props.IntProperty(
        name="Density",
        description=(
            "Segments across the shorter side. Raise it for smoother waves and "
            "bends, lower it for lighter scenes"
        ),
        default=32, min=4, soft_max=128,
    )
    carry_content: bpy.props.BoolProperty(
        name="Carry Text and Icon",
        description=(
            "When the element is button-shaped (one text, at most one icon), "
            "rebuild them onto the mesh instead of leaving them as separate objects"
        ),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        prefs = props.preferences()
        name = prefs.geo_node_group if prefs else geo.DEFAULT_GROUP
        library = prefs.geo_library if prefs else ""
        try:
            group = geo.node_group(name, library)
        except ValueError as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}

        selected = set(context.selected_objects)
        made, skipped = [], []
        for obj in list(context.selected_objects):
            # SVG parts belong to their root; texts and groups have no shape
            # of their own to rebuild; a Geo Mesh is already one.
            if (obj.get("figblend_part") or obj.get(geo.GEO_KEY)
                    or obj.type not in {"CURVE", "MESH"}):
                if not (obj.get("figblend_part") and obj.parent in selected):
                    skipped.append(obj.name)
                continue
            try:
                made.append(geo.make(obj, group, self.height, self.carry_content, self.density))
            except (ValueError, RuntimeError) as err:
                skipped.append(obj.name)
                self.report({"WARNING"}, str(err))

        if not made:
            self.report({"WARNING"}, "Select a shape or image plane to rebuild")
            return {"CANCELLED"}

        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in made:
            obj.select_set(True)
        context.view_layer.objects.active = made[0]

        for note in geo.take_notes():
            self.report({"INFO"}, note)
        message = "Rebuilt %d element(s) with '%s'" % (len(made), group.name)
        if skipped:
            message += ", skipped %d" % len(skipped)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class FIGBLEND_OT_merge_geo(bpy.types.Operator):
    bl_idname = "figblend.merge_geo"
    bl_label = "Merge"
    bl_description = (
        "Move the selected text and/or icon onto the active Geo Mesh -- text "
        "into its text section, a shape or image into its icon section -- at "
        "the same size and position, keeping their thickness. Select them, then "
        "the Geo Mesh last so it is active. Flat gives them back"
    )
    bl_options = {"REGISTER", "UNDO"}

    @staticmethod
    def _split(context, target):
        """(texts, icons) among the selection, apart from the Geo Mesh itself."""
        texts, icons = [], []
        for obj in context.selected_objects:
            if obj is target or obj.get(geo.GEO_KEY):
                continue
            if obj.type == "FONT":
                texts.append(obj)
            elif obj.type in {"CURVE", "MESH"}:
                # A path of a multi-path shape stands for the whole shape.
                root = obj.parent if obj.get("figblend_part") and obj.parent else obj
                if root not in icons:
                    icons.append(root)
        return texts, icons

    @classmethod
    def poll(cls, context):
        active = context.view_layer.objects.active
        if active is None or geo.find_modifier(active) is None:
            cls.poll_message_set("Make the active object a Geo Mesh first")
            return False
        texts, icons = cls._split(context, active)
        if not (texts or icons):
            cls.poll_message_set("Also select the text or icon to merge")
            return False
        return True

    def execute(self, context):
        target = context.view_layer.objects.active
        texts, icons = self._split(context, target)
        if len(texts) > 1 or len(icons) > 1:
            self.report({"ERROR"},
                        "Select at most one text and one icon -- a Geo Mesh shows one of each")
            return {"CANCELLED"}
        text = texts[0] if texts else None
        icon = icons[0] if icons else None
        try:
            geo.merge(target, text=text, icon=icon)
        except (ValueError, RuntimeError) as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}
        for obj in context.selected_objects:
            obj.select_set(obj is target)
        merged = " and ".join("'%s'" % o.name for o in (text, icon) if o is not None)
        self.report({"INFO"}, "Merged %s into '%s'" % (merged, target.name))
        for note in geo.take_notes():
            self.report({"INFO"}, note)
        return {"FINISHED"}


class FIGBLEND_OT_select_relight(bpy.types.Operator):
    bl_idname = "figblend.select_relight"
    bl_label = "Select Shapes Needing Light"
    bl_description = (
        "Select every imported shape whose Figma shadow or blur was discarded, "
        "so you can light them properly"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        found = []
        for obj in context.view_layer.objects:
            # figma_* is the pre-FigBlend property name, still present in any
            # .blend imported before the rename.
            if obj.get("figblend_effects_dropped") or obj.get("figma_effects_dropped"):
                obj.select_set(True)
                found.append(obj)
            else:
                obj.select_set(False)
        if not found:
            self.report({"INFO"}, "No imported shapes had effects dropped")
            return {"CANCELLED"}
        context.view_layer.objects.active = found[0]
        self.report({"INFO"}, "Selected %d shape(s)" % len(found))
        return {"FINISHED"}


class FIGBLEND_OT_toggle_bridge(bpy.types.Operator):
    bl_idname = "figblend.toggle_bridge"
    bl_label = "Live Link"
    bl_description = (
        "Listen on 127.0.0.1 so the Figma plugin can send an export straight "
        "into this file, with assets written to ./figAssets beside the .blend"
    )

    def execute(self, context):
        if bridge.is_running():
            ok, message = bridge.stop()
        else:
            if not bpy.data.filepath:
                self.report({"ERROR"},
                            "Save the .blend first -- assets go to ./figAssets "
                            "beside it")
                return {"CANCELLED"}
            ok, message = bridge.start()
        self.report({"INFO"} if ok else {"ERROR"}, message)
        return {"FINISHED"} if ok else {"CANCELLED"}


class FIGBLEND_OT_repair_link(bpy.types.Operator):
    bl_idname = "figblend.repair_link"
    bl_label = "Re-pair"
    bl_description = (
        "Issue a new Live Link token and accept one pairing request for the "
        "next few minutes. Use this if the Figma plugin reports that it is not "
        "paired with this Blender"
    )

    @classmethod
    def poll(cls, context):
        return bridge.is_running()

    def execute(self, context):
        bridge.open_pairing()
        self.report({"INFO"},
                    "Pairing open for %d s -- press Export in Figma now"
                    % int(bridge.PAIR_WINDOW))
        return {"FINISHED"}


class FIGBLEND_OT_check_update(bpy.types.Operator):
    bl_idname = "figblend.check_update"
    bl_label = "Check for Updates"
    bl_description = "Ask GitHub whether a newer FigBlend has been released"

    def execute(self, context):
        updater.check(force=True)
        info = updater.state()
        if info["status"] == "disabled":
            self.report({"WARNING"}, info["message"])
            return {"CANCELLED"}
        return {"FINISHED"}


class FIGBLEND_OT_get_update(bpy.types.Operator):
    bl_idname = "figblend.get_update"
    bl_label = "Get Update"
    bl_description = (
        "Open Blender's Extensions page to install the update. A copy that was "
        "installed from a downloaded zip cannot update in place, so this opens "
        "the release page instead"
    )

    def execute(self, context):
        if updater.install_source() == "repository":
            # Blender's own page, filtered to installed extensions, where the
            # FigBlend entry carries an Update button. Present since 4.2.
            if hasattr(bpy.ops.extensions, "userpref_show_for_update"):
                bpy.ops.extensions.userpref_show_for_update()
            else:
                context.preferences.active_section = "EXTENSIONS"
                bpy.ops.screen.userpref_show("INVOKE_DEFAULT")
            return {"FINISHED"}
        bpy.ops.wm.url_open(url=updater.release_url())
        return {"FINISHED"}


class FIGBLEND_OT_rebuild_fonts(bpy.types.Operator):
    bl_idname = "figblend.rebuild_font_index"
    bl_label = "Rescan Installed Fonts"
    bl_description = (
        "Rebuild the font index. Run this after installing a font that the "
        "importer reported as missing"
    )

    def execute(self, context):
        fonts.clear_cache()
        index = fonts.rebuild_index()
        self.report({"INFO"}, "Indexed %d font families" % len(index))
        return {"FINISHED"}


classes = (
    FIGBLEND_OT_import,
    FIGBLEND_OT_split_material,
    FIGBLEND_OT_apply_shader_mode,
    FIGBLEND_OT_set_shadow,
    FIGBLEND_OT_set_render_method,
    FIGBLEND_OT_build_geometry,
    FIGBLEND_OT_flatten_geometry,
    FIGBLEND_OT_drag_thickness,
    FIGBLEND_OT_make_geo,
    FIGBLEND_OT_merge_geo,
    FIGBLEND_OT_select_relight,
    FIGBLEND_OT_toggle_bridge,
    FIGBLEND_OT_repair_link,
    FIGBLEND_OT_check_update,
    FIGBLEND_OT_get_update,
    FIGBLEND_OT_rebuild_fonts,
)
