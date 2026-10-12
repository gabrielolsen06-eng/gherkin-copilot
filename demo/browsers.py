"""Find browsers that Playwright can drive but doesn't know by name.

Brave is built on Chromium, so Playwright can run it like Chrome as long as it
is given the path to the Brave executable.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

MAC_APPLICATIONS = Path("/Applications")  # the system-wide folder (tests point this elsewhere)


def brave_candidates() -> list[Path]:
    if sys.platform == "darwin":
        app = "Brave Browser.app/Contents/MacOS/Brave Browser"
        return [MAC_APPLICATIONS / app, Path.home() / "Applications" / app]
    if sys.platform == "win32":
        exe = Path("BraveSoftware/Brave-Browser/Application/brave.exe")
        roots = [os.environ.get(v) for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        return [Path(r) / exe for r in roots if r]
    found = [shutil.which(n) for n in ("brave-browser", "brave", "brave-browser-stable")]
    return [Path(p) for p in found if p]


def find_brave() -> str:
    for path in brave_candidates():
        if path.is_file():
            return str(path)
    raise FileNotFoundError(
        "Brave was not found in its usual install locations. "
        "Set DEMO_BROWSER_PATH to the full path of the Brave executable instead."
    )


def browser_path_from_env() -> str | None:
    """DEMO_BROWSER_PATH wins; DEMO_BROWSER=brave looks Brave up automatically."""
    if path := os.environ.get("DEMO_BROWSER_PATH"):
        return path
    if os.environ.get("DEMO_BROWSER", "").lower() == "brave":
        return find_brave()
    return None
