"""Resolve Figma font names to real font files on disk.

Figma gives us {"family": "Inter", "style": "Semi Bold"}. Blender needs a path
to a .ttf. Nothing bridges the two, so we index the system's fonts by reading
their internal name tables and match against that.

Filename guessing is deliberately avoided as the primary strategy -- "Inter-SB.ttf"
tells you far less than the font's own name records do.
"""

import collections
import json
import os
import struct
import sys

import bpy

_CACHE_NAME = "figblend_font_index.json"
_CACHE_VERSION = 2
_index = None  # {normalized_family: {normalized_style: {"path": str, "variable": bool}}}

# `exact` is False when we had to substitute a different weight, and `variable`
# is True when the file is a variable font -- Blender loads only its default
# instance, so a Semi Bold request silently renders as Regular.
FontMatch = collections.namedtuple("FontMatch", "path style variable exact")


def _font_dirs():
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
        win = os.environ.get("WINDIR", "C:\\Windows")
        return [
            os.path.join(win, "Fonts"),
            os.path.join(local, "Microsoft", "Windows", "Fonts"),
        ]
    if sys.platform == "darwin":
        return [
            "/System/Library/Fonts",
            "/Library/Fonts",
            os.path.join(home, "Library", "Fonts"),
        ]
    return [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.join(home, ".fonts"),
        os.path.join(home, ".local", "share", "fonts"),
    ]


def normalize(value):
    """'Semi Bold' -> 'semibold', so Figma and font vendors agree."""
    return "".join(ch for ch in value.lower() if ch.isalnum())


# --- sfnt name table parsing -------------------------------------------------

_NAME_FAMILY = 1
_NAME_SUBFAMILY = 2
_NAME_TYPO_FAMILY = 16
_NAME_TYPO_SUBFAMILY = 17


def _read_font_info(path):
    """Return (family, subfamily, is_variable) from a font's tables, or None."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    if len(data) < 12:
        return None

    offset = 0
    if data[:4] == b"ttcf":
        # Font collection: index the first face only.
        if len(data) < 16:
            return None
        offset = struct.unpack(">I", data[12:16])[0]
        if offset + 12 > len(data):
            return None

    try:
        num_tables = struct.unpack(">H", data[offset + 4:offset + 6])[0]
    except struct.error:
        return None

    name_offset = None
    is_variable = False
    for i in range(num_tables):
        entry = offset + 12 + i * 16
        if entry + 16 > len(data):
            return None
        tag = data[entry:entry + 4]
        if tag == b"name":
            name_offset = struct.unpack(">I", data[entry + 8:entry + 12])[0]
        elif tag == b"fvar":
            # An fvar table means this is a variable font.
            is_variable = True
    if name_offset is None or name_offset + 6 > len(data):
        return None

    count, string_offset = struct.unpack(">HH", data[name_offset + 2:name_offset + 6])
    strings_base = name_offset + string_offset

    found = {}
    for i in range(count):
        rec = name_offset + 6 + i * 12
        if rec + 12 > len(data):
            break
        platform_id, encoding_id, _lang, name_id, length, str_off = struct.unpack(
            ">HHHHHH", data[rec:rec + 12])
        if name_id not in (_NAME_FAMILY, _NAME_SUBFAMILY,
                           _NAME_TYPO_FAMILY, _NAME_TYPO_SUBFAMILY):
            continue
        start = strings_base + str_off
        raw = data[start:start + length]
        if not raw:
            continue
        try:
            if platform_id == 3 or (platform_id == 0):
                text = raw.decode("utf-16-be", errors="ignore")
            elif platform_id == 1 and encoding_id == 0:
                text = raw.decode("latin-1", errors="ignore")
            else:
                continue
        except (UnicodeDecodeError, LookupError):
            continue
        text = text.strip("\x00").strip()
        if text and name_id not in found:
            found[name_id] = text

    # Typographic names (16/17) are more accurate for families with many weights,
    # where the legacy names collapse everything into Regular/Bold pairs.
    family = found.get(_NAME_TYPO_FAMILY) or found.get(_NAME_FAMILY)
    subfamily = found.get(_NAME_TYPO_SUBFAMILY) or found.get(_NAME_SUBFAMILY) or "Regular"
    if not family:
        return None
    return family, subfamily, is_variable


# --- metrics -----------------------------------------------------------------
# Figma's font size is the em. Blender's is not: it scales a font so that the
# font's whole bounding box -- tallest accent to deepest descender, from the
# head table -- equals the size. For a typical font that box is 1.2 to 1.4 em,
# so text set to Figma's number renders 25-30% small, by a different amount
# for every font. These numbers let the importer convert exactly.

FontMetrics = collections.namedtuple(
    "FontMetrics",
    # size_factor: Blender text size per em.
    # ascent, descent, line_gap: in ems, descent negative (the hhea values,
    # which is what both FreeType and Figma's Auto line height use).
    "size_factor ascent descent line_gap")

_metrics = {}


def _metrics_from_bytes(data):
    offset = 0
    if data[:4] == b"ttcf":
        offset = struct.unpack(">I", data[12:16])[0]
    try:
        num_tables = struct.unpack(">H", data[offset + 4:offset + 6])[0]
        tables = {}
        for i in range(num_tables):
            entry = offset + 12 + i * 16
            tables[data[entry:entry + 4]] = struct.unpack(">I", data[entry + 8:entry + 12])[0]
        head, hhea = tables.get(b"head"), tables.get(b"hhea")
        if head is None or hhea is None:
            return None
        units_per_em = struct.unpack(">H", data[head + 18:head + 20])[0]
        _x_min, y_min, _x_max, y_max = struct.unpack(">hhhh", data[head + 36:head + 44])
        ascent, descent, line_gap = struct.unpack(">hhh", data[hhea + 4:hhea + 10])
    except struct.error:
        return None
    if not units_per_em:
        return None
    # Blender falls back to 1/1000 per unit when the box is degenerate.
    span = (y_max - y_min) if y_max != y_min else 1000
    return FontMetrics(span / units_per_em, ascent / units_per_em,
                       descent / units_per_em, line_gap / units_per_em)


def metrics(font):
    """FontMetrics for a Blender font datablock, or None if unreadable.

    Read from the packed copy when there is one, else from the file. Blender's
    built-in font has neither, so it gets None and is sized as given.
    """
    if font is None:
        return None
    packed = font.packed_file
    key = ("packed", font.name) if packed else bpy.path.abspath(font.filepath)
    if key in _metrics:
        return _metrics[key]
    data = None
    if packed:
        data = bytes(packed.data)
    elif font.filepath and font.filepath != "<builtin>":
        try:
            with open(key, "rb") as handle:
                data = handle.read()
        except OSError:
            data = None
    result = _metrics_from_bytes(data) if data else None
    _metrics[key] = result
    return result


# --- index -------------------------------------------------------------------

def _cache_path():
    config = bpy.utils.user_resource("CONFIG", create=True)
    return os.path.join(config, _CACHE_NAME)


def build_index(force=False):
    """Scan font directories. Cached to disk -- scanning is slow on Windows."""
    global _index
    if _index is not None and not force:
        return _index

    cache = _cache_path()
    if not force and os.path.exists(cache):
        try:
            with open(cache, "r", encoding="utf-8") as handle:
                cached = json.load(handle)
            if cached.get("version") == _CACHE_VERSION:
                _index = cached["families"]
                return _index
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Corrupt or outdated cache, rebuild.

    index = {}
    for directory in _font_dirs():
        if not os.path.isdir(directory):
            continue
        for root, _dirs, files in os.walk(directory):
            for filename in files:
                if not filename.lower().endswith((".ttf", ".otf", ".ttc", ".otc")):
                    continue
                path = os.path.join(root, filename)
                info = _read_font_info(path)
                if not info:
                    continue
                family, subfamily, is_variable = info
                index.setdefault(normalize(family), {}).setdefault(
                    normalize(subfamily), {"path": path, "variable": is_variable})

    _index = index
    try:
        with open(cache, "w", encoding="utf-8") as handle:
            json.dump({"version": _CACHE_VERSION, "families": index}, handle)
    except OSError:
        pass
    return _index


def rebuild_index():
    return build_index(force=True)


# Figma style names that vendors spell differently.
_STYLE_ALIASES = {
    "regular": ["regular", "normal", "book", "roman", ""],
    "italic": ["italic", "oblique", "regularitalic"],
    "semibold": ["semibold", "demibold", "demi", "600"],
    "extrabold": ["extrabold", "ultrabold", "800"],
    "extralight": ["extralight", "ultralight", "200"],
    "medium": ["medium", "500"],
    "light": ["light", "300"],
    "thin": ["thin", "hairline", "100"],
    "black": ["black", "heavy", "900"],
    "bold": ["bold", "700"],
}


def resolve(family, style):
    """Return a FontMatch for the closest available face, or None.

    Falls back through: exact style -> known aliases -> regular -> any weight in
    the family. `exact` records whether we actually got the requested weight,
    because a silent substitution renders text at the wrong weight and the user
    needs to be told.
    """
    index = build_index()
    styles = index.get(normalize(family))
    if not styles:
        return None

    wanted = normalize(style)

    def match(key, exact):
        entry = styles[key]
        return FontMatch(entry["path"], key, entry["variable"], exact)

    if wanted in styles:
        return match(wanted, True)

    for alias in _STYLE_ALIASES.get(wanted, []):
        if alias in styles:
            return match(alias, True)

    # Reverse lookup: the font may use a name that aliases *to* what we want.
    for canonical, aliases in _STYLE_ALIASES.items():
        if wanted in aliases and canonical in styles:
            return match(canonical, True)

    # Nearest weight, keeping italic-ness. Falling back to Regular instead
    # turns an ExtraBold heading into body text; the next weight down keeps it
    # reading as a heading.
    want_weight, want_italic = _weight_of(wanted)
    best = min(styles, key=lambda key: (
        _weight_of(key)[1] != want_italic,
        abs(_weight_of(key)[0] - want_weight),
        # On a tie, heavier for bold requests and lighter for light ones, as
        # CSS font matching does.
        -_weight_of(key)[0] if want_weight > 500 else _weight_of(key)[0],
    ), default=None)
    return match(best, False) if best else None


# Weight names to CSS numbers, longest first so "extrabold" wins over "bold".
_WEIGHTS = (
    ("hairline", 100), ("thin", 100),
    ("extralight", 200), ("ultralight", 200),
    ("semibold", 600), ("demibold", 600), ("demi", 600),
    ("extrabold", 800), ("ultrabold", 800),
    ("light", 300), ("medium", 500), ("bold", 700),
    ("black", 900), ("heavy", 900),
)


def _weight_of(style_key):
    """'extrabolditalic' -> (800, True); anything unrecognised is Regular."""
    italic = "italic" in style_key or "oblique" in style_key
    for name, weight in _WEIGHTS:
        if name in style_key:
            return weight, italic
    digits = "".join(ch for ch in style_key if ch.isdigit())
    if len(digits) == 3:
        return int(digits), italic
    return 400, italic


_loaded = {}

# Tried in order when a design's font is not installed at all.
_FALLBACK_FAMILIES = ("Arial", "Helvetica", "Helvetica Neue", "Segoe UI",
                      "DejaVu Sans", "Liberation Sans", "Roboto")


def _alive(datablock):
    """False once Blender has freed the ID this Python object pointed at.

    The cache is module state, so it outlives the file it was filled from.
    Opening another .blend frees every font datablock in it while the cache
    keeps handing them out, and the next text import dies on
    "StructRNA of type VectorFont has been removed".
    """
    try:
        datablock.name
        return True
    except ReferenceError:
        return False


def load(family, style, report=None, on_substitute=None):
    """Load (and cache) a Blender font datablock.

    Returns (font_datablock_or_None, note_or_None). A note means the text will
    not look exactly like Figma, and the caller should surface it.
    """
    key = (normalize(family), normalize(style))
    if key in _loaded:
        cached_font = _loaded[key][0]
        if cached_font is None or _alive(cached_font):
            return _loaded[key]
        del _loaded[key]  # freed with the previous file; load it again below

    wanted = "%s %s" % (family, style)
    match = resolve(family, style)
    font = None
    note = None
    stand_in = None

    if match is None:
        # A common system sans at the right weight is a closer stand-in than
        # Blender's built-in font -- and, unlike it, has metrics that let the
        # text be sized to match the design.
        for fallback in _FALLBACK_FAMILIES:
            match = resolve(fallback, style)
            if match is not None:
                stand_in = fallback
                break

    if match is None:
        note = "%s (not installed)" % wanted
        if report:
            report("Font not installed: '%s' -- using Blender's default." % wanted)
    elif stand_in is not None:
        try:
            font = bpy.data.fonts.load(match.path, check_existing=True)
        except RuntimeError:
            font = None
        note = "%s (not installed, using %s)" % (wanted, stand_in)
        if report:
            report("Font not installed: '%s' -- using %s %s instead."
                   % (wanted, stand_in, match.style))
    else:
        try:
            font = bpy.data.fonts.load(match.path, check_existing=True)
        except RuntimeError as err:
            note = "%s (failed to load)" % wanted
            if report:
                report("Could not load '%s' from %s: %s" % (wanted, match.path, err))

        if font is not None:
            if not match.exact:
                note = "%s (substituted '%s')" % (wanted, match.style)
                if report:
                    report("No '%s' face for %s -- using '%s' instead."
                           % (style, family, match.style))
            elif match.variable and normalize(style) not in ("regular", "normal"):
                # Blender loads a variable font's default instance only, so a
                # weight request cannot be honoured. Inter ships this way, which
                # makes it the common case rather than an edge case.
                note = "%s (variable font: renders at default weight)" % wanted
                if report:
                    report("'%s' is a variable font. Blender uses its default "
                           "instance, so '%s' will render at the default weight. "
                           "Install the static '%s %s' file to fix this."
                           % (family, style, family, style))

    if note and on_substitute:
        on_substitute(note)

    _loaded[key] = (font, note)
    return font, note


def clear_cache():
    _loaded.clear()
    _metrics.clear()
    global _index
    _index = None
