from pathlib import Path

import pytest

from gherkin_copilot.indexer import StepDef, build_index
from gherkin_copilot.matcher import find, find_duplicates, is_exact_match, normalize_pattern, normalize_text, strip_keyword

EXAMPLE = Path(__file__).parent.parent / "demo"


def step(pattern, kind="parse", keyword="given"):
    return StepDef(keyword, pattern, kind, "fn", "steps.py", 1)


@pytest.mark.parametrize("text", ["Given I am here", "  And I am here", "* I am here", "I am here"])
def test_strip_keyword(text):
    assert strip_keyword(text) == "I am here"


@pytest.mark.parametrize(
    "definition, text, expected",
    [
        (step("I am on the login page", "string"), "Given I am on the login page", True),
        (step("I am on the login page", "string"), "Given I am on the login pages", False),
        (step('I log in as "{user}"'), 'When I log in as "bob"', True),
        (step("the badge shows {count:d}"), "Then the badge shows 3", True),
        (step("the badge shows {count:d}"), "Then the badge shows three", False),
        (step(r"^the cart has (?P<n>\d+) items?$", "regex"), "And the cart has 1 item", True),
        (step(r"the cart has (?P<n>\d+) items?", "regex"), "And the cart has many items", False),
        (step("price is (1+", "regex"), "price is (1+", False),  # invalid regex never matches
    ],
)
def test_exact_match(definition, text, expected):
    assert is_exact_match(text, definition) is expected


def test_normalizing_puts_patterns_and_text_on_the_same_footing():
    assert normalize_pattern(step('I log in as "{user}" with {n:d} tries')) == "log as <arg> with <arg> tries"
    assert normalize_text('When I log in as "bob" with 3 tries') == "log as <arg> with <arg> tries"
    assert normalize_pattern(step(r"^the cart has (?P<n>\d+) items?$", "regex")) == "cart has <arg> items"


def test_find_on_example_project():
    steps = build_index(EXAMPLE)

    exact = find('When I add "Sauce Labs Onesie" to the cart', steps)
    assert exact[0].exact and exact[0].step.function == "add_to_cart"

    suggestions = find("Given I open the login page", steps)
    assert not any(m.exact for m in suggestions)
    assert suggestions[0].step.function == "open_login_page"

    assert find("Then the moon is made of cheese", steps) == []


def test_duplicates_on_example_project():
    pairs = find_duplicates(build_index(EXAMPLE))
    found = {frozenset((p.first.function, p.second.function)) for p in pairs}
    assert found == {
        frozenset(("open_login_page", "user_on_login_page")),
        frozenset(("add_to_cart", "put_in_cart")),
    }
