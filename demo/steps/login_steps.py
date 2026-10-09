"""Login steps (pytest-bdd + Playwright)."""

from playwright.sync_api import Page, expect
from pytest_bdd import given, parsers, then, when


PASSWORD = "secret_sauce"  # nosec B105: public practice password shown on the demo shop and saucedemo.com login pages


@given("I am on the login page")
def open_login_page(page: Page, app_url: str):
    page.goto(app_url)


@given("the user is on the login page")  # near-duplicate of open_login_page, on purpose for the dupes demo
def user_on_login_page(page: Page, app_url: str):
    page.goto(app_url)


@when(parsers.parse('I log in as "{username}" with password "{password}"'))
def log_in(page: Page, username: str, password: str):
    page.fill("#user-name", username)
    page.fill("#password", password)
    page.click("#login-button")


@given(parsers.parse('I am logged in as "{username}"'))
def logged_in_as(page: Page, app_url: str, username: str):
    page.goto(app_url)
    log_in(page, username, PASSWORD)
    expect(page.locator(".title")).to_have_text("Products")


@then("I should see the products page")
def products_page(page: Page):
    expect(page.locator(".title")).to_have_text("Products")


@then(parsers.parse('I should see the error "{message}"'))
def error_message(page: Page, message: str):
    expect(page.locator("[data-test=error]")).to_contain_text(message)
