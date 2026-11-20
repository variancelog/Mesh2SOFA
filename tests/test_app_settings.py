"""Tests for core/app_settings.py and the add-on's mirrored settings path."""
import json
import os
import sys

import pytest

import app_settings as A
from test_boundary_materials import _load_addon


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated user dir + fake repo root. Never touches the real user file."""
    user_dir = tmp_path / "user"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("MESH2SOFA_SETTINGS_DIR", str(user_dir))
    monkeypatch.setattr(A, "REPO_ROOT", str(repo))
    return user_dir, repo


def _w(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def test_no_files_gives_defaults(env):
    s, notes = A.load()
    assert s == A.DEFAULTS and notes == []


def test_layering(env):
    user_dir, repo = env
    _w(repo / "app_settings.template.json", {"mesh2hrtf_path": "T", "blender_path": "TB"})
    s, _ = A.load()
    assert s["mesh2hrtf_path"] == "T" and s["blender_path"] == "TB"
    _w(user_dir / "app_settings.json", {"blender_path": "U", "extra": 1})
    s, _ = A.load()
    assert s["blender_path"] == "U" and s["mesh2hrtf_path"] == "T" and s["extra"] == 1


def test_empty_template_values_keep_defaults(env):
    _, repo = env
    _w(repo / "app_settings.template.json", {"mesh2hrtf_path": ""})
    assert A.load()[0]["mesh2hrtf_path"] == A.DEFAULTS["mesh2hrtf_path"]


def test_corrupt_user_json(env):
    user_dir, _ = env
    _w(user_dir / "app_settings.json", "{not json")
    s, notes = A.load()
    assert s == A.DEFAULTS and len(notes) == 1


def test_migration(env):
    user_dir, repo = env
    _w(repo / "app_settings.json", {"mesh2hrtf_path": "L", "blender_path": "", "grading_bin_path": "G"})
    s, notes = A.load()
    assert s["mesh2hrtf_path"] == "L" and s["grading_bin_path"] == "G"
    assert "Imported settings from the old app_settings.json" in notes
    saved = json.loads((user_dir / "app_settings.json").read_text())
    assert saved["mesh2hrtf_path"] == "L"
    # second load: no migration note
    s2, notes2 = A.load()
    assert notes2 == [] and s2 == s
    # legacy file untouched
    assert json.loads((repo / "app_settings.json").read_text())["mesh2hrtf_path"] == "L"


def test_migration_skips_all_empty_legacy(env):
    user_dir, repo = env
    _w(repo / "app_settings.json", {"mesh2hrtf_path": "", "blender_path": ""})
    s, notes = A.load()
    assert notes == [] and not (user_dir / "app_settings.json").exists()


def test_existing_user_file_never_overwritten(env):
    user_dir, repo = env
    _w(user_dir / "app_settings.json", {"mesh2hrtf_path": "U"})
    _w(repo / "app_settings.json", {"mesh2hrtf_path": "L"})
    s, notes = A.load()
    assert s["mesh2hrtf_path"] == "U" and notes == []
    assert json.loads((user_dir / "app_settings.json").read_text())["mesh2hrtf_path"] == "U"


def test_save_roundtrip_creates_folder_no_tmp(env):
    user_dir, _ = env
    assert not user_dir.exists()
    assert A.save({"mesh2hrtf_path": "X", "blender_path": "B"}) is None
    assert A.load()[0]["blender_path"] == "B"
    assert os.listdir(user_dir) == ["app_settings.json"]


def test_save_error_returns_string(env, monkeypatch):
    def boom(*a, **k):
        raise OSError("nope")
    monkeypatch.setattr(os, "replace", boom)
    err = A.save({"a": 1})
    assert isinstance(err, str) and "nope" in err
    user_dir, _ = env
    assert not (user_dir / "app_settings.json.tmp").exists()


def _platform(monkeypatch, plat, home, local=None, xdg=None):
    monkeypatch.delenv("MESH2SOFA_SETTINGS_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", plat)
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)
    for k, v in (("LOCALAPPDATA", local), ("XDG_CONFIG_HOME", xdg)):
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


_home = os.path.abspath(os.sep + "h")
_abs = os.path.abspath(os.sep + "xdgdir")
CASES = [
    ("win32", dict(local=os.path.abspath(os.sep + "LA")), lambda: os.path.join(os.path.abspath(os.sep + "LA"), "Mesh2SOFA")),
    ("win32", dict(), lambda: os.path.join(os.path.expanduser("~"), "AppData", "Local", "Mesh2SOFA")),
    ("darwin", dict(), lambda: os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Mesh2SOFA")),
    ("linux", dict(xdg=_abs), lambda: os.path.join(_abs, "Mesh2SOFA")),
    ("linux", dict(), lambda: os.path.join(os.path.expanduser("~"), ".config", "Mesh2SOFA")),
    ("linux", dict(xdg="relative/dir"), lambda: os.path.join(os.path.expanduser("~"), ".config", "Mesh2SOFA")),
]


@pytest.mark.parametrize("plat,kw,expected", CASES)
def test_user_settings_path_per_platform(monkeypatch, plat, kw, expected):
    _platform(monkeypatch, plat, _home, **kw)
    assert A.user_settings_path() == os.path.join(expected(), "app_settings.json")


def test_override_wins_everywhere(monkeypatch, tmp_path):
    for plat in ("win32", "darwin", "linux"):
        _platform(monkeypatch, plat, _home, local="x", xdg=_abs)
        monkeypatch.setenv("MESH2SOFA_SETTINGS_DIR", str(tmp_path))
        assert A.user_settings_path() == os.path.join(str(tmp_path), "app_settings.json")


@pytest.mark.parametrize("plat,kw,expected", CASES)
def test_addon_mirror_matches_core(monkeypatch, plat, kw, expected):
    addon = _load_addon()
    _platform(monkeypatch, plat, _home, **kw)
    assert addon.user_settings_path() == A.user_settings_path()
    monkeypatch.setenv("MESH2SOFA_SETTINGS_DIR", _home)
    assert addon.user_settings_path() == A.user_settings_path()
