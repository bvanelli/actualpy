import csv
import datetime
import decimal
import pathlib

from actual import Actual
from actual.exceptions import UnknownFileId
from actual.queries import get_or_create_account, reconcile_transaction


def load_csv_data(file: pathlib.Path) -> list[dict]:
    # load data from the csv
    data = []
    with open(file) as csvfile:
        for entry in csv.DictReader(csvfile):
            entry: dict
            data.append(entry)
    return data


def main():
    file = pathlib.Path(__file__).parent / "files/transactions.csv"
    with Actual(password="mypass") as actual:
        try:
            actual.set_file("CSV Import")
            actual.download_budget()
        except UnknownFileId:
            actual.create_budget("CSV Import")
            actual.upload_budget()
        # now try to do all the changes
        added_transactions = []
        for row in load_csv_data(file):
            # here, we define the basic information from the file
            account_name, payee, notes, category, cleared, date, amount = (
                row["Account"],
                row["Payee"],
                row["Notes"],
                row["Category"],
                row["Cleared"] == "Cleared",  # transform to boolean
                datetime.datetime.strptime(row["Date"], "%Y-%m-%d").date(),  # transform to date
                decimal.Decimal(row["Amount"]),  # transform to decimal (float is also possible)
            )
            # we then create the required account, with empty starting balances, if it does not exist
            # this is required because the transaction methods will refuse to auto-create accounts
            account = get_or_create_account(actual.session, account_name)
            # reconcile transaction. Here, it is important to pass the transactions added so far because the importer
            # might overwrite transactions that look very similar (same value, same date) due to them being flagged as
            # duplicates
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
        # finally, the commit will push the changes to the server
        actual.commit()


if __name__ == "__main__":
    main()
