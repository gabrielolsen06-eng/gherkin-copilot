import json
from pathlib import Path

from gherkin_copilot.cli import main
from gherkin_copilot.report import attach_artifacts, load_report, render_html

REPORT = [
    {
        "keyword": "Feature", "uri": "features/cart.feature", "name": "Cart", "description": "Cart things",
        "elements": [
            {
                "keyword": "Scenario", "id": "test_add_two_products", "name": "Add two products", "line": 3,
                "type": "scenario", "tags": [{"name": "@smoke"}],
                "steps": [
                    {"keyword": "Given ", "name": "I am logged in", "line": 4, "result": {"status": "passed", "duration": 1_500_000_000}},
                    {"keyword": "Then ", "name": "the badge shows <script>2</script>", "line": 5,
                     "result": {"status": "failed", "duration": 10, "error_message": "noise\nE   AssertionError: expected 3\nmore noise"}},
                    {"keyword": "And ", "name": "the cart has 2 items", "line": 6, "result": {"status": "failed", "duration": 0, "error_message": ""}},
                ],
            },
            {
                "keyword": "Scenario Outline", "id": "test_login[bob-wrong]", "name": "Login", "line": 9, "type": "scenario",
                "steps": [{"keyword": "Given", "name": "I log in", "line": 10, "result": {"status": "passed", "duration": 2}}],
            },
        ],
    }
]


def write(tmp_path):
    path = tmp_path / "cucumber.json"
    path.write_text(json.dumps(REPORT))
    return path


def test_load_report(tmp_path):
    [feature] = load_report(write(tmp_path))
    add, login = feature.scenarios
    assert add.status == "failed" and login.status == "passed" and feature.status == "failed"
    assert [s.status for s in add.steps] == ["passed", "failed", "skipped"]  # step after the failure never ran
    assert add.steps[0].seconds == 1.5 and add.tags == ["@smoke"]
    assert login.example == "bob-wrong"


def test_html_is_escaped_and_failures_are_open(tmp_path):
    html = render_html(load_report(write(tmp_path)), tmp_path, "cucumber.json")
    assert "<script>2</script>" not in html and "&lt;script&gt;2&lt;/script&gt;" in html
    assert "<script" not in html.lower()  # the page itself has no scripts at all
    assert "AssertionError: expected 3" in html
    assert '<details class="scenario failed" open>' in html
    assert '<details class="scenario passed">' in html
    assert ">50%<" in html  # pass rate tile


def test_artifacts_are_matched_to_scenarios(tmp_path):
    features = load_report(write(tmp_path))
    art = tmp_path / "artifacts"
    for folder in ["tests-test-features-py-test-add-two-products", "tests-test-features-py-test-login-bob-wrong-chromium",
                   "tests-test-features-py-test-add-two-products-again"]:
        (art / folder).mkdir(parents=True)
        for name in ["test-finished-1.png", "video.webm", "trace.zip", "notes.txt"]:
            (art / folder / name).touch()
    attach_artifacts(features, art)
    add, login = features[0].scenarios
    assert {k: [p.parent.name for p in v] for k, v in add.artifacts.items()} == {
        "screenshots": ["tests-test-features-py-test-add-two-products"],
        "videos": ["tests-test-features-py-test-add-two-products"],
        "traces": ["tests-test-features-py-test-add-two-products"],
    }
    assert login.artifacts["videos"][0].parent.name.endswith("bob-wrong-chromium")
    html = render_html(features, tmp_path, "cucumber.json")
    assert 'src="artifacts/tests-test-features-py-test-add-two-products/video.webm"' in html
    assert "playwright show-trace" in html


def test_cli_report(tmp_path, capsys):
    out = tmp_path / "out" / "report.html"
    assert main(["report", str(write(tmp_path)), "-o", str(out), "--artifacts", str(tmp_path / "none")]) == 1
    assert out.is_file() and "1/2 scenarios passed" in capsys.readouterr().out
    assert main(["report", str(tmp_path / "missing.json")]) == 2
