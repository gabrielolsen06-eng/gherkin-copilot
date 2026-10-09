import ast
from pathlib import Path

from gherkin_copilot.checker import check_steps, make_stub, parse_feature
from gherkin_copilot.cli import main
from gherkin_copilot.indexer import StepDef, build_index

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
    from gherkin_copilot.checker import FeatureStep

    stub = make_stub(FeatureStep("when", 'I enter postcode "90210" and 2 gift cards', "", "f", 1))
    assert "parsers.parse('I enter postcode \"{text1}\" and {number2:d} gift cards')" in stub
    assert "def i_enter_postcode_and_gift_cards(page, text1, number2):" in stub
    ast.parse(stub)
    ast.parse(make_stub(FeatureStep("given", "the price is {weird} 1.5", "", "f", 1)))


def test_demo_features_are_fully_bound(capsys):
    assert main(["check", str(DEMO / "features"), "--path", str(DEMO)]) == 0
    assert "0 missing, 0 ambiguous" in capsys.readouterr().out


def test_check_reports_missing_with_stubs(tmp_path, capsys):
    feature = tmp_path / "new.feature"
    feature.write_text('Feature: x\n  Scenario: y\n    Given I am on the login page\n    When I go to checkout\n')
    assert main(["check", str(feature), "--path", str(DEMO), "--stubs"]) == 1
    out = capsys.readouterr().out
    assert "✗   4  When I go to checkout" in out
    assert "def i_go_to_checkout(page):" in out
