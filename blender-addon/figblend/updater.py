"""Tell the user when a newer FigBlend is on GitHub.

Blender does the actual updating. Each GitHub release publishes an extension
repository index, and a FigBlend installed from that repository is upgraded from
Blender's own Extensions page -- checksum-verified and reloaded in place, with
nothing of ours overwriting its own files.

What Blender does not do is make the update obvious: its notice is a small
status-bar icon. So this checks the latest release once a session, the FigBlend
panel says when there is one, and its button goes to the right place -- the
Extensions page when this copy came from the FigBlend repository, the release
page when it was installed from a downloaded zip and so cannot update itself.
"""

import json
import re
import sys
import threading
import urllib.error
import urllib.request

import bpy

# Stamped by `npm run bump` from "repository" in the root package.json.
GITHUB_REPO = "explainervid-glitch/figblend"

_PLACEHOLDER = "OWNER/REPO"
API_URL = "https://api.github.com/repos/{repo}/releases/latest"
TIMEOUT = 10.0

_lock = threading.Lock()
# status: idle | checking | current | available | error | disabled
_state = {"status": "idle", "latest": None, "message": ""}


# --- facts -------------------------------------------------------------------

def configured():
    """False until the release repository has been set -- no point asking
    GitHub about a placeholder."""
    return bool(GITHUB_REPO) and GITHUB_REPO != _PLACEHOLDER


def installed_version():
    """The running version as a tuple, however FigBlend was installed.

    An extension's own bl_info is deleted by Blender and regenerated from
    blender_manifest.toml, so ask addon_utils rather than reading either.
    """
    import addon_utils
    module = sys.modules.get(__package__)
    if module is None:
        return None
    version = addon_utils.module_bl_info(module).get("version")
    return tuple(version) if version else None


def install_source():
    """Where this copy came from: 'repository', 'file' or 'legacy'.

    Only a copy installed from the FigBlend repository can be upgraded by
    Blender. One installed from a zip lands in a local repository that Blender
    never syncs, and a legacy add-on is outside the extension system entirely.
    """
    parts = (__package__ or "").split(".")
    if len(parts) < 3 or parts[0] != "bl_ext":
        return "legacy"
    for repo in bpy.context.preferences.extensions.repos:
        if repo.module == parts[1]:
            if repo.use_remote_url and repo.remote_url:
                return "repository"
            return "file"
    return "file"


def parse_version(text):
    """'v1.2.3' -> (1, 2, 3), or None.

    Anything after the third number is dropped. GitHub's "latest release" never
    points at a pre-release, so a suffix there is not something to rank by.
    """
    match = re.match(r"\s*v?(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(part) for part in match.groups()) if match else None


def version_text(version):
    return ".".join(str(part) for part in version) if version else "?"


def release_url():
    # Built here rather than taken from the API response, so the button can
    # only ever open this project's own release page.
    return "https://github.com/%s/releases/latest" % GITHUB_REPO


def state():
    with _lock:
        return dict(_state)


# --- checking ----------------------------------------------------------------

def check(force=False):
    """Ask GitHub for the latest release, on a worker thread.

    Once a session unless forced: the answer will not change between two
    panel redraws, and unauthenticated GitHub API calls are rate-limited.
    """
    if not configured():
        _set(status="disabled", message="No release repository configured")
        return
    if not bpy.app.online_access:
        # Extensions must not go online when the user has said not to.
        _set(status="disabled",
             message="Online access is off in Blender's System preferences")
        return

    with _lock:
        if _state["status"] == "checking":
            return
        if _state["status"] in ("current", "available") and not force:
            return
        _state["status"] = "checking"
        _state["message"] = ""

    # addon_utils is main-thread only, so read the version before handing off.
    current = installed_version()
    threading.Thread(target=_worker, args=(current,), daemon=True).start()
    if not bpy.app.timers.is_registered(_redraw_when_done):
        bpy.app.timers.register(_redraw_when_done, first_interval=0.5)


def _worker(current):
    request = urllib.request.Request(
        API_URL.format(repo=GITHUB_REPO),
        headers={
            "Accept": "application/vnd.github+json",
            # GitHub rejects API requests that carry no User-Agent.
            "User-Agent": "FigBlend-Blender",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as err:
        _set(status="error", message="No release published yet"
             if err.code == 404 else "GitHub answered %d" % err.code)
        return
    except (urllib.error.URLError, OSError, ValueError) as err:
        _set(status="error",
             message="Could not reach GitHub (%s)" % getattr(err, "reason", err))
        return

    latest = parse_version(payload.get("tag_name", ""))
    if latest is None:
        _set(status="error",
             message="Unrecognised release tag %r" % payload.get("tag_name"))
        return

    newer = current is not None and latest > current
    _set(status="available" if newer else "current", latest=latest,
         message="FigBlend %s is available" % version_text(latest) if newer
         else "Up to date (%s)" % version_text(current))


def _set(**changes):
    with _lock:
        _state.update(changes)


def _redraw_when_done():
    """Main-thread timer: repaint the panels once the worker has an answer."""
    if state()["status"] == "checking":
        return 0.5
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "PREFERENCES"}:
                area.tag_redraw()
    return None


# --- lifecycle ---------------------------------------------------------------

def _startup_check():
    from . import props
    prefs = props.preferences()
    if prefs is not None and prefs.check_updates:
        check()
    return None  # one-shot


def register():
    # A render farm or a CI job has no one to tell, and should not phone home.
    if bpy.app.background:
        return
    # Deferred: preferences are not reliably readable during register(), and
    # there is no reason to slow down Blender's startup with a network call.
    bpy.app.timers.register(_startup_check, first_interval=5.0)


def unregister():
    for timer in (_startup_check, _redraw_when_done):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
