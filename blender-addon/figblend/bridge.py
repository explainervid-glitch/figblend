"""A loopback HTTP bridge so the Figma plugin can push an export straight in.

Without this, every iteration is: export, find the download, switch to Blender,
File > Import, pick the file. With it, the plugin sees Blender is running and
sends the bundle directly.

The server binds to 127.0.0.1 only and is off until the user starts it. HTTP
runs on a worker thread; every Blender API call is deferred to the main thread
through a timer, because bpy is not thread-safe.

Loopback is not the same as private. Any page in any browser on this machine can
reach a localhost port, and a Figma plugin's requests carry `Origin: null` --
indistinguishable from a sandboxed iframe on a hostile site, so there is nothing
in an origin check worth having. Instead /import needs a token, handed out by
/pair only while a pairing window is open: the few minutes after the user starts
Live Link, closed again the moment a client takes it. See `_pair_open`.
"""

import errno
import hmac
import json
import os
import secrets
import socket
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bpy
from bpy.app.handlers import persistent

from . import builder, props

PORT = 8787
# Several Blenders can run at once, so each takes the next free port. The plugin
# manifest has to list every one of these explicitly: Figma's allowedDomains
# wildcard covers subdomains only, not ports.
PORT_SPAN = 8

ASSET_DIR_NAME = "figAssets"

# How long /pair keeps giving out the token after Live Link starts. Long enough
# to click Export in Figma, short enough that a page opened later cannot pair.
PAIR_WINDOW = 300.0

# A bundle is a few MB. The cap is only here so a bad or hostile request cannot
# make Blender read an unbounded body into memory.
MAX_BUNDLE_BYTES = 256 * 1024 * 1024

_servers = []
_port = None
_queue = []
_lock = threading.Lock()
_last_result = {"state": "idle", "message": ""}

_token = None
_pair_deadline = 0.0
_paired = False


# --- paths -------------------------------------------------------------------

def asset_dir():
    """`./figAssets` beside the .blend, or None when the file is unsaved.

    Relative-to-the-blend is the whole point, so an unsaved file is refused
    rather than quietly dumped somewhere in temp.
    """
    if not bpy.data.filepath:
        return None
    return os.path.join(os.path.dirname(bpy.data.filepath), ASSET_DIR_NAME)


def is_running():
    return bool(_servers)


# --- pairing -----------------------------------------------------------------

def _pair_open():
    """True while /pair will still hand out the token."""
    return bool(_token) and not _paired and time.monotonic() < _pair_deadline


def open_pairing(seconds=PAIR_WINDOW):
    """Issue a fresh token and start accepting one pairing request.

    Called when Live Link starts, and again by the Re-pair button -- which is
    the way out if the plugin ever loses its token, or pairs on a machine where
    something else got there first.
    """
    global _token, _pair_deadline, _paired
    _token = secrets.token_urlsafe(24)
    _pair_deadline = time.monotonic() + seconds
    _paired = False
    return _token


def pairing_state():
    """What the sidebar shows: 'open', 'paired' or 'closed'."""
    if _paired:
        return "paired"
    if _pair_open():
        return "open"
    return "closed"


def pairing_seconds_left():
    if not _pair_open():
        return 0
    return max(0, int(_pair_deadline - time.monotonic()))


def _authenticated(handler):
    supplied = handler.headers.get("X-FigBlend-Token") or ""
    if not _token or not supplied:
        return False
    return hmac.compare_digest(supplied, _token)


# --- HTTP --------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # The plugin UI is a null-origin iframe, so it needs permissive CORS.
        # Permissive by necessity: the plugin UI is a null-origin iframe, so
        # there is no origin to allow-list. The token is what actually gates
        # anything that touches the file system or the scene.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-Bundle-Name, X-FigBlend-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        if self.path.rstrip("/") not in ("/ping", ""):
            self._send(404, {"error": "not found"})
            return

        # Enough for an unpaired client to say "Blender is here, pair with it",
        # and no more. Which file is open, where it lives on disk and what was
        # imported last are all none of an unauthenticated caller's business.
        info = {
            "app": "blender",
            "blender": ".".join(str(v) for v in bpy.app.version),
            "addon": "figblend",
            "blend_saved": bool(bpy.data.filepath),
            # Port and pid tell two instances apart even when the .blend files
            # share a name, which they often do across shot folders.
            "port": _port,
            "pid": os.getpid(),
            "paired": _authenticated(self),
            "pairing_open": _pair_open(),
        }
        if info["paired"]:
            info.update({
                "blend_name": os.path.basename(bpy.data.filepath) or None,
                "blend_path": bpy.data.filepath or None,
                "asset_dir": asset_dir(),
                "last": _last_result,
            })
        self._send(200, info)

    def do_POST(self):
        route = self.path.rstrip("/")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0

        if route == "/pair":
            self._drain_body(length)
            self._pair()
            return
        if route != "/import":
            self._drain_body(length)
            self._send(404, {"error": "not found"})
            return

        if length > MAX_BUNDLE_BYTES:
            # Not drained: reading it is exactly what we are refusing to do, so
            # the connection goes instead.
            self.close_connection = True
            self._send(413, {"error": "bundle larger than %d MB"
                                      % (MAX_BUNDLE_BYTES // (1024 * 1024))})
            return

        # The body is read before any rejection. On a keep-alive connection an
        # unread body is parsed as the next request, which answers one bad POST
        # with a 400 and then a 501 and leaves the client with neither.
        data = self.rfile.read(length) if length > 0 else b""

        if not _authenticated(self):
            self._send(401, {
                "error": "Not paired with this Blender.",
                "pairing_open": _pair_open(),
            })
            return

        directory = asset_dir()
        if directory is None:
            self._send(409, {
                "error": "Save the .blend file first -- assets are written to "
                         "./%s beside it." % ASSET_DIR_NAME,
            })
            return

        if not data:
            self._send(400, {"error": "empty body"})
            return

        name = self.headers.get("X-Bundle-Name") or "figma-export.zip"
        name = os.path.basename(name) or "figma-export.zip"
        if not name.lower().endswith(".zip"):
            name += ".zip"

        try:
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, name)
            with open(path, "wb") as handle:
                handle.write(data)
        except OSError as err:
            self._send(500, {"error": "could not write bundle: %s" % err})
            return

        with _lock:
            _queue.append(path)

        # The import itself happens on the main thread; report acceptance now.
        self._send(202, {"queued": name, "bytes": len(data), "asset_dir": directory})

    def _drain_body(self, length):
        """Swallow a request body we are not going to use.

        Anything left in the socket becomes the next request on a keep-alive
        connection, and the parse failure surfaces as a 400 on a request the
        client never made.
        """
        remaining = min(max(length, 0), MAX_BUNDLE_BYTES)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            remaining -= len(chunk)

    def _pair(self):
        """Hand over the token, once, while the window is open."""
        global _paired
        if _authenticated(self):
            self._send(200, {"token": _token, "already_paired": True})
            return
        if not _pair_open():
            self._send(403, {
                "error": "Pairing is closed. Click Re-pair in Blender's "
                         "FigBlend panel, then try again.",
            })
            return
        # Closed on the way out: one pairing per window, so a second caller
        # cannot quietly take a token for the same session.
        _paired = True
        print("[FigBlend] Paired with a client on port %s" % _port)
        self._send(200, {"token": _token, "already_paired": False})

    def log_message(self, *args):
        pass  # Blender's console is noisy enough


# --- main-thread work --------------------------------------------------------

class _BridgeOptions:
    """Stands in for the import operator when the bridge drives the importer.

    A Live Link send never opens the import dialog, so anything the user would
    have set there has to come from addon preferences instead.
    """
    px_per_meter = 0
    z_step = 0.0001
    shader_mode = 0.0
    collection_mode = "GROUPS"
    geometry_depth = 0.0005
    build_geometry = False
    # A Live Link send is an iteration on the same frame, so it updates that
    # frame's collection rather than adding another copy beside it.
    replace_existing = True

    def __init__(self):
        prefs = props.preferences()
        if prefs is not None:
            self.build_geometry = prefs.live_build_geometry
            self.geometry_depth = prefs.live_geometry_depth
    group_mode = "MERGE"
    no_shadows = False
    text_no_shadow = True
    # Left unpacked on purpose: the assets living in ./figAssets beside the
    # .blend is the reason this bridge exists.
    pack_images = False


def _relink_images(asset_root):
    """Rewrite imported image paths as blend-relative so the file stays portable."""
    if not bpy.data.filepath:
        return
    root = os.path.normpath(asset_root)
    for image in bpy.data.images:
        if not image.filepath or image.packed_file:
            continue
        absolute = os.path.normpath(bpy.path.abspath(image.filepath))
        if absolute.startswith(root):
            try:
                image.filepath = bpy.path.relpath(absolute)
            except (ValueError, RuntimeError):
                pass  # different drive on Windows; absolute path still works


def _drain():
    """Timer callback -- runs on the main thread, where bpy is safe."""
    global _last_result
    with _lock:
        pending = list(_queue)
        _queue.clear()

    for path in pending:
        try:
            collection, report = builder.import_bundle(path, _BridgeOptions())
            _relink_images(os.path.dirname(path))
            summary = "%s %d objects from %s" % (
                "Updated" if report.replaced else "Imported",
                report.objects, os.path.basename(path))
            if report.font_notes:
                summary += " (%d font issue(s))" % len(report.font_notes)
            _last_result = {"state": "ok", "message": summary,
                            "collection": collection.name}
            print("[FigBlend] %s" % summary)
            for warning in report.warnings[:10]:
                print("[FigBlend] %s" % warning)
        except Exception as err:  # a bad bundle must not kill the timer
            _last_result = {"state": "error", "message": str(err)}
            print("[FigBlend] Import failed: %s" % err)
            traceback.print_exc()

    return 0.25 if _servers else None


# --- lifecycle ---------------------------------------------------------------

class _IPv4Server(ThreadingHTTPServer):
    # SO_REUSEADDR is on by default, and on Windows that lets a second socket
    # bind a port another one already holds -- connections then land in
    # whichever server the OS picks. That silently breaks the port-per-instance
    # scheme and sends bundles to the wrong Blender, so refuse to share.
    allow_reuse_address = False


class _IPv6Server(_IPv4Server):
    address_family = socket.AF_INET6


def _bind_pair(port):
    """Try to take `port` on both loopback addresses.

    Figma's manifest rejects IP literals in allowedDomains, so the plugin has to
    call `localhost` by name -- and on Windows that resolves to ::1 before
    127.0.0.1. Binding only IPv4 leaves every request waiting on a refused IPv6
    connection first, so we want both.

    Returns (servers, errors). Servers are not yet serving.
    """
    made = []
    errors = []
    for family, host in ((_IPv4Server, "127.0.0.1"), (_IPv6Server, "::1")):
        try:
            made.append(family((host, port), _Handler))
        except OSError as err:
            errors.append((host, err))

    if len(made) == 2:
        return made, []

    # A partial bind means either another Blender already holds this port on the
    # other family -- in which case take a different port, so the two instances
    # stay cleanly separated -- or this machine has no stack for that family at
    # all, which is fine to run single-family.
    taken = any(err.errno == errno.EADDRINUSE for _, err in errors)
    if made and not taken:
        return made, [("%s unavailable: %s" % (h, e)) for h, e in errors]

    for server in made:
        server.server_close()
    return [], ["%s: %s" % (h, e) for h, e in errors]


def start(port=None):
    """Claim the first free port in the range, so several Blenders coexist.

    The plugin scans the same range and lists whatever answers, which is how you
    pick between instances.
    """
    global _servers, _port
    if _servers:
        return True, "Already running on port %d" % _port

    candidates = [port] if port else range(PORT, PORT + PORT_SPAN)
    last_errors = []
    for candidate in candidates:
        made, errors = _bind_pair(candidate)
        if not made:
            last_errors = errors
            continue

        for server in made:
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, daemon=True).start()
        _servers = made
        _port = candidate
        open_pairing()
        if not bpy.app.timers.is_registered(_drain):
            bpy.app.timers.register(_drain, persistent=True)

        families = "IPv4 + IPv6" if len(made) == 2 else errors[0]
        return True, "Listening on localhost:%d (%s)" % (candidate, families)

    return False, "No free port in %d-%d (%s)" % (
        PORT, PORT + PORT_SPAN - 1, "; ".join(last_errors))


def auto_start_enabled():
    prefs = props.preferences()
    return bool(prefs and prefs.auto_start)


def _auto_start(reason):
    """Start if the preference is on and this file can host ./figAssets."""
    if not auto_start_enabled() or is_running() or not bpy.data.filepath:
        return None
    ok, message = start()
    print("[FigBlend] auto-start (%s): %s" % (reason, message))
    return None  # one-shot when used as a timer


@persistent
def _on_load_post(_dummy):
    # Handlers are wiped on file load unless marked persistent, so this has to
    # carry @persistent or it only ever fires once.
    _auto_start("file opened")


def register_handlers():
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    # Enabling the addon with a saved file already open should start it too.
    # Deferred, because preferences are not reliably readable during register().
    bpy.app.timers.register(lambda: _auto_start("addon enabled"),
                            first_interval=0.2)


def unregister_handlers():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)


def stop():
    global _servers, _port, _token, _paired, _pair_deadline
    if not _servers:
        return False, "Not running"
    for server in _servers:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
    _servers = []
    _port = None
    # The token dies with the server, so a restart always needs a fresh pairing.
    _token = None
    _paired = False
    _pair_deadline = 0.0
    if bpy.app.timers.is_registered(_drain):
        try:
            bpy.app.timers.unregister(_drain)
        except ValueError:
            pass
    return True, "Stopped"
