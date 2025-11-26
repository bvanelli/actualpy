from __future__ import annotations

import datetime
import decimal
import json
import typing
import uuid
import warnings

import sqlalchemy
from pydantic import TypeAdapter
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from sqlalchemy.sql.expression import Select
from sqlmodel import Session, select

from actual.crypto import is_uuid
from actual.database import (
    Accounts,
    Categories,
    CategoryGroups,
    CategoryMapping,
    MessagesClock,
    PayeeMapping,
    Payees,
    Preferences,
    ReflectBudgets,
    Rules,
    Schedules,
    Tags,
    Transactions,
    ZeroBudgetMonths,
    ZeroBudgets,
)
from actual.exceptions import ActualError
from actual.protobuf_models import HULC_Client
from actual.rules import Action, ActionType, BetweenValue, Condition, ConditionType, Rule, RuleSet
from actual.schedules import EndMode, Frequency, Pattern, Schedule, WeekendSolveMode
from actual.utils.conversions import (
    cents_to_decimal,
    current_timestamp,
    date_to_int,
    decimal_to_cents,
    month_range,
)
from actual.utils.title import title

T = typing.TypeVar("T")


def _transactions_base_query(
    s: Session,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    account: Accounts | str | None | None = None,
    category: Categories | str | None = None,
    include_deleted: bool = False,
) -> Select:
    query = (
        select(Transactions)
        .options(
            joinedload(Transactions.account),
            joinedload(Transactions.category),
            joinedload(Transactions.payee),
        )
        .filter(
            Transactions.date.isnot(None),
            Transactions.acct.isnot(None),
        )
        .order_by(
            Transactions.date.desc(),
            Transactions.starting_balance_flag,
            Transactions.sort_order.desc(),
            Transactions.id,
        )
    )
    if start_date:
        query = query.filter(Transactions.date >= date_to_int(start_date))
    if end_date:
        query = query.filter(Transactions.date < date_to_int(end_date))
    if not include_deleted:
        query = query.filter(sqlalchemy.func.coalesce(Transactions.tombstone, 0) == 0)
    if account:
        account = get_account(s, account)
        if account:
            query = query.filter(Transactions.acct == account.id)
    if category:
        category = get_category(s, category)
        if category:
            query = query.filter(Transactions.category_id == category.id)
    return query


def _balance_base_query(
    s: Session,
    start_date: datetime.date | None,
    end_date: datetime.date | None,
    account: Accounts | str | None = None,
    category: Categories | str | None = None,
) -> Select:
    query = select(func.coalesce(func.sum(Transactions.amount), 0)).where(
        Transactions.is_parent == 0,
        Transactions.tombstone == 0,
    )
    if start_date is not None:
        query = query.filter(Transactions.date >= date_to_int(start_date))
    if end_date is not None:
        query = query.filter(Transactions.date < date_to_int(end_date))
    if account:
        account = get_account(s, account)
        if account:
            query = query.filter(Transactions.acct == account.id)
    if category:
        category = get_category(s, category)
        if category:
            query = query.filter(Transactions.category_id == category.id)
    return query


def get_transactions(
    s: Session,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    notes: str | None = None,
    account: Accounts | str | None = None,
    category: Categories | str | None = None,
    is_parent: bool = False,
    include_deleted: bool = False,
    budget: ZeroBudgets | None = None,
    cleared: bool = None,
) -> typing.Sequence[Transactions]:
    """
    Returns a list of all available transactions, sorted by date in descending order.

    :param s: Session from the Actual local database.
    :param start_date: Optional start date for the transaction period (inclusive)
    :param end_date: optional end date for the transaction period (exclusive)
    :param notes: optional notes filter for the transactions. This looks for a case-insensitive pattern rather than for
    the exact match, i.e. 'foo' would match 'Foo Bar'.
    :param account: Optional account (either Account object or Account name) filter for the transactions.
    :param category: Optional category (either Category object or Category name) filter for the transactions.
    :param is_parent: Optional boolean flag to indicate if a transaction is a parent. Parent transactions are either
                      single transactions or the main transaction with the `Transactions.splits` property. The default
                      is to return all individual splits, and the parent can be retrieved by
                      the `Transactions.parent` property.
    :param include_deleted: Includes deleted transactions from the search.
    :param budget: Optional budget filter for the transactions. The budget range and category will be used to filter the
                   final results. **Usually not used together with the `start_date` and `end_date` filters, as they
                   might hide results.
    :param cleared: Optional cleared filter for the transactions. Defaults to None, meaning both cleared
                    and non-cleared transactions are included.
    :return: List of transactions with `account`, `category` and `payee` preloaded.
    """
    query = _transactions_base_query(s, start_date, end_date, account, category, include_deleted)
    query = query.filter(Transactions.is_parent == int(is_parent))
    if notes:
        query = query.filter(Transactions.notes.ilike(f"%{sqlalchemy.text(notes).compile()}%"))
    if cleared is not None:
        query = query.filter(Transactions.cleared == int(cleared))
    if budget:
        budget_start, budget_end = budget.range
        if (start_date and start_date >= budget_end) or (end_date and end_date < budget_start):
            warnings.warn(
                f"Provided date filters [{start_date}, {end_date}) to get_transactions are outside the bounds of the "
                f"budget range [{budget_start}, {budget_end}). Results might be empty!"
            )
        budget_start, budget_end = (date_to_int(d) for d in budget.range)
        query = query.filter(
            Transactions.date >= budget_start,
            Transactions.date < budget_end,
            Transactions.category_id == budget.category_id,
        )
    return s.exec(query).all()


def match_transaction(
    s: Session,
    date: datetime.date,
    account: str | Accounts,
    payee: str | Payees = "",
    amount: decimal.Decimal | float | int = 0,
    imported_id: str | None = None,
    already_matched: list[Transactions] | None = None,
) -> Transactions | None:
    """Matches a transaction with another transaction based on the fuzzy matching.

    Described at [reconcileTransactions](
    https://github.com/actualbudget/actual/blob/b192ad955ed222d9aa388fe36557b39868029db4/packages/loot-core/src/server/accounts/sync.ts#L347).
    The matches, from strongest to the weakest are defined as follows:

    - The strongest match will be the `imported_id` (or `financial_id`),
    - The transaction with the same exact amount and around the same date (7 days), with the same payee (closest first).
    - The transaction with the same exact amount and around the same date (7 days, closest first).

    """
    # First, match with an existing transaction's imported_id
    if imported_id:
        query = _transactions_base_query(s, account=account)
        imported_transaction = s.exec(query.filter(Transactions.financial_id == imported_id)).first()
        if imported_transaction:
            return imported_transaction  # noqa
    # if not matched, look 7 days ahead and 7 days back when fuzzy matching
    query = _transactions_base_query(
        s, date - datetime.timedelta(days=7), date + datetime.timedelta(days=8), account=account
    ).filter(Transactions.amount == round(amount * 100))
    results: list[Transactions] = s.exec(query).all()  # noqa
    # filter out the ones that were already matched
    if already_matched:
        matched = {t.id for t in already_matched}
        results = [r for r in results if r.id not in matched]
    if not results:
        # nothing to be matched
        return None
    # sort the results by their distance to the original date
    results.sort(key=lambda t: abs((t.get_date() - date).total_seconds()))
    # Next, do the fuzzy matching. This first pass matches based on the
    # payee id. We do this in multiple passes so that higher fidelity
    # matching always happens first, i.e. a transaction should
    # match with low fidelity if a later transaction is going to match
    # the same one with high fidelity.
    payee = get_payee(s, payee)
    if payee:
        matching_payee = [r for r in results if r.payee_id == payee.id]
        if matching_payee:
            return matching_payee[0]
    # The final fuzzy matching pass. This is the lowest fidelity
    # matching: it just find the first transaction that hasn't been
    # matched yet. Remember the dataset only contains transactions
    # around the same date with the same amount.
    return results[0]


def create_transaction_from_ids(
    s: Session,
    date: datetime.date,
    account_id: str,
    payee_id: str | None,
    notes: str,
    category_id: str | None = None,
    amount: decimal.Decimal = 0,
    imported_id: str | None = None,
    cleared: bool = False,
    imported_payee: str | None = None,
    process_payee: bool = True,
) -> Transactions:
    """Internal method to generate a transaction from ids instead of objects."""
    date_int = date_to_int(date)
    t = Transactions(
        id=str(uuid.uuid4()),
        acct=account_id,
        date=date_int,
        amount=decimal_to_cents(amount),
        category_id=category_id,
        notes=notes,
        reconciled=0,
        cleared=int(cleared),
        sort_order=current_timestamp(),
        financial_id=imported_id,
        imported_description=imported_payee,
    )
    s.add(t)
    if process_payee:
        set_transaction_payee(s, t, payee_id)
    else:  # simply set the field without evaluating as a transfer
        t.payee_id = payee_id
    return t


def create_transaction(
    s: Session,
    date: datetime.date,
    account: str | Accounts,
    payee: str | Payees | None = None,
    notes: str | None = "",
    category: str | Categories | None = None,
    amount: decimal.Decimal | float | int = 0,
    imported_id: str | None = None,
    cleared: bool = False,
    imported_payee: str | None = None,
) -> Transactions:
    """
    Creates a transaction from the provided input.

    :param s: Session from the Actual local database.
    :param date: Date of the transaction.
    :param account: Either account name or account object (via `get_account` or `get_accounts`). Will not be
    auto-created if missing.
    :param payee: Optional name of the payee from the transaction. Will be created if missing.
    :param notes: Optional description for the transaction.
    :param category: Optional category for the transaction. Will be created if not existing.
    :param amount: Amount of the transaction. Positive indicates that the account balance will go up (deposit), and
    negative that the account balance will go down (payment)
    :param imported_id: unique id of the imported transaction. This is often provided if the transaction comes from
    a third-party system that contains unique ids (i.e., via bank sync).
    :param cleared: Visual indication that the transaction is in both your budget and in your account statement,
    and they match.
    :param imported_payee: Known internally as imported_description, this is the original name of the payee, when
    importing data and before running rules.
    :return: The generated transaction object.
    """
    acct = get_account(s, account)
    if acct is None:
        raise ActualError(f"Account {account} not found")
    if imported_payee:
        imported_payee = imported_payee.strip()
        if not payee:
            payee = imported_payee
    payee_id = None
    if payee is not None:
        payee_id = get_or_create_payee(s, payee).id
    if category:
        category_id = get_or_create_category(s, category).id
    else:
        category_id = None

    return create_transaction_from_ids(
        s, date, acct.id, payee_id, notes, category_id, amount, imported_id, cleared, imported_payee
    )


def set_transaction_payee(s: Session, transaction: Transactions, payee: Payees | str | None) -> None:
    """
    Sets a payee safely by checking if this payee is a transfer. If it is, then the transfer will be created.

    This is necessary since the payee can be set to a "transfer id", which references an account. When this happens,
    the transaction will be marked as a transfer between the two accounts, and a new transaction will need to be created
    on the other account, with the negative amount.

    :param s: Session from the Actual local database.
    :param transaction: Transaction to exchange the payee.
    :param payee: Object or unique id of the payee to be set. Must be existing
    """
    current_payee: Payees | None = None
    if isinstance(payee, str):
        payee = s.scalar(select(Payees).where(Payees.id == payee))
    if transaction.payee_id:  # resolve based on the id, in case the relationship did not load
        current_payee = s.scalar(select(Payees).where(Payees.id == transaction.payee_id))
    # if old payee was a transfer, we delete that transfer based on the `transferred_id`
    if current_payee and current_payee.transfer_acct:
        old_tr = s.scalar(select(Transactions).where(Transactions.id == transaction.transferred_id))
        if old_tr:  # should exist, but check for safety
            old_tr.delete()
            transaction.transferred_id = None
    # if setting a transfer payee, we create a transfer
    if payee and payee.transfer_acct:
        transfer = create_transaction_from_ids(
            s,
            transaction.get_date(),
            payee.account.id,
            transaction.account.payee.id,
            transaction.notes,
            None,
            cleared=bool(transaction.cleared),
            amount=-transaction.get_amount(),
            process_payee=False,
        )

        # Clear the category for on-budget account transfers, keep it untouched for off-budget account transfers
        if not payee.account.offbudget:
            transaction.category_id = None

        transfer.transferred_id, transaction.transferred_id = transaction.id, transfer.id

    # finally set the payee
    transaction.payee_id = payee.id if payee else None


def normalize_payee(payee_name: str | None, raw_payee_name: bool = False) -> str:
    """
    Normalizes the payees to make sure they are consistent across imports.

    Done according to the source code found at the [official source code](
    https://github.com/actualbudget/actual/blob/f02ca4e3d26f5b91f4234317e024022fcae2c13c/packages/loot-core/src/server/accounts/sync.ts#L206-L214)

    To make sure that the payees are consistent across the imports, an example string like `'MY PAYEE '` turns into
    `'My Payee'`, as well as `'My PaYeE'`. Those are considered to be the same payee.

    :param payee_name: The original payee name to be normalized.
    :param raw_payee_name: If the original payee name should be used instead. If the payee provided consists of spaces
                           or an empty string, it will still be assigned to `None`.
    :return: The normalized payee name.
    """
    if payee_name:
        trimmed = payee_name.strip()
        if raw_payee_name:
            return trimmed
        else:
            return title(trimmed)
    return ""


def reconcile_transaction(
    s: Session,
    date: datetime.date,
    account: str | Accounts,
    payee: str | Payees | None = None,
    notes: str | None = None,
    category: str | Categories | None = None,
    amount: decimal.Decimal | float | int = 0,
    imported_id: str | None = None,
    cleared: bool | None = None,
    imported_payee: str | None = None,
    update_existing: bool = True,
    already_matched: list[Transactions] | None = None,
) -> Transactions:
    """Matches the transaction to an existing transaction using fuzzy matching.

    :param s: Session from the Actual local database.
    :param date: Date of the transaction.
    :param account: Either account name or account object (via `get_account` or `get_accounts`). Will not be
    auto-created if missing.
    :param payee: Name of the payee from the transaction. Will be created if missing.
    :param notes: Optional description for the transaction.
    :param category: Optional category for the transaction. Will be created if not existing.
    :param amount: Amount of the transaction. Positive indicates that the account balance will go up (deposit), and
    negative that the account balance will go down (payment)
    :param imported_id: unique id of the imported transaction. This is often provided if the transaction comes from
    a third-party system that contains unique ids (i.e., via bank sync).
    :param cleared: This is a visual indication that the transaction is in both your budget and in your account
    statement, and they match.
    :param imported_payee: Known internally as imported_description, this is the original name of the payee, when
    importing data and before running rules.
    :param update_existing: If the transaction should be updated to the provided properties, if a match is found.
    :param already_matched: List of the transactions that were already matched. When importing a list of transactions,
    this would prevent transactions with the exact same (date, amount) to be assigned as duplicates.
    :return: The generated or matched transaction object.
    """
    account = get_account(s, account)
    match = match_transaction(s, date, account, payee, amount, imported_id, already_matched)
    if match:
        # try to update fields
        if update_existing:
            if notes is not None:
                match.notes = notes
            if cleared is not None:
                match.cleared = cleared
            if category:
                match.category_id = get_or_create_category(s, category).id
            if payee:
                match.payee_id = get_or_create_payee(s, payee).id
            if imported_id:
                match.financial_id = imported_id
            match.set_date(date)
        return match
    if cleared is None:
        cleared = False
    if notes is None:
        notes = ""
    return create_transaction(s, date, account, payee, notes, category, amount, imported_id, cleared, imported_payee)


def create_splits(
    s: Session, transactions: typing.Sequence[Transactions], payee: str | Payees = None, notes: str = ""
) -> Transactions:
    """
    Creates a transaction with splits based on the list of transactions.

    The total amount will be evaluated as the sum of the individual amounts. All dates must be set to the same value.

    :param s: Session from the Actual local database.
    :param transactions: List of transactions that will be added to the splits.
    :param payee: (Deprecated) Name or object of the payee from the transaction. Ignored for all purposes, since
        only the child transactions have a payee.
    :param notes: Optional description for the transaction.
    :return: The generated transaction object for the parent transaction.
    """
    if payee:
        warnings.warn(
            "Transactions parents shall have no payee defined, only individual splits.", category=DeprecationWarning
        )
    if not all(transactions[0].date == t.date for t in transactions) or not all(
        transactions[0].acct == t.acct for t in transactions
    ):
        raise ActualError("`date` and `acct` must be the same for all transactions in splits")
    split_amount = decimal.Decimal(sum(t.get_amount() for t in transactions))
    split_transaction = create_transaction_from_ids(
        s, transactions[0].get_date(), transactions[0].acct, None, notes, None, split_amount
    )
    split_transaction.is_parent = 1
    split_transaction.is_child = 0
    for transaction in transactions:
        transaction.is_parent = 0
        transaction.is_child = 1
        transaction.parent_id = split_transaction.id
    return split_transaction


def create_split(s: Session, transaction: Transactions, amount: float | decimal.Decimal) -> Transactions:
    """
    Creates a transaction split based on the parent transaction.

    This is the opposite of [create_splits][actual.queries.create_splits], that joins all transactions as one big
    transaction. When using this method, you need to make sure all splits that you add to a transaction are then valid.

    :param s: Session from the Actual local database.
    :param transaction: Parent transaction to the split you want to create.
    :param amount: Amount of the split.
    :return: The generated transaction object for the split transaction.
    """
    split = create_transaction(
        s, transaction.get_date(), transaction.account, transaction.payee, None, transaction.category, amount=amount
    )
    split.parent_id, split.is_parent, split.is_child = transaction.id, 0, 1
    return split


def _base_query(instance: type[T], name: str | None = None, include_deleted: bool = False) -> Select:
    """Internal method to reduce querying complexity on sub-functions."""
    query = select(instance)
    if not include_deleted:
        query = query.filter(sqlalchemy.func.coalesce(instance.tombstone, 0) == 0)
    if name:
        query = query.filter(instance.name.ilike(f"%{sqlalchemy.text(name).compile()}%"))
    return query


def get_category_groups(s: Session, name: str = None, include_deleted: bool = False, is_income: bool = None):
    """
    Returns a list of all available category groups.

    :param s: Session from the Actual local database.
    :param name: Pattern name of the category group, case-insensitive.
    :param include_deleted: Includes all category groups deleted via frontend. They would not show normally.
    :param is_income: If set, it will filter by the `is_income` property of the category.
    :return: List of category groups with `categories` already loaded.
    """
    query = (
        _base_query(CategoryGroups, name, include_deleted)
        .order_by(CategoryGroups.sort_order)
        .options(joinedload(CategoryGroups.categories))
    )
    if is_income is not None:
        query = query.filter(CategoryGroups.is_income == is_income)
    return s.exec(query).unique().all()


def create_category_group(s: Session, name: str) -> CategoryGroups:
    """
    Creates a new category with the group name `name`.

    Make sure you avoid creating payees with duplicate names, as it makes it difficult to find them without knowing
    the unique id beforehand.
    """
    category_group = CategoryGroups(id=str(uuid.uuid4()), name=name, is_income=0, sort_order=0)
    s.add(category_group)
    return category_group


def get_or_create_category_group(s: Session, name: str) -> CategoryGroups:
    """
    Gets or create the category group, if not found with `name`.

    Deleted category groups are excluded from the search.
    """
    category_group = s.exec(
        select(CategoryGroups).filter(CategoryGroups.name == name, CategoryGroups.tombstone == 0)
    ).one_or_none()
    if not category_group:
        category_group = create_category_group(s, name)
    return category_group


def get_categories(
    s: Session, name: str | None = None, include_deleted: bool = False, is_income: bool = None
) -> typing.Sequence[Categories]:
    """
    Returns a list of all available categories.

    :param s: Session from the Actual local database.
    :param name: Pattern name of the category, case-insensitive.
    :param include_deleted: Includes all categories deleted via frontend. They would not show normally.
    :param is_income: If set, it will filter by the `is_income` property of the category.
    :return: List of categories with `transactions` already loaded.
    """
    query = (
        _base_query(Categories, name, include_deleted)
        .order_by(Categories.sort_order)
        .options(joinedload(Categories.transactions))
    )
    if is_income is not None:
        query = query.filter(Categories.is_income == is_income)
    return s.exec(query).unique().all()


def create_category(
    s: Session,
    name: str,
    group_name: str | None = None,
) -> Categories:
    """
    Creates a new category with the `name` and `group_name`. If the group is not existing, it will also be created.

    Make sure you avoid creating categories with duplicate names, as it makes it difficult to find them without knowing
    the unique id beforehand. The exception is to have them in separate group names, but you then need to provide the
    group name to the method also.

    If a group name is not provided, the default 'Usual Expenses' will be picked.
    """
    category_group = get_or_create_category_group(s, group_name if group_name is not None else "Usual Expenses")
    category = Categories(
        id=str(uuid.uuid4()), name=name, hidden=0, is_income=0, sort_order=0, cat_group=category_group.id
    )
    category_mapping = CategoryMapping(id=category.id, transfer_id=category.id)
    s.add(category)
    s.add(category_mapping)
    return category


def get_tag(s: Session, name: str) -> Tags | None:
    """Gets one individual tag based on an exact match of the name."""
    return s.exec(select(Tags).where(Tags.tag == name.lstrip("#"))).one_or_none()


def get_tags(
    s: Session, name: str | None = None, description: str | None = None, include_deleted: bool = False
) -> typing.Sequence[Tags]:
    """Get all tags stored on the Actual database using a name or description pattern.

    :param s: Session from the Actual local database.
    :param name: Pattern name of the tag name, case-insensitive.
    :param description: Pattern name of the tag description, case-insensitive.
    :param include_deleted: Includes all tags which were deleted via frontend. They would not show normally.
    """
    query = _base_query(Tags, None, include_deleted)
    if name:
        query = query.filter(Tags.tag.ilike(f"%{sqlalchemy.text(name.lstrip('#')).compile()}%"))
    if description:
        query = query.filter(Tags.description.ilike(f"%{sqlalchemy.text(name).compile()}%"))
    return s.exec(query).unique().all()


def create_tag(s: Session, name: str, description: str | None = None, color: str = "#690CB0") -> Tags:
    """
    Creates a new tag with the `name` and `description`. Name **should not** be provided with the hashtag.

    The name will be the tag used inside the transaction. You can use this tag afterward by setting the
    notes of a transaction. If your tag is called `'foo'`, you can append `'#foo'` to the transaction notes.

    The color of the tag can be provided as hexadecimal (i.e. `'#690CB0'` for purple or `'#1976D2'` for blue).
    """
    tag = Tags(id=str(uuid.uuid4()), tag=name.lstrip("#"), description=description, color=color)
    s.add(tag)
    return tag


def get_category(
    s: Session, name: str | Categories, group_name: str | None = None, strict_group: bool = False
) -> Categories | None:
    """Gets an existing category by name, returns `None` if not found. Deleted payees are excluded from the search."""
    if isinstance(name, Categories):
        return name
    category = s.exec(
        select(Categories)
        .join(CategoryGroups)
        .filter(Categories.name == name, Categories.tombstone == 0, CategoryGroups.name == group_name)
    ).one_or_none()
    if not category and not strict_group:
        # try to find it without the group name
        category = s.exec(select(Categories).filter(Categories.name == name, Categories.tombstone == 0)).one_or_none()
    return category


def get_or_create_category(
    s: Session, name: str | Categories, group_name: str | None = None, strict_group: bool = False
) -> Categories:
    """
    Gets or create the category, if not found with `name`.

    If the category already exists, but in a different group, but the category name is still unique, it will be
    returned, unless `strict_group` is set to `True`.

    If a group name is not provided, the default 'Usual Expenses' will be picked.
    """
    category = get_category(s, name, group_name, strict_group)
    if not category:
        category = create_category(s, name, group_name or "Usual Expenses")
    return category


def get_accounts(
    s: Session,
    name: str | None = None,
    include_deleted: bool = False,
    closed: bool | None = None,
    off_budget: bool | None = None,
) -> typing.Sequence[Accounts]:
    """
    Returns a list of all available accounts.

    :param s: Session from the Actual local database.
    :param name: Pattern name of the payee, case-insensitive.
    :param include_deleted: Includes all payees deleted via frontend. They would not show normally.
    :param closed: Optional closed filter; Defaults to None (includes open and closed accounts).
    :param off_budget: Optional off_budget filter; Defaults to None (includes off and on budget accounts).
    :return: List of accounts with `transactions` already loaded.
    """
    query = (
        _base_query(Accounts, name, include_deleted)
        .order_by(Accounts.sort_order)
        .options(joinedload(Accounts.transactions))
    )

    if closed is not None:
        query = query.filter(Accounts.closed == int(closed))
    if off_budget is not None:
        query = query.filter(Accounts.offbudget == int(off_budget))

    return s.exec(query).unique().all()


def get_payees(s: Session, name: str | None = None, include_deleted: bool = False) -> typing.Sequence[Payees]:
    """
    Returns a list of all available payees.

    :param s: Session from the Actual local database.
    :param name: Pattern name of the payee, case-insensitive.
    :param include_deleted: Includes all payees deleted via frontend. They would not show normally.
    :return: List of payees with `transactions` already loaded.
    """
    query = _base_query(Payees, name, include_deleted).options(joinedload(Payees.transactions))
    return s.exec(query).unique().all()


def get_payee(s: Session, name: str | Payees) -> Payees | None:
    """Gets an existing payee by name, returns `None` if not found. Deleted payees are excluded from the search."""
    if isinstance(name, Payees):
        return name
    return s.exec(select(Payees).filter(Payees.name == name, Payees.tombstone == 0)).one_or_none()


def create_payee(s: Session, name: str | None) -> Payees:
    """
    Creates a new payee with the desired name.

    Make sure you avoid creating payees with duplicate names, as it makes it difficult to find them without knowing the
    unique id beforehand.
    """
    payee = Payees(id=str(uuid.uuid4()), name=name)
    s.add(payee)
    # add also the payee mapping
    s.add(PayeeMapping(id=payee.id, target_id=payee.id))
    return payee


def get_or_create_payee(s: Session, name: str | Payees) -> Payees:
    """
    Gets an existing payee by name, and if it does not exist, creates a new one.

    If the payee is created twice, this method will fail with a database error.
    """
    payee = get_payee(s, name)
    if not payee:
        payee = create_payee(s, name)
    return payee


def create_account(
    s: Session, name: str, initial_balance: decimal.Decimal | float = decimal.Decimal(0), off_budget: bool = False
) -> Accounts:
    """
    Creates a new account with the name and balance.

    Make sure you avoid creating accounts with duplicate names, as it makes it difficult to find them without knowing
    the unique id beforehand.
    """
    acct = Accounts(id=str(uuid.uuid4()), name=name, offbudget=int(off_budget), closed=0)
    s.add(acct)
    # add a blank payee
    payee = create_payee(s, None)
    payee.transfer_acct = acct.id
    s.add(payee)
    # if there is no initial balance, create it
    if initial_balance:
        payee_starting = get_or_create_payee(s, "Starting Balance")
        category = get_or_create_category(s, "Starting Balances", "Income")
        create_transaction_from_ids(
            s, datetime.date.today(), acct.id, payee_starting.id, "", category.id, initial_balance
        )
    return acct


def get_account(s: Session, name: str | Accounts) -> Accounts | None:
    """
    Gets an account with the desired name, otherwise returns `None`. Deleted accounts are excluded from the search.
    """
    if isinstance(name, Accounts):
        return name
    if is_uuid(name):
        query = select(Accounts).filter(Accounts.id == name, Accounts.tombstone == 0)
    else:
        query = select(Accounts).filter(Accounts.name == name, Accounts.tombstone == 0)
    return s.exec(query).one_or_none()


def get_or_create_account(s: Session, name: str | Accounts) -> Accounts:
    """Gets or create the account, if not found with `name`. The initial balance will be set to `0`."""
    account = get_account(s, name)
    if not account:
        account = create_account(s, name)
    return account


def _get_budget_table(s: Session) -> type[ReflectBudgets | ZeroBudgets]:
    """
    Finds out which type of budget the user uses. The types are:

    - Envelope budgeting (default, recommended): `budgetType` is `rollover` or `envelope`, table is ZeroBudgets
    - Tracking budgeting: `budgetType` is `report` or `tracking`, table is `ReflectBudgets`

    :param s: Session from the Actual local database.
    :return: Table object for the budget type, based on the preferences.
    """
    budget_type = get_preference(s, "budgetType")
    if budget_type and budget_type.value in ("report", "tracking"):
        return ReflectBudgets
    else:  # budgetType is rollover or envelope
        return ZeroBudgets


def get_budgets(
    s: Session, month: datetime.date | None = None, category: str | Categories | None = None
) -> typing.Sequence[ZeroBudgets | ReflectBudgets]:
    """
    Returns a list of all available budgets.

    The object type returned will be either [ZeroBudgets][actual.database.ZeroBudgets] or
    [ReflectBudgets][actual.database.ReflectBudgets], depending on the type of budget selected globally. The budget
    options are:

    - Envelope budgeting (default): [ZeroBudgets][actual.database.ZeroBudgets]
    - Tracking budgeting: [ReflectBudgets][actual.database.ReflectBudgets]

    :param s: Session from the Actual local database.
    :param month: Month to get budgets for, as a date for that month. Use `datetime.date.today()` if you want the budget
                  for the current month.
    :param category: The category to filter for the budget. By default, the query looks for all budgets.
    :return: List of budgets. It's important to note that budgets will only exist if they are actively set beforehand.
             When the frontend shows a budget as 0.00, it might not be returned by this method.
    """
    table = _get_budget_table(s)
    query = select(table).options(joinedload(table.category)).order_by(table.month.asc())
    if month:
        month_filter = date_to_int(month, month_only=True)
        query = query.filter(table.month == month_filter)
    if category:
        category = get_category(s, category)
        if not category:
            raise ActualError("Category is provided but does not exist.")
        query = query.filter(table.category_id == category.id)
    return s.exec(query).unique().all()


def get_budget(s: Session, month: datetime.date, category: str | Categories) -> ZeroBudgets | ReflectBudgets | None:
    """
    Gets an existing budget by category name, returns `None` if not found.

    :param s: Session from the Actual local database.
    :param month: Month to get budgets for, as a date for that month. Use `datetime.date.today()` if you want the budget
                  for the current month.
    :param category: Category to filter for the budget.
    :return: Returns the budget matching the month and category. If not found, returns `None`. If the budget is not
             set via frontend, it will show as 0.00, but this function will still return `None`.
    """
    budgets = get_budgets(s, month, category)
    return budgets[0] if budgets else None


def create_budget(
    s: Session,
    month: datetime.date,
    category: str | Categories,
    amount: decimal.Decimal | float | int = 0.0,
    carryover: bool | None = None,
) -> ZeroBudgets | ReflectBudgets:
    """
    Gets an existing budget based on the month and category. If it already exists, the amount will be replaced by
    the new amount.

    :param s: Session from the Actual local database.
    :param month: Month to get budgets for, as a date for that month. Use `datetime.date.today()` if you want the budget
                  for the current month.
    :param category: Category to filter for the budget.
    :param amount: Amount for the budget.
    :param carryover: If set to `True`, the budget will carry the negative balances over to the next month. By default,
                      no value is actively set, and the database will set it to `False`.
    :return: Return budget matching the month and category, and assigns the amount to the budget. If not found, creates
             a new budget.
    """
    table = _get_budget_table(s)
    budget = get_budget(s, month, category)
    if budget:
        budget.set_amount(amount)
        return budget
    category = get_category(s, category)
    budget = table(id=str(uuid.uuid4()), category_id=category.id)
    budget.set_date(month)
    budget.set_amount(amount)
    if carryover is not None:
        budget.carryover = int(carryover)
    s.add(budget)
    return budget


def get_budgeted_balance(s: Session, month: datetime.date, category: str | Categories) -> decimal.Decimal:
    """
    Returns the budgeted balance under the category for the individual month, not taking into account accumulated value.

    If you want a function that consideres the accumulated value in case of an envelope budget, check the
    [get_accumulated_budgeted_balance][actual.queries.get_accumulated_budgeted_balance].

    :param s: Session from the Actual local database.
    :param month: Month to get budgets for, as a date for that month. Use `datetime.date.today()` if you want the budget
                  for the current month.
    :param category:  Category to filter for the budget.
    :return: A decimal representing the budget real balance for the category.
    """

    budget = get_budget(s, month, category)
    if not budget:
        # create a temporary budget
        range_start, range_end = month_range(month)
        balance = s.scalar(_balance_base_query(s, range_start, range_end, category=category))
        budget_leftover = cents_to_decimal(balance)
    else:
        budget_leftover = budget.get_amount() + budget.balance  # we can sum because the balance is negative
    return budget_leftover


def get_accumulated_budgeted_balance(s: Session, month: datetime.date, category: str | Categories) -> decimal.Decimal:
    """
    Returns the budgeted balance as shown by the Actual UI under the category.

    This is calculated by summing all considered budget values and subtracting all transactions for them.

    When using **envelope budget**, this value will accumulate with each consecutive month that your spending is
    greater than your budget. If this value goes under 0.00, your budget is reset for the next month, unless the
    `carryover` flag is set on the budget. In this case, the balance will be held for the next month.

    When using **tracking budget**, only the current month is considered for savings, so no previous values will carry
    over.

    :param s: Session from the Actual local database.
    :param month: Month to get budgets for, as a date for that month. Use `datetime.date.today()` if you want the budget
                  for the current month.
    :param category:  Category to filter for the budget.
    :return: A decimal representing the budget real balance for the category. This is evaluated by adding all
             previous leftover budgets that have a value greater than 0.
    """
    category = get_category(s, category)
    from actual.budgets import get_budget_history

    history = get_budget_history(s, month)
    if not history:
        return decimal.Decimal(0)
    return history[-1].from_category(category).accumulated_balance


def get_held_budget(s: Session, month: datetime.date) -> ZeroBudgetMonths | None:
    """
    Gets the budget held for a budget month from the database.

    The held budget only applies to envelope budgeting.

    :param s: Session from the Actual local database.
    :param month: Month to get budgets for, as a date for that month. Use `datetime.date.today()` if you want
                  the current month.
    :return: Returns the held budget object.
    """
    converted_month = datetime.date.strftime(month, "%Y-%m")
    query = select(ZeroBudgetMonths).where(ZeroBudgetMonths.id == converted_month)
    return s.exec(query).one_or_none()


def create_transfer(
    s: Session,
    date: datetime.date,
    source_account: str | Accounts,
    dest_account: str | Accounts,
    amount: decimal.Decimal | int | float,
    notes: str | None = None,
) -> tuple[Transactions, Transactions]:
    """
    Creates a transfer of money between two accounts. The amount is provided as a positive value.

    :param s: Session from the Actual local database.
    :param date: Date of the transfer.
    :param source_account: Account that will transfer the money and reduce its balance.
    :param dest_account: Account that will receive the money and increase its balance.
    :param amount: Amount, as a positive decimal, to be transferred.
    :param notes: Additional description for the transfer.
    :return: Tuple containing both transactions, as one is created per account. The transactions would be
             cross-referenced by their `transferred_id`.
    """
    if amount <= 0:
        raise ActualError("Amount must be a positive value.")
    source: Accounts = get_account(s, source_account)
    dest: Accounts = get_account(s, dest_account)
    source_transaction = create_transaction_from_ids(
        s, date, source.id, dest.payee.id, notes, None, -amount, process_payee=False
    )
    dest_transaction = create_transaction_from_ids(
        s, date, dest.id, source.payee.id, notes, None, amount, process_payee=False
    )
    # swap the transferred ids
    source_transaction.transferred_id = dest_transaction.id
    dest_transaction.transferred_id = source_transaction.id
    # add and return objects
    s.add(source_transaction)
    s.add(dest_transaction)
    return source_transaction, dest_transaction


def get_rules(s: Session, include_deleted: bool = False) -> typing.Sequence[Rules]:
    """
    Returns a list of all available rules.

    :param s: Session from the Actual local database.
    :param include_deleted: Includes all payees deleted via frontend. They would not show normally.
    :return: List of rules.
    """
    return s.exec(_base_query(Rules, None, include_deleted)).all()


def get_ruleset(s: Session) -> RuleSet:
    """
    Returns a list of all available rules, but as a rule set that can be used to be applied to existing transactions.

    :param s: Session from the Actual local database.
    :return: RuleSet object that contains all rules and can be either.
    """
    rule_set = list()
    for rule in get_rules(s):
        conditions = TypeAdapter(list[Condition]).validate_json(rule.conditions)
        actions = TypeAdapter(list[Action]).validate_json(rule.actions)
        rs = Rule(conditions=conditions, operation=rule.conditions_op, actions=actions, stage=rule.stage)  # noqa
        rule_set.append(rs)
    return RuleSet(rules=rule_set)


def create_rule(
    s: Session,
    rule: Rule,
    run_immediately: bool = False,
) -> Rules:
    """
    Creates a rule based on the conditions and actions defined on the input rule. The rule can be ordered to run
    immediately, running the action for all entries that match the conditions on insertion.

    :param s: Session from the Actual local database.
    :param rule: A constructed [Rule][actual.rules.Rule] object. The rule format and data types are validated on the
                 constructor **but the data itself is not**. Make sure that, if you reference uuids, that they exist.
    :param run_immediately: If the run should run for all transactions on insert, defaults to `False`.
    :return: Rule database object created.
    """
    conditions = json.dumps([c.model_dump(mode="json", by_alias=True) for c in rule.conditions])
    actions = json.dumps([a.model_dump(mode="json", by_alias=True) for a in rule.actions])
    database_rule = Rules(
        id=str(uuid.uuid4()), stage=rule.stage, conditions_op=rule.operation, conditions=conditions, actions=actions
    )
    s.add(database_rule)
    if run_immediately:
        for t in get_transactions(s):
            if rule.run(t):
                s.add(t)
    return database_rule


def get_schedules(
    s: Session,
    name: str | None = None,
    include_deleted: bool = False,
    include_completed: bool = False,
) -> typing.Sequence[Schedules]:
    """
    Returns a list of all available schedules, automatically filtering completed schedules.

    :param s: Session from the Actual local database.
    :param name: Pattern name of the payee, case-insensitive.
    :param include_deleted: Includes all payees deleted via frontend. They would not show normally.
    :param include_completed: Includes all schedules that have been completed. They are also shown as hidden on the
           frontend.
    :return: List of schedules.
    """
    query = _base_query(Schedules, name, include_deleted)
    if not include_completed:
        query = query.filter(Schedules.completed == 0)
    return s.exec(query).all()


def create_schedule(
    s: Session,
    date: datetime.date | datetime.datetime | Schedule,
    amount: decimal.Decimal | float | tuple[decimal.Decimal, decimal.Decimal] | tuple[float, float],
    amount_operation: typing.Literal["is", "isapprox", "isbetween"] = "isapprox",
    name: str | None = None,
    payee: str | Payees | None = None,
    account: str | Accounts | None = None,
    posts_transaction: bool = False,
) -> Schedules:
    """
    Create a schedule database object based on the parameters.

    :param s: Session from the Actual local database.
    :param date: Date of the schedule (if it only happens once) or [Schedule][actual.schedules.Schedule] object, that
                 can be initialized more easily with the helper
                 [create_schedule_config][actual.queries.create_schedule_config].
    :param amount: Amount that the scheduled transaction will have. Provide only one number, except if the amount uses
                   an `isbetween` in `amount_operation`, in this case num1 and 2 should be provided as a tuple
                   `(num1, num2)`, where `num1 < num2`.
    :param amount_operation: Controls how amount is interpreted. Set this value to `is` when your schedule always
                             matches a fixed amount. By default, `isapprox` will be used, and the amount will be
                             treated as an estimate and will match transactions that are plus/minus 7.5% of the
                             amount entered. When using `isbetween`, make sure that the amount is provided as a
                             tuple (num1, num2), where `num1 < num2`.
    :param name: Not mandatory. Schedule names must be unique.
    :param payee: Optional; will default to `None`.
    :param account: Optional; will default to `None`.
    :param posts_transaction: Whether the schedule should auto-post transactions on your behalf. Defaults to false.
    :return: Rule database object created.
    """
    if amount_operation == "isbetween" and not isinstance(amount, tuple):
        raise ActualError("When using 'isbetween', amount must be a tuple (num1, num2), where num1 < num2.")

    schedule_id = str(uuid.uuid4())
    conditions = []
    # Handle the payee condition
    if payee := get_payee(s, payee):
        conditions.append(
            Condition(field="description", op=ConditionType.IS, value=payee.id),
        )
    else:
        # payees specifically have a fallback case, to look for only entries without payee
        conditions.append(
            Condition(field="description", op=ConditionType.IS, value=None),
        )
    # Handle the account condition
    if account := get_account(s, account):
        conditions.append(
            Condition(field="acct", op=ConditionType.IS, value=account.id),
        )
    # Handle the date condition
    conditions.append(
        Condition(field="date", op=ConditionType.IS_APPROX, value=date),
    )
    # Handle the amount condition
    if amount_operation == "isbetween":
        conditions.append(
            Condition(
                field="amount",
                op=ConditionType(amount_operation),
                value=BetweenValue(num1=decimal_to_cents(amount[0]), num2=decimal_to_cents(amount[1])),
            )
        )
    else:
        conditions.append(Condition(field="amount", op=ConditionType(amount_operation), value=decimal_to_cents(amount)))

    actions = [
        Action(op=ActionType.LINK_SCHEDULE, value=schedule_id),
    ]
    rule_obj = Rule(conditions=conditions, operation="and", actions=actions, stage=None)
    rule = create_rule(s, rule_obj, run_immediately=False)
    schedule = Schedules(
        id=schedule_id,
        rule_id=rule.id,
        active=0,
        completed=0,
        posts_transaction=int(posts_transaction),
        tombstone=0,
        name=name,
        rule=rule,
    )
    s.add(schedule)
    return schedule


def create_schedule_config(
    start: datetime.date | datetime.datetime,
    end_mode: EndMode | typing.Literal["never", "on_date", "after_n_occurrences"] | str = EndMode.NEVER,
    end_occurrences: int | None = None,
    end_date: datetime.date | datetime.datetime | None = None,
    interval: int = 1,
    frequency: Frequency | typing.Literal["daily", "weekly", "monthly", "yearly"] | str = Frequency.MONTHLY,
    patterns: list[Pattern] | None = None,
    skip_weekend: bool = False,
    weekend_solve_mode: WeekendSolveMode | typing.Literal["before", "after"] | str = WeekendSolveMode.AFTER,
) -> Schedule:
    """
    Created a recurring config for the schedule.

    This configuration indicates how the schedule runs. It is an alternative way of creating a schedule (instead of
    the simple start date and frequency option) that can be configured quite

    For the date fields, if a datetime is provided, only the truncated value will be used.

    :param start: The date indicating the start date of the recurrence.
    :param end_mode: Specifies how the recurrence ends: never ends, after a number of occurrences,
                     or on a specific date.
    :param end_occurrences: Used when `end_mode` is `'after_n_occurrences'`. Indicates how many times it should repeat.
    :param end_date: Used when `end_mode` is `'on_date'`. The date object indicating when the recurrence should end.
    :param interval: The interval at which the recurrence happens. Defaults to `1` if omitted.
    :param frequency: How often the schedule repeats.
    :param patterns: Optional patterns to control specific dates for recurrence (e.g., certain weekdays or month days).
    :param skip_weekend: If true, skips weekends when calculating recurrence dates. This option can be further
                         configured with the `weekend_solve_mode` parameter.
    :param weekend_solve_mode: If a calculated date falls on a weekend and `skip_weekend` is true, this controls whether
                               the date moves to the before or after weekday.
    :return: Schedule object (not database object) that can be used to create a schedule via
             [create_schedule][actual.queries.create_schedule].
    """
    if isinstance(frequency, str):
        frequency = Frequency(frequency)
    if isinstance(weekend_solve_mode, str):
        weekend_solve_mode = WeekendSolveMode(weekend_solve_mode)
    if isinstance(end_mode, str):
        end_mode = EndMode(end_mode)
    if end_mode == EndMode.ON_DATE and end_date is None:
        raise ActualError("When using end_mode 'on_date', the end_date must be provided.")
    if end_mode == EndMode.AFTER_N_OCCURRENCES and end_occurrences is None:
        raise ActualError("When using end_mode 'after_n_occurrences', the end_occurrences must be provided.")

    if end_date is None:
        # do the same as the frontend and fill in the start date
        end_date = start
    if end_occurrences is None:
        end_occurrences = 1

    return Schedule(
        start=start,
        interval=interval,
        frequency=frequency,
        patterns=patterns or [],
        skipWeekend=skip_weekend,
        weekendSolveMode=weekend_solve_mode,
        endMode=end_mode,
        endOccurrences=end_occurrences,
        endDate=end_date,
    )


def get_or_create_clock(s: Session, client: HULC_Client | None = None) -> MessagesClock:
    """
    Loads the HULC Clock from the database that tells the client from when the messages should be retrieved.

    See the [original implementation.](
    https://github.com/actualbudget/actual/blob/5bcfc71be67c6e7b7c8b444e4c4f60da9ea9fdaa/packages/loot-core/src/server/db/index.ts#L81-L98)

    If the clock is not existing, it will be created based on the passed client. If the client is missing, an empty
    client is created. If the clock was already existing, the timestamp will only be overwritten if a client is
    provided, otherwise the original value will be returned.

    :param s: Session from the Actual local database.
    :param client: HULC Client object.
    :return: The message clock object.
    """
    clock = s.exec(select(MessagesClock)).one_or_none()
    if not clock:
        clock = MessagesClock(id=1)
        if not client:
            client = HULC_Client()  # create a default client
        clock.set_clock({"timestamp": str(client), "merkle": {}})
        s.add(clock)
    else:
        # update the clock only if the client was provided
        if client:
            clock.set_timestamp(client)
    return clock


def get_preferences(s: Session) -> typing.Sequence[Preferences]:
    """
    Loads the preference list from the database.

    :param s: Session from the Actual local database.
    :return: List of preferences.
    """
    return s.exec(select(Preferences)).all()


def get_or_create_preference(s: Session, key: str, value: str) -> Preferences:
    """
    Loads the preference list from the database. If the key is missing, a new one is created, otherwise it's updated.

    :param s: Session from the Actual local database.
    :param key: Key of the preference.
    :param value: Value of the preference.
    :return: The preference object.
    """
    preference = get_preference(s, key)
    if preference is None:
        preference = Preferences(id=key, value=value)
        s.add(preference)
    else:
        preference.value = value
    return preference


def get_preference(s: Session, key: str, default: str | None = None) -> Preferences | None:
    """
    Gets an existing preference by key name, returns `None` if not found.

    :param s: Session from the Actual local database.
    :param key: Preference name.
    :param default: Default value to be returned if the key is not found.
    :return: Preference matching the key provided. If not found, returns `None`.
    """
    return s.exec(select(Preferences).where(Preferences.id == key)).one_or_none() or default
