import json
import os
import shutil
import subprocess  # nosec B404: builds throwaway git repositories to check the reader against git itself

import pytest
from test_ui import DEMO, request

from gherkin_copilot.ui import gitinfo, history, home
from gherkin_copilot.ui.projects import Project, ProjectStore
from gherkin_copilot.ui.server import start_in_background

FEATURE = """@shop
Feature: Shop
  Background:
    Given I am logged in

  @smoke
  Scenario: Add one
    When I add "Onesie" to the cart
    Then the cart has 1 item

  Scenario Outline: Add twice
    When I add "<product>" to the cart
    And I add "<product>" to the cart
    Then nothing has been written for this
    Examples:
      | product |
      | Onesie  |
      | Jacket  |
"""
STEPS = """from pytest_bdd import given, parsers, then, when


@given("I am logged in")
def logged_in():
    pass


@when(parsers.parse('I add "{product}" to the cart'))
def add(product):
    pass


@then(parsers.parse("the cart has {n:d} item"))
def cart_has(n):
    pass


@then("this step is never used")
def never_used():
    pass
"""
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git isn't installed")


@pytest.fixture
def project(tmp_path):
    (tmp_path / "features").mkdir()
    (tmp_path / "steps").mkdir()
    (tmp_path / "features" / "shop.feature").write_text(FEATURE)
    (tmp_path / "steps" / "shop_steps.py").write_text(STEPS)
    return Project("p", "P", str(tmp_path / "steps"), str(tmp_path / "features"))


def test_statistics_count_scenarios_steps_reuse_and_tags(project):
    stats = home.statistics(project)
    assert (stats["feature_files"], stats["scenarios"], stats["outlines"], stats["example_rows"]) == (1, 2, 1, 2)
    assert (stats["feature_lines"], stats["definitions"], stats["used"], stats["missing"]) == (6, 4, 3, 1)
    assert [(s["function"], s["uses"]) for s in stats["top"]] == [("add", 3), ("logged_in", 1), ("cart_has", 1)]
    assert [s["function"] for s in stats["unused"]] == ["never_used"] and stats["unused_count"] == 1
    assert stats["unused"][0]["open_url"].startswith("vscode://file")
    assert stats["tags"] == [{"tag": "@shop", "scenarios": 2}, {"tag": "@smoke", "scenarios": 1}]


def test_recent_changes_count_people_and_leave_out_diffs(project):
    for who in ("Ada", "Ada", "Grace"):
        history.record(project, "edit", who, {"shop.feature": ("a\n", "b\n")}, scenario="Add one", file="shop.feature")
    changes = home.recent_changes(project)
    assert (changes["changes"], changes["authors"]) == (3, 2)
    assert changes["recent"][0]["author"] == "Grace" and "diff" not in changes["recent"][0]


def test_home_endpoint(tmp_path):
    server, app, _ = start_in_background(ProjectStore(tmp_path / "projects.json"))
    try:
        port = server.server_address[1]
        request(port, "POST", "/api/projects/add", {"name": "Demo", "steps": str(DEMO)}, app.token)
        status, _, data, _ = request(port, "GET", "/api/home?project=demo")
        body = json.loads(data)
        assert status == 200 and body["stats"]["scenarios"] > 0 and "recent" in body["history"]
        assert body["git"] is None or "branch" in body["git"]  # this repository, or none in a source download
        assert request(port, "GET", "/api/home?project=nope")[0] == 404
    finally:
        server.shutdown()
        server.server_close()


# --- git ---------------------------------------------------------------------------

@pytest.fixture
def git(tmp_path):
    config = tmp_path / "gitconfig"
    config.write_text("[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n[pull]\n\trebase = false\n")

    def run(cwd, *args, who="Ada", raw=False, date=None):
        # a git hook (e.g. pre-commit running these tests) exports GIT_INDEX_FILE and friends for its own repository
        clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env = {**clean, "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_AUTHOR_NAME": who, "GIT_AUTHOR_EMAIL": f"{who.lower()}@example.com",
               "GIT_COMMITTER_NAME": who, "GIT_COMMITTER_EMAIL": f"{who.lower()}@example.com"}
        if date:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
        out = subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True).stdout  # nosec B603 B607
        return out if raw else out.decode().strip()
    return run


def commit(git, repo, message, who="Ada", file="notes.txt", line=None):
    path = repo / file
    lines = path.read_text().splitlines() if path.exists() else [f"line {i}" for i in range(300)]
    lines[len(lines) // 2] = line or message  # similar versions, so git stores them as deltas
    path.write_text("\n".join(lines) + "\n")
    git(repo, "add", file, who=who)
    git(repo, "commit", "-q", "-m", message, who=who)


@pytest.fixture
def repos(tmp_path, git):
    """``shared`` with three commits, and ``copy``, a clone one commit ahead and two behind after a fetch."""
    shared, copy = tmp_path / "shared", tmp_path / "copy"
    shared.mkdir()
    git(shared, "init", "-q")
    for n in range(3):
        commit(git, shared, f"Shared {n}")
    git(tmp_path, "clone", "-q", str(shared), str(copy))
    commit(git, copy, "Mine", who="Grace", file="mine.txt")
    commit(git, shared, "Theirs 1")
    commit(git, shared, "Theirs 2")
    git(copy, "fetch", "-q")
    return shared, copy


@needs_git
def test_summary_matches_git_for_loose_and_packed_objects(repos, git):
    _, copy = repos
    for pack in (False, True):
        if pack:
            git(copy, "gc", "-q", "--aggressive")
            assert not list((copy / ".git" / "objects").glob("??/*"))  # everything is in a pack now
        (copy / "tests").mkdir(exist_ok=True)
        info = gitinfo.summary(copy / "tests" if pack else copy)  # also found from a subfolder
        assert info["branch"] == "main" and info["upstream"] == "origin/main"
        assert info["head"]["sha"] == git(copy, "rev-parse", "HEAD")
        assert (info["head"]["subject"], info["head"]["author"]) == ("Mine", "Grace")
        assert (info["ahead"], info["behind"]) == (1, 2)
        assert info["activity"][0]["kind"] == "commit" and info["activity"][-1]["kind"] == "clone"
        assert info["last_pull"] is None and info["last_fetch"] is not None
        assert info["week"] == {"commits": 4, "authors": 2, "capped": False}


@needs_git
def test_a_pull_is_recorded_and_brings_the_copy_up_to_date(repos, git):
    _, copy = repos
    git(copy, "pull", "-q", "--no-edit", who="Grace")
    info = gitinfo.summary(copy)
    assert info["last_pull"]["action"].startswith("pull") and info["activity"][0]["kind"] == "pull"
    assert (info["ahead"], info["behind"]) == (2, 0)  # Mine and the merge commit
    assert info["head"]["subject"].startswith("Merge branch")


@needs_git
def test_ahead_and_behind_match_git_when_commits_share_a_timestamp(tmp_path, git):
    """A rebase or a script makes many commits in the same second, so time alone can't order them."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")

    def make(message):
        git(repo, "commit", "-q", "--allow-empty", "-m", message, date="1700000000 +0000")
        return git(repo, "rev-parse", "HEAD")

    tips = [make("base"), make("main 1")]
    git(repo, "switch", "-q", "-c", "a", tips[0])
    tips += [make("a 1"), make("a 2")]
    git(repo, "switch", "-q", "main")
    tips.append(make("main 2"))
    git(repo, "merge", "-q", "--no-ff", "-m", "merge a", "a", date="1700000000 +0000")
    tips += [git(repo, "rev-parse", "HEAD"), make("main 3")]
    git(repo, "switch", "-q", "a")
    tips.append(make("a 3"))
    with gitinfo.Objects(repo / ".git" / "objects") as objects:
        for local in tips:
            for remote in tips:
                counts = git(repo, "rev-list", "--left-right", "--count", f"{local}...{remote}").split()
                assert gitinfo.ahead_behind(objects, local, remote) == tuple(map(int, counts)), (local, remote)


@needs_git
def test_every_object_in_a_pack_reads_back_exactly(repos, git):
    _, copy = repos
    git(copy, "gc", "-q", "--aggressive")
    listing = git(copy, "verify-pack", "-v", *map(str, (copy / ".git" / "objects" / "pack").glob("*.idx")))
    assert any(len(row.split()) >= 7 for row in listing.splitlines())  # some objects are deltas
    with gitinfo.Objects(copy / ".git" / "objects") as objects:
        for line in git(copy, "rev-list", "--all", "--objects").splitlines():
            sha = line.split()[0]
            kind, data = objects.read(sha)
            assert data == git(copy, "cat-file", kind, sha, raw=True)


@needs_git
def test_a_linked_worktree_reads_its_own_branch(repos, git, tmp_path):
    shared, _ = repos
    git(shared, "worktree", "add", "-q", "-b", "feature", str(tmp_path / "wt"))
    info = gitinfo.summary(tmp_path / "wt")
    assert info["branch"] == "feature" and info["head"]["subject"] == "Theirs 2"


@needs_git
def test_a_damaged_object_falls_back_to_the_reflog(repos, git):
    _, copy = repos
    sha = git(copy, "rev-parse", "HEAD")
    path = copy / ".git" / "objects" / sha[:2] / sha[2:]
    path.chmod(0o644)
    path.write_bytes(b"not zlib")
    info = gitinfo.summary(copy)
    assert info["head"]["subject"] == "Mine" and info["head"]["author"] == ""
    assert info["ahead"] is None


def test_no_repository(tmp_path):
    assert gitinfo.find_repository(tmp_path) is None


def test_reflog_hides_credentials_and_skips_stash_resets(tmp_path):
    logs = tmp_path / ".git" / "logs"
    logs.mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    a, b = "a" * 40, "b" * 40
    (logs / "HEAD").write_text(
        f"{'0' * 40} {a} Ada <ada@example.com> 1700000000 +0000\tclone: from https://ada:s3cret@git.example.com/shop.git\n"
        f"{a} {a} Ada <ada@example.com> 1700000100 +0000\treset: moving to HEAD\n"
        f"{a} {b} Ada <ada@example.com> 1700000200 -0130\tpull origin main: Fast-forward\n")
    info = gitinfo.summary(tmp_path)
    assert [e["kind"] for e in info["activity"]] == ["pull", "clone"]
    assert info["activity"][1]["message"] == "from https://git.example.com/shop.git"
    assert "s3cret" not in json.dumps(info)
    assert info["last_pull"] == {"time": 1700000200, "action": "pull origin main", "message": "Fast-forward"}


def test_damaged_deltas_are_refused():
    with pytest.raises(ValueError):
        gitinfo.apply_delta(b"abc", bytes([3, 3, 0]))  # opcode 0 is reserved
    with pytest.raises(ValueError):
        gitinfo.apply_delta(b"abc", bytes([4, 3, 0x91, 0, 3]))  # base size doesn't match
    assert gitinfo.apply_delta(b"hello", bytes([5, 8, 0x90, 5, 3]) + b"!!!") == b"hello!!!"
