"""Shared fixtures for the desktop demo: scenarios that drive a Windows app through UI Automation.

The app under test comes from environment variables, so the same steps work for any app:

    DESKTOP_APP      path to its .exe (default: Character Map, which every Windows has)
    DESKTOP_WINDOW   part of its main window's title; only needed if it starts through a launcher
    DESKTOP_WORKDIR  folder to start it in (default: the .exe's own folder)
"""

import os
from pathlib import Path

import pytest

from desktop_demo import uia

# Every module in steps/ is loaded, like the web demo, so a new step file needs no registering
STEPS_DIR = Path(__file__).resolve().parent / "steps"
pytest_plugins = [f"desktop_demo.steps.{p.stem}" for p in sorted(STEPS_DIR.glob("*.py")) if p.stem != "__init__"]


@pytest.fixture
def desktop_config():
    return {"exe": os.environ.get("DESKTOP_APP", r"C:\Windows\System32\charmap.exe"),
            "title": os.environ.get("DESKTOP_WINDOW", ""), "cwd": os.environ.get("DESKTOP_WORKDIR", "")}


@pytest.fixture
def desktop_apps():
    """Apps opened by this scenario; all closed afterwards, even when the scenario fails."""
    apps: list[uia.App] = []
    yield apps
    for app in apps:
        uia.close(app)
