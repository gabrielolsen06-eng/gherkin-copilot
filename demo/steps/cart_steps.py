"""Cart steps (pytest-bdd + Playwright)."""

from playwright.sync_api import Page, expect
from pytest_bdd import parsers, then, when


def product_button(page: Page, product: str):
    return page.locator(".inventory_item", has_text=product).locator("button")


@when(parsers.parse('I add "{product}" to the cart'))
def add_to_cart(page: Page, product):
    product_button(page, product).click()


@when(parsers.parse('I put the "{product}" in my cart'))  # near-duplicate of add_to_cart, on purpose for the dupes demo
def put_in_cart(page: Page, product):
    product_button(page, product).click()


@when(parsers.parse('I remove "{product}" from the cart'))
def remove_from_cart(page: Page, product):
    button = product_button(page, product)
    expect(button).to_have_text("Remove")
    button.click()


@then(parsers.parse("the cart badge shows {count:d}"))
def cart_badge(page: Page, count):
    expect(page.locator(".shopping_cart_badge")).to_have_text(str(count))


@then("the cart badge is hidden")
def cart_badge_hidden(page: Page):
    expect(page.locator(".shopping_cart_badge")).to_be_hidden()


@then(parsers.re(r"the cart should contain (?P<count>\d+) items?"))
def cart_contains(page: Page, count):
    page.click(".shopping_cart_link")
    expect(page.locator(".cart_item")).to_have_count(int(count))
