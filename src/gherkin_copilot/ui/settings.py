"""How a project's tests run, kept in the project so everyone runs them the same way.

``.gherkin-copilot/settings.json`` sits next to the history file and is meant to be committed: the test
command, the folder to run it from (relative to the project folder, so it works on every computer) and whether
to keep screenshots and traces. A project without that file uses the settings saved on this computer, in
``~/.gherkin-copilot/projects.json``.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from gherkin_copilot.files import resolves_inside, save_text
from gherkin_copilot.ui import history
from gherkin_copilot.ui.builder import json_object
from gherkin_copilot.ui.projects import Project, existing_folder
from gherkin_copilot.ui.runner import SECRET_NAME, parse_command

MAX_COMMAND = 500


def settings_file(project: Project) -> Path:
    return history.project_root(project) / ".gherkin-copilot" / "settings.json"


def apply(project: Project) -> Project:
    """The project with the settings from its settings file, if it has one."""
    path = settings_file(project)
    if not path.is_file():
        return project
    try:
        data = json_object(json.loads(path.read_text(encoding="utf-8"))) or {}
    except ValueError as exc:  # also UnicodeDecodeError, e.g. a half-resolved merge
        raise ValueError(f"{path} can't be read. Fix it, or save the test settings in Details again.") from exc
    command, run_from = data.get("test_command"), data.get("run_from")
    folder = history.project_root(project) / run_from if isinstance(run_from, str) and run_from else None
    return replace(project, test_command=command if isinstance(command, str) else "",
                   run_from=str(folder.resolve()) if folder else "", capture=data.get("capture") is not False)


def save(project: Project, test_command: str, run_from: str, capture: bool, author: object) -> Project:
    """Write the project's settings file, and note the change in its history."""
    test_command = " ".join(test_command.split())
    if len(test_command) > MAX_COMMAND:
        raise ValueError(f"Keep the test command under {MAX_COMMAND} characters.")
    if test_command:
        env, _ = parse_command(test_command)
        if secret := next((name for name in env if SECRET_NAME.search(name)), None):
            raise ValueError(f"{secret} would be committed with the project's test settings. Set it in the "
                             "environment before you start the app instead.")
    root = history.project_root(project)
    relative = ""
    if run_from.strip():
        folder = existing_folder(run_from, "run-from")
        try:
            relative = Path(os.path.relpath(folder, root)).as_posix()
        except ValueError as exc:  # Windows: on another drive
            raise ValueError("The run-from folder must be on the same drive as the project.") from exc
    path = settings_file(project)
    if not resolves_inside(path, root):
        raise ValueError(f"{path} links to somewhere outside the project, so the app won't write to it.")
    before = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    after = json.dumps({"test_command": test_command, "run_from": relative, "capture": bool(capture)}, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    save_text(path, after)
    if after != before:
        history.record(project, "settings", author, {path.relative_to(root).as_posix(): (before, after)})
    return apply(project)
