"""Steps for a Windows desktop app. Which app: desktop_app in pytest.ini (or the DESKTOP_APP variable)."""

import uia
from pytest_bdd import given, parsers, then, when


@given("the app is open", target_fixture="app")
def app_is_open(desktop_apps, desktop_config):
    app = uia.start(**desktop_config)
    desktop_apps.append(app)  # closed after the scenario, even if it fails
    return app


@when(parsers.parse('I click "{name}"'))
def click(app, name):
    uia.click(app, name)


@when(parsers.parse('I tick "{name}"'))
def tick(app, name):
    uia.tick(app, name)


@when(parsers.parse('I type "{text}" into "{field}"'))
def type_into(app, text, field):
    uia.type_into(app, field, text)


@then(parsers.parse('"{field}" shows "{text}"'))
def field_shows(app, field, text):
    uia.expect_text(app, field, text)


@then(parsers.parse('"{field}" is not empty'))
def field_not_empty(app, field):
    uia.expect_not_empty(app, field)


@then(parsers.parse('"{name}" is ticked'))
def is_ticked(app, name):
    uia.expect_ticked(app, name)


@then(parsers.parse('I see "{text}"'))
def i_see(app, text):
    uia.expect_visible(app, text)
