import ast
import json
from pathlib import Path

from gherkin_copilot.checker import FeatureStep, check_steps, make_stub, parse_feature
from gherkin_copilot.cli import main
from gherkin_copilot.indexer import StepDef, index_file
from gherkin_copilot.matcher import is_exact_match

DEMO = Path(__file__).parent.parent / "demo"

FEATURE = '''Feature: Shop
  # a comment with Given in it
  Background:
    Given I am logged in

  @tag
  Scenario: Buy
    When I add 2 items
    And I pay "visa"
      | card | 4111 |
    But I don't get a receipt
    Then I see "thanks"
      """
      Then this docstring line is not a step
      """

  Scenario Outline: Login
    Given I log in as "<user>"
    Then I see "<greeting>"

    Examples:
      | user  | greeting |
      | alice | hi alice |
      | bob   | hi bob   |
'''


def defn(keyword, pattern, kind="parse"):
    return StepDef(keyword, pattern, kind, "fn", "steps.py", 1)


def test_parse_feature(tmp_path):
    path = tmp_path / "shop.feature"
    path.write_text(FEATURE)
    steps = parse_feature(path)
    assert [(s.keyword, s.text, s.line) for s in steps] == [
        ("given", "I am logged in", 4),
        ("when", "I add 2 items", 8),
        ("when", 'I pay "visa"', 9),
        ("when", "I don't get a receipt", 11),
        ("then", 'I see "thanks"', 12),
        ("given", 'I log in as "alice"', 18),  # filled from the first Examples row
        ("then", 'I see "hi alice"', 19),
    ]


def test_check_is_keyword_aware_and_flags_ambiguity(tmp_path):
    path = tmp_path / "shop.feature"
    path.write_text(FEATURE)
    definitions = [
        defn("given", "I am logged in", "string"),
        defn("when", "I add {n:d} items"),
        defn("then", "I add {n:d} items"),  # wrong keyword: must not bind to a When line
        defn("then", 'I see "{text}"'),
        defn("step", 'I see "{message}"'),  # behave-style @step binds to any keyword
    ]
    results = {r.step.line: r for r in check_steps(parse_feature(path), definitions)}
    assert results[4].status == "ok"
    assert results[8].status == "ok" and results[8].bound[0].keyword == "when"
    assert results[9].status == "missing"
    assert results[12].status == "ambiguous"


def test_stubs_are_valid_python_with_parameters():
    stub = make_stub(FeatureStep("when", 'I enter postcode "90210" and 2 gift cards', "", "f", 1))
    assert "parsers.parse('I enter postcode \"{text1}\" and {number2:d} gift cards')" in stub
    assert "def i_enter_postcode_and_gift_cards(page, text1, number2):" in stub
    ast.parse(stub)
    ast.parse(make_stub(FeatureStep("given", "the price is {weird} 1.5", "", "f", 1)))


def test_demo_features_are_fully_bound(capsys):
    assert main(["check", str(DEMO / "features"), "--path", str(DEMO)]) == 0
    out = capsys.readouterr().out
    assert "0 missing, 0 ambiguous" in out and "look like passwords" not in out  # secret_sauce and wrong are allowed


def test_check_warns_about_passwords_without_failing(tmp_path, capsys):
    (tmp_path / "login.feature").write_text(
        'Feature: x\n  Scenario Outline: y\n    Given I am on the login page\n'
        '    When I log in as "bob" with password "hunter2"\n'
        '    When I log in as "<user>" with password "<password>"\n\n'
        '    Examples:\n      | user | Password |\n      # a comment\n      | bob  | hunter2  |\n      | ann  | wrong    |\n'
        '\n    Examples:\n      | user | result |\n      | bob  | ok     |\n')
    (tmp_path / "steps.py").write_text(
        'from pytest_bdd import given, parsers, when\n\n'
        'ADMIN_PASSWORD = "hunter2"\nPASSWORD_FIELD = "#password"\nLOGIN = {"password": "s3cret"}\n'
        'PRACTICE_PASSWORD = "secret_sauce"\nOTHER_PASSWORD = "x"  # nosec B105: not a real one\n\n\n'
        '@given("I am on the login page")\ndef login_page(page):\n    page.goto("/")\n\n\n'
        '@when(parsers.parse(\'I log in as "{user}" with password "{password}"\'))\n'
        'def log_in(page, user, password):\n    page.fill("#password", password)\n')
    assert main(["check", str(tmp_path), "--path", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    warnings = out.split("look like passwords")[1]
    assert "hunter2" not in warnings and "s3cret" not in out  # the warning doesn't copy them into a CI log
    assert warnings.splitlines()[1:] == [
        '  ! login.feature:4  When I log in as "bob" with password "***"',
        "  ! login.feature:10  | bob | *** |",
        '  ! steps.py:3  ADMIN_PASSWORD = "***"',
        '  ! steps.py:5  LOGIN = {"password": "***"}',
    ]
    assert main(["check", str(tmp_path), "--path", str(tmp_path), "--json"]) == 0
    found = [e for e in json.loads(capsys.readouterr().out) if e["status"] == "password"]
    assert [(e["file"], e["line"]) for e in found] == [("login.feature", 4), ("login.feature", 10), ("steps.py", 3),
                                                       ("steps.py", 5)]


def test_check_fails_on_lines_pytest_bdd_would_reject(tmp_path, capsys):
    feature = tmp_path / "typo.feature"
    feature.write_text("Feature: x\n  Scenario: y\n    Given I am on the login page\n    Whne I sign in\n")
    assert main(["check", str(feature), "--path", str(DEMO)]) == 1  # used to pass: the line was skipped
    assert "typo.feature:4  Whne I sign in" in capsys.readouterr().out
    assert main(["check", str(feature), "--path", str(DEMO), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)[-1] == {"file": "typo.feature", "line": 4, "step": "Whne I sign in",
                                                       "status": "invalid", "bound_to": []}


def test_check_reports_missing_with_stubs(tmp_path, capsys):
    feature = tmp_path / "new.feature"
    feature.write_text('Feature: x\n  Scenario: y\n    Given I am on the login page\n    When I go to checkout\n')
    assert main(["check", str(feature), "--path", str(DEMO), "--stubs"]) == 1
    out = capsys.readouterr().out
    assert "✗   4  When I go to checkout" in out
    assert "def i_go_to_checkout(page):" in out


def test_stub_for_a_line_with_braces_binds(tmp_path):
    stub = make_stub(FeatureStep("given", "the request body is {}", "", "f", 1))
    assert "@given('the request body is {}')" in stub  # used to be {{}}, which a plain string never matches
    path = tmp_path / "steps.py"
    path.write_text("from pytest_bdd import given\n\n\n" + stub)
    assert is_exact_match("Given the request body is {}", index_file(path)[0])


def test_lines_bound_to_unwritten_stubs_fail_check(tmp_path, capsys):
    feature = tmp_path / "pay.feature"
    feature.write_text("Feature: x\n  Scenario: y\n    Given I pay by card\n")
    stub = make_stub(FeatureStep("given", "I pay by card", "", "f", 3))
    (tmp_path / "steps.py").write_text("from pytest_bdd import given\n\n\n" + stub)
    assert main(["check", str(feature), "--path", str(tmp_path)]) == 1  # used to pass: the stub exists
    assert "(not written yet)" in capsys.readouterr().out
    assert main(["check", str(feature), "--path", str(tmp_path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)[0]["status"] == "unwritten"


def test_check_steps_can_skip_suggestions():
    line = FeatureStep("given", "I am on the logn page", "", "f", 1)
    steps = [defn("given", "I am on the login page", "string")]
    assert check_steps([line], steps)[0].suggestions
    assert not check_steps([line], steps, suggest=False)[0].suggestions  # the Scenarios list doesn't need them
