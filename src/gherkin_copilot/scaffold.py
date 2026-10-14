"""Create a starter BDD project for `gherkin-copilot init`.

The project uses pytest-bdd and Playwright, keeps one shared step library in steps/, has an
example scenario that runs without a website, and sets out the conventions in its README.
The files are templates in starter/ (named *.tmpl so linters and packaging leave them
alone). Creating a project only ever adds files: anything that already exists is kept.

`init --desktop` makes a project for a Windows desktop app instead: the templates in
starter_desktop/ (copies of the repo's desktop_demo/, which a test keeps identical) plus the
files both kinds of project share.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

SUFFIX = ".tmpl"
RENAMED = {"gitignore": ".gitignore", "gitlab-ci.yml": ".gitlab-ci.yml"}  # dotfiles in package data are easy to lose
NAME = "{{name}}"
CI_FILES = {"gitlab": ".gitlab-ci.yml"}  # pipeline files, only added when asked for with --ci
SHARED = {"requirements.lock", "requirements.in", ".gitignore", "steps/__init__.py"}  # web and desktop projects


@dataclass
class InitResult:
    root: Path
    created: list[str] = field(default_factory=list[str])  # paths relative to root, with forward slashes
    skipped: list[str] = field(default_factory=list[str])  # already there, so left untouched


def _read(folder: Traversable, prefix: str = "") -> dict[str, str]:
    found: dict[str, str] = {}
    for item in sorted(folder.iterdir(), key=lambda i: i.name):
        if item.is_dir():
            found |= _read(item, f"{prefix}{item.name}/")
        elif item.name.endswith(SUFFIX):
            name = item.name.removesuffix(SUFFIX)
            found[prefix + RENAMED.get(name, name)] = item.read_text(encoding="utf-8")
    return found


def templates(ci: str | None = None, desktop: bool = False) -> dict[str, str]:
    """Relative path -> contents, for every file in the starter project (plus ci's pipeline file)."""
    found = _read(files("gherkin_copilot") / "starter")
    if desktop:
        found = {path: text for path, text in found.items() if path in SHARED}
        found |= _read(files("gherkin_copilot") / "starter_desktop")
    unwanted = {path for name, path in CI_FILES.items() if name != ci}
    return {path: text for path, text in sorted(found.items()) if path not in unwanted}


def create(root: Path, name: str, dry_run: bool = False, ci: str | None = None, desktop: bool = False) -> InitResult:
    """Write the starter files under root, skipping any that exist. With dry_run, write nothing."""
    result = InitResult(root)
    for relative, text in templates(ci, desktop).items():
        path = root / relative
        if path.exists():
            result.skipped.append(relative)
            continue
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as out:  # "x": never replace a file, even one created just now
                out.write(text.replace(NAME, name))
        result.created.append(relative)
    return result
