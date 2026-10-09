from pathlib import Path

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

@step("I wait {seconds} seconds")
def e(): ...
''')
    steps = index_file(path)
    assert [(s.keyword, s.kind, s.function) for s in steps] == [
        ("given", "string", "a"),
        ("when", "parse", "b"),
        ("then", "regex", "c"),
        ("given", "parse", "d"),
        ("step", "parse", "e"),
    ]
    assert steps[1].pattern == "I add {count:d} items"
    assert steps[1].line == 8


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
    assert len(steps) == 12
    assert all(s.file.startswith("steps/") for s in steps)
    out = tmp_path / "index.json"
    save_index(steps, out)
    assert load_index(out) == steps
