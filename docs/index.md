# Quickstart

You can install actualpy using your package manager of choice:

```bash
pip install actualpy
```

## Using relationships and properties

The SQLAlchemy model already contains relationships to the referenced foreign keys and some properties. For example,
it's pretty simple to get the current balances for accounts, payees, and budgets:

```python
from actual import Actual
from actual.queries import get_accounts, get_payees, get_budgets

with Actual(base_url="http://localhost:5006", password="mypass", file="My budget") as actual:
    # Print each account balance, for the entire dataset
    for account in get_accounts(actual.session):
        print(f"Balance for account {account.name} is {account.balance}")
    # Print each payee balance, for the entire dataset
    for payee in get_payees(actual.session):
        print(f"Balance for payee {payee.name} is {payee.balance}")
    # Print the leftover budget balance, for each category and the current month
    for budget in get_budgets(actual.session):
        print(f"Balance for budget {budget.category.name} is {budget.balance}")
```

You can quickly iterate over the transactions of one specific account:

```python
from actual import Actual
from actual.queries import get_account

with Actual(base_url="http://localhost:5006", password="mypass", file="My budget") as actual:
    account = get_account(actual.session, "Bank name")
    for transaction in account.transactions:
        # Get the payee, notes and amount of each transaction
        print(f"Transaction ({transaction.payee.name}, {transaction.notes}) has a value of {transaction.get_amount()}")
```

## Adding new transactions

After creating your first budget (or when updating an existing budget), you can add new transactions by adding them
using the [`create_transaction`][actual.queries.create_transaction] method, and commit it using
[`actual.commit`][actual.Actual.commit]. You cannot use the SQLAlchemy session directly because that adds the entries
to your local database, but will not sync the results back to the server (that is only possible when re-uploading the
file).

The method will make sure the local database is updated, but will also send a SYNC request with the added data so that
it will be immediately available on the frontend:

```python
import decimal
import datetime
from actual import Actual
from actual.queries import create_transaction, create_account

with Actual(base_url="http://localhost:5006", password="mypass", file="My budget") as actual:
    act = create_account(actual.session, "My account")
    t = create_transaction(
        actual.session,
        datetime.date.today(),
        act,
        "My payee",
        notes="My first transaction",
        amount=decimal.Decimal(-10.5),
    )
    actual.commit()  # use the actual.commit() instead of session.commit()!
```

Will produce:

![added-transaction](./static/added-transaction.png?raw=true)

## Updating existing transactions

You may also update transactions using the SQLModel directly, you just need to make sure to commit the results at the
end:

```python
from actual import Actual
from actual.queries import get_transactions


with Actual(base_url="http://localhost:5006", password="mypass", file="My budget") as actual:
    for transaction in get_transactions(actual.session):
        # change the transactions notes
        if transaction.notes is not None and "my pattern" in transaction.notes:
            transaction.notes = transaction.notes + " my suffix!"
    # commit your changes!
    actual.commit()

```

When working with transactions, it is important to keep in mind that the value amounts are set with floating-point
numbers, but the value stored in the database will be an integer (number of cents) instead. So instead of updating a
transaction with [Transactions.amount][actual.database.Transactions], use the
[Transactions.set_amount][actual.database.Transactions.set_amount] instead.

!!! warning
    You can also modify the relationships, for example the `transaction.payee.name`, but you need to be aware that
    this payee might be used for more than one transaction. Whenever the relationship is anything but 1:1, you have to
    track the changes already done to prevent modifying a field twice.

## Generating backups

You can use actualpy to generate regular backups of your server files. Here is a script that will backup your server
file to the current folder:

```python
from actual import Actual
from datetime import datetime

with Actual(base_url="http://localhost:5006", password="mypass", file="My budget") as actual:
    current_date = datetime.now().strftime("%Y%m%d-%H%M")
    actual.export_data(f"actual_backup_{current_date}.zip")
```
