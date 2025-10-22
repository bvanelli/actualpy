# Contributing

The goal is to have more features implemented and tested on the Actual API. If you have ideas, comments, bug fixes or
requests, feel free to [open an issue](https://github.com/bvanelli/actualpy/issues/new/choose) and/or
submit a pull request.

If you have questions that do not belong to an issue, you can also
[open a discussion thread](https://github.com/bvanelli/actualpy/discussions/new/choose).

Installing requirements is easy with `uv`:

```bash
uv sync --all-extras --group docs
```

We use [`pre-commit`](https://pre-commit.com/) to ensure consistent formatting across different developers. To develop
locally, make sure you install all development requirements, then install `pre-commit` hooks. This would make sure the
formatting runs on every commit.

```bash
pre-commit install
```

To run tests, make sure you have **docker installed** (see
[how to install docker](https://docs.docker.com/engine/install/)). Run the tests on your machine:

```bash
uv run pytest
```

If after your changes the tests are running fine, you can commit your changes and open a pull request!

(make sure you follow the [guidelines to a good pull request](
https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/getting-started/helping-others-review-your-changes))
