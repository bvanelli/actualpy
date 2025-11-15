import decimal
from datetime import date

import pytest

from actual import ActualError
from actual.budgets import get_budget_history
from actual.database import ReflectBudgets, ZeroBudgets
from actual.queries import (
    create_account,
    create_budget,
    create_transaction,
    get_accumulated_budgeted_balance,
    get_budgets,
    get_or_create_category,
    get_or_create_preference,
    get_transactions,
)


@pytest.mark.parametrize("budget_name", ["Expenses", None])
def test_empty_budgets(session, budget_name):
    if budget_name:
        category = get_or_create_category(session, budget_name)
        assert len(get_budgets(session, date(2025, 10, 1), budget_name)) == 0
        assert get_accumulated_budgeted_balance(session, date(2025, 10, 1), category) == decimal.Decimal(0)
    assert len(get_budgets(session)) == 0
    assert len(get_budgets(session, date(2025, 10, 1))) == 0
    assert get_budget_history(session, date(2025, 10, 1)) == []


@pytest.mark.parametrize(
    "budget_type,budget_table",
    [("rollover", ZeroBudgets), ("report", ReflectBudgets), ("envelope", ZeroBudgets), ("tracking", ReflectBudgets)],
)
def test_budgets(session, budget_type, budget_table):
    # set the config
    get_or_create_preference(session, "budgetType", budget_type)
    # insert a budget
    category = get_or_create_category(session, "Expenses")
    unrelated_category = get_or_create_category(session, "Unrelated")
    session.commit()
    create_budget(session, date(2024, 10, 7), category, 10.0)
    assert len(get_budgets(session)) == 1
    assert len(get_budgets(session, date(2024, 10, 1))) == 1
    assert len(get_budgets(session, date(2024, 10, 1), category)) == 1
    assert len(get_budgets(session, date(2024, 9, 1))) == 0
    budget = get_budgets(session)[0]
    assert isinstance(budget, budget_table)
    assert budget.get_amount() == 10.0
    assert budget.get_date() == date(2024, 10, 1)
    # get a budget that already exists, but re-set it
    create_budget(session, date(2024, 10, 7), category, 20.0)
    assert budget.get_amount() == 20.0
    assert budget.range == (date(2024, 10, 1), date(2024, 11, 1))
    # insert a transaction in the range and see if they are counted on the balance
    bank = create_account(session, "Bank")
    t1 = create_transaction(session, date(2024, 10, 1), bank, category=category, amount=-10.0)
    t2 = create_transaction(session, date(2024, 10, 15), bank, category=category, amount=-10.0)
    t3 = create_transaction(session, date(2024, 10, 31), bank, category=category, amount=-15.0)
    # should not be counted
    create_transaction(session, date(2024, 10, 1), bank, category=category, amount=-15.0).delete()
    create_transaction(session, date(2024, 11, 1), bank, category=category, amount=-20.0)
    create_transaction(session, date(2024, 10, 15), bank, category=unrelated_category, amount=-20.0)
    assert budget.balance == -35.0
    budget_transactions = get_transactions(session, budget=budget)
    assert len(budget_transactions) == 3
    assert all(t in budget_transactions for t in (t1, t2, t3))
    # test if it fails if category does not exist
    with pytest.raises(ActualError, match="Category is provided but does not exist"):
        get_budgets(session, category="foo")
    # filtering by budget will raise a warning if get_transactions with budget also provides a start-end outside range
    with pytest.warns(match="Provided date filters"):
        get_transactions(session, date(2024, 9, 1), date(2024, 9, 15), budget=budget)


@pytest.mark.parametrize(
    "budget_type,with_reset,with_previous_value,expected_value_previous_month,expected_value_current_month",
    [
        ("envelope", False, False, decimal.Decimal(5), decimal.Decimal(25)),
        ("envelope", False, True, decimal.Decimal(15), decimal.Decimal(35)),
        ("envelope", True, True, decimal.Decimal(-5), decimal.Decimal(20)),
        ("envelope", True, False, decimal.Decimal(-15), decimal.Decimal(20)),
        ("tracking", False, True, decimal.Decimal(-5), decimal.Decimal(20)),
    ],
)
def test_accumulated_budget_amount(
    session, budget_type, with_reset, with_previous_value, expected_value_current_month, expected_value_previous_month
):
    get_or_create_preference(session, "budgetType", budget_type)

    category = get_or_create_category(session, "Expenses")
    bank = create_account(session, "Bank")

    # create three months of budgets
    create_budget(session, date(2025, 1, 1), category, 20.0)
    create_budget(session, date(2025, 2, 1), category, 20.0)
    create_budget(session, date(2025, 3, 1), category, 20.0)
    # should be considered since is an income before the beginning of the budget period
    if with_previous_value:
        create_transaction(session, date(2024, 10, 1), bank, category=category, amount=10.0)
    # other transactions
    create_transaction(session, date(2025, 1, 1), bank, category=category, amount=-10.0)
    create_transaction(session, date(2025, 2, 1), bank, category=category, amount=-10.0)
    create_transaction(session, date(2025, 2, 3), bank, category=category, amount=-15.0)
    # should reset rollover budget
    if with_reset:
        create_transaction(session, date(2025, 2, 4), bank, category=category, amount=-20.0)

    # check first history entries
    history1 = get_budget_history(session, date(2025, 2, 1))
    assert history1[-1].from_category(category).accumulated_balance == expected_value_previous_month

    # check second history entries
    history2 = get_budget_history(session, date(2025, 3, 1))
    assert history2[-2].from_category(category).accumulated_balance == expected_value_previous_month
    assert history2[-1].from_category(category).accumulated_balance == expected_value_current_month

    # check also the accumulated balance method
    assert get_accumulated_budgeted_balance(session, date(2025, 2, 1), category) == expected_value_previous_month
    assert get_accumulated_budgeted_balance(session, date(2025, 3, 1), category) == expected_value_current_month


@pytest.mark.parametrize("last_month_carryover", (True, False))
def test_accumulated_budget_amount_with_carryover(session, last_month_carryover):
    get_or_create_preference(session, "budgetType", "envelope")

    category = get_or_create_category(session, "Expenses")
    bank = create_account(session, "Bank")
    create_budget(session, date(2025, 1, 1), category, 10.0, carryover=True)
    create_budget(session, date(2025, 2, 1), category, 10.0, carryover=True)
    create_budget(session, date(2025, 3, 1), category, 0.0, carryover=last_month_carryover)

    # Add a transaction and check final value
    create_transaction(session, date(2025, 1, 1), bank, category=category, amount=-30.0)
    history = get_budget_history(session, date(2025, 3, 1))
    assert history[-1].from_category(category).accumulated_balance == -10
    assert history[-2].from_category(category).accumulated_balance == -10
    # Check also the accumulated balance method
    assert get_accumulated_budgeted_balance(session, date(2025, 2, 1), category) == -10
    assert get_accumulated_budgeted_balance(session, date(2025, 3, 1), category) == -10
