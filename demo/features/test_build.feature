Feature: test_build

  Scenario: test_build
    Given I am on the login page
    When I log in as "standard_user" with password "secret_sauce"
    Then I see the products page
    When I put the "Sauce Labs Backpack" in my cart
    Then the cart badge shows 1
    When I press the cart icon
