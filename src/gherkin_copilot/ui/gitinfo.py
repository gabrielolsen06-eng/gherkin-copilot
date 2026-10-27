"""Git facts for the home page: branch, latest commit, last pull, commits to push or pull.

Read straight from the ``.git`` folder with the standard library; the app never
runs ``git``. HEAD and refs are small text files, the reflog (``logs/HEAD``)
records every commit, pull and checkout made in this copy, and commits are
zlib-compressed objects, stored loose or in pack files. Anything that can't be
read is left out rather than guessed, and user names or tokens in URLs are
never shown.
"""

from __future__ import annotations

import contextlib
import heapq
import mmap
import re
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

MAX_OBJECT = 1_000_000  # commits are a few hundred bytes; never inflate anything big
MAX_DEPTH = 100  # longest delta chain followed (git's default is 50)
MAX_WALK = 5000  # most commits looked at for one count
RECENT = 6
WEEK = 7 * 86400
SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
REFLOG = re.compile(r"([0-9a-f]+) ([0-9a-f]+) .*? <[^>]*> (\d+) [+-]\d{4}\t(.*)")
PERSON = re.compile(r"(.*?) <[^>]*> (\d+) [+-]\d{4}")
URL_USER = re.compile(r"(\w+://)[^/@\s]+@")  # https://user:token@host/x -> https://host/x
TYPES = {1: "commit", 2: "tree", 3: "blob", 4: "tag"}
OFS_DELTA, REF_DELTA = 6, 7
UNREADABLE = (LookupError, ValueError, OSError, zlib.error, struct.error)


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    author: str
    authored: int
    committed: int
    subject: str


@dataclass(frozen=True)
class Event:
    """One reflog line: something that moved HEAD in this copy of the repository."""

    kind: str  # commit, pull, merge, checkout, reset, rebase, clone, ...
    action: str  # as git wrote it, e.g. "commit (amend)" or "pull --rebase (finish)"
    message: str
    time: int
    old: str
    new: str


def parse_commit(sha: str, data: bytes) -> Commit:
    head, _, message = data.partition(b"\n\n")
    parents: list[str] = []
    author, authored, committed = "", 0, 0
    for line in head.decode("utf-8", "replace").split("\n"):
        key, _, value = line.partition(" ")
        person = PERSON.fullmatch(value)
        if key == "parent":
            parents.append(value)
        elif key == "author" and person:
            author, authored = person[1], int(person[2])
        elif key == "committer" and person:
            committed = int(person[2])
    subject = message.decode("utf-8", "replace").strip().split("\n", 1)[0].strip()
    return Commit(sha, tuple(parents), author, authored, committed, subject)


# --- the object store ------------------------------------------------------------

def _inflate(buffer: bytes | mmap.mmap, pos: int, size: int) -> bytes:
    """Decompress one pack entry of known size, starting at ``pos``."""
    if size > MAX_OBJECT:
        raise ValueError("Object too large.")
    inflater = zlib.decompressobj()
    out = bytearray()
    while len(out) < size and not inflater.eof:
        chunk = buffer[pos : pos + 16384]
        if not chunk:
            break
        pos += len(chunk)
        out += inflater.decompress(chunk, size - len(out))
    if len(out) != size:
        raise ValueError("Damaged object.")
    return bytes(out)


def apply_delta(base: bytes, delta: bytes) -> bytes:
    """Rebuild an object from its base and a git delta (copy and insert instructions)."""
    pos = 0

    def varint() -> int:
        nonlocal pos
        value = shift = 0
        while True:
            byte = delta[pos]
            pos += 1
            value |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                return value

    if varint() != len(base):
        raise ValueError("Delta doesn't match its base.")
    size = varint()
    if size > MAX_OBJECT:
        raise ValueError("Object too large.")
    out = bytearray()
    while pos < len(delta):
        op = delta[pos]
        pos += 1
        if op & 0x80:  # copy a range of the base
            start = length = 0
            for i in range(4):
                if op & (1 << i):
                    start |= delta[pos] << (8 * i)
                    pos += 1
            for i in range(3):
                if op & (0x10 << i):
                    length |= delta[pos] << (8 * i)
                    pos += 1
            out += base[start : start + (length or 0x10000)]
        elif op:  # insert the next op bytes
            out += delta[pos : pos + op]
            pos += op
        else:
            raise ValueError("Damaged delta.")
    if len(out) != size:
        raise ValueError("Damaged delta.")
    return bytes(out)


def _find(index: mmap.mmap, raw: bytes) -> int | None:
    """Offset of an object in a pack, from its version 2 ``.idx`` file."""
    if index[:8] != b"\xfftOc\x00\x00\x00\x02":
        return None
    fanout = struct.unpack_from(">256I", index, 8)
    total, lo, hi = fanout[255], fanout[raw[0] - 1] if raw[0] else 0, fanout[raw[0]]
    names, n = 8 + 1024, len(raw)
    while lo < hi:
        mid = (lo + hi) // 2
        name = index[names + mid * n : names + (mid + 1) * n]
        if name == raw:
            offsets = names + total * n + total * 4
            offset: int = struct.unpack_from(">I", index, offsets + 4 * mid)[0]
            if offset & 0x80000000:  # stored in the table of 8-byte offsets
                offset = struct.unpack_from(">Q", index, offsets + 4 * total + 8 * (offset & 0x7FFFFFFF))[0]
            return offset
        lo, hi = (mid + 1, hi) if name < raw else (lo, mid)
    return None


def _entry_header(pack: mmap.mmap, pos: int) -> tuple[int, int, int]:
    """(type, size, position of the data) of the pack entry at ``pos``."""
    byte = pack[pos]
    kind, size, shift = (byte >> 4) & 7, byte & 15, 4
    pos += 1
    while byte & 0x80:
        byte = pack[pos]
        size |= (byte & 0x7F) << shift
        shift += 7
        pos += 1
    return kind, size, pos


class Objects:
    """Read-only access to a repository's objects: loose files first, then pack files."""

    def __init__(self, folder: Path):
        self.folder = folder
        self._files = contextlib.ExitStack()
        self._packs: list[tuple[mmap.mmap, mmap.mmap]] | None = None
        self._commits: dict[str, Commit] = {}

    def __enter__(self) -> Objects:
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 trace: TracebackType | None) -> None:
        self._files.close()

    def _map(self, path: Path) -> mmap.mmap:
        handle = self._files.enter_context(path.open("rb"))
        return self._files.enter_context(mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ))

    def _pack_files(self) -> list[tuple[mmap.mmap, mmap.mmap]]:
        if self._packs is None:
            self._packs = []
            for index in sorted((self.folder / "pack").glob("pack-*.idx")):
                with contextlib.suppress(OSError, ValueError):  # e.g. a pack still being written
                    self._packs.append((self._map(index), self._map(index.with_suffix(".pack"))))
        return self._packs

    def read(self, sha: str, depth: int = 0) -> tuple[str, bytes]:
        """(type, content). LookupError if the object isn't here, ValueError if it can't be read."""
        loose = self.folder / sha[:2] / sha[2:]
        if loose.is_file():
            data = zlib.decompressobj().decompress(loose.read_bytes(), MAX_OBJECT + 64)
            header, _, body = data.partition(b"\0")
            kind, _, size = header.decode("ascii", "replace").partition(" ")
            if not size.isdigit() or int(size) != len(body):
                raise ValueError("Object too large or damaged.")
            return kind, body
        raw = bytes.fromhex(sha)
        for index, pack in self._pack_files():
            offset = _find(index, raw)
            if offset is not None:
                return self._unpack(pack, offset, len(raw), depth)
        raise LookupError(sha)

    def _unpack(self, pack: mmap.mmap, offset: int, hash_size: int, depth: int) -> tuple[str, bytes]:
        deltas: list[bytes] = []
        for _ in range(MAX_DEPTH):
            kind, size, pos = _entry_header(pack, offset)
            if kind == OFS_DELTA:  # the base is earlier in this pack
                byte = pack[pos]
                back = byte & 0x7F
                pos += 1
                while byte & 0x80:
                    byte = pack[pos]
                    back = ((back + 1) << 7) | (byte & 0x7F)
                    pos += 1
                deltas.append(_inflate(pack, pos, size))
                offset -= back
            elif kind == REF_DELTA:  # the base is named by its hash
                if depth >= MAX_DEPTH:
                    raise ValueError("Delta chain too long.")
                deltas.append(_inflate(pack, pos + hash_size, size))
                base_kind, data = self.read(pack[pos : pos + hash_size].hex(), depth + 1)
                break
            elif kind in TYPES:
                base_kind, data = TYPES[kind], _inflate(pack, pos, size)
                break
            else:
                raise ValueError("Damaged pack.")
        else:
            raise ValueError("Delta chain too long.")
        for delta in reversed(deltas):
            data = apply_delta(data, delta)
        return base_kind, data

    def commit(self, sha: str) -> Commit:
        if sha not in self._commits:
            kind, data = self.read(sha)
            if kind != "commit":
                raise ValueError(f"{sha} isn't a commit.")
            self._commits[sha] = parse_commit(sha, data)
        return self._commits[sha]


# --- counting commits ------------------------------------------------------------

def _parents(objects: Objects, sha: str) -> list[str]:
    """Parents that are in the repository (a shallow clone's oldest commits have parents that aren't)."""
    out: list[str] = []
    for parent in objects.commit(sha).parents:
        with contextlib.suppress(LookupError):
            objects.commit(parent)
            out.append(parent)
    return out


def _children_first(objects: Objects, group: set[str]) -> list[str]:
    waiting = dict.fromkeys(group, 0)
    for sha in group:
        for parent in _parents(objects, sha):
            if parent in waiting:
                waiting[parent] += 1
    ready = [sha for sha, n in waiting.items() if n == 0]
    order: list[str] = []
    while ready:
        order.append(ready.pop())
        for parent in _parents(objects, order[-1]):
            if parent in waiting:
                waiting[parent] -= 1
                if not waiting[parent]:
                    ready.append(parent)
    return order


def ahead_behind(objects: Objects, local: str, remote: str) -> tuple[int, int] | None:
    """Commits only on ``local`` and only on ``remote``, like ``git rev-list --left-right --count``.

    Walks back from both, newest first, marking which side reaches each commit, and stops once
    everything left to visit is shared history. Commits with the same timestamp (a rebase or a
    script makes many) are handled together, children before parents, so no commit is passed on
    before it knows every side that reaches it. None if that takes too long.
    """
    if local == remote:
        return 0, 0
    sides = {local: 1, remote: 2}
    queue = [(-objects.commit(sha).committed, sha) for sha in (local, remote)]
    heapq.heapify(queue)
    walked = 0
    while not all(sides[sha] == 3 for _, sha in queue):
        newest = queue[0][0]
        group, pending = set[str](), list[str]()
        while queue and queue[0][0] == newest:
            pending.append(heapq.heappop(queue)[1])
        while pending:  # this timestamp's commits, including parents made in the same second
            sha = pending.pop()
            if sha not in group:
                group.add(sha)
                pending += [p for p in _parents(objects, sha) if -objects.commit(p).committed == newest]
        walked += len(group)
        if walked > MAX_WALK:
            return None
        for sha in _children_first(objects, group):
            for parent in _parents(objects, sha):
                reached = sides.get(parent, 0) | sides[sha]
                if reached != sides.get(parent, 0):
                    sides[parent] = reached
                    if parent not in group:
                        heapq.heappush(queue, (-objects.commit(parent).committed, parent))
    return sum(s == 1 for s in sides.values()), sum(s == 2 for s in sides.values())


def commits_since(objects: Objects, head: str, since: float) -> dict[str, int]:
    """How many commits reachable from ``head`` were made after ``since``, and by how many people."""
    seen, authors, count = {head}, set[str](), 0
    queue = [(-objects.commit(head).committed, head)]
    while queue and count < MAX_WALK:
        committed, sha = heapq.heappop(queue)
        if -committed < since:
            break
        commit = objects.commit(sha)
        count += 1
        authors.add(commit.author)
        for parent in commit.parents:
            if parent not in seen:
                seen.add(parent)
                with contextlib.suppress(LookupError):
                    heapq.heappush(queue, (-objects.commit(parent).committed, parent))
    return {"commits": count, "authors": len(authors), "capped": count >= MAX_WALK}


# --- refs, config and the reflog -------------------------------------------------------

def find_repository(start: Path) -> tuple[Path, Path] | None:
    """(git folder, common folder) of the repository ``start`` is in. They differ in a linked worktree,
    whose ``.git`` is a file pointing at its own git folder; objects and branches live in the common one."""
    for folder in (start, *start.parents):
        dot_git = folder / ".git"
        if dot_git.is_file():
            text = dot_git.read_text(encoding="utf-8", errors="replace").strip()
            if not text.startswith("gitdir:"):
                return None
            dot_git = folder / text.removeprefix("gitdir:").strip()
        if dot_git.is_dir():
            common = dot_git / "commondir"
            if common.is_file():
                return dot_git, (dot_git / common.read_text(encoding="utf-8").strip()).resolve()
            return dot_git, dot_git
    return None


def _packed_ref(common: Path, name: str) -> str | None:
    path = common / "packed-refs"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        sha, _, ref = line.partition(" ")
        if ref == name and SHA.fullmatch(sha):
            return sha
    return None


def resolve(git_dir: Path, common: Path, name: str) -> str | None:
    """The commit a ref points at, following symbolic refs like HEAD -> refs/heads/main."""
    for _ in range(5):
        if name != "HEAD" and not name.startswith("refs/"):
            return None
        path = (git_dir if name == "HEAD" else common) / name
        text = path.read_text(encoding="utf-8", errors="replace").strip() if path.is_file() \
            else _packed_ref(common, name)
        if text is None or not text.startswith("ref:"):
            return text if text and SHA.fullmatch(text) else None
        name = text.removeprefix("ref:").strip()
    return None


def head(start: Path) -> dict[str, str | None] | None:
    """Branch and commit, read from the refs only (no history walk, never runs git)."""
    found = find_repository(start)
    if found is None:
        return None
    git_dir, common = found
    try:
        text = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    branch = text.removeprefix("ref: refs/heads/") if text.startswith("ref: refs/heads/") else None
    return {"branch": branch, "commit": resolve(git_dir, common, "HEAD")}


def commit_tree(objects: Objects, sha: str) -> str:
    kind, data = objects.read(sha)
    first = data.split(b"\n", 1)[0].decode("ascii", "replace")
    if kind != "commit" or not first.startswith("tree "):
        raise ValueError(f"{sha} isn't a commit.")
    return first.removeprefix("tree ")


def tree_files(objects: Objects, tree: str, paths: set[str]) -> dict[str, str]:
    """Object ids of these files (``a/b.py``) in a tree; paths that aren't files in it are left out.
    Only the folders on the way to a wanted path are read."""
    kind, data = objects.read(tree)
    if kind != "tree":
        raise ValueError(f"{tree} isn't a tree.")
    size, pos = len(tree) // 2, 0
    entries: dict[str, tuple[bytes, str]] = {}
    while pos < len(data):  # "<mode> <name>\0<raw id>", repeated
        space, end = data.index(b" ", pos), data.index(b"\0", pos)
        name = data[space + 1 : end].decode("utf-8", "replace")
        entries[name] = (data[pos:space], data[end + 1 : end + 1 + size].hex())
        pos = end + 1 + size
    found: dict[str, str] = {}
    below: dict[str, set[str]] = {}
    for path in paths:
        name, _, rest = path.partition("/")
        mode, sha = entries.get(name, (b"", ""))
        if rest and mode == b"40000":
            below.setdefault(name, set()).add(rest)
        elif not rest and mode and mode not in {b"40000", b"160000"}:  # not a folder or a submodule
            found[path] = sha
    for folder, wanted in below.items():
        found |= {f"{folder}/{path}": sha for path, sha in tree_files(objects, entries[folder][1], wanted).items()}
    return found


def tags_at(git_dir: Path, common: Path, objects: Objects, commit: str) -> list[str]:
    """Tags pointing at this commit, lightweight or annotated."""
    folder = common / "refs" / "tags"
    names: set[str] = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()} \
        if folder.is_dir() else set()
    packed = common / "packed-refs"
    for line in packed.read_text(encoding="utf-8", errors="replace").splitlines() if packed.is_file() else []:
        names |= {line.partition(" refs/tags/")[2]} - {""}
    found: list[str] = []
    for name in sorted(names):
        sha = resolve(git_dir, common, f"refs/tags/{name}")
        for _ in range(5):  # an annotated tag is an object pointing at the commit (or at another tag)
            if sha is None or sha == commit:
                break
            kind, data = objects.read(sha)
            sha = data.split(b"\n", 1)[0].removeprefix(b"object ").decode("ascii", "replace") if kind == "tag" else None
        if sha == commit:
            found.append(name)
    return found


def upstream_ref(common: Path, branch: str) -> str | None:
    """The remote-tracking ref a branch pulls from (``[branch "main"] remote/merge`` in .git/config)."""
    section, remote, merge = "", "", ""
    config = common / "config"
    lines = config.read_text(encoding="utf-8", errors="replace").splitlines() if config.is_file() else []
    for raw in lines:
        line = raw.split("#")[0].split(";")[0].strip()
        if line.startswith("["):
            section = line
        elif section == f'[branch "{branch}"]':
            key, _, value = line.partition("=")
            remote = value.strip() if key.strip().lower() == "remote" else remote
            merge = value.strip() if key.strip().lower() == "merge" else merge
    if remote and remote != "." and merge.startswith("refs/heads/"):
        return f"refs/remotes/{remote}/{merge.removeprefix('refs/heads/')}"
    fallback = f"refs/remotes/origin/{branch}"
    return fallback if (common / fallback).is_file() or _packed_ref(common, fallback) else None


def clean(text: str) -> str:
    return URL_USER.sub(r"\1", text)


def reflog(git_dir: Path) -> list[Event]:
    path = git_dir / "logs" / "HEAD"
    if not path.is_file():
        return []
    events: list[Event] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = REFLOG.fullmatch(line)
        if not match or not match[4]:
            continue
        action, _, message = match[4].partition(": ")
        events.append(Event(action.split(" ")[0], clean(action), clean(message), int(match[3]), match[1], match[2]))
    return events


# --- the summary --------------------------------------------------------------------

def summary(start: Path, now: float | None = None) -> dict[str, Any] | None:
    """Everything the home page shows about git, or None if ``start`` isn't in a repository."""
    found = find_repository(start)
    if found is None:
        return None
    git_dir, common = found
    head_file = git_dir / "HEAD"
    head_text = head_file.read_text(encoding="utf-8", errors="replace").strip() if head_file.is_file() else ""
    branch = head_text.removeprefix("ref: refs/heads/") if head_text.startswith("ref: refs/heads/") else None
    head = resolve(git_dir, common, "HEAD")
    tracking = upstream_ref(common, branch) if branch else None
    upstream = resolve(git_dir, common, tracking) if tracking else None
    events = reflog(git_dir)
    pull = next((e for e in reversed(events) if e.kind == "pull"), None)
    fetched = [p.stat().st_mtime for p in (git_dir / "FETCH_HEAD", common / "FETCH_HEAD") if p.is_file()]
    shown = [e for e in reversed(events) if not (e.kind == "reset" and e.old == e.new)][:RECENT]  # skip `git stash`
    info: dict[str, Any] = {
        "branch": branch,
        "head": None,
        "upstream": tracking.removeprefix("refs/remotes/") if tracking and upstream else None,
        "ahead": None,
        "behind": None,
        "last_pull": {"time": pull.time, "action": pull.action, "message": pull.message} if pull else None,
        "last_fetch": int(max(fetched)) if fetched else None,
        "week": None,
        "activity": [{"kind": e.kind, "action": e.action, "message": e.message, "time": e.time, "commit": e.new[:7]}
                     for e in shown],
    }
    if head is None:  # a new repository with no commits yet
        return info
    made = next((e for e in reversed(events) if e.new == head and e.kind == "commit"), None)
    info["head"] = {"sha": head, "short": head[:7], "subject": made.message if made else "", "author": "",
                    "time": made.time if made else None}
    with Objects(common / "objects") as objects:
        with contextlib.suppress(*UNREADABLE):
            commit = objects.commit(head)
            info["head"].update(subject=commit.subject, author=commit.author, time=commit.authored)
        if upstream:
            with contextlib.suppress(*UNREADABLE):
                info["ahead"], info["behind"] = ahead_behind(objects, head, upstream) or (None, None)
        with contextlib.suppress(*UNREADABLE):
            info["week"] = commits_since(objects, head, (time.time() if now is None else now) - WEEK)
    return info
