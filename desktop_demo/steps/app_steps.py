"""Steps for a Windows desktop app. Which app is set by DESKTOP_APP (see conftest.py)."""

from pytest_bdd import given, parsers, then, when

from desktop_demo import uia


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
    uia.wait_until(lambda: uia.text_of(app, field) == text,
                   lambda: f"“{field}” to show “{text}” (it shows “{uia.text_of(app, field)}”)")


@then(parsers.parse('"{field}" is not empty'))
def field_not_empty(app, field):
    uia.wait_until(lambda: bool(uia.text_of(app, field)), f"“{field}” to have something in it")


@then(parsers.parse('"{name}" is ticked'))
def is_ticked(app, name):
    uia.wait_until(lambda: uia.is_ticked(app, name), f"“{name}” to be ticked")


@then(parsers.parse('I see "{text}"'))
def i_see(app, text):
    uia.wait_until(lambda: uia.shows(app, text), f"“{text}” to appear")
