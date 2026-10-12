import shutil
from pathlib import Path

import pytest

from gherkin_copilot.indexer import build_index, index_file, load_index, save_index

EXAMPLE = Path(__file__).parent.parent / "demo"


def write(tmp_path, source):
    path = tmp_path / "steps.py"
    path.write_text(source)
    return path


def test_reads_all_decorator_styles(tmp_path):
    path = write(tmp_path, '''
from pytest_bdd import given, when, then, parsers
import pytest_bdd

@given("I am on the home page")
def a(): ...

@when(parsers.parse("I add {count:d} items"))
def b(): ...

@then(parsers.re(r"the total is (?P<total>\\d+)"))
def c(): ...

@pytest_bdd.given(parsers.cfparse("I am {name}"))
def d(): ...

@then("the payload is {}")
def e(): ...
''')
    steps = index_file(path)
    assert [(s.keyword, s.kind, s.function) for s in steps] == [
        ("given", "string", "a"),
        ("when", "parse", "b"),
        ("then", "regex", "c"),
        ("given", "parse", "d"),
        ("then", "string", "e"),  # pytest-bdd matches a plain string literally, braces included
    ]
    assert steps[1].pattern == "I add {count:d} items"
    assert steps[1].line == 8


def test_behave_plain_strings_are_parse_patterns(tmp_path):
    path = write(tmp_path, '''
from behave import step

@step("I wait {seconds} seconds")
def e(context, seconds): ...
''')
    assert [(s.keyword, s.kind) for s in index_file(path)] == [("step", "parse")]


def test_projects_inside_a_build_folder_are_scanned(tmp_path):
    project = tmp_path / "build" / "my-project"
    shutil.copytree(EXAMPLE / "steps", project / "steps")
    (project / "steps" / "build").mkdir()
    (project / "steps" / "build" / "generated.py").write_text("from pytest_bdd import given\n@given('x')\ndef x(): ...\n")
    steps = build_index(project)
    assert len(steps) == 15 and not any("generated" in s.file for s in steps)  # build/ *inside* the project is skipped


def test_ignores_unrelated_decorators_and_bad_files(tmp_path):
    path = write(tmp_path, '''
import pytest

@pytest.fixture
def page(): ...

@pytest.mark.parametrize("x", [1])
def test_x(x): ...

@given(SOME_CONSTANT)
def dynamic(): ...
''')
    assert index_file(path) == []
    assert index_file(write(tmp_path, "def broken(:")) == []


def test_example_project_and_round_trip(tmp_path):
    steps = build_index(EXAMPLE)
    assert len(steps) == 15
    assert all(s.file.startswith("steps/") for s in steps)
    out = tmp_path / "index.json"
    save_index(steps, out, EXAMPLE)
    index = load_index(out)
    assert index.steps == steps and index.root == EXAMPLE.resolve() and index.created > 0


def test_older_index_files_still_load_and_bad_ones_say_so(tmp_path):
    old = tmp_path / "old.json"
    old.write_text('[{"keyword": "given", "pattern": "x", "kind": "string", "function": "f", "file": "s.py", "line": 1}]')
    assert load_index(old).root is None and len(load_index(old).steps) == 1
    for text in ["not json", "{}", '{"steps": [{"nope": 1}]}']:
        old.write_text(text)
        with pytest.raises(ValueError, match="isn't a valid index file"):
            load_index(old)
