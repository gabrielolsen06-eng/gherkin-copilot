
import pytest

from demo import browsers


def test_explicit_path_wins(monkeypatch):
    monkeypatch.setenv("DEMO_BROWSER_PATH", "/opt/some/browser")
    monkeypatch.setenv("DEMO_BROWSER", "brave")
    assert browsers.browser_path_from_env() == "/opt/some/browser"


def test_no_setting_means_playwright_default(monkeypatch):
    monkeypatch.delenv("DEMO_BROWSER_PATH", raising=False)
    monkeypatch.delenv("DEMO_BROWSER", raising=False)
    assert browsers.browser_path_from_env() is None


def test_brave_found_on_mac(monkeypatch, tmp_path):
    # the real /Applications might contain Brave (it does on the developer's Mac), so use stand-ins for both folders
    system, home = tmp_path / "system", tmp_path / "home"
    monkeypatch.setattr(browsers, "MAC_APPLICATIONS", system)
    monkeypatch.setattr(browsers.sys, "platform", "darwin")
    monkeypatch.setattr(browsers.Path, "home", lambda: home)  # HOME alone isn't enough on Windows
    app = "Brave Browser.app/Contents/MacOS/Brave Browser"
    exe = home / "Applications" / app
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.delenv("DEMO_BROWSER_PATH", raising=False)
    monkeypatch.setenv("DEMO_BROWSER", "Brave")
    assert browsers.brave_candidates()[0] == system / app  # the system folder is checked first
    assert browsers.browser_path_from_env() == str(exe)  # ...and the user's own folder when it isn't there

    system_exe = system / app
    system_exe.parent.mkdir(parents=True)
    system_exe.touch()
    assert browsers.browser_path_from_env() == str(system_exe)


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
