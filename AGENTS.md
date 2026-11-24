# Agent Development Guide

A file for [guiding AI coding agents](https://agents.md/).

## Project Overview

actualpy is a Python implementation of the Actual Budget API. It provides a Pythonic way to interact with Actual Budget servers using SQLAlchemy ORM, as opposed to the Node.js implementation. The library enables programmatic access to budget data, transactions, accounts, and categories.

### Repository Structure

- `actual/__init__.py` - `Actual` class, main entry point for interacting with an Actual server (extends `ActualServer`, context manager)
- `actual/api/` - `ActualServer` class, low-level API implementation (auth, HTTP requests)
- `actual/database.py` - SQLAlchemy/SQLModel ORM models for all Actual tables (`Transactions`, `Accounts`, `Categories`, `Payees`, `Rules`, `Schedules`, `BaseModel`)
- `actual/queries.py` - High-level query functions (`get_transactions()`, `create_transaction()`, etc.); prefer these over raw database operations
- `actual/protobuf_models.py` - Sync protocol implementation (`HULC_Client`, `Message`, `SyncRequest`/`SyncResponse`)
- `actual/rules.py` - Rules engine for automatic transaction categorization (`Rule`, `RuleSet`)
- `actual/schedules.py` - Recurring transaction schedules
- `actual/crypto.py` - File encryption/decryption for encrypted budgets
- `actual/cli/` - CLI tool entry point (`actualpy` command)
- `tests/` - Test suite, mirrors the structure of `actual/`
- `docs/` - mkdocs-material documentation source

### Code style

- When the code line is obvious, do not add a comment on top of it.
- When modifying existing code, do not update its comments if this is not relevant or if it was not requested.
- Imports go on the top of the file, rather than lazy import.
- Prefer fixtures rather than helper methods doing mocks.
- **Avoid using globals**. Prefer to add the global instead as a default value for the function instead.

## Commands

### Development Commands

- `uv sync --all-extras --group docs` - Install all dependencies including docs
- `pre-commit install` - Install pre-commit hooks (ensures consistent formatting)

### Testing

- `uv run pytest` - Run all tests
- `uv run pytest tests/test_database.py` - Run specific test file
- `uv run pytest tests/test_database.py::test_function_name` - Run specific test function
- `uv run pytest --cov=actual --cov-report=html` - Run with coverage

**Important**: Tests require Docker to be running since they use testcontainers for integration tests with Actual server versions.

### Linting & Formatting

- `pre-commit run --all-files` - Run all pre-commit hooks (linting + formatting); use this to reformat files

### Documentation

Documentation is generated directly from the docstring:

- `uv run mkdocs build` - Build documentation locally
- `uv run mkdocs serve` - Serve documentation with live reload
- `uv run mkdocs gh-deploy` - Deploy documentation (maintainers only)

Style notes:

- When adding python examples to the docstring, always use the ```python notation since tabs or spaces are not recognized by mkdocs. Do not use an extra tab here.
- When providing :param: documentation in a Python docstring, always include the period at the end of the line.
- When displaying examples in docs, you have to first show the example, then display the :param elements, otherwise the example is improperly formatted on Mkdocs.

## Validation

This repository implementation can be validated against the Actual's source code. This is available under ./node/actual (or clone from https://github.com/actualbudget/actual.git). You want to validate against their specs and look out for inconsistencies.

## Contributing, Issue, and PR Guidelines

- Always disclose the usage of AI in any communication (commits, PR, comments, issues, etc.) by adding an `(AI-assisted)` text to all messages.
- Never create an issue.
- Never create a PR.
- Never create comments on issues or PRs.
