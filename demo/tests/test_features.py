"""Turn every scenario in demo/features into a pytest test."""

from pytest_bdd import scenarios

scenarios("login.feature", "cart.feature")
