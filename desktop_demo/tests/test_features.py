"""Turn every scenario in desktop_demo/features into a pytest test. Windows only."""

import sys

import pytest
from pytest_bdd import scenarios

if sys.platform != "win32":  # a skip here works; in conftest.py it would crash the whole run
    pytest.skip("needs Windows: these scenarios drive a desktop app", allow_module_level=True)

scenarios(".")  # the whole bdd_features_base_dir (features/), subfolders included
