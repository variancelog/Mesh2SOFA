"""Pytest bootstrap: put the repo's core/ folder on sys.path so the test files
can keep their flat imports (`from project_store import ...`, `from
tunnel_loop_extractor import ...`, etc.) now that the modules live in core/.
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORE_DIR = os.path.join(_REPO_ROOT, "core")
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)
