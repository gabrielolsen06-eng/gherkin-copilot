Feature: Checkout (draft)
  A scenario someone has just written, before anyone has coded its steps.
  `gherkin-copilot check demo/drafts --stubs` shows which steps already exist
  and drafts code for the rest.

  Scenario: Check out one product
    Given I am logged in as "standard_user"
    When I add "Sauce Labs Backpack" to the cart
    And I open the cart page
    And I go to checkout
    And I enter my details "Ada" "Lovelace" "90210"
    Then the order total is "$29.99"
