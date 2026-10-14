"""Turn every scenario in features/ into a pytest test. They need Windows: elsewhere each one is skipped."""

import sys

import pytest
from pytest_bdd import scenarios

# Skip each scenario rather than the whole file: a whole-file skip makes pytest exit with "no tests collected"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows: these scenarios drive a desktop app")

scenarios(".")  # the whole bdd_features_base_dir (features/), subfolders included
