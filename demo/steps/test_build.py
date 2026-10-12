"""Steps built with gherkin-copilot's action blocks."""

from pytest_bdd import when


@when('I press the cart icon')
def i_press_the_cart_icon(page):
    page.get_by_role("link", name='cart').click()
