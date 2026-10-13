"""Drive a Windows desktop app through UI Automation, the accessibility interface built into Windows.

Buttons are pressed and fields filled through UI Automation's own actions (invoke, toggle, set
value), never a fake mouse or keyboard, so tests also run on a locked screen and in CI. Only
Windows' own UIAutomationCore.dll is used, through comtypes (pure Python). comtypes is imported on
first use, so the step library still loads on a Mac or Linux machine (the tests skip there).
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import os
import signal
import subprocess  # nosec B404: the tests start the app under test from its configured path, no shell
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TIMEOUT = 15.0  # seconds to wait for a window, control or text to appear

# UI Automation ids, from Windows' UIAutomationClient.h
BUTTON, CHECKBOX, COMBOBOX, EDIT, LINK, LISTITEM = 50000, 50002, 50003, 50004, 50005, 50007
MENUITEM, RADIO = 50011, 50013
TABITEM, TEXT, TREEITEM, DOCUMENT, SPLITBUTTON = 50019, 50020, 50024, 50030, 50031
KINDS = {BUTTON: "button", CHECKBOX: "checkbox", COMBOBOX: "dropdown", EDIT: "field", LINK: "link",
         LISTITEM: "list item", MENUITEM: "menu item", RADIO: "option", TABITEM: "tab", TEXT: "text",
         TREEITEM: "tree item", DOCUMENT: "document", SPLITBUTTON: "button"}
CLICKABLE = {BUTTON, CHECKBOX, LINK, LISTITEM, MENUITEM, RADIO, TABITEM, TREEITEM, SPLITBUTTON}
TYPABLE = {EDIT, COMBOBOX, DOCUMENT}
READABLE = TYPABLE | {TEXT}
INVOKE, SELECTION_ITEM, VALUE, WINDOW, TOGGLE = 10000, 10010, 10002, 10009, 10015
CHILDREN, DESCENDANTS = 2, 4


@dataclass
class App:
    window: Any  # its main window's UI Automation element
    pid: int  # the process that owns the window (not the launcher, if it had one)
    process: subprocess.Popen[bytes]  # what we started: the app itself, or its launcher


@functools.cache
def _uia() -> tuple[Any, Any]:
    """The UI Automation client and its type library (Windows only, loaded on first use)."""
    import comtypes.client

    library = comtypes.client.GetModule("UIAutomationCore.dll")
    return comtypes.client.CreateObject(library.CUIAutomation, interface=library.IUIAutomation), library


def _safe(read: Callable[[], Any], default: Any = None) -> Any:
    """A window can close while we look at it: treat it as gone."""
    try:
        return read()
    except Exception:  # comtypes' COMError, e.g. "element not available"
        return default


def _norm(text: str) -> str:
    """'Characters to cop&y :' and 'Characters to copy' are the same control to a person."""
    return " ".join(text.replace("&", "").split()).rstrip(" :").casefold()


def _wait(found: Callable[[], Any], what: str | Callable[[], str], timeout: float = TIMEOUT) -> Any:
    deadline = time.monotonic() + timeout
    while not (result := found()):
        if time.monotonic() > deadline:
            detail = what if isinstance(what, str) else what()
            raise AssertionError(f"Gave up after {timeout:.0f} s waiting for {detail}")
        time.sleep(0.2)
    return result


def _find_all(element: Any, scope: int) -> list[Any]:
    client, _ = _uia()
    found = element.FindAll(scope, client.CreateTrueCondition())
    return [found.GetElement(i) for i in range(found.Length)]


def _windows(pid: int | None = None) -> list[Any]:
    windows = _find_all(_uia()[0].GetRootElement(), CHILDREN)
    return [w for w in windows if pid is None or _safe(lambda w=w: w.CurrentProcessId) == pid]


def _elements(app: App) -> list[Any]:
    """Every control in every window the app has open: its main window, dialogs and message boxes."""
    # shortcut: walks every control on each look; fine for forms, slow on windows with thousands of controls
    return [e for window in _windows(app.pid) for e in (window, *_find_all(window, DESCENDANTS))]


def _pattern(element: Any, pattern_id: int, interface: str) -> Any:
    unknown = element.GetCurrentPattern(pattern_id)
    return unknown.QueryInterface(getattr(_uia()[1], interface)) if unknown else None


def _act(action: Callable[[], Any], what: str, wait: float = 1.0) -> None:
    """Run a UI Automation action without waiting for it to finish.

    A classic Windows button only returns once its own code has, so a button that opens a message
    box would hold the test until someone closed the box. Errors that happen straight away are
    still reported; a message box is found by the next step like any other window.
    """
    errors: list[BaseException] = []

    def run() -> None:
        import comtypes

        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        try:
            action()
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(wait)
    if errors:
        raise AssertionError(f"Couldn't {what}: {errors[0]}")


def _exe(pid: int) -> str:
    """The full path of a running program, to spot an app that's already open."""
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        return buffer.value if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(handle)


# --- what the steps use ----------------------------------------------------------------------

def start(exe: str, title: str = "", cwd: str = "") -> App:
    """Start the app and wait for its window. ``title`` (part of the window title) is only needed for
    apps that start through a launcher, whose window then belongs to another process."""
    path = Path(exe)

    def titled(window: Any) -> bool:
        return bool(title) and title.casefold() in _safe(lambda: window.CurrentName, "").casefold()

    def already_open(window: Any) -> bool:
        return titled(window) or os.path.normcase(_exe(_safe(lambda: window.CurrentProcessId, 0))) == \
            os.path.normcase(str(path))

    if any(already_open(w) for w in _windows()):
        raise AssertionError(f"{path.name} is already open. Close it first: the test would take over that window "
                             "and close it afterwards, losing anything unsaved.")
    try:
        process = subprocess.Popen([str(path)], cwd=cwd or path.parent)  # nosec B603: the configured app, no shell
    except OSError as exc:
        if getattr(exc, "winerror", None) == 740:  # ERROR_ELEVATION_REQUIRED
            raise AssertionError(f"{path.name} needs administrator rights: start the tests as administrator.") from exc
        raise

    def main_window() -> Any:
        windows = _windows(process.pid) or [w for w in _windows() if titled(w)]
        return windows[0] if windows else None

    hint = "" if title else " (if it starts through a launcher, set DESKTOP_WINDOW to part of its window title)"
    window = _wait(main_window, f"a window from {path.name}{hint}")
    return App(window, window.CurrentProcessId, process)


def close(app: App) -> None:
    """Close every window the app has, and make sure its process is gone, even after a failed scenario."""
    for window in _windows(app.pid):
        pattern = _safe(lambda w=window: _pattern(w, WINDOW, "IUIAutomationWindowPattern"))
        if pattern:
            with contextlib.suppress(AssertionError):
                _act(pattern.Close, "close a window")
    with contextlib.suppress(AssertionError):
        _wait(lambda: not _windows(app.pid), "the app to close", timeout=5)
    if _windows(app.pid):  # still open, e.g. asking whether to save: end it
        with contextlib.suppress(OSError):
            os.kill(app.pid, signal.SIGTERM)  # on Windows this ends the process
    if app.process.poll() is None:
        app.process.kill()


def describe(app: App) -> str:
    """The named controls the app shows, for error messages: what the test could have meant."""
    seen = []
    for element in _elements(app):
        kind, name = _safe(lambda e=element: (e.CurrentControlType, e.CurrentName), (None, ""))
        if kind in KINDS and name:
            seen.append(f"{KINDS[kind]} “{name}”")
    return ", ".join(list(dict.fromkeys(seen))[:40]) or "no named controls"


def find(app: App, name: str, kinds: set[int]) -> Any:
    """The one control of these kinds called ``name``, or with that AutomationId (the developer's id)."""
    def matches() -> list[Any]:
        candidates = [e for e in _elements(app) if _safe(lambda e=e: e.CurrentControlType) in kinds]
        return [e for e in candidates if _norm(_safe(lambda e=e: e.CurrentName, "")) == _norm(name)] or \
            [e for e in candidates if _safe(lambda e=e: e.CurrentAutomationId) == name]

    what = " or ".join(sorted({KINDS[k] for k in kinds}))
    try:
        found = _wait(matches, f"a {what} called “{name}”")
    except AssertionError as exc:
        raise AssertionError(f"{exc}. The app shows: {describe(app)}") from None
    if len(found) > 1:  # a field's label usually has the field's name too: the field is the one meant
        found = [e for e in found if _safe(lambda e=e: e.CurrentControlType) != TEXT] or found
    if len(found) > 1:
        raise AssertionError(f"{len(found)} controls are called “{name}”. Use the control's AutomationId instead.")
    return found[0]


def click(app: App, name: str) -> None:
    element = find(app, name, CLICKABLE)
    for pattern_id, interface, method in ((INVOKE, "IUIAutomationInvokePattern", "Invoke"),
                                          (TOGGLE, "IUIAutomationTogglePattern", "Toggle"),
                                          (SELECTION_ITEM, "IUIAutomationSelectionItemPattern", "Select")):
        pattern = _pattern(element, pattern_id, interface)
        if pattern:
            return _act(getattr(pattern, method), f"click “{name}”")
    raise AssertionError(f"“{name}” can't be clicked through UI Automation")


def tick(app: App, name: str) -> None:
    """Tick a checkbox, unless it already is: the same scenario works however the app last left it."""
    if not is_ticked(app, name):
        click(app, name)


def is_ticked(app: App, name: str) -> bool:
    element = find(app, name, {CHECKBOX, RADIO})
    toggle = _pattern(element, TOGGLE, "IUIAutomationTogglePattern")
    if toggle:
        return bool(toggle.CurrentToggleState == 1)
    selected = _pattern(element, SELECTION_ITEM, "IUIAutomationSelectionItemPattern")
    return bool(selected and selected.CurrentIsSelected)


def type_into(app: App, name: str, text: str) -> None:
    pattern = _pattern(find(app, name, TYPABLE), VALUE, "IUIAutomationValuePattern")
    if not pattern or pattern.CurrentIsReadOnly:
        raise AssertionError(f"“{name}” doesn't accept typed text")
    _act(lambda: pattern.SetValue(text), f"type into “{name}”", wait=5.0)  # typing rarely opens a message box


def text_of(app: App, name: str) -> str:
    element = find(app, name, READABLE)
    pattern = _pattern(element, VALUE, "IUIAutomationValuePattern")
    text = str(pattern.CurrentValue if pattern else element.CurrentName)
    return " ".join(text.split())  # as a person reads it: Character Map's field ends with a line break


def wait_until(check: Callable[[], bool], what: str | Callable[[], str]) -> None:
    _wait(check, what)


def shows(app: App, text: str) -> bool:
    """Whether any visible control's name or value contains this text."""
    wanted = text.casefold()

    def visible_text(element: Any) -> str:
        if element.CurrentIsOffscreen:
            return ""
        value = _pattern(element, VALUE, "IUIAutomationValuePattern")
        return f"{element.CurrentName} {value.CurrentValue if value else ''}".casefold()

    return any(wanted in _safe(lambda e=e: visible_text(e), "") for e in _elements(app))
