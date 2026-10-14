"""Saving the user's files: line endings kept, nothing half-written, links not followed out of the project."""

import os
import stat
import sys

import pytest

from gherkin_copilot import files


def symlink(link, target, is_dir=False):
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except OSError:  # Windows without the right to make links
        pytest.skip("can't create symlinks here")


def test_line_endings_are_kept(tmp_path):
    crlf, lf, new = tmp_path / "crlf.feature", tmp_path / "lf.feature", tmp_path / "new.feature"
    crlf.write_bytes(b"Feature: a\r\n  Scenario: b\r\n")
    lf.write_bytes(b"Feature: a\n  Scenario: b\n")
    for path in (crlf, lf, new):
        files.save_text(path, "Feature: a\n  Scenario: c\n")
    assert crlf.read_bytes() == b"Feature: a\r\n  Scenario: c\r\n"  # used to come back as \n on macOS and Linux
    assert lf.read_bytes() == new.read_bytes() == b"Feature: a\n  Scenario: c\n"  # and as \r\n on Windows


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no Unix permission bits")
def test_permissions_are_kept(tmp_path):
    path = tmp_path / "steps.py"
    path.write_text("x = 1\n")
    path.chmod(0o640)
    files.save_text(path, "x = 2\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_a_failed_write_leaves_the_old_file(tmp_path, monkeypatch):
    path = tmp_path / "a.feature"
    path.write_text("old\n")

    def full_disk(*_):
        raise OSError("No space left on device")

    monkeypatch.setattr(os, "replace", full_disk)
    with pytest.raises(OSError, match="No space"):
        files.save_text(path, "new\n")
    assert path.read_text() == "old\n" and [p.name for p in tmp_path.iterdir()] == ["a.feature"]


def test_a_linked_file_is_replaced_not_written_through(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me\n")
    link = tmp_path / "project" / "a.feature"
    link.parent.mkdir()
    symlink(link, outside)
    files.save_text(link, "Feature: x\n")
    assert outside.read_text() == "keep me\n" and not link.is_symlink()


def test_resolves_inside_follows_links(tmp_path):
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    symlink(project / ".gherkin-copilot", outside, is_dir=True)
    assert files.resolves_inside(project / "a.feature", project)
    assert not files.resolves_inside(project / ".gherkin-copilot" / "history.jsonl", project)
