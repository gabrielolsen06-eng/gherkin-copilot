"""Writing the user's files: keep their line endings, and never leave a half-written file behind."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def save_text(path: Path, text: str) -> None:
    """Write ``text`` (with \\n line endings) to ``path``, keeping the file's own endings (\\r\\n stays \\r\\n).

    Written to a temporary file next to it, then swapped in, so a crash or a full disk leaves the old file
    as it was. The swap also replaces a symlink itself rather than writing through it to another file.
    """
    exists = path.is_file()
    newline = "\r\n" if exists and b"\r\n" in path.read_bytes() else "\n"
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline=newline)
        if exists:
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def resolves_inside(path: Path, folder: Path) -> bool:
    """Whether ``path`` really is inside ``folder``, after following links (a repo can commit a symlink)."""
    return path.resolve().is_relative_to(folder.resolve())
