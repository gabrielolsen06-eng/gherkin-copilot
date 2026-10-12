"""Create a starter BDD project for `gherkin-copilot init`.

The project uses pytest-bdd and Playwright, keeps one shared step library in steps/, has an
example scenario that runs without a website, and sets out the conventions in its README.
The files are templates in starter/ (named *.tmpl so linters and packaging leave them
alone). Creating a project only ever adds files: anything that already exists is kept.
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


@dataclass
class InitResult:
    root: Path
    created: list[str] = field(default_factory=list)  # paths relative to root, with forward slashes
    skipped: list[str] = field(default_factory=list)  # already there, so left untouched


def templates(ci: str | None = None) -> dict[str, str]:
    """Relative path -> contents, for every file in the starter project (plus ci's pipeline file)."""
    found: dict[str, str] = {}

    def walk(folder: Traversable, prefix: str) -> None:
        for item in sorted(folder.iterdir(), key=lambda i: i.name):
            if item.is_dir():
                walk(item, f"{prefix}{item.name}/")
            elif item.name.endswith(SUFFIX):
                name = item.name.removesuffix(SUFFIX)
                found[prefix + RENAMED.get(name, name)] = item.read_text(encoding="utf-8")

    walk(files("gherkin_copilot") / "starter", "")
    unwanted = {path for name, path in CI_FILES.items() if name != ci}
    return {path: text for path, text in sorted(found.items()) if path not in unwanted}


def create(root: Path, name: str, dry_run: bool = False, ci: str | None = None) -> InitResult:
    """Write the starter files under root, skipping any that exist. With dry_run, write nothing."""
    result = InitResult(root)
    for relative, text in templates(ci).items():
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
