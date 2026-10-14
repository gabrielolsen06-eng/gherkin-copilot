import json
import shutil
from pathlib import Path

from gherkin_copilot.cli import main
from gherkin_copilot.indexer import build_index
from gherkin_copilot.matcher import find_duplicates
from gherkin_copilot.merger import apply, plan_merges

DEMO = Path(__file__).parent.parent / "demo"

HEADER = "from pytest_bdd import given, parsers, then, when\n\n\n"


def project(tmp_path, steps, feature):
    (tmp_path / "steps.py").write_text(HEADER + steps)
    (tmp_path / "shop.feature").write_text(feature)
    return tmp_path


def demo_copy(tmp_path):
    shutil.copytree(DEMO, tmp_path / "demo", ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", ".gherkin-copilot"))
    return tmp_path / "demo"


def test_preview_writes_nothing(tmp_path):
    demo = demo_copy(tmp_path)
    before = {p: p.read_bytes() for p in demo.rglob("*") if p.is_file()}
    result = plan_merges(demo)
    assert [(p.keep.function, p.drop.function, p.ok) for p in result.plans] == [
        ("open_login_page", "user_on_login_page", True),
        ("add_to_cart", "put_in_cart", True),
    ]
    assert {p: p.read_bytes() for p in demo.rglob("*") if p.is_file()} == before


def test_apply_on_demo_rewrites_lines_and_removes_duplicates(tmp_path, capsys):
    demo = demo_copy(tmp_path)
    apply(plan_merges(demo))

    assert find_duplicates(build_index(demo)) == []
    cart = (demo / "steps" / "cart_steps.py").read_text()
    assert "def put_in_cart" not in cart and "def add_to_cart" in cart
    assert "\n\n\n\n" not in cart
    feature = (demo / "features" / "cart.feature").read_text()
    assert '    When I add "Sauce Labs Onesie" to the cart\n' in feature
    assert "put the" not in feature
    assert main(["check", str(demo / "features"), "--path", str(demo)]) == 0


def test_refuses_when_bodies_differ(tmp_path):
    root = project(tmp_path, '''@given("I am on the login page")
def a(page):
    page.goto("/")


@given("the user is on the login page")
def b(page):
    page.goto("/login")
''', "Feature: x\n  Scenario: y\n    Given the user is on the login page\n")
    result = plan_merges(root)
    assert not result.plans[0].ok
    assert "don't do the same thing" in result.plans[0].problems[0]
    assert result.changes == {}


def test_refuses_when_the_function_is_called_elsewhere(tmp_path):
    root = project(tmp_path, '''@given("I am on the login page")
def a(page):
    page.goto("/")


@given("the user is on the login page")
def b(page):
    page.goto("/")


@given("I am logged in")
def c(page):
    b(page)
''', "Feature: x\n  Scenario: y\n    Given I am on the login page\n    Given I am on the login page\n")
    plan = plan_merges(root).plans[0]
    assert (plan.keep.function, plan.drop.function) == ("a", "b")
    assert any("b is used elsewhere: steps.py:" in p for p in plan.problems)


def test_refuses_when_decorator_options_differ(tmp_path):
    root = project(tmp_path, '''@given("I am logged in", target_fixture="user")
def a(page):
    return "bob"


@given("I am now logged in")
def b(page):
    return "bob"


@when("I check out")
def c(page, user):
    pass
''', "Feature: x\n  Scenario: y\n    Given I am logged in\n")
    plan = plan_merges(root).plans[0]
    assert plan.problems == ["their decorators differ (target_fixture: 'user' vs not set)"]
    assert plan_merges(root).changes == {}


def test_same_decorator_options_still_merge(tmp_path):
    root = project(tmp_path, '''@given(parsers.parse("I am logged in as {name}", extra_types={}), target_fixture="user")
def a(page, name):
    return name


@given(parsers.parse("I am now logged in as {name}", extra_types={}), target_fixture="user")
def b(page, name):
    return name
''', "Feature: x\n  Scenario: y\n    Given I am now logged in as bob\n")
    result = plan_merges(root, keep=frozenset({"a"}))
    assert result.plans[0].ok
    apply(result)
    assert "Given I am logged in as bob" in (root / "shop.feature").read_text()


def test_refuses_when_placeholders_dont_line_up(tmp_path):
    root = project(tmp_path, '''@when(parsers.parse('I add "{product}" to the cart'))
def a(page, product):
    pass


@when(parsers.parse('I add "{item}" to my cart'))
def b(page, product):
    pass
''', "Feature: x\n")
    assert "placeholders don't line up" in plan_merges(root).plans[0].problems[0]


def test_carries_values_and_outline_parameters_across(tmp_path):
    root = project(tmp_path, '''@when(parsers.parse('I add "{product}" to the cart'))
def a(page, product):
    pass


@when(parsers.re(r'I put the "(?P<product>.+)" in my cart'))
def b(page, product):
    pass
''', '''Feature: x
  Scenario: one
    When I add "hat" to the cart

  Scenario Outline: many
    When I put the "<thing>" in my cart
    And I put the "socks" in my cart

    Examples:
      | thing |
      | shoe  |
''')
    result = plan_merges(root, keep=frozenset({"a"}))
    assert result.plans[0].ok
    apply(result)
    lines = (root / "shop.feature").read_text().splitlines()
    assert lines[5] == '    When I add "<thing>" to the cart'
    assert lines[6] == '    And I add "socks" to the cart'
    assert "def b" not in (root / "steps.py").read_text()


def test_lines_written_in_other_capitals_are_rewritten_too(tmp_path):
    root = project(tmp_path, '''@when(parsers.parse('I add "{product}" to the cart'))
def a(page, product):
    pass


@when(parsers.parse('I put the "{product}" in my cart'))
def b(page, product):
    pass
''', 'Feature: x\n  Scenario: y\n    When I add "hat" to the cart\n    And I put the "Onesie" in My Cart\n')
    apply(plan_merges(root, keep=frozenset({"a"})))
    assert (root / "shop.feature").read_text().splitlines()[-1] == '    And I add "Onesie" to the cart'
    assert main(["check", str(root / "shop.feature"), "--path", str(root)]) == 0  # used to be left behind, unbound


def test_values_converted_differently_need_merge_anyway(tmp_path):
    root = project(tmp_path, '''@when(parsers.parse("I add {count} items"))
def a(page, count):
    assert page.size() == count


@when(parsers.parse("I put {count:d} items in"))
def b(page, count):
    assert page.size() == count
''', "Feature: x\n  Scenario: y\n    When I add 3 items\n    When I put 3 items in\n")
    plan = plan_merges(root).plans[0]
    assert not plan.ok and plan.can_force  # the code is the same text, but count would be "3", not 3
    assert "{count} vs {count:d}" in plan.problems[0] or "{count:d} vs {count}" in plan.problems[0]


def test_refuses_to_rewrite_lines_into_a_regex_step(tmp_path):
    root = project(tmp_path, '''@when(parsers.parse('I add "{product}" to the cart'))
def a(page, product):
    pass


@when(parsers.re(r'I put the "(?P<product>.+)" in my cart'))
def b(page, product):
    pass
''', 'Feature: x\n  Scenario: y\n    When I add "hat" to the cart\n')
    assert "regex step" in plan_merges(root, keep=frozenset({"b"})).plans[0].problems[0]


def test_keep_overrides_the_default_choice(tmp_path):
    demo = demo_copy(tmp_path)
    plan = plan_merges(demo, keep=frozenset({"put_in_cart"})).plans[1]
    assert (plan.keep.function, plan.drop.function) == ("put_in_cart", "add_to_cart")
    assert len(plan.edits) == 3  # the other "I add ... to the cart" lines, in features and drafts


def test_cli_preview_and_json(tmp_path, capsys):
    demo = demo_copy(tmp_path)
    assert main(["merge", str(demo)]) == 0
    out = capsys.readouterr().out
    assert "-    When I put the \"Sauce Labs Onesie\" in my cart" in out
    assert "Preview only" in out

    assert main(["merge", str(demo), "--json", "--apply"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["applied"] and len(data["merges"]) == 2 and len(data["files_changed"]) == 3
    shown = data["files_changed"] + [e["file"] for m in data["merges"] for e in m["rewritten_lines"]]
    assert shown and not any("\\" in path for path in shown)  # forward slashes on Windows too
    assert main(["merge", str(demo)]) == 0
    assert "No likely duplicates" in capsys.readouterr().out


def test_features_can_live_outside_the_steps_folder(tmp_path):
    steps_dir, specs = tmp_path / "steps", tmp_path / "specs"
    steps_dir.mkdir()
    specs.mkdir()
    (steps_dir / "steps.py").write_text(HEADER + '''@given("I am on the login page")
def a(page):
    pass


@given("the user is on the login page")
def b(page):
    pass
''')
    (specs / "login.feature").write_text("Feature: x\n  Scenario: y\n    Given the user is on the login page\n"
                                        "    Given the user is on the login page\n    Given I am on the login page\n")
    assert plan_merges(steps_dir).plans[0].edits == []  # without --features the lines aren't seen

    result = plan_merges(steps_dir, features=specs)
    plan = result.plans[0]
    assert (plan.keep.function, plan.keep_uses, plan.drop_uses) == ("b", 2, 1)
    assert len(plan.edits) == 1
    assert main(["merge", str(steps_dir), "--features", str(specs), "--apply"]) == 0
    assert (specs / "login.feature").read_text().count("the user is on the login page") == 3
    assert "def a(" not in (steps_dir / "steps.py").read_text()


def test_differing_code_can_be_forced_but_other_problems_cannot(tmp_path):
    steps = '''@when(parsers.parse('I add "{product}" to the cart'))
def add(page, product):
    page.click(product)


@when(parsers.parse('I put the "{product}" in my cart'))
def put(page, product):
    page.get_by_text(product).click()
'''
    feature = 'Feature: x\n  Scenario: y\n    When I add "a" to the cart\n    And I put the "b" in my cart\n'
    plan = plan_merges(project(tmp_path, steps, feature)).plans[0]
    assert (plan.ok, plan.can_force, plan.blockers) == (False, True, [])
    assert plan.problems == plan.differences and "don't do the same thing" in plan.differences[0]
    assert [e.new for e in plan.edits] == ['    And I add "b" to the cart']  # planned, in case the user merges anyway

    (tmp_path / "helpers.py").write_text("from steps import put\n")
    plan = plan_merges(tmp_path).plans[0]
    assert not plan.can_force and any("put is used elsewhere" in b for b in plan.blockers)
