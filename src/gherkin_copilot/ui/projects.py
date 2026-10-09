"""The projects the app can switch between.

Stored as plain JSON in the user's home folder, so the same list is there
whichever folder the app is started from:

    {"active": "shop", "projects": [{"id": "shop", "name": "Shop", "steps": "...", "features": "..."}]}
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_FILE = Path.home() / ".gherkin-copilot" / "projects.json"
MAX_NAME = 60


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    steps: str  # folder scanned for step definitions (absolute)
    features: str  # folder with .feature files (absolute)
    test_command: str = ""  # e.g. "python -m pytest tests"; empty means the app can't run tests
    run_from: str = ""  # folder to run it from; empty means the project folder
    capture: bool = True  # add pytest-playwright screenshot and trace options


class ProjectStore:
    def __init__(self, path: Path = DEFAULT_FILE):
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> tuple[list[Project], str | None]:
        if not self.path.is_file():
            return [], None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return [Project(**p) for p in data.get("projects", [])], data.get("active")

    def _write(self, projects: list[Project], active: str | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"active": active, "projects": [asdict(p) for p in projects]}, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(self.path)

    def load(self) -> tuple[list[Project], str | None]:
        """All projects and the active project's id (falls back to the first project)."""
        with self._lock:
            projects, active = self._read()
        if active not in {p.id for p in projects}:
            active = projects[0].id if projects else None
        return projects, active

    def get(self, project_id: str) -> Project | None:
        return next((p for p in self.load()[0] if p.id == project_id), None)

    def add(self, name: str, steps: str, features: str = "") -> Project:
        """Validate and save a new project, and make it the active one. Raises ValueError with a readable message."""
        name = name.strip()
        if not name:
            raise ValueError("Give the project a name.")
        if len(name) > MAX_NAME:
            raise ValueError(f"Keep the name under {MAX_NAME} characters.")
        steps_dir = _folder(steps, "steps")
        features_dir = _folder(features, "features") if features.strip() else steps_dir
        with self._lock:
            projects, _ = self._read()
            base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"
            taken = {p.id for p in projects}
            project_id = next(c for c in (base if n == 1 else f"{base}-{n}" for n in range(1, len(taken) + 2))
                              if c not in taken)
            project = Project(project_id, name, str(steps_dir), str(features_dir))
            self._write([*projects, project], project.id)
        return project

    def remove(self, project_id: str) -> None:
        """Forget a project. Its files are not touched."""
        with self._lock:
            projects, active = self._read()
            remaining = [p for p in projects if p.id != project_id]
            if len(remaining) == len(projects):
                raise ValueError("No such project.")
            self._write(remaining, None if active == project_id else active)

    def update_tests(self, project_id: str, test_command: str, run_from: str, capture: bool) -> Project:
        """Save how to run the project's tests (the command is checked by the caller)."""
        test_command = " ".join(test_command.split())
        if len(test_command) > 500:
            raise ValueError("Keep the test command under 500 characters.")
        folder = str(_folder(run_from, "run-from")) if run_from.strip() else ""
        with self._lock:
            projects, active = self._read()
            current = next((p for p in projects if p.id == project_id), None)
            if current is None:
                raise ValueError("No such project.")
            updated = Project(current.id, current.name, current.steps, current.features, test_command, folder,
                              bool(capture))
            self._write([updated if p.id == project_id else p for p in projects], active)
        return updated

    def set_active(self, project_id: str) -> None:
        with self._lock:
            projects, _ = self._read()
            if project_id not in {p.id for p in projects}:
                raise ValueError("No such project.")
            self._write(projects, project_id)


def _folder(text: str, label: str) -> Path:
    if not text.strip():
        raise ValueError(f"Enter the {label} folder.")
    path = Path(text.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError(f"Use the full path to the {label} folder (it should start with / or a drive letter).")
    if not path.is_dir():
        raise ValueError(f"The {label} folder doesn't exist: {path}")
    return path.resolve()
