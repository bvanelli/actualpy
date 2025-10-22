# actualpy

|                      | **[Documentation](https://actualpy.readthedocs.io/en/latest/)** · **[Examples](https://actualpy.readthedocs.io/en/latest/examples)** · **[Releases](https://github.com/bvanelli/actualpy/releases)**                                                                                                                                                                                                                                                                                                                                                                                                                  |
|----------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Open&#160;Source** | [![MIT License](https://img.shields.io/github/license/bvanelli/actualpy)](https://github.com/bvanelli/actualpy/blob/main/LICENSE)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Community**        | [![Discussions](https://img.shields.io/github/discussions/bvanelli/actualpy)](https://github.com/bvanelli/actualpy/discussions/new/choose) ![GitHub contributors](https://img.shields.io/github/contributors/bvanelli/actualpy)                                                                                                                                                                                                                                                                                                                                                                                       |
| **CI/CD**            | [![github-actions](https://github.com/bvanelli/actualpy/workflows/Tests/badge.svg)](https://github.com/bvanelli/actualpy/actions) [![docs](https://readthedocs.org/projects/actualpy/badge/?version=latest)](https://actualpy.readthedocs.io/)                                                                                                                                                                                                                                                                                                                                                                        |
| **Code**             | [![!pypi](https://img.shields.io/pypi/v/actualpy?color=orange)](https://pypi.org/project/actualpy/) [![codecov](https://codecov.io/github/bvanelli/actualpy/graph/badge.svg?token=N6V05MY70U)](https://codecov.io/github/bvanelli/actualpy) [![!python-versions](https://img.shields.io/pypi/pyversions/actualpy)](https://www.python.org/) [![ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)  [![codestyle](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/python/black) |
| **Downloads**        | ![PyPI - Downloads](https://img.shields.io/pypi/dw/actualpy) ![PyPI - Downloads](https://img.shields.io/pypi/dm/actualpy) [![Downloads](https://img.shields.io/pepy/dt/actualpy?label=cumulative%20(pypi))](https://pepy.tech/project/actualpy)                                                                                                                                                                                                                                                                                                                                                                       |

Python API implementation for the Actual server.

[Actual Budget](https://actualbudget.org/) is a superfast and privacy-focused app for managing your finances.

This library is a re-implementation of the Node.js version of the npm package
[@actual-app/api](https://actualbudget.org/docs/api/).
It implements a different approach, offering a more Pythonic way to deal with database objects using the SQLAlchemy
ORM. This means that you can use the full power of SQLAlchemy to query the database and build your own queries. This
is useful if you want to build a custom tool to manage your budget or to export your data to another format. All the
useful relationships between the objects are also available to facilitate data handling.

If you find any issues with the library, please open an issue on the
[GitHub repository](https://github.com/bvanelli/actualpy/issues).

If you have a question, you can also open a new discussion on the
[GitHub repository](https://github.com/bvanelli/actualpy/discussions/new/choose).

# Installation

Install it via Pip:

```bash
pip install actualpy
```

If you want to have the latest git version, you can also install it using the repository URL:

```bash
pip install git+https://github.com/bvanelli/actualpy.git
```

For querying basic information, you can additionally install the CLI. Check out the
[basic documentation](https://actualpy.readthedocs.io/en/latest/command-line-interface/) for details.

# Basic usage

The most common usage involves downloading a budget to more easily build queries. This allows you to handle the
Actual database using SQLAlchemy instead of having to retrieve data via export. The following script will print
every single transaction registered in the Actual budget file:

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

The `file` parameter will be matched to one of the following:

- The name of the budget, found in the top-left corner
- The ID of the budget, a UUID that is only available if you inspect the result of the `list_user_files` method
- The Sync ID of the budget, a UUID available on the frontend under "Advanced options"
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

The Actual budget is stored in a SQLite database hosted on the user's browser. This means all your data is fully local
and can be encrypted with a local key so that not even the server can read your financial statements.

The Actual Server is a way of hosting only files and changes. Since re-uploading the full database on every single
change is too resource-intensive, Actual only stores one state of the "base database" and everything added by the user
via the frontend or APIs represents individual changes applied on top. This means that for every locally made change,
the frontend performs a SYNC request with a list of the following string parameters:

- `dataset`: The name of the table where the change occurred.
- `row`: The row identifier for the entry that was added/updated. This is the primary key of the row (a UUID value).
- `column`: The column that had its value changed.
- `value`: The new value. Since it's a string, values are prefixed by `S:` to denote a string, `N:` to denote
  a numeric value, and `0:` to denote a null value.

All individual column changes are computed for an insert or update, serialized with protobuf, and sent to the server to
be stored. Null values and server defaults are not required to be present in the SYNC message unless a column is
changed to null. If the file is encrypted, the protobuf content will also be encrypted so that the server does not know
what was changed.

When you open your budget on another device, the client can use these individual changes to update its local copies.
Whenever a SYNC request is made, the response will also contain changes that might have been made in other devices,
so that the user is informed about the latest information.

However, this also means that new users may need to download a long list of changes, potentially making initialization
slow. Thankfully, users are also allowed to reset the sync. When resetting a file via the frontend, the browser
resets the file completely and clears the list of changes, re-uploading the full file as the new "base database".
This is done on the frontend under *Settings > Reset sync*, and it causes the current file to be
reset (removed from the server) and re-uploaded again, with all changes already in place.

This means that when using this library to perform changes on the database, you have to **make sure that either**:

- A sync request is made using the `actual.commit()` method. This only handles pending operations that haven't yet
  been committed, generates a change list with them, and posts them to the sync endpoint.
- A full re-upload of the database is performed.

# Contributing

See how to set up your local project in [CONTRIBUTING.md](CONTRIBUTING.md).
