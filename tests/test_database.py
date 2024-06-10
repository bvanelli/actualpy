import decimal
import tempfile
from datetime import date, timedelta

import pytest
from sqlmodel import Session, create_engine

from actual import ActualError
from actual.database import SQLModel
from actual.queries import (
    create_account,
    create_splits,
    create_transaction,
    create_transfer,
    get_or_create_category,
    get_or_create_payee,
    get_transactions,
)


@pytest.fixture
def session():
    with tempfile.NamedTemporaryFile() as f:
        sqlite_url = f"sqlite:///{f.name}"
        engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            yield session


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


def test_create_transaction_without_account_error(session):
    with pytest.raises(ActualError):
        create_transaction(session, date.today(), "foo", "")
    with pytest.raises(ActualError):
        create_transaction(session, date.today(), None, "")
