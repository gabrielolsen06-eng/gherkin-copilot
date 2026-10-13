Feature: Character Map
  Character Map is on every Windows machine, so these scenarios prove desktop testing works
  anywhere, including GitHub's Windows machines. Point DESKTOP_APP at your own app to test it.

  Scenario: Type into a field
    Given the app is open
    When I type "Hello" into "Characters to copy"
    Then "Characters to copy" shows "Hello"
    And I see "Hello"

  Scenario: Press a button
    Given the app is open
    When I click "Select"
    Then "Characters to copy" is not empty

  Scenario: Tick a checkbox
    Given the app is open
    When I tick "Advanced view"
    Then "Advanced view" is ticked
