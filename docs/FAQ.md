# FAQ

## Can the added transactions have the bold effect of new transactions similar to the frontend?

No. Unfortunately, this effect that appears when you import transactions via CSV is only stored in memory and applied
by the frontend. This means that the bold formatting is not persisted in the database and cannot be replicated
through the API.

If you want to import transactions and later confirm them manually, you can use the cleared flag instead.
This will show the green checkbox on the right side of the transaction in the interface.

Read more about the cleared flag [here](https://actualbudget.org/docs/accounts/reconciliation/#work-flow).
