# Getting budget information

!!! important
    It's important to note that the budget history does not contain the income information yet.

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
we need:

```python
import datetime

from actual import Actual
from actual.budgets import get_budget_history


with Actual("http://localhost:5006", password="mypass", file="Budget") as actual:
    history = get_budget_history(
        actual.session,
        datetime.date(2025, 11, 1),   # Can be omitted and will output the current month instead
    )
```

The history is a list of budget information objects, one for each month, that contain all data computed via frontend.
Because the budget history has to be computed from the first defined income or budget amount in your file, and the final
result is a computation of many accumulated values, multiple months will have to be computed.

Since they are all computed, the function already returns the entirety of the budget history, unlike the JS API,
that returns only one month at a time.

Also, the content of the budget history might be different depending on which budget type you are using: for
envelope budgeting, the [EnvelopeBudget][actual.budgets.EnvelopeBudget] will be returned, while for tracking budgeting,
the [TrackingBudget][actual.budgets.TrackingBudget] will be returned.

We can start by sub-selecting the budget information for a specific month:

```python
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
    "lastMonthOverspent": decimal_to_cents(budget.overspent_prev_month),
    "forNextMonth": decimal_to_cents(budget.for_next_month),
    "totalBudgeted": decimal_to_cents(-budget.budgeted),
    "toBudget": decimal_to_cents(budget.to_budget),
    "fromLastMonth": decimal_to_cents(budget.from_last_month),
    "totalIncome": decimal_to_cents(budget.income),
    "totalSpent": decimal_to_cents(budget.expenses),
    "totalBalance": decimal_to_cents(budget.balance),
    "categories": [],
}
```

Notice that actualpy always offers the budget information as a decimal. If you want the information in cents like
it is provided on the JS API, you can use the [decimal_to_cents][actual.utils.conversions.decimal_to_cents]
helper function.

Afterward, we can iterate over the category groups and build the information for each category:

```python
for category in category_group.categories:
    category_balance = {
        "id": category.category.id,
        "name": category.category.name,
        "is_income": bool(category.category.is_income),
        "hidden": bool(category.category.hidden),
        "group_id": category_group.category_group.id,
        "budgeted": decimal_to_cents(category.budgeted),
        "spent": decimal_to_cents(category.spent),
        "balance": decimal_to_cents(category.balance),
        "carryover": category.carryover,
    }
    category_group_balance["categories"].append(category_balance)
budget_general_info["categoryGroups"].append(category_group_balance)
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
    # build the general info
    budget_general_info = {
        "month": budget.month.strftime("%Y-%m"),
        "incomeAvailable": decimal_to_cents(budget.available_funds),
        "lastMonthOverspent": decimal_to_cents(budget.overspent_prev_month),
        "forNextMonth": decimal_to_cents(budget.for_next_month),
        "totalBudgeted": decimal_to_cents(-budget.budgeted),
        "toBudget": decimal_to_cents(budget.to_budget),
        "fromLastMonth": decimal_to_cents(budget.from_last_month),
        "totalIncome": decimal_to_cents(budget.income),
        "totalSpent": decimal_to_cents(budget.expenses),
        "totalBalance": decimal_to_cents(budget.balance),
        "categoryGroups": [],
    }
    # For each category group, we will now also have to build the information from the JSON object individually
    for category_group in budget.category_groups:
        # the object is stored in category_group.category_group
        category_group_balance = {
            "id": category_group.category_group.id,
            "name": category_group.category_group.name,
            "is_income": bool(category_group.category_group.is_income),
            "hidden": bool(category_group.category_group.hidden),
            "budgeted": decimal_to_cents(category_group.budgeted),
            "spent": decimal_to_cents(category_group.spent),
            "balance": decimal_to_cents(category_group.balance),
            "categories": [],
        }
        # We can also iterate over the categories and fill those properties too
        for category in category_group.categories:
            category_balance = {
                "id": category.category.id,
                "name": category.category.name,
                "is_income": bool(category.category.is_income),
                "hidden": bool(category.category.hidden),
                "group_id": category_group.category_group.id,
                "budgeted": decimal_to_cents(category.budgeted),
                "spent": decimal_to_cents(category.spent),
                "balance": decimal_to_cents(category.balance),
                "carryover": category.carryover,
            }
            category_group_balance["categories"].append(category_balance)
        budget_general_info["categoryGroups"].append(category_group_balance)
    # print with JSON formatting
    print(json.dumps(budget_general_info, indent=3))
```
