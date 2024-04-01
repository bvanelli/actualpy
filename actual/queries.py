from __future__ import annotations

import datetime
import decimal
import typing
import uuid

import sqlalchemy
from sqlalchemy.orm import Session, joinedload

from actual.database import (
    Accounts,
    Categories,
    CategoryGroups,
    CategoryMapping,
    PayeeMapping,
    Payees,
    Transactions,
)

T = typing.TypeVar("T")


def is_uuid(text: str, version: int = 4):
    """
    Check if uuid_to_test is a valid UUID.

    Taken from https://stackoverflow.com/a/54254115/12681470

     Parameters
    ----------
    uuid_to_test : str
    version : {1, 2, 3, 4}

     Returns
    -------
    `True` if uuid_to_test is a valid UUID, otherwise `False`.

     Examples
    --------
    >>> is_uuid('c9bf9e57-1685-4c89-bafb-ff5af830be8a')
    True
    >>> is_uuid('c9bf9e58')
    False
    """
    try:
        uuid.UUID(str(text), version=version)
        return True
    except ValueError:
        return False


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
        category_id=category_id,
        payee_id=payee_id,
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
    payee_name: str | Payees,
    notes: str,
    category_name: str = None,
    amount: decimal.Decimal = 0,
):
    acct = get_account(s, account_name)
    payee = get_or_create_payee(s, payee_name)
    if category_name:
        category_id = get_or_create_category(s, category_name, "").id
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


def create_category_group(s: Session, name: str) -> CategoryGroups:
    category_group = CategoryGroups(id=str(uuid.uuid4()), name=name, is_income=0, is_hidden=0, sort_order=0)
    s.add(category_group)
    return category_group


def get_or_create_category_group(s: Session, name: str) -> CategoryGroups:
    category_group = s.query(CategoryGroups).filter(CategoryGroups.name == name).one_or_none()
    if not category_group:
        category_group = create_category_group(s, name)
    return category_group


def get_categories(s: Session, name: str = None, include_deleted: bool = False) -> typing.List[Categories]:
    query = base_query(s, Categories, name, include_deleted).options(joinedload(Payees.transactions))
    return query.all()


def create_category(
    s: Session,
    name: str,
    group_name: str,
) -> Categories:
    category_group = get_or_create_category_group(s, group_name)
    category = Categories(
        id=str(uuid.uuid4()), name=name, hidden=0, is_income=0, sort_order=0, cat_group=category_group.id
    )
    category_mapping = CategoryMapping(id=category.id, transfer_id=category.id)
    s.add(category)
    s.add(category_mapping)
    return category


def get_category(
    s: Session, name: str | Categories, group_name: str = None, strict_group: bool = False
) -> typing.Optional[Categories]:
    if isinstance(name, Categories):
        return name
    category = (
        s.query(Categories)
        .join(CategoryGroups)
        .filter(Categories.name == name, CategoryGroups.name == group_name)
        .one_or_none()
    )
    if not category and not strict_group:
        # try to find it without the group name
        category = s.query(Categories).filter(Categories.name == name).one_or_none()
    return category


def get_or_create_category(
    s: Session, name: str | Categories, group_name: str, strict_group: bool = False
) -> Categories:
    category = get_category(s, name, group_name, strict_group)
    if not category:
        category = create_category(s, name, group_name)
    return category


def get_accounts(s: Session, name: str = None, include_deleted: bool = False) -> typing.List[Accounts]:
    query = base_query(s, Accounts, name, include_deleted).options(joinedload(Accounts.transactions))
    return query.all()


def get_payees(s: Session, name: str = None, include_deleted: bool = False) -> typing.List[Payees]:
    query = base_query(s, Payees, name, include_deleted).options(joinedload(Payees.transactions))
    return query.all()


def get_payee(s: Session, name: str | Payees) -> Payees:
    if isinstance(name, Payees):
        return name
    return s.query(Payees).filter(Payees.name == name).one_or_none()


def create_payee(s: Session, name: str | None) -> Payees:
    payee = Payees(id=str(uuid.uuid4()), name=name)
    s.add(payee)
    # add also the payee mapping
    s.add(PayeeMapping(id=payee.id, target_id=payee.id))
    return payee


def get_or_create_payee(s: Session, name: str | Payees | None) -> Payees:
    payee = get_payee(s, name)
    if not payee:
        payee = create_payee(s, name)
    return payee


def create_account(
    s: Session, name: str, initial_balance: decimal.Decimal = decimal.Decimal(0), off_budget: bool = False
) -> Accounts:
    acct = Accounts(id=str(uuid.uuid4()), name=name, offbudget=int(off_budget), closed=0)
    s.add(acct)
    # add a blank payee
    payee = create_payee(s, None)
    payee.transfer_acct = acct.id
    s.add(payee)
    # if there is an no initial balance, create it
    if initial_balance:
        payee_starting = get_or_create_payee(s, "Starting Balance")
        category = get_or_create_category(s, "Starting Balances", "Income")
        create_transaction_from_ids(
            s, acct.id, datetime.date.today(), payee_starting.id, "", category.id, initial_balance
        )
    return acct


def get_account(s: Session, name: str | Accounts) -> typing.Optional[Accounts]:
    if isinstance(name, Accounts):
        return name
    if is_uuid(name):
        account = s.query(Accounts).filter(Accounts.id == name).one_or_none()
    else:
        account = s.query(Accounts).filter(Accounts.name == name).one_or_none()
    return account


def get_or_create_account(s: Session, name: str | Accounts) -> Accounts:
    account = get_account(s, name)
    if not account:
        account = create_account(s, name)
    return account


def create_transfer(
    s: Session,
    source_account: str | Accounts,
    dest_account: str | Accounts,
    amount: decimal.Decimal,
    date: datetime.date,
    notes: str = None,
) -> typing.Tuple[Transactions, Transactions]:
    source: Accounts = get_account(s, source_account)
    dest: Accounts = get_account(s, dest_account)
    source_transaction = create_transaction_from_ids(s, source.id, date, dest.payee.id, notes, None, -amount)
    dest_transaction = create_transaction_from_ids(s, dest.id, date, source.payee.id, notes, None, amount)
    # swap the transferred ids
    source_transaction.transferred_id = dest_transaction.id
    dest_transaction.transferred_id = source_transaction.id
    # add and return objects
    s.add(source_transaction)
    s.add(dest_transaction)
    return source_transaction, dest_transaction
