"""Turn every scenario in demo/features into a pytest test, including new feature files."""

from pytest_bdd import scenarios

scenarios(".")  # the whole bdd_features_base_dir (features/), subfolders included
