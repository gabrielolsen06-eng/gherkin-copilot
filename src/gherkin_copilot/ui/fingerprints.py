"""What ran: a fingerprint (SHA-256) of every file that defines a project's tests, taken as a run starts.

The files are feature files, Python files, pytest's configuration, requirements files, the app's test settings
and anything in a top-level ``data/`` folder. Hidden folders (``.git``, ``.venv``, the app's results), virtual
environments, caches, reports and test records are left out. A test record keeps the fingerprints, so anyone can
check later exactly which files ran.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from gherkin_copilot.indexer import SKIP_DIRS

CONFIG = {"pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"}
SKIP = SKIP_DIRS | {"reports", "test-records"}
SETTINGS = ".gherkin-copilot/settings.json"


def _wanted(relative: str) -> bool:
    name = relative.rpartition("/")[2]
    return (name.endswith((".feature", ".py")) or name in CONFIG or name.startswith("requirements")
            or relative.startswith("data/"))


def files(root: Path) -> list[Path]:
    """The files that define the tests under ``root``, sorted."""
    found = [root / SETTINGS] if (root / SETTINGS).is_file() else []
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]  # never walked into
        base = Path(folder)
        found += [base / name for name in names if _wanted((base / name).relative_to(root).as_posix())]
    return sorted(found)


def fingerprints(root: Path) -> dict[str, str]:
    """Path relative to ``root`` -> SHA-256 of the file's bytes."""
    found: dict[str, str] = {}
    for path in files(root):
        shown = path.relative_to(root).as_posix()
        try:
            with path.open("rb") as handle:
                found[shown] = hashlib.file_digest(handle, "sha256").hexdigest()
        except OSError:
            found[shown] = "unreadable"
    return found
