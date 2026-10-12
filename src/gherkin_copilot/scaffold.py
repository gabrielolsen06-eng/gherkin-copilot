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
RENAMED = {"gitignore": ".gitignore"}  # a dotfile in package data is easy to lose when packaging
NAME = "{{name}}"


@dataclass
class InitResult:
    root: Path
    created: list[str] = field(default_factory=list)  # paths relative to root, with forward slashes
    skipped: list[str] = field(default_factory=list)  # already there, so left untouched


def templates() -> dict[str, str]:
    """Relative path -> contents, for every file in the starter project."""
    found: dict[str, str] = {}

    def walk(folder: Traversable, prefix: str) -> None:
        for item in sorted(folder.iterdir(), key=lambda i: i.name):
            if item.is_dir():
                walk(item, f"{prefix}{item.name}/")
            elif item.name.endswith(SUFFIX):
                name = item.name.removesuffix(SUFFIX)
                found[prefix + RENAMED.get(name, name)] = item.read_text(encoding="utf-8")

    walk(files("gherkin_copilot") / "starter", "")
    return dict(sorted(found.items()))


def create(root: Path, name: str, dry_run: bool = False) -> InitResult:
    """Write the starter files under root, skipping any that exist. With dry_run, write nothing."""
    result = InitResult(root)
    for relative, text in templates().items():
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
