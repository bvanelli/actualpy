# Converting a GnuCash budget to Actual

!!! warning

    The following example is not guaranteed to work, and you might need to change some parameters to make it work
    for your use case.

When I first migrated to Actual, I was using GnuCash to manage my budget. I then started writing _actualpy_
as a way to automate this migration, since at the time it _seemed_ like it was going to be straightforward:
read the GnuCash data with the pre-existing Python library, then import everything into Actual using HTTP requests.

This led me on an entire journey of re-engineering the Actual protocol, and trying to provide a high-quality API for
the Python community.

If you still want to use this, here is the original code, without any warranty that this will actually work.

```python
import datetime
import decimal
import pathlib

import piecash

from actual import Actual
from actual.queries import (
    create_transaction,
    create_transfer,
    get_or_create_account,
    get_or_create_category,
    get_or_create_payee,
)


def insert_transaction(
    session, account_source: str, expense_source: str, notes: str, date: datetime.date, value: decimal.Decimal
):
    # do inserts, if it's an expense or transfer
    payee = get_or_create_payee(session, "")  # payee is non-existing on gnucash
    if account_source.startswith("Assets:") and expense_source.startswith("Expenses:"):
        account = get_or_create_account(session, account_source.replace("Assets:", ""))
        group_name, _, category_name = expense_source.partition(":")
        category = get_or_create_category(session, category_name, group_name)
        create_transaction(session, date, account, payee, notes, category, -value)
    elif account_source.startswith("Income:") and expense_source.startswith("Assets:"):
        expense = get_or_create_account(session, expense_source.replace("Assets:", ""))
        group_name, _, category_name = account_source.partition(":")
        category = get_or_create_category(session, category_name, group_name)
        create_transaction(session, date, expense, payee, notes, category, value)
    elif account_source.startswith("Assets:") and expense_source.startswith("Assets:"):
        account = get_or_create_account(session, account_source.replace("Assets:", ""))
        expense = get_or_create_account(session, expense_source.replace("Assets:", ""))
        session.flush()
        # transfer between accounts
        if value < 0:
            # reverse everything
            account, expense, value = expense, account, -value
        create_transfer(session, date, account, expense, value, notes)
    else:
        print(f"Could not parse transaction '{account_source}' to '{expense_source}', '{notes}', {value}")
    session.flush()


def parse_transaction(session, transaction: piecash.Transaction):
    notes: str = transaction.description
    date: datetime.date = transaction.post_date

    expense_source: str = transaction.splits[0].account.fullname
    account_source: str = transaction.splits[1].account.fullname
    value: decimal.Decimal = transaction.splits[0].quantity
    # swap around if it's a transfer back, set it with negative value
    if (account_source.startswith("Expenses:") and expense_source.startswith("Assets:")) or (
        account_source.startswith("Assets:") and expense_source.startswith("Income:")
    ):
        expense_source, account_source, value = account_source, expense_source, -value

    # create accounts for assets
    insert_transaction(session, account_source, expense_source, notes, date, value)


def main():
    with Actual(password="mypass", bootstrap=True) as actual:
        actual.create_budget("Gnucash Import")
        # go through files from gnucash and find all that match .gnucash extension
        path = pathlib.Path(__file__).parent / "files/"
        for file in path.rglob("*.gnucash"):
            book = piecash.open_book(str(file.absolute()), readonly=True)
            for transaction in book.transactions:
                if len(transaction.splits) > 2:
                    print(
                        f"Could not parse transaction {transaction.guid}. "
                        "Please, make sure you support splits manually"
                    )
                    continue
                # for the actual transaction, get account in and out
                parse_transaction(actual.session, transaction)

        # if everything goes well we upload our budget
        actual.session.commit()
        actual.upload_budget()


if __name__ == "__main__":
    main()
```
