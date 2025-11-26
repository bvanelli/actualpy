# Getting budget information

As you know from the [Actual JS API Reference](https://actualbudget.org/docs/api/reference), there are functions
to get the entire budget information for one or more months. This data is available on the JS library in
 the `getBudgetMonth` API call, that returns something like this:

```json
{
  "month": "2025-10",
  "incomeAvailable": 37000,
  "lastMonthOverspent": 0,
  "forNextMonth": 5000,
  "totalBudgeted": -9000,
  "toBudget": 23000,
  "fromLastMonth": 17000,
  "totalIncome": 20000,
  "totalSpent": -8000,
  "totalBalance": -1000,
  "categoryGroups": [
    "..."
  ]
}
```

Our goal is to replicate this same functionality in Python. We can use budget history methods to get the information
we need (see [get_budget_history][actual.budgets.get_budget_history]).

The history is a list of budget information objects, one for each month, that contain all data computed via frontend.
Because the budget history has to be computed from the first defined income or budget amount in your file, and the final
result is a computation of many accumulated values, multiple months will have to be computed.

Since they are all computed, the function already returns the entirety of the budget history, unlike the JS API,
that returns only one month at a time.

Also, the content of the budget history might be different depending on which budget type you are using: for
envelope budgeting, the [EnvelopeBudget][actual.budgets.EnvelopeBudget] will be returned, while for tracking budgeting,
the [TrackingBudget][actual.budgets.TrackingBudget] will be returned. Refer to their documentation for more details of
which fields exist for each budget type.

We can start by sub-selecting the budget information for a specific month:

```python
import datetime

from actual import Actual
from actual.budgets import get_budget_history


with Actual("http://localhost:5006", password="mypass", file="Budget") as actual:
    history = get_budget_history(
        actual.session,
        datetime.date(2025, 11, 1),   # Can be omitted and will output the current month instead
    )
    # subselect our target month
    budget = history.from_month(datetime.date(2025, 11, 1))
    # or use the indexing to get the last
    budget = history[-1]
```

We can now extract the information we need from the budget object — assuming it is an envelope budget, we can do:

```python
from actual.utils.conversions import decimal_to_cents

budget_general_info = {
    "month": budget.month.strftime("%Y-%m"),
    "incomeAvailable": decimal_to_cents(budget.available_funds),
    "lastMonthOverspent": decimal_to_cents(budget.last_month_overspent),
    "forNextMonth": decimal_to_cents(budget.for_next_month),
    "totalBudgeted": decimal_to_cents(-budget.budgeted),
    "toBudget": decimal_to_cents(budget.to_budget),
    "fromLastMonth": decimal_to_cents(budget.from_last_month),
    "totalIncome": decimal_to_cents(budget.received),
    "totalSpent": decimal_to_cents(budget.spent),
    "totalBalance": decimal_to_cents(budget.accumulated_balance),
}
```

Notice that actualpy always offers the budget information as a decimal. If you want the information in cents like
it is provided on the JS API, you can use the [decimal_to_cents][actual.utils.conversions.decimal_to_cents]
helper function.

If you are fine with dealing with the values as a float, you can instead use the `as_dict` method available to all
budget entries.

```python
print(budget.as_dict())
```

This will give you an output that mostly resembles the one from the JS API but using floats instead of cents.

You can also iterate over the category groups and build the information for the categories that you need:

```python
for category_group in budget.category_groups:
    for category in category_group.categories:
        # ... do something with the category information
        pass
# You can also access the income category group separately
for income_category_group in budget.income_category_groups:
    for income_category in income_category_group.categories:
        # ... do something with the income category information
        pass
```

Putting it all together, we can now build the budget information for the entire budget history:

```python
import datetime
import json

from actual import Actual
from actual.budgets import get_budget_history
from actual.utils.conversions import decimal_to_cents

with Actual("http://localhost:5006", password="mypass", file="Budget") as actual:
    history = get_budget_history(
        actual.session,
        datetime.date(2025, 11, 1),
    )
    # subselect our target month
    budget = history.from_month(datetime.date(2025, 11, 1))
    # build the general info, converting the entry to JSON
    budget_as_json = budget.as_dict()
    # Print with JSON formatting to make it easier to read
    print(json.dumps(budget_as_json, indent=3))
```

The output will look something like this (assuming you use envelope budgeting — the fields for tracking budget look
 slightly different):

```json
{
  "month": "2025-10",
  "incomeAvailable": 370.0,
  "lastMonthOverspent": -40.0,
  "forNextMonth": 50.0,
  "totalBudgeted": 90.0,
  "toBudget": 190.0,
  "fromLastMonth": 170.0,
  "totalIncome": 200.0,
  "totalSpent": -80.0,
  "totalBalance": 30.0,
  "categoryGroups": [
    {
      "id": "fc3825fd-b982-4b72-b768-5b30844cf832",
      "name": "Usual Expenses",
      "is_income": false,
      "hidden": false,
      "budgeted": 90.0,
      "spent": -80.0,
      "balance": 30.0,
      "categories": [
        {
          "id": "d4b0f075-3343-4408-91ed-fae94f74e5bf",
          "name": "Bills",
          "is_income": false,
          "hidden": false,
          "budgeted": 90.0,
          "spent": -80.0,
          "balance": 30.0,
          "carryover": false
        }
      ]
    },
    {
      "id": "2E1F5BDB-209B-43F9-AF2C-3CE28E380C00",
      "name": "Income",
      "is_income": true,
      "hidden": false,
      "received": 200.0,
      "budgeted": 0.0,
      "categories": [
        {
          "id": "3c1699a5-522a-435e-86dc-93d900a14f0e",
          "name": "Income",
          "is_income": true,
          "hidden": false,
          "received": 200.0,
          "budgeted": 0.0
        },
        {
          "id": "506e8d9d-7ed0-4397-84e4-07a9185dc6b2",
          "name": "Starting Balances",
          "is_income": true,
          "hidden": false,
          "received": 0.0,
          "budgeted": 0.0
        }
      ]
    }
  ]
}
```

To retrieve this information using the CLI, see
[the CLI documentation for budgets](../command-line-interface.md#retrieving-budget-information).
