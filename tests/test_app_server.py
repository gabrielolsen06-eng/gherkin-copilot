"""app_server.py, which `init` puts in a web project: start the program that serves the site, then stop it and
everything it started. Runs on Windows (job object), macOS and Linux (process group) alike."""

import importlib.util
import socket
import sys
import time

import pytest

from gherkin_copilot.cli import main

# Like a launcher .exe: print what its pipeline does, start the real server, and exit straight away
LAUNCHER = """import subprocess, sys
print("pipeline: building the site", flush=True)
subprocess.Popen([sys.executable, "-m", "http.server", sys.argv[1], "--bind", "127.0.0.1"])
"""


@pytest.fixture
def app_server(tmp_path, capsys):
    assert main(["init", str(tmp_path / "project")]) == 0
    capsys.readouterr()
    spec = importlib.util.spec_from_file_location("generated_app_server", tmp_path / "project" / "app_server.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def gone(app_server, url, timeout=15):
    deadline = time.monotonic() + timeout
    while app_server.answers(url) and time.monotonic() < deadline:
        time.sleep(0.2)
    return not app_server.answers(url)


def test_waits_for_the_site_and_stops_what_the_launcher_left_running(app_server, tmp_path):
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    (tmp_path / "launcher.py").write_text(LAUNCHER, encoding="utf-8")
    command = [sys.executable, str(tmp_path / "launcher.py"), str(port)]
    log = tmp_path / "reports" / "app_server.log"
    server = app_server.start(command, url, wait=60, log=log)
    try:
        assert app_server.answers(url)
        assert server.process.wait(15) == 0  # the launcher has gone; the server it started keeps running
        with pytest.raises(RuntimeError, match="already answers"):  # never test against a site that was already up
            app_server.start(command, url, wait=5, log=log)
    finally:
        server.stop()
    assert gone(app_server, url)  # stopped with everything it started
    assert "pipeline: building the site" in log.read_text(encoding="utf-8")


def test_a_failing_pipeline_ends_the_wait_with_its_output(app_server, tmp_path):
    (tmp_path / "fail.py").write_text('print("step 3 of 5 failed: no database")\nraise SystemExit(2)\n', encoding="utf-8")
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="exit code 2") as error:
        app_server.start([sys.executable, str(tmp_path / "fail.py")], f"http://127.0.0.1:{free_port()}/", wait=120,
                         log=tmp_path / "app_server.log")
    assert "no database" in str(error.value) and time.monotonic() - started < 60


def test_only_web_addresses(app_server, tmp_path):
    with pytest.raises(RuntimeError, match="http"):
        app_server.start([sys.executable, "-c", "pass"], "file:///etc/passwd", log=tmp_path / "app_server.log")
    assert app_server.answers("http://127.0.0.1:1/") is False  # nothing listens there
