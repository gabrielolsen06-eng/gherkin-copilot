import json
from pathlib import Path

from gherkin_copilot.cli import main

EXAMPLE = str(Path(__file__).parent.parent / "demo")


def test_index_saves_and_find_uses_it(tmp_path, capsys):
    index = tmp_path / "idx.json"
    assert main(["index", EXAMPLE, "--index", str(index)]) == 0
    assert len(json.loads(index.read_text())["steps"]) == 15

    capsys.readouterr()
    assert main(["find", "Then I see the products page", "--index", str(index)]) == 0
    assert "✓ Then I see the products page" in capsys.readouterr().out


def test_find_exit_code_is_1_when_nothing_binds(capsys):
    assert main(["find", "Given I open the login page", "--path", EXAMPLE]) == 1
    out = capsys.readouterr().out
    assert "Did you mean" in out and "I am on the login page" in out


def test_dupes_json(capsys):
    assert main(["dupes", "--path", EXAMPLE, "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2
