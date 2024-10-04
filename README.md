[![codecov](https://codecov.io/github/bvanelli/actualpy/graph/badge.svg?token=N6V05MY70U)](https://codecov.io/github/bvanelli/actualpy)

# actualpy

Python API implementation for Actual server.

[Actual Budget](https://actualbudget.org/) is a superfast and privacy-focused app for managing your finances.

> [!WARNING]
> The [Javascript API](https://actualbudget.org/docs/api/) to interact with Actual server already exists,
> and is battle-tested as it is the core of the Actual frontend libraries. If you intend to use a reliable and well
> tested library, that is the way to go.

# Installation

Install it via Pip:

```bash
pip install actualpy
```

If you want to have the latest git version, you can also install using the repository url:

```bash
pip install git+https://github.com/bvanelli/actualpy.git
```

For querying basic information, you additionally install the CLI, checkout the
[basic documentation](https://actualpy.readthedocs.io/en/latest/command-line-interface/)

# Basic usage

The most common usage would be downloading a budget to more easily build queries. This would you could handle the
Actual database using SQLAlchemy instead of having to retrieve the data via the export. The following script will print
every single transaction registered on the Actual budget file:

```python
from actual import Actual
from actual.queries import get_transactions

with Actual(
        base_url="http://localhost:5006",  # Url of the Actual Server
        password="<your_password>",  # Password for authentication
        encryption_password=None,  # Optional: Password for the file encryption. Will not use it if set to None.
        # Set the file to work with. Can be either the file id or file name, if name is unique
        file="<file_id_or_name>",
        # Optional: Directory to store downloaded files. Will use a temporary if not provided
        data_dir="<path_to_data_directory>",
        # Optional: Path to the certificate file to use for the connection, can also be set as False to disable SSL verification
        cert="<path_to_cert_file>"
) as actual:
    transactions = get_transactions(actual.session)
    for t in transactions:
        account_name = t.account.name if t.account else None
        category = t.category.name if t.category else None
        print(t.date, account_name, t.notes, t.amount, category)
```

The `file` will be matched to either one of the following:

- The name of the budget, found top the top left cornet
- The ID of the budget, a UUID that is only available if you inspect the result of the method `list_user_files`
- The Sync ID of the budget, a UUID available on the frontend on the "Advanced options"
- If none of those options work for you, you can search for the file manually with `list_user_files` and provide the
  object directly:

```python
from actual import Actual

with Actual("http://localhost:5006", password="mypass") as actual:
    actual.set_file(actual.list_user_files().data[0])
    actual.download_budget()
```

Checkout [the full documentation](https://actualpy.readthedocs.io) for more examples.

# Understanding how Actual handles changes

The Actual budget is stored in a sqlite database hosted on the user's browser. This means all your data is fully local
and can be encrypted with a local key, so that not even the server can read your statements.

The Actual Server is a way of only hosting files and changes. Since re-uploading the full database on every single
change is too heavy, Actual only stores one state of the database and everything added by the user via frontend
or via the APIs are individual changes on top of the "base database" stored on the server. This means that on every
change, done locally, a SYNC request is sent to the server with a list of the following string parameters:

- `dataset`: the name of the table where the change happened.
- `row`: the row identifier for the entry that was added/update. This would be the primary key of the row (a uuid value)
- `column`: the column that had the value changed
- `value`: the new value. Since it's a string, the values are either prefixed by `S:` to denote a string, `N:` to denote
  a numeric value and `0:` to denote a null value.

All individual column changes are computed on an insert, serialized with protobuf and sent to the server to be stored.
Null values and server defaults are not required to be present in the SYNC message, unless a column is changed to null.
If the file is encrypted, the protobuf content will also be encrypted, so that the server does not know what was
changed.

New clients can use this individual changes to then sync their local copies and add the changes executed on other users.
Whenever a SYNC request is done, the response will also contain changes that might have been done in other browsers, so
that the user the retrieve the information and update its local copy.

But this also means that new users need to download a long list of changes, possibly making the initialization slow.
Thankfully, user is also allowed to reset the sync. When doing a reset of the file via frontend, the browser is then
resetting the file completely and clearing the list of changes. This would make sure all changes are actually stored in
the
database. This is done on the frontend under *Settings > Reset sync*, and causes the current file to be reset (removed
from the server) and re-uploaded again, with all changes already in place.

This means that, when using this library to operate changes on the database, you have to make sure that either:

- do a sync request is made using the `actual.commit()` method. This only handles pending operations that haven't yet
  been committed, generates a change list with them and posts them on the sync endpoint.
- do a full re-upload of the database is done.

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
