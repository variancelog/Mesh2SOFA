"""Per-user app settings (Mesh2HRTF / Blender / grading-tool paths).

The repo ships only `app_settings.template.json`; the real file lives outside the repo so
machine-specific paths are never committed and survive re-clones. stdlib only.

blender_scripts/mesh2SOFA_blender_addon.py mirrors user_settings_path() (it cannot import
core); tests/test_app_settings.py asserts the two agree.
"""
import json
import os
import sys

APP_NAME = "Mesh2SOFA"
FILE_NAME = "app_settings.json"
OVERRIDE_ENV = "MESH2SOFA_SETTINGS_DIR"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "mesh2hrtf_path": "C:/Mesh2HRTF/mesh2hrtf",
    "blender_path": "",
    "grading_bin_path": "",  # empty: the GUI falls back to its working directory
}


def user_settings_dir():
    override = os.environ.get(OVERRIDE_ENV)
    if override:
        return override
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, APP_NAME)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", APP_NAME)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg and os.path.isabs(xdg):
        return os.path.join(xdg, APP_NAME)
    return os.path.join(os.path.expanduser("~"), ".config", APP_NAME)


def user_settings_path():
    return os.path.join(user_settings_dir(), FILE_NAME)


def _read_json(path):
    """Returns (dict or None, note or None)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None, None
    except Exception as e:
        return None, "Could not read %s (%s); using defaults" % (path, e)
    if not isinstance(data, dict):
        return None, "Ignored %s (not a JSON object)" % path
    return data, None


def load():
    """DEFAULTS <- template (non-empty values) <- user file. Returns (settings, notes)."""
    settings = dict(DEFAULTS)
    notes = []

    template, note = _read_json(os.path.join(REPO_ROOT, "app_settings.template.json"))
    if note:
        notes.append(note)
    for k, v in (template or {}).items():
        if v not in ("", None):
            settings[k] = v

    user_path = user_settings_path()
    user, note = _read_json(user_path)
    if note:
        notes.append(note)
    elif user is None:
        # One-time migration from the legacy repo-root file (left untouched).
        legacy, _ = _read_json(os.path.join(REPO_ROOT, FILE_NAME))
        kept = {k: v for k, v in (legacy or {}).items() if v not in ("", None)}
        if kept:
            merged = dict(settings)
            merged.update(kept)
            err = save(merged)
            if err:
                notes.append(err)
            else:
                notes.append("Imported settings from the old app_settings.json")
            user = kept
    if user:
        settings.update(user)
    return settings, notes


def save(settings):
    """Atomic write to the user file. Returns an error string, or None on success."""
    path = user_settings_path()
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4)
        os.replace(tmp, path)
        return None
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return "Could not save settings to %s: %s" % (path, e)
