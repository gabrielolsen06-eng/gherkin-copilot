"""Shared setup for every scenario: the app under test, and the step library.

Which app to test is set in pytest.ini (desktop_app), like base_url for a website. Each
setting can be overridden for one run with an environment variable of the same name in
capitals, e.g. DESKTOP_APP=C:\\Apps\\Billing\\Billing.exe.
"""

import os
from pathlib import Path

import pytest
import uia

# Step definitions live in steps/ and are shared by every feature file. Every module in that
# folder is loaded here, so a new step file works without registering it anywhere.
STEPS_DIR = Path(__file__).resolve().parent / "steps"
pytest_plugins = [f"steps.{p.stem}" for p in sorted(STEPS_DIR.glob("*.py")) if p.stem != "__init__"]
SETTINGS = {
    "desktop_app": "the .exe to test",
    "desktop_window": "part of its main window's title; only for apps that start through a launcher",
    "desktop_workdir": "folder to start it in (default: the .exe's own folder)",
}


def pytest_addoption(parser):
    for name, help_text in SETTINGS.items():
        parser.addini(name, help_text)


def pytest_configure(config):
    """pytest-bdd doesn't create the --cucumberjson folder, so reports/cucumber.json would fail on a fresh checkout."""
    path = config.getoption("cucumber_json_path", None)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def desktop_config(pytestconfig):
    value = {name: os.environ.get(name.upper()) or pytestconfig.getini(name) for name in SETTINGS}
    if not value["desktop_app"]:
        pytest.fail("Set desktop_app in pytest.ini to the .exe to test")
    return {"exe": value["desktop_app"], "title": value["desktop_window"], "cwd": value["desktop_workdir"]}


@pytest.fixture
def desktop_apps():
    """Apps opened by this scenario; all closed afterwards, even when the scenario fails."""
    apps = []
    yield apps
    for app in apps:
        uia.close(app)
