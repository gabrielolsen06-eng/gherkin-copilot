Feature: Login
  As a shopper I want to sign in so that I can buy products.

  Scenario: Standard user logs in
    Given I am on the login page
    When I log in as "standard_user" with password "secret_sauce"
    Then I should see the products page

  Scenario Outline: Rejected logins show a helpful error
    Given I am on the login page
    When I log in as "<username>" with password "<password>"
    Then I should see the error "<error>"

    Examples:
      | username        | password     | error                                    |
      | locked_out_user | secret_sauce | Sorry, this user has been locked out.    |
      | standard_user   | wrong        | Username and password do not match       |
