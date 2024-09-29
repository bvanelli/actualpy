import decimal
import json
from datetime import date, timedelta

import pytest

from actual import Actual, ActualError
from actual.database import Notes
from actual.queries import (
    create_account,
    create_rule,
    create_splits,
    create_transaction,
    create_transfer,
    get_accounts,
    get_or_create_category,
    get_or_create_payee,
    get_ruleset,
    get_transactions,
    normalize_payee,
    reconcile_transaction,
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
