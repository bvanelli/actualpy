import datetime
import decimal
import json
import warnings
from datetime import date, timedelta

import pytest

from actual import Actual, ActualError, reflect_model
from actual.database import Notes, ReflectBudgets, Transactions, ZeroBudgets
from actual.queries import (
    create_account,
    create_budget,
    create_rule,
    create_schedule,
    create_schedule_config,
    create_splits,
    create_tag,
    create_transaction,
    create_transfer,
    get_accounts,
    get_accumulated_budgeted_balance,
    get_budgets,
    get_or_create_category,
    get_or_create_clock,
    get_or_create_payee,
    get_or_create_preference,
    get_payee,
    get_preferences,
    get_ruleset,
    get_schedules,
    get_tag,
    get_tags,
    get_transactions,
    normalize_payee,
    reconcile_transaction,
    set_transaction_payee,
)
from actual.rules import Action, Condition, ConditionType, Rule
from actual.schedules import EndMode, Frequency, Pattern, WeekendSolveMode

today = date.today()


def test_account_relationships(session):
    bank = create_account(session, "Bank", 5000)
    create_account(session, "Savings")
    landlord = get_or_create_payee(session, "Landlord")
    rent = get_or_create_category(session, "Rent")
    rent_payment = create_transaction(session, today, "Bank", "Landlord", "Paying rent", "Rent", -1200)
    utilities_payment = create_transaction(session, today, "Bank", "Landlord", "Utilities", "Rent", -50)
    create_transfer(session, today, "Bank", "Savings", 200, "Saving money")
    session.commit()
    assert bank.balance == decimal.Decimal(3550)
    assert landlord.balance == decimal.Decimal(-1250)
    assert rent.balance == decimal.Decimal(-1250)
    assert rent_payment.category == rent
    assert len(bank.transactions) == 4  # includes starting balance and one transfer
    assert len(landlord.transactions) == 2
    assert len(rent.transactions) == 2
    # let's now void the utilities_payment
    utilities_payment.delete()
    session.commit()
    assert bank.balance == decimal.Decimal(3600)
    assert landlord.balance == decimal.Decimal(-1200)
    assert rent.balance == decimal.Decimal(-1200)
    assert len(bank.transactions) == 3
    assert len(landlord.transactions) == 1
    assert len(rent.transactions) == 1
    # delete the payee and category
    rent.delete()
    landlord.delete()
    session.commit()
    assert rent_payment.category is None
    assert rent_payment.payee is None
    # find the deleted transaction again
    deleted_transaction = get_transactions(
        session, today - timedelta(days=1), today + timedelta(days=1), "Util", bank, include_deleted=True
    )
    assert [utilities_payment] == deleted_transaction
    assert get_accounts(session, "Bank") == [bank]


def test_transaction(session):
    other = create_account(session, "Other")
    coffee = create_transaction(session, date=today, account="Other", payee="Starbucks", notes="coffee", amount=(-9.95))
    session.commit()
    assert coffee.amount == -995
    assert len(other.transactions) == 1
    assert other.balance == decimal.Decimal("-9.95")


def test_transaction_without_payee(session):
    other = create_account(session, "Other")
    tr = create_transaction(session, date=today, account=other)
    assert tr.payee_id is None


def test_transfer(session):
    bank = create_account(session, "Bank", 200)
    savings = create_account(session, "Savings")
    origin, dst = create_transfer(session, today, "Bank", "Savings", 200, "Saving money")
    assert origin.payee_id == savings.payee.id
    assert dst.payee_id == bank.payee.id
    assert bank.balance == decimal.Decimal(0.0)
    assert savings.balance == decimal.Decimal(200.0)


def test_reconcile_transaction(session):
    create_account(session, "Bank")
    rent_payment = create_transaction(session, today, "Bank", "Landlord", "Paying rent", "Expenses", -1200)
    unrelated = create_transaction(
        session, today - timedelta(days=5), "Bank", "Carshop", "Car maintenance", "Car", -1200
    )
    session.commit()
    assert (
        reconcile_transaction(
            session,
            today + timedelta(days=1),
            "Bank",
            category="Rent",
            amount=-1200,
            notes="New notes",
            imported_id="unique",
        ).id
        == rent_payment.id
    )
    session.commit()
    # check if the property was updated
    assert rent_payment.get_date() == today + timedelta(days=1)
    assert rent_payment.category.name == "Rent"
    assert rent_payment.financial_id == "unique"
    assert rent_payment.payee.name == "Landlord"  # payee stayed the same
    # should still be able to match if the payee is defined, as the match is stronger
    assert (
        reconcile_transaction(
            session, today - timedelta(days=5), payee="Landlord", account="Bank", amount=-1200, update_existing=False
        ).id
        == rent_payment.id
    )
    # should not be able to match without payee
    assert reconcile_transaction(session, today - timedelta(days=5), account="Bank", amount=-1200).id == unrelated.id
    # regardless of date, the match by unique id should work
    assert (
        reconcile_transaction(
            session,
            today - timedelta(days=30),
            account="Bank",
            amount=-1200,
            imported_id="unique",
            update_existing=False,
        ).id
        == rent_payment.id
    )
    # but if it's too far, it will be a new transaction
    assert reconcile_transaction(session, today - timedelta(days=30), account="Bank", amount=-1200).id not in (
        rent_payment.id,
        unrelated.id,
    )


def test_reconcile_transaction_update(session):
    # Here, we want to test if using reconcile actually updates all fields that it should
    create_account(session, "Bank")
    rent_payment = reconcile_transaction(
        session, today, "Bank", "Random ID", "Paying rent", "Expenses", -1200, imported_id="unique"
    )
    assert rent_payment.notes == "Paying rent"
    assert bool(rent_payment.cleared) is False
    session.commit()
    reconciled = reconcile_transaction(
        session, today, payee="Landlord", account="Bank", amount=-1200, cleared=True, update_existing=True
    )
    session.commit()  # this commit will update the payee property
    assert rent_payment.id == reconciled.id
    assert bool(reconciled.cleared) is True
    assert reconciled.payee_id == get_or_create_payee(session, "Landlord").id
    assert reconciled.payee.name == "Landlord"
    assert rent_payment.notes == "Paying rent"
    assert bool(rent_payment.cleared) is True
    reconciled = reconcile_transaction(
        session, today, payee="Landlord", account="Bank", amount=-1200, notes="New notes", update_existing=True
    )
    session.commit()
    assert bool(rent_payment.cleared) is True  # Should not reset the cleared flag
    assert reconciled.notes == "New notes"


def test_create_splits(session):
    bank = create_account(session, "Bank")
    t = create_transaction(session, today, bank, category="Dining", amount=-10.0)
    t_taxes = create_transaction(session, today, bank, category="Taxes", amount=-2.5)
    parent_transaction = create_splits(session, [t, t_taxes], notes="Dining")
    # find all children
    trs = get_transactions(session)
    assert len(trs) == 2
    assert t in trs
    assert t_taxes in trs
    assert all(tr.parent == parent_transaction for tr in trs)
    # find all parents
    parents = get_transactions(session, is_parent=True)
    assert len(parents) == 1
    assert len(parents[0].splits) == 2
    # find all with category
    category = get_transactions(session, category="Dining")
    assert len(category) == 1


def test_create_splits_deprecation(session):
    bank = create_account(session, "Bank")
    t = create_transaction(session, today, bank, category="Dining", amount=-10.0)
    t_taxes = create_transaction(session, today, bank, category="Taxes", amount=-2.5)
    with warnings.catch_warnings(record=True) as w:
        parent_transaction = create_splits(session, [t, t_taxes], "foobar", notes="Dining")
        assert len(w) == 1
    assert parent_transaction.payee_id is None


def test_create_splits_error(session):
    bank = create_account(session, "Bank")
    wallet = create_account(session, "Wallet")
    t1 = create_transaction(session, today, bank, category="Dining", amount=-10.0)
    t2 = create_transaction(session, today, wallet, category="Taxes", amount=-2.5)
    t3 = create_transaction(session, today - timedelta(days=1), bank, category="Taxes", amount=-2.5)
    with pytest.raises(ActualError, match="must be the same for all transactions in splits"):
        create_splits(session, [t1, t2])
    with pytest.raises(ActualError, match="must be the same for all transactions in splits"):
        create_splits(session, [t1, t3])


def test_create_transaction_without_account_error(session):
    with pytest.raises(ActualError):
        create_transaction(session, today, "foo", "")
    with pytest.raises(ActualError):
        create_transaction(session, today, None, "")


def test_rule_insertion_method(session):
    # create one example transaction
    create_transaction(session, date(2024, 1, 4), create_account(session, "Bank"), "")
    session.commit()
    # create and run rule
    action = Action(field="cleared", value=1)
    assert action.model_dump(mode="json", by_alias=True) == {
        "field": "cleared",
        "op": "set",
        "type": "boolean",
        "value": True,
    }
    condition = Condition(field="date", op=ConditionType.IS_APPROX, value=date(2024, 1, 2))
    assert condition.model_dump(mode="json", by_alias=True) == {
        "field": "date",
        "op": "isapprox",
        "type": "date",
        "value": "2024-01-02",
    }
    # test full rule
    rule = Rule(conditions=[condition], actions=[action], operation="all", stage="pre")
    created_rule = create_rule(session, rule, run_immediately=True)
    assert [condition.model_dump(mode="json", by_alias=True)] == json.loads(created_rule.conditions)
    assert [action.model_dump(mode="json", by_alias=True)] == json.loads(created_rule.actions)
    assert created_rule.conditions_op == "and"
    assert created_rule.stage == "pre"
    trs = get_transactions(session)
    assert trs[0].cleared == 1
    session.flush()
    rs = get_ruleset(session)
    assert len(rs.rules) == 1
    assert str(rs) == "If all of these conditions match 'date' isapprox '2024-01-02' then set 'cleared' to 'True'"


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

    assert get_accumulated_budgeted_balance(session, date(2025, 2, 1), category) == expected_value_previous_month
    assert get_accumulated_budgeted_balance(session, date(2025, 3, 1), category) == expected_value_current_month


def test_normalize_payee():
    assert normalize_payee("   mY paYeE ") == "My Payee"
    assert normalize_payee("  ", raw_payee_name=True) == ""
    assert normalize_payee(" My PayeE ", raw_payee_name=True) == "My PayeE"


def test_rollback(session):
    create_account(session, "Bank", 5000)
    session.flush()
    assert "messages" in session.info
    assert len(session.info["messages"])
    session.rollback()
    assert "messages" not in session.info


def test_model_notes(session):
    account_with_note = create_account(session, "Bank 1")
    account_without_note = create_account(session, "Bank 2")
    session.add(Notes(id=f"account-{account_with_note.id}", note="My note"))
    session.commit()
    assert account_with_note.notes == "My note"
    assert account_without_note.notes is None


def test_default_imported_payee(session):
    t = create_transaction(session, date(2024, 1, 4), create_account(session, "Bank"), imported_payee=" foo ")
    session.flush()
    assert t.payee.name == "foo"
    assert t.imported_description == "foo"


def test_session_error(mocker):
    mocker.patch("actual.Actual.validate")
    with Actual(token="foo") as actual:
        with pytest.raises(ActualError, match="No session defined"):
            print(actual.session)  # try to access the session, should raise an exception


def test_apply_changes(session, mocker):
    mocker.patch("actual.Actual.validate")
    actual = Actual(token="foo")
    actual._session, actual.engine, actual._meta = session, session.bind, reflect_model(session.bind)
    # create elements but do not commit them
    account = create_account(session, "Bank")
    transaction = create_transaction(session, date(2024, 1, 4), account, amount=35.7)
    session.flush()
    messages_size = len(session.info["messages"])
    transaction.notes = "foobar"
    session.flush()
    assert len(session.info["messages"]) == messages_size + 1
    messages = session.info["messages"]
    # undo all changes, but apply via database
    session.rollback()
    changes = actual.apply_changes(messages)
    # make sure elements got committed correctly
    accounts = get_accounts(session, "Bank")
    assert len(accounts) == 1
    assert accounts[0].id == account.id
    assert accounts[0].name == account.name
    transactions = get_transactions(session)
    assert len(transactions) == 1
    assert transactions[0].id == transaction.id
    assert transactions[0].notes == transaction.notes
    assert transactions[0].get_date() == transaction.get_date()
    assert transactions[0].get_amount() == transaction.get_amount()
    # make sure the changes are correct: 1 account, 1 payee, 1 payee mapping, 1 transaction
    # the transaction update will be grouped together even though it is a different changeset
    assert len(changes) == 4
    assert changes[-1].table is Transactions
    assert changes[-1].from_orm(session) == transactions[0]


def test_get_or_create_clock(session):
    clock = get_or_create_clock(session)
    assert clock.get_timestamp().ts == datetime.datetime(1970, 1, 1, 0, 0, 0)
    assert clock.get_timestamp().initial_count == 0


def test_get_preferences(session):
    assert len(get_preferences(session)) == 0
    preference = get_or_create_preference(session, "foo", "bar")
    assert preference.value == "bar"
    preferences = get_preferences(session)
    assert len(preferences) == 1
    assert preferences[0] == preference
    # update preference
    get_or_create_preference(session, "foo", "foobar")
    new_preferences = get_preferences(session)
    assert len(new_preferences) == 1
    assert new_preferences[0].value == "foobar"


def test_set_payee_to_transfer(session):
    wallet = create_account(session, "Wallet")
    bank = create_account(session, "Bank")
    session.commit()
    # Create a transaction setting the payee
    t = create_transaction(session, today, bank, wallet.payee, amount=-50)
    session.commit()
    transactions = get_transactions(session)
    assert len(transactions) == 2
    assert transactions[0].get_amount() == -transactions[1].get_amount()
    assert transactions[0].transferred_id == transactions[1].id
    assert transactions[1].transferred_id == transactions[0].id
    # Set this payee to something else, transaction should be deleted
    set_transaction_payee(session, t, None)
    session.commit()
    assert len(get_transactions(session)) == 1
    assert t.payee_id is None
    assert t.transferred_id is None
    # Set payee_id back, transaction should be recreated
    set_transaction_payee(session, t, wallet.payee.id)
    session.commit()
    assert t.payee_id == wallet.payee.id
    assert t.transfer.transfer == t
    assert t.transfer.payee_id == bank.payee.id


def test_set_payee_to_transfer_off_budget(session):
    bank = create_account(session, "Bank")
    off_budget = create_account(session, "Off Budget", off_budget=True)
    category = get_or_create_category(session, "Groceries")
    session.commit()
    create_transaction(session, date.today(), bank, off_budget.payee, category=category, amount=-50)
    transactions = get_transactions(session)
    assert len(transactions) == 2
    assert bank.transactions[0].category == category
    assert off_budget.transactions[0].category is None


def test_tags(session):
    create_account(session, "Wallet")
    tag = create_tag(session, "#happy", "For the happy moments in life")
    coffee = create_transaction(session, date=today, account="Wallet", notes="Coffee #happy", amount=(-4.50))
    session.commit()
    tags = get_tags(session)
    assert tags == [tag]
    assert tags[0].transactions == [coffee]
    assert tags[0] == get_tag(session, "#happy")
    assert get_tags(session, "#foobar", "moments") == []


def test_schedules(session):
    config = create_schedule_config(datetime.date(2025, 10, 11))
    schedule_created = create_schedule(session, config, 500.0, name="foobar")
    session.commit()

    schedules = get_schedules(session)
    assert len(schedules) == 1
    cond = json.loads(schedules[0].rule.conditions)
    assert cond[1] == {
        "field": "date",
        "type": "date",
        "op": "isapprox",
        "value": {
            "start": "2025-10-11",
            "interval": 1,
            "frequency": "monthly",
            "patterns": [],
            "skipWeekend": False,
            "weekendSolveMode": "after",
            "endMode": "never",
            "endOccurrences": 1,
            "endDate": "2025-10-11",
        },
    }
    assert schedule_created == schedules[0]
    # change to complete and requery
    schedule_created.completed = 1
    assert len(get_schedules(session)) == 0


def test_schedule_is_betweeen(session):
    expected_date = datetime.date(2025, 10, 11)
    account = create_account(session, "Bank")
    payee = get_or_create_payee(session, "Insurance company")
    # should always be paid on the first working day of the month
    config = create_schedule_config(expected_date, patterns=[Pattern(1, "day")], skip_weekend=True)
    # if the amount_operation="isbetween", the schedule needs two amounts
    with pytest.raises(ActualError, match="amount must be a tuple"):
        create_schedule(session, config, 100.0, "isbetween", "Insurance", payee, account)

    schedule = create_schedule(session, config, (100.0, 110.0), "isbetween", "Insurance", payee, account)
    assert json.loads(schedule.rule.conditions) == [
        {"field": "description", "type": "id", "op": "is", "value": payee.id},
        {"field": "acct", "type": "id", "op": "is", "value": account.id},
        {
            "field": "date",
            "type": "date",
            "op": "isapprox",
            "value": {
                "frequency": "monthly",
                "interval": 1,
                "patterns": [{"type": "day", "value": 1}],
                "skipWeekend": True,
                "start": "2025-10-11",
                "weekendSolveMode": "after",
                "endMode": "never",
                "endOccurrences": 1,
                "endDate": "2025-10-11",
            },
        },
        {"field": "amount", "type": "number", "op": "isbetween", "value": {"num1": 10000, "num2": 11000}},
    ]


def test_schedule_config(session):
    # should work
    sc = create_schedule_config(today, "never", frequency="monthly", skip_weekend=True, weekend_solve_mode="after")
    assert sc.end_mode == EndMode.NEVER
    assert sc.frequency == Frequency.MONTHLY
    assert sc.weekend_solve_mode == WeekendSolveMode.AFTER
    # should raise validation issues
    with pytest.raises(ActualError, match="the end_date must be provided"):
        create_schedule_config(today, end_mode="on_date")
    with pytest.raises(ActualError, match="the end_occurrences must be provided"):
        create_schedule_config(today, end_mode="after_n_occurrences")


def test_get_transactions_with_cleared_filter(session):
    acct = create_account(session, "ClearedTxs")
    create_transaction(session, date=today, account=acct, amount=10, cleared=False)
    create_transaction(session, date=today, account=acct, amount=11, cleared=False)
    create_transaction(session, date=today, account=acct, amount=12, cleared=False)
    create_transaction(session, date=today, account=acct, amount=21, cleared=True)
    create_transaction(session, date=today, account=acct, amount=22, cleared=True)

    # Request all transactions
    txs = get_transactions(session, account=acct)
    assert len(txs) == 5

    # Request only non-cleared transactions
    txs = get_transactions(session, account=acct, cleared=False)
    assert len(txs) == 3
    for t in txs:
        assert not t.cleared

    # Request only cleared transactions
    txs = get_transactions(session, account=acct, cleared=True)
    assert len(txs) == 2
    for t in txs:
        assert t.cleared


def test_get_accounts_with_closed_filter(session):
    """Test get_accounts filtering by closed attribute."""
    open_account = create_account(session, "Investment")
    closed_account = create_account(session, "Checking")

    closed_account.closed = 1
    session.commit()

    # Test getting all accounts
    result = get_accounts(session)
    assert len(result) == 2, "Default should return all accounts"

    # Test getting open accounts
    result = get_accounts(session, closed=False)
    assert len(result) == 1, "Should only return open account"
    assert result[0].name == open_account.name
    assert result[0].closed == open_account.closed

    # Test getting closed accounts
    result = get_accounts(session, closed=True)
    assert len(result) == 1, "Should only return closed account"
    assert result[0].name == closed_account.name
    assert result[0].closed == closed_account.closed


def test_get_accounts_with_off_budget_filter(session):
    """Test get_accounts filtering by off_budget attribute."""
    on_budget_account = create_account(session, "Checking", off_budget=False)
    off_budget_account = create_account(session, "Mortgage", off_budget=True)
    session.commit()

    # Test getting all accounts
    all_accounts = get_accounts(session)
    assert len(all_accounts) == 2, "Default should return all accounts"

    # Test getting on-budget accounts
    on_budget_only = get_accounts(session, off_budget=False)
    assert len(on_budget_only) == 1, "Should only return on-budget account"
    assert on_budget_only[0].name == on_budget_account.name
    assert on_budget_only[0].offbudget == on_budget_account.offbudget

    # Test getting off-budget accounts
    off_budget_only = get_accounts(session, off_budget=True)
    assert len(off_budget_only) == 1, "Should only return off-budget account"
    assert off_budget_only[0].name == off_budget_account.name
    assert off_budget_only[0].offbudget == off_budget_account.offbudget


def test_get_transactions_with_payee_filter(session):
    """Test get_transactions filtering by payee attribute."""
    account = create_account(session, "Checking")
    payee_name = "Walmart"
    create_transaction(session, date=today, account=account, payee=payee_name, amount=10)
    create_transaction(session, date=today, account=account, payee=payee_name, amount=11.5)
    create_transaction(session, date=today, account=account, payee="Target", amount=11.50)

    # Test getting all transactions
    all_transactions = get_transactions(session, account=account)
    assert len(all_transactions) == 3, "Default should return all transactions"

    # Test getting only transactions matching payee name
    transactions = get_transactions(session, account=account, payee=payee_name)
    assert len(transactions) == 2, f"Should only return transactions with {payee_name} payee"
    for transaction in transactions:
        assert transaction.payee.name == payee_name

    # Test getting only transactions matching payee
    payee = get_payee(session, payee_name)
    transactions = get_transactions(session, account=account, payee=payee)
    assert len(transactions) == 2, f"Should only return transactions with {payee} as payee"
    for transaction in transactions:
        assert transaction.payee == payee

    # Test getting transactions when payee name matches nothing
    transactions = get_transactions(session, account=account, payee="Amazon")
    assert len(transactions) == 0, "Should not return any transactions"

    # Test getting transactions when payee matches nothing
    payee = get_or_create_payee(session, "Amazon")
    transactions = get_transactions(session, account=account, payee=payee)
    assert len(transactions) == 0, "Should not return any transactions"


def test_get_transactions_with_amount_filter(session):
    """Test get_transactions filtering by amount attribute."""
    account = create_account(session, "Checking")
    create_transaction(session, date=today, account=account, amount=10)
    create_transaction(session, date=today, account=account, amount=11.5)
    create_transaction(session, date=today, account=account, amount=11.50)

    # Test getting all transactions
    all_transactions = get_transactions(session, account=account)
    assert len(all_transactions) == 3, "Default should return all transactions"

    # Testing getting only transactions matching 10 (passed as int)
    transactions = get_transactions(session, account=account, amount=10)
    assert len(transactions) == 1, "Should only return transaction of value 10"
    assert transactions[0].amount == 10 * 100

    # Testing getting only transactions matching 11.50 (passed as float)
    transactions = get_transactions(session, account=account, amount=11.50)
    assert len(transactions) == 2, "Should only return transactions of value 11.50"
    for transaction in transactions:
        assert transaction.amount == 11.50 * 100

    # Testing getting only transactions matching 11.50 (passed as Decimal)
    transactions = get_transactions(session, account=account, amount=decimal.Decimal(11.50))
    assert len(transactions) == 2, "Should only return transactions of value 11.50"
    for transaction in transactions:
        assert transaction.amount == 11.50 * 100


def test_get_transactions_with_positional_args(session):
    """Test get_transactions using positional arguments."""
    account = create_account(session, "Checking")
    category = get_or_create_category(session, "Utilities")
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)
    notes = "Late payment"
    payee = "City"
    amount = 9001
    imported_id = "Imported ID"
    cleared = True
    imported_payee = "Imported Payee"

    transaction = create_transaction(
        session, today, account, payee, notes, category, amount, imported_id, cleared, imported_payee
    )
    transaction.delete()

    transactions = get_transactions(
        session, yesterday, tomorrow, notes, account, category, False, True, None, cleared, payee, amount, False
    )
    assert len(transactions) == 1, "Should return the transaction"
    assert transactions[0].date == transaction.date
    assert transactions[0].notes == transaction.notes
    assert transactions[0].account == transaction.account
    assert transactions[0].category == transaction.category
    assert not transactions[0].is_parent
    assert transactions[0].tombstone
    assert transactions[0].type is None
    assert transactions[0].cleared == transaction.cleared
    assert transactions[0].payee_id == transaction.payee_id
    assert transactions[0].amount == transaction.amount
    assert transactions[0].transferred_id is None


def test_get_transactions_with_transfer_filter(session):
    """Test get_transactions filtering by transfer attribute."""
    account_checking = create_account(session, "Checking")
    account_savings = create_account(session, "Savings")
    create_transfer(session, date=today, source_account=account_savings.id, dest_account=account_checking.id, amount=10)
    create_transaction(session, date=today, account=account_checking, amount=11.5)
    create_transaction(session, date=today, account=account_checking, amount=11.50)

    # Test getting all transactions
    all_transactions = get_transactions(session, account=account_checking)
    assert len(all_transactions) == 3, "Default should return all transactions"

    # Testing getting only the transfer transactions
    transactions = get_transactions(session, account=account_checking, amount=10, transfer=True)
    assert len(transactions) == 1, "Should only return the transfer transaction"
    assert transactions[0].amount == 10 * 100

    # Testing getting only the non-transfer transactions
    transactions = get_transactions(session, account=account_checking, amount=11.50, transfer=False)
    assert len(transactions) == 2, "Should only return non-transfer transactions"
    for transaction in transactions:
        assert transaction.amount == 11.50 * 100
