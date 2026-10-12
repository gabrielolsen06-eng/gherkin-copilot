"""Shared fixtures for the demo suite.

By default the suite runs against the bundled demo shop on 127.0.0.1, so it
needs no internet access. Set DEMO_BASE_URL=https://www.saucedemo.com to run
the same scenarios against the public saucedemo site instead.

Set DEMO_BROWSER=brave (or run_demo.py --brave) to use Brave, or
DEMO_BROWSER_PATH to point at any Chromium-based browser executable.
"""

import os
from pathlib import Path

import pytest

from demo.browsers import browser_path_from_env
from demo.server import start_in_background

# Step definitions live in steps/ and are shared by every feature file. Every module in
# the folder is loaded, so a new step file is available without registering it here.
STEPS_DIR = Path(__file__).resolve().parent / "steps"
pytest_plugins = [f"demo.steps.{p.stem}" for p in sorted(STEPS_DIR.glob("*.py")) if p.stem != "__init__"]


@pytest.fixture(scope="session")
def app_url():
    external = os.environ.get("DEMO_BASE_URL")
    if external:
        yield external.rstrip("/")
        return
    server, url = start_in_background()
    yield url
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Allow a specific browser binary, e.g. Brave or a company-approved Chrome build."""
    path = browser_path_from_env()
    return {**browser_type_launch_args, "executable_path": path} if path else browser_type_launch_args
