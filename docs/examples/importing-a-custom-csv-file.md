# Importing a custom CSV file

This example shows how to import a CSV file directly into Actual without having to use the UI.

Suppose you have the following csv file (in the notes), which contains the fields:

- Account
- Date
- Payee
- Notes
- Category
- Amount

??? note "transactions.csv"

    ```csv
    Account,Date,Payee,Notes,Category,Amount
    Current Checking Account,2024-04-01,,Paying rent,Rent,-250
    Current Savings Account,2024-01-31,Current Checking Account,Saving money,,200
    Current Checking Account,2024-01-31,Current Savings Account,Saving money,,-200
    Current Checking Account,2024-01-31,,Streaming services,Online Services,-15
    Current Checking Account,2024-01-30,,Groceries,Groceries,-15
    Current Checking Account,2024-01-26,,New pants,Clothes,-40
    Current Checking Account,2024-01-26,,Groceries,Groceries,-25
    Current Checking Account,2024-01-19,,Groceries,Groceries,-25
    Current Checking Account,2024-01-18,,University book,Books,-30
    Current Checking Account,2024-01-16,,Phone contract,Phone,-15
    Current Checking Account,2024-01-13,,Cinema tickets,Entertainment:Music/Movies,-10
    Current Checking Account,2024-01-12,,Groceries,Groceries,-25
    Current Cash in Wallet,2024-01-06,,A couple of beers at a bar,Entertainment:Recreation,-25
    Current Cash in Wallet,2024-01-06,Current Checking Account,Cash withdrawal,,50
    Current Checking Account,2024-01-06,Current Cash in Wallet,Cash withdrawal,,-50
    Current Checking Account,2024-01-05,,Groceries,Groceries,-25
    Current Checking Account,2024-01-01,,Mobility Bonus,Other Income,100
    Current Checking Account,2024-01-01,,Salary Payment,Salary,700
    ```

When you import transactions, you do not know which entries have already been imported from a previously imported file,
so it's useful to be able to match entries that are already in the database.

To do that, you can use the [reconcile_transaction][actual.queries.reconcile_transaction]. The important thing to
note here is that **you will need to pass the transactions that were already imported to the function to prevent
flagging duplicates**. Check [here how Actual handles duplicates](
https://actualbudget.org/docs/transactions/importing#avoiding-duplicate-transactions).

```python
import csv
import datetime
import decimal
import pathlib

from actual import Actual
from actual.exceptions import UnknownFileId
from actual.queries import get_or_create_account, reconcile_transaction


def load_csv_data(file: pathlib.Path) -> list[dict]:
    # Loads the data from the CSV as a list of dictionaries
    data = []
    with open(file) as csvfile:
        for entry in csv.DictReader(csvfile):
            entry: dict
            data.append(entry)
    return data


def main():
    file = pathlib.Path(__file__).parent / "transactions.csv"
    with Actual(password="mypass") as actual:
        # First, we create a budget if it does not exist
        try:
            actual.set_file("CSV Import")
            actual.download_budget()
        except UnknownFileId:
            actual.create_budget("CSV Import")
            actual.upload_budget()
        # Second, we loop through the csv file and import the transactions one by one
        added_transactions = []
        for row in load_csv_data(file):
            # We define the basic information from the row data, parsing the date and amount to the correct types
            account_name, payee, notes, category, cleared, date, amount = (
                row["Account"],
                row["Payee"],
                row["Notes"],
                row["Category"],
                row["Cleared"] == "Cleared",  # transform to boolean
                datetime.datetime.strptime(row["Date"], "%Y-%m-%d").date(),  # transform to date
                decimal.Decimal(row["Amount"]),  # transform to decimal (float is also possible)
            )
            # We then create the required account, with empty starting balances, if it does not exist.
            # This is required because the transaction methods will refuse to auto-create accounts
            account = get_or_create_account(actual.session, account_name)
            # Reconcile the transaction. Here, it is important to pass the transactions
            # added so far because the importer might overwrite transactions that look
            # very similar (same value, same date) due to them being flagged as duplicates
            t = reconcile_transaction(
                actual.session,
                date,
                account,
                payee,
                notes,
                category,
                amount,
                cleared=cleared,
                already_matched=added_transactions,
            )
            added_transactions.append(t)
            if t.changed():
                print(f"Added or modified {t}")
        # Finally, the commit will push the changes to the server.
        actual.commit()


if __name__ == "__main__":
    main()
```
