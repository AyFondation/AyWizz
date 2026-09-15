# conftest.py (ay_extractor root)
# Make the src-layout `ayextractor` package importable from a bare checkout
# WITHOUT an editable install (dev convenience + CI image already installs it,
# in which case this is a no-op). The package maps `ayextractor -> src` via
# `[tool.setuptools.package-dir]`; we reproduce that mapping for the test run.

import importlib.util
import sys
import types
from pathlib import Path

if importlib.util.find_spec("ayextractor") is None:
    _src = Path(__file__).parent / "src"
    _pkg = types.ModuleType("ayextractor")
    _pkg.__path__ = [str(_src)]
    sys.modules["ayextractor"] = _pkg
