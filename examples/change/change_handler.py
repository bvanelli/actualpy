import time

from sqlmodel import Session

from actual import Actual, Changeset
from actual.database import Transactions
from actual.queries import get_transactions


def change_handler(session: Session, change: Changeset, existing_transactions: set) -> None:
    # We ignore all changes that are not transactions
    if change.table is not Transactions:
        return
    # If the transaction already exists, we ignore it because it is not new
    if change.id in existing_transactions:
        return
    # Return the transaction object from the database
    changed_obj: Transactions = change.from_orm(session)  # type: ignore
    print(f"A new transaction with name '{changed_obj.notes}' was added with the amount {changed_obj.get_amount()}")
    # Modify the copy of existing transactions that was passed to the function, so they are ignored in the future
    existing_transactions.add(change.id)


def main() -> None:
    with Actual(password="mypass", file="State") as actual:
        # We create a set of all transactions to avoid processing modifications to existing transactions
        transaction_set = {t.id for t in get_transactions(actual.session)}

        # Handle the change listener
        while True:
            changes = actual.sync()
            for change in changes:
                # Implement callback logic here
                change_handler(actual.session, change, transaction_set)
            time.sleep(5)


if __name__ == "__main__":
    main()
