Feature: Cart
  As a shopper I want to manage my cart before checking out.

  Background:
    Given I am logged in as "standard_user"

  Scenario: Add two products
    When I add "Sauce Labs Backpack" to the cart
    And I add "Sauce Labs Bike Light" to the cart
    Then the cart badge shows 2
    And the cart should contain 2 items

  Scenario: Remove a product again
    When I add "Sauce Labs Onesie" to the cart
    And I remove "Sauce Labs Onesie" from the cart
    Then the cart badge is hidden
