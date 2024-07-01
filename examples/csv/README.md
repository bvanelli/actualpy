This example shows how to import a CSV file directly into Actual without having to use the UI.

It also reconciles the transactions, to make sure that a transaction cannot be inserted twice into the database.

The file under `files/transactions.csv` is a direct export from Actual and contains the following fields:

- Account
- Date
- Payee
- Notes
- Category
- Amount
- Cleared
