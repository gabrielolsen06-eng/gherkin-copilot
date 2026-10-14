"""Steps for a Windows desktop app. Which app: desktop_app in pytest.ini (or the DESKTOP_APP variable)."""

import uia
from pytest_bdd import given, parsers, then, when


@given("the app is open", target_fixture="app")
def app_is_open(desktop_apps: list[uia.App], desktop_config: dict[str, str]) -> uia.App:
    app = uia.start(**desktop_config)
    desktop_apps.append(app)  # closed after the scenario, even if it fails
    return app


@when(parsers.parse('I click "{name}"'))
def click(app: uia.App, name: str):
    uia.click(app, name)


@when(parsers.parse('I tick "{name}"'))
def tick(app: uia.App, name: str):
    uia.tick(app, name)


@when(parsers.parse('I type "{text}" into "{field}"'))
def type_into(app: uia.App, text: str, field: str):
    uia.type_into(app, field, text)


@then(parsers.parse('"{field}" shows "{text}"'))
def field_shows(app: uia.App, field: str, text: str):
    uia.expect_text(app, field, text)


@then(parsers.parse('"{field}" is not empty'))
def field_not_empty(app: uia.App, field: str):
    uia.expect_not_empty(app, field)


@then(parsers.parse('"{name}" is ticked'))
def is_ticked(app: uia.App, name: str):
    uia.expect_ticked(app, name)


@then(parsers.parse('I see "{text}"'))
def i_see(app: uia.App, text: str):
    uia.expect_visible(app, text)
