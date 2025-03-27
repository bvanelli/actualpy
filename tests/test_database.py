import datetime
import decimal
import json
from datetime import date, timedelta

import pytest

from actual import Actual, ActualError, reflect_model
from actual.database import Notes, ReflectBudgets, ZeroBudgets
from actual.queries import (
    create_account,
    create_budget,
    create_rule,
    create_splits,
    create_transaction,
    create_transfer,
    get_accounts,
    get_accumulated_budgeted_balance,
    get_budgets,
    get_or_create_category,
    get_or_create_clock,
    get_or_create_payee,
    get_or_create_preference,
    get_preferences,
    get_ruleset,
    get_transactions,
    normalize_payee,
    reconcile_transaction,
    set_transaction_payee,
)
from actual.rules import Action, Condition, ConditionType, Rule


def test_account_relationships(session):
    today = date.today()
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
    today = date.today()
    other = create_account(session, "Other")
    coffee = create_transaction(
        session, date=today, account="Other", payee="Starbucks", notes="coffee", amount=float(-9.95)
    )
    session.commit()
    assert coffee.amount == -995
    assert len(other.transactions) == 1
    assert other.balance == decimal.Decimal("-9.95")


def test_transaction_without_payee(session):
    other = create_account(session, "Other")
    tr = create_transaction(session, date=date.today(), account=other)
    assert tr.payee_id is None


def test_reconcile_transaction(session):
    today = date.today()
    create_account(session, "Bank")
    rent_payment = create_transaction(
        session, today, "Bank", "Landlord", "Paying rent", "Expenses", -1200, imported_id="unique"
    )
    unrelated = create_transaction(
        session, today - timedelta(days=5), "Bank", "Carshop", "Car maintenance", "Car", -1200
    )
    session.commit()
    assert (
        reconcile_transaction(session, today + timedelta(days=1), "Bank", category="Rent", amount=-1200).id
        == rent_payment.id
    )
    session.commit()
    # check if the property was updated
    assert rent_payment.get_date() == today + timedelta(days=1)
    assert rent_payment.category.name == "Rent"
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


def test_create_splits(session):
    bank = create_account(session, "Bank")
    t = create_transaction(session, date.today(), bank, category="Dining", amount=-10.0)
    t_taxes = create_transaction(session, date.today(), bank, category="Taxes", amount=-2.5)
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


def test_create_splits_error(session):
    bank = create_account(session, "Bank")
    wallet = create_account(session, "Wallet")
    t1 = create_transaction(session, date.today(), bank, category="Dining", amount=-10.0)
    t2 = create_transaction(session, date.today(), wallet, category="Taxes", amount=-2.5)
    t3 = create_transaction(session, date.today() - timedelta(days=1), bank, category="Taxes", amount=-2.5)
    with pytest.raises(ActualError, match="must be the same for all transactions in splits"):
        create_splits(session, [t1, t2])
    with pytest.raises(ActualError, match="must be the same for all transactions in splits"):
        create_splits(session, [t1, t3])


def test_create_transaction_without_account_error(session):
    with pytest.raises(ActualError):
        create_transaction(session, date.today(), "foo", "")
    with pytest.raises(ActualError):
        create_transaction(session, date.today(), None, "")


def test_rule_insertion_method(session):
    # create one example transaction
    create_transaction(session, date(2024, 1, 4), create_account(session, "Bank"), "")
    session.commit()
    # create and run rule
    action = Action(field="cleared", value=1)
    assert action.as_dict() == {"field": "cleared", "op": "set", "type": "boolean", "value": True}
    condition = Condition(field="date", op=ConditionType.IS_APPROX, value=date(2024, 1, 2))
    assert condition.as_dict() == {"field": "date", "op": "isapprox", "type": "date", "value": "2024-01-02"}
    # test full rule
    rule = Rule(conditions=[condition], actions=[action], operation="all", stage="pre")
    created_rule = create_rule(session, rule, run_immediately=True)
    assert [condition.as_dict()] == json.loads(created_rule.conditions)
    assert [action.as_dict()] == json.loads(created_rule.actions)
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
    [("rollover", ZeroBudgets), ("report", ReflectBudgets)],
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
        ("rollover", False, False, decimal.Decimal(5), decimal.Decimal(25)),
        ("rollover", False, True, decimal.Decimal(15), decimal.Decimal(35)),
        ("rollover", True, True, decimal.Decimal(-5), decimal.Decimal(20)),
        ("rollover", True, False, decimal.Decimal(-15), decimal.Decimal(20)),
        ("report", False, True, decimal.Decimal(-5), decimal.Decimal(20)),
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
    actual.apply_changes(messages)
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
    t = create_transaction(session, date.today(), bank, wallet.payee, amount=-50)
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
