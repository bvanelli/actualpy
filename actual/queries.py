import datetime
import decimal
import typing
import uuid

import sqlalchemy
from sqlalchemy.orm import Session, joinedload

from actual.database import Accounts, Categories, PayeeMapping, Payees, Transactions

T = typing.TypeVar("T")


def get_transactions(s: Session, notes: str = None, include_deleted: bool = False) -> typing.List[Transactions]:
    query = (
        s.query(Transactions)
        .options(
            joinedload(Transactions.account),
            joinedload(Transactions.category),
            joinedload(Transactions.payee),
        )
        .filter(
            Transactions.date.isnot(None),
            Transactions.acct.isnot(None),
            sqlalchemy.or_(Transactions.is_child == 0, Transactions.parent_id.isnot(None)),
        )
        .order_by(
            Transactions.date.desc(),
            Transactions.starting_balance_flag,
            Transactions.sort_order.desc(),
            Transactions.id,
        )
    )
    if not include_deleted:
        query = query.filter(sqlalchemy.func.coalesce(Transactions.tombstone, 0) == 0)
    if notes:
        query = query.filter(Transactions.notes.ilike(f"%{sqlalchemy.text(notes).compile()}%"))
    return query.all()


def create_transaction_from_ids(
    s: Session,
    account_id: str,
    date: datetime.date,
    payee_id: typing.Optional[str],
    notes: str,
    category_id: str = None,
    amount: decimal.Decimal = 0,
) -> Transactions:
    date_int = int(datetime.date.strftime(date, "%Y%m%d"))
    t = Transactions(
        id=str(uuid.uuid4()),
        acct=account_id,
        date=date_int,
        amount=int(amount * 100),
        category=category_id,
        payee=payee_id,
        notes=notes,
        reconciled=0,
        cleared=0,
        sort_order=datetime.datetime.utcnow().timestamp(),
    )
    s.add(t)
    return t


def create_transaction(
    s: Session,
    account_name: str,
    date: datetime.date,
    payee_name: str,
    notes: str,
    category_name: str = None,
    amount: decimal.Decimal = 0,
):
    acct = get_account(s, account_name)
    payee = get_or_create_payee(s, payee_name)
    if category_name:
        category_id = get_or_create_category(s, category_name).id
    else:
        category_id = None
    return create_transaction_from_ids(s, acct.id, date, payee.id, notes, category_id, amount)


def base_query(s: Session, instance: typing.Type[T], name: str, include_deleted: bool = False) -> typing.List[T]:
    query = s.query(instance)
    if not include_deleted:
        query = query.filter(sqlalchemy.func.coalesce(instance.tombstone, 0) == 0)
    if name:
        query = query.filter(instance.name.ilike(f"%{sqlalchemy.text(name).compile()}%"))
    return query


def get_categories(s: Session, name: str = None, include_deleted: bool = False) -> typing.List[Categories]:
    query = base_query(s, Categories, name, include_deleted).options(joinedload(Payees.transactions))
    return query.all()


def get_or_create_category(s: Session, name: str) -> Categories:
    pass


def get_accounts(s: Session, name: str = None, include_deleted: bool = False) -> typing.List[Accounts]:
    query = base_query(s, Accounts, name, include_deleted).options(joinedload(Accounts.transactions))
    return query.all()


def get_payees(s: Session, name: str = None, include_deleted: bool = False) -> typing.List[Payees]:
    query = base_query(s, Payees, name, include_deleted).options(joinedload(Payees.transactions))
    return query.all()


def create_payee(s: Session, name: str) -> Payees:
    payee = Payees(id=uuid.uuid4(), name=name)
    s.add(payee)
    # add also the payee mapping
    s.add(PayeeMapping(id=payee.id, target_id=payee.id))
    return payee


def get_or_create_payee(s: Session, name: str) -> Payees:
    payee = s.query(Payees).filter(Payees.name == name).one_or_none()
    if not payee:
        payee = create_payee(s, name)
    return payee


def create_account(
    s: Session, name: str, initial_balance: decimal.Decimal = decimal.Decimal(0), off_budget: bool = False
) -> Accounts:
    acct = Accounts(id=uuid.uuid4(), name=name, offbudget=int(off_budget), closed=0)
    s.add(acct)
    # add a blank payee
    payee = create_payee(s, "")
    payee.target_id = None
    payee.transfer_acct = acct.id
    s.add(payee)
    # if there is an no initial balance, create it
    if initial_balance:
        payee_starting = get_or_create_payee(s, "Starting Balance")
        category = get_or_create_category(s, "Starting Balances")
        create_transaction_from_ids(
            s, acct.id, datetime.date.today(), payee_starting.id, "", category.id, initial_balance
        )
    return acct


def get_account(s: Session, name: str) -> typing.Optional[Accounts]:
    account = s.query(Accounts).filter(Accounts.name == name).one_or_none()
    return account
