import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "demo"))
import browsers


def test_explicit_path_wins(monkeypatch):
    monkeypatch.setenv("DEMO_BROWSER_PATH", "/opt/some/browser")
    monkeypatch.setenv("DEMO_BROWSER", "brave")
    assert browsers.browser_path_from_env() == "/opt/some/browser"


def test_no_setting_means_playwright_default(monkeypatch):
    monkeypatch.delenv("DEMO_BROWSER_PATH", raising=False)
    monkeypatch.delenv("DEMO_BROWSER", raising=False)
    assert browsers.browser_path_from_env() is None


def test_brave_found_on_mac(monkeypatch, tmp_path):
    monkeypatch.setattr(browsers.sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    exe = tmp_path / "Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.delenv("DEMO_BROWSER_PATH", raising=False)
    monkeypatch.setenv("DEMO_BROWSER", "Brave")
    assert browsers.brave_candidates()[0] == Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")
    assert browsers.browser_path_from_env() == str(exe)


def test_brave_found_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(browsers.sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    exe = tmp_path / "BraveSoftware/Brave-Browser/Application/brave.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    assert browsers.find_brave() == str(exe)


def test_missing_brave_gives_a_clear_error(monkeypatch):
    monkeypatch.setattr(browsers, "brave_candidates", lambda: [])
    with pytest.raises(FileNotFoundError, match="DEMO_BROWSER_PATH"):
        browsers.find_brave()
