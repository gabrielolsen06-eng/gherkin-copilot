"""What ran: a fingerprint (SHA-256) of every file that defines a project's tests, taken as a run starts, and
whether those files were under version control.

The files are feature files, Python files, pytest's configuration, requirements files, the app's test settings
and anything in a top-level ``data/`` folder. Hidden folders (``.git``, ``.venv``, the app's results), virtual
environments, caches, reports and test records are left out. A test record keeps the fingerprints, so anyone can
check later exactly which files ran.

Each file is also compared with the commit HEAD points at, from git's own files (never by running git). A file
that changed or isn't committed, a branch other than main, master or release/..., or no git at all is a reason
the run isn't controlled: test records still save, and list the reasons.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from gherkin_copilot.indexer import SKIP_DIRS
from gherkin_copilot.ui import gitinfo

CONFIG = {"pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"}
SKIP = SKIP_DIRS | {"reports", "test-records"}
SETTINGS = ".gherkin-copilot/settings.json"
CONTROLLED = {"main", "master"}  # and release/...
SHOWN = 5  # file names listed in a reason


def _wanted(relative: str) -> bool:
    name = relative.rpartition("/")[2]
    return (name.endswith((".feature", ".py")) or name in CONFIG or name.startswith("requirements")
            or relative.startswith("data/"))


def files(root: Path) -> list[Path]:
    """The files that define the tests under ``root``, sorted."""
    found = [root / SETTINGS] if (root / SETTINGS).is_file() else []
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]  # never walked into
        base = Path(folder)
        found += [base / name for name in names if _wanted((base / name).relative_to(root).as_posix())]
    return sorted(found)


def _same(data: bytes, blob: str) -> bool:
    """Whether a file's bytes are the blob git committed, also when git stores it with \\n and the file has \\r\\n
    (core.autocrlf on Windows). shortcut: other clean filters (git-lfs, ident) aren't applied, so such files show as
    changed; read .gitattributes if a team uses them."""
    algorithm = "sha256" if len(blob) == 64 else "sha1"  # the repository's object format

    def git_id(content: bytes) -> str:
        return hashlib.new(algorithm, b"blob %d\0" % len(content) + content, usedforsecurity=False).hexdigest()

    return git_id(data) == blob or (b"\r\n" in data and git_id(data.replace(b"\r\n", b"\n")) == blob)


def _names(paths: list[str]) -> str:
    return ", ".join(paths[:SHOWN]) + (f" and {len(paths) - SHOWN} more" if len(paths) > SHOWN else "")


def _compare(root: Path, contents: dict[str, bytes | None]) -> tuple[dict[str, Any] | None, list[str]]:
    """(branch, commit and tags, or None outside git; the reasons the files that ran aren't controlled)."""
    git: dict[str, Any] | None = gitinfo.head(root)
    found = gitinfo.find_repository(root)
    if git is None or found is None:
        return None, ["Not in a git repository, so what ran can't be traced to a commit."]
    git["tags"] = []
    commit, branch = git["commit"], git["branch"]
    reasons = [] if branch in CONTROLLED or (branch or "").startswith("release/") else [
        f"Not on main, master or a release/ branch: {branch}" if branch else "Not on a branch (detached HEAD)."]
    if not commit:
        return git, [*reasons, "The repository has no commits yet."]
    work_tree = next(f for f in (root, *root.parents) if (f / ".git").exists())  # where find_repository found it
    prefix = root.relative_to(work_tree).as_posix()
    in_repo = {path: path if prefix == "." else f"{prefix}/{path}" for path in contents}
    git_dir, common = found
    try:
        with gitinfo.Objects(common / "objects") as objects:
            committed = gitinfo.tree_files(objects, gitinfo.commit_tree(objects, commit), set(in_repo.values()))
            git["tags"] = gitinfo.tags_at(git_dir, common, objects, commit)
    except gitinfo.UNREADABLE:
        return git, [*reasons, f"The files couldn't be compared with commit {commit[:7]}."]
    new = [path for path in contents if in_repo[path] not in committed]
    changed = [path for path, data in contents.items()
               if in_repo[path] in committed and (data is None or not _same(data, committed[in_repo[path]]))]
    if changed:
        reasons.append(f"Changed since commit {commit[:7]}: {_names(changed)}")
    if new:
        reasons.append(f"Not in commit {commit[:7]}: {_names(new)}")
    return git, reasons


def snapshot(root: Path) -> dict[str, Any]:
    """``fingerprints`` (path relative to ``root`` -> SHA-256), ``git`` and ``uncontrolled`` (reasons)."""
    root = root.resolve()
    contents: dict[str, bytes | None] = {}
    for path in files(root):
        try:  # shortcut: read whole, twice the memory of the biggest file; stream if data/ gets files of 100s of MB
            contents[path.relative_to(root).as_posix()] = path.read_bytes()
        except OSError:
            contents[path.relative_to(root).as_posix()] = None
    git, reasons = _compare(root, contents)
    return {"fingerprints": {path: hashlib.sha256(data).hexdigest() if data is not None else "unreadable"
                             for path, data in contents.items()},
            "git": git, "uncontrolled": reasons}
