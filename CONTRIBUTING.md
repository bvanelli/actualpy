# Contributing

The goal is to have more features implemented and tested on the Actual API. If you have ideas, comments, bug fixes or
requests, feel free to open an issue or submit a pull request.

Installing requirements is easy with `uv`:

```bash
uv sync --dev
```

We use [`pre-commit`](https://pre-commit.com/) to ensure consistent formatting across different developers. To develop
locally, make sure you install all development requirements, then install `pre-commit` hooks. This would make sure the
formatting runs on every commit.

```bash
pre-commit install
```

To run tests, make sure you have docker installed ([how to install docker](https://docs.docker.com/engine/install/)).
Run the tests on your machine:

```bash
uv run pytest
```

If after your changes the tests are running fine, you can commit your changes and open a pull request!
