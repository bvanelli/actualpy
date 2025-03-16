# Contributing

The goal is to have more features implemented and tested on the Actual API. If you have ideas, comments, bug fixes or
requests feel free to open an issue or submit a pull request.

To install requirements, install both requirements files:

```bash
# optionally setup a venv (recommended)
python3 -m venv venv && source venv/bin/activate
# install requirements
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

We use [`pre-commit`](https://pre-commit.com/) to ensure consistent formatting across different developers. To develop
locally, make sure you install all development requirements, then install `pre-commit` hooks. This would make sure the
formatting runs on every commit.

```
pre-commit install
```

To run tests, make sure you have docker installed ([how to install docker](https://docs.docker.com/engine/install/)).
Run the tests on your machine:

```bash
pytest
```
