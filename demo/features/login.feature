Feature: Login
  As a shopper I want to sign in so that I can buy products.

  # Declarative: says what the customer does, not which fields to fill in
  Scenario: A registered customer signs in
    Given I am a registered customer
    When I sign in
    Then I see the products page

  # Imperative: spells out the exact values, useful when the values are the point of the test
  Scenario: Standard user logs in
    Given I am on the login page
    When I log in as "standard_user" with password "secret_sauce"
    Then I see the products page

  Scenario Outline: Rejected logins show a helpful error
    Given I am on the login page
    When I log in as "<username>" with password "<password>"
    Then I see the error "<error>"

    Examples:
      | username        | password     | error                                    |
      | locked_out_user | secret_sauce | Sorry, this user has been locked out.    |
      | standard_user   | wrong        | Username and password do not match       |
