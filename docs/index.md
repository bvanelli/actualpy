# Quickstart

## Adding new transactions

After you created your first budget (or when updating an existing budget), you can add new transactions by adding them
using the `actual.session.add()` method. You cannot use the SQLAlchemy session directly because that adds the entries to your
local database, but will not sync the results back to the server (that is only possible when re-uploading the file).

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

!!! warning
    You can also modify the relationships, for example the `transaction.payee.name`, but you to be aware that
    this payee might be used for more than one transaction. Whenever the relationship is anything but 1:1, you have to
    track the changes already done to prevent modifying a field twice.

## Generating backups

You can use actualpy to generate regular backups of your server files. Here is a script that will backup your server
file on the current folder:

```python
from actual import Actual
from datetime import datetime

with Actual(base_url="http://localhost:5006", password="mypass", file="My budget") as actual:
    current_date = datetime.now().strftime("%Y%m%d-%H%M")
    actual.export_data(f"actual_backup_{current_date}.zip")
```
