"""
This file was partially generated using sqlacodegen using the downloaded version of the db.sqlite file export
in order to update this file, you can generate the code with:

```bash
sqlacodegen --generator sqlmodels sqlite:///db.sqlite
```

and patch the necessary models by merging the results. The [BaseModel][actual.database.BaseModel] defines all models
that can be updated from the user, and must contain a unique `id`. Those models can then be converted automatically
into a protobuf change message using [BaseModel.convert][actual.database.BaseModel.convert].

It is preferred to create database entries using the [queries][actual.queries], rather than using the raw database
model.
"""

import datetime
import decimal
import json
from collections.abc import Sequence
from typing import Optional

from sqlalchemy import MetaData, Table, engine, event, inspect
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import class_mapper, object_session, validates
from sqlmodel import (
    JSON,
    Boolean,
    Column,
    Field,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Relationship,
    Session,
    SQLModel,
    Text,
    func,
    or_,
    select,
    text,
)

from actual.exceptions import ActualInvalidOperationError
from actual.protobuf_models import HULC_Client, Message
from actual.utils.conversions import cents_to_decimal, date_to_int, decimal_to_cents, int_to_date, month_range

"""
This variable contains the internal model mappings for all databases. It solves a couple of issues, namely having the
mapping from `__tablename__` to the actual SQLAlchemy class, and later mapping the SQL column into the Pydantic field,
which could be different and follows the Python naming convention. An example is the field `Transactions.is_parent`,
that converts into the SQL equivalent `transactions.isParent`. In this case, we would have the following entries:

```
__TABLE_COLUMNS_MAP__ = {
    "transactions": {
        "entity": <class 'actual.database.Transactions'>,
        "columns": {
            "isParent": "is_parent"
        }
    }
}
```
"""
__TABLE_COLUMNS_MAP__ = dict()


def reflect_model(eng: engine.Engine) -> MetaData:
    """Reflects the current state of the database, containing the state of all remote tables and columns."""
    local_meta = MetaData()
    local_meta.reflect(bind=eng)
    return local_meta


def get_class_from_reflected_table_name(metadata: MetaData, table_name: str) -> type[Table] | None:
    """
    Returns, based on the defined tables on the reflected model the corresponding SQLAlchemy table.

    If not found, returns `None`.
    """
    return metadata.tables.get(table_name, None)


def get_attribute_from_reflected_table_name(metadata: MetaData, table_name: str, column_name: str) -> Column | None:
    """
    Returns, based on the defined reflected model the corresponding and the SAColumn.

    If not found, returns `None`.
    """
    table = get_class_from_reflected_table_name(metadata, table_name)
    return table.columns.get(column_name, None)


def get_class_by_table_name(table_name: str) -> type[SQLModel] | None:
    """
    Returns, based on the defined tables `__tablename__` the corresponding SQLModel object.

    If not found, returns `None`.
    """
    return __TABLE_COLUMNS_MAP__.get(table_name, {}).get("entity", None)


def get_attribute_by_table_name(table_name: str, column_name: str, reverse: bool = False) -> str | None:
    """
    Returns, based on the defined tables `__tablename__` and the SAColumn name, the correct pydantic attribute.

    The search can be reversed by setting the `reverse` flag to `True`. If not found, returns `None`.

    :param table_name: SQL table name.
    :param column_name: SQL column name.
    :param reverse: If true, reverses the search and returns the SAColumn from the Pydantic attribute.
    :return: Pydantic attribute name or SAColumn name.
    """
    return (
        __TABLE_COLUMNS_MAP__.get(table_name, {})
        .get("columns" if not reverse else "rev_columns", {})
        .get(column_name, None)
    )


def apply_change(
    session: Session, table: type[Table], table_id: str, values: dict[Column, str | int | float | None]
) -> None:
    """
    This function upserts multiple changes into a table based on the `table_id` as the primary key.

    All the `values` will be inserted as a new row, and if the `id` already exists, the values will be updated.

    This function has no return value, as the insert statement was crafter to execute as quick as possible.
    """
    insert_stmt = (
        insert(table).values({"id": table_id, **values}).on_conflict_do_update(index_elements=["id"], set_=values)
    )
    session.exec(insert_stmt)  # type: ignore - the insert type here is correct


def strong_reference_session(session: Session):
    """
    References a session so that all object instances created on the session can be tracked.

    This is used to make sure that every update on the budget via the library can be converted to a sync request that
    will be sent to the Actual server.
    """

    @event.listens_for(session, "before_flush")
    def before_flush(sess, flush_context, instances):
        if len(sess.deleted):
            raise ActualInvalidOperationError(
                "Actual does not allow deleting entries, set the `tombstone` to 1 instead or call the .delete() method"
            )
        if "messages" not in sess.info:
            sess.info["messages"] = messages = []
        else:
            messages = sess.info["messages"]
        # convert entries from the model
        for instance in sess.new:
            # all entries that were added new
            messages.extend(instance.convert(is_new=True))
        for instance in sess.dirty:
            # all entries that were modified
            messages.extend(instance.convert(is_new=False))

    @event.listens_for(session, "after_commit")
    @event.listens_for(session, "after_soft_rollback")
    def after_commit_or_rollback(
        sess,
        previous_transaction=None,
    ):
        if sess.info.get("messages"):
            del sess.info["messages"]

    return session


class BaseModel(SQLModel):
    id: str = Field(sa_column=Column("id", Text, primary_key=True))

    def convert(self, is_new: bool = True) -> list[Message]:
        """Convert the object into distinct entries for sync method. Based on the [original implementation](
        https://github.com/actualbudget/actual/blob/98c17bd5e0f13e27a09a7f6ac176510530572be7/packages/loot-core/src/server/aql/schema-helpers.ts#L146)
        """
        row = getattr(self, "id", None)  # also helps lazy loading the instance
        if row is None:
            raise AttributeError(
                f"Cannot convert model {self.__name__} because it misses the 'id' attribute.\n"
                f"If you see this error, make sure your entry has a unique 'id' as primary key."
            )
        # compute changes from a sqlalchemy instance, see https://stackoverflow.com/a/28353846/12681470
        changes = []
        for column in self.changed():
            converted_attr_name = get_attribute_by_table_name(self.__tablename__, column, reverse=True)
            m = Message(dict(dataset=self.__tablename__, row=row, column=converted_attr_name))
            value = self.__getattribute__(column)
            # we cannot store boolean values, so we always convert it to integer
            if isinstance(value, bool):
                value = int(value)
            # if the entry is new, we can ignore null columns, otherwise consider it an update to None
            if value is not None or not is_new:
                m.set_value(value)
                changes.append(m)
        return changes

    def changed(self) -> list[str]:
        """Returns a list of attributes changed."""
        changed_attributes = []
        inspr = inspect(self)
        attrs = class_mapper(self.__class__).column_attrs  # exclude relationships
        for attr in attrs:
            column = attr.key
            if column == "id":
                continue
            hist = getattr(inspr.attrs, column).history
            if hist.has_changes():
                changed_attributes.append(column)
        return changed_attributes

    def delete(self):
        """Deletes the model, by setting the `tombstone` attribute to 1. It is only possible to hard delete
        transactions by updating and re-uploading the downloaded budget."""
        if not hasattr(self, "tombstone"):
            raise AttributeError(f"Model {self.__class__.__name__} has no tombstone field and cannot be deleted.")
        setattr(self, "tombstone", 1)


class Meta(SQLModel, table=True):
    __tablename__ = "__meta__"

    key: str | None = Field(default=None, sa_column=Column("key", Text, primary_key=True))
    value: str | None = Field(default=None, sa_column=Column("value", Text))


class Migrations(SQLModel, table=True):
    __tablename__ = "__migrations__"

    id: int | None = Field(default=None, sa_column=Column("id", Integer, primary_key=True))


class Accounts(BaseModel, table=True):
    """
    Represents an account entity with detailed attributes describing account properties, transactions, and
    relationships.

    This class is used to model financial accounts, and it includes methods and attributes necessary
    for managing and interacting with account-related data.
    """

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    account_id: str | None = Field(default=None, sa_column=Column("account_id", Text))
    name: str | None = Field(default=None, sa_column=Column("name", Text))
    # Careful when using those balance fields, are they might be empty. Use account.balance property instead
    balance_current: int | None = Field(default=None, sa_column=Column("balance_current", Integer))
    balance_available: int | None = Field(default=None, sa_column=Column("balance_available", Integer))
    balance_limit: int | None = Field(default=None, sa_column=Column("balance_limit", Integer))
    mask: str | None = Field(default=None, sa_column=Column("mask", Text))
    official_name: str | None = Field(default=None, sa_column=Column("official_name", Text))
    subtype: str | None = Field(default=None, sa_column=Column("subtype", Text))
    bank_id: str | None = Field(default=None, sa_column=Column("bank", Text, ForeignKey("banks.id")))
    offbudget: int | None = Field(default=None, sa_column=Column("offbudget", Integer, server_default=text("0")))
    closed: int | None = Field(default=None, sa_column=Column("closed", Integer, server_default=text("0")))
    tombstone: int | None = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))
    sort_order: float | None = Field(default=None, sa_column=Column("sort_order", Float))
    type: str | None = Field(default=None, sa_column=Column("type", Text))
    account_sync_source: str | None = Field(default=None, sa_column=Column("account_sync_source", Text))
    last_sync: str | None = Field(default=None, sa_column=Column("last_sync", Text))
    last_reconciled: str | None = Field(default=None, sa_column=Column("last_reconciled", Text))

    payee: "Payees" = Relationship(back_populates="account", sa_relationship_kwargs={"uselist": False})
    transactions: list["Transactions"] = Relationship(
        back_populates="account",
        sa_relationship_kwargs={
            "primaryjoin": (
                "and_(Accounts.id == Transactions.acct,Transactions.is_parent == 0, Transactions.tombstone==0)"
            )
        },
    )
    bank: "Banks" = Relationship(
        back_populates="account",
        sa_relationship_kwargs={
            "uselist": False,
            "primaryjoin": "and_(Accounts.bank_id == Banks.id,Banks.tombstone == 0)",
        },
    )

    @property
    def balance(self) -> decimal.Decimal:
        """Returns the current balance of the account. Deleted transactions are ignored."""
        value = object_session(self).scalar(
            select(func.coalesce(func.sum(Transactions.amount), 0)).where(
                Transactions.acct == self.id,
                Transactions.is_parent == 0,
                Transactions.tombstone == 0,
            )
        )
        return cents_to_decimal(value)

    @property
    def notes(self) -> str | None:
        """Returns notes for the account. If none are present, returns `None`."""
        return object_session(self).scalar(select(Notes.note).where(Notes.id == f"account-{self.id}"))

    @notes.setter
    def notes(self, note: str | None) -> None:
        """Set the note for the account as a string."""
        object_session(self).merge(Notes(id=f"account-{self.id}", note=note))


class Banks(BaseModel, table=True):
    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    bank_id: str | None = Field(default=None, sa_column=Column("bank_id", Text))
    name: str | None = Field(default=None, sa_column=Column("name", Text))
    tombstone: int | None = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))

    account: "Accounts" = Relationship(back_populates="bank")


class Categories(BaseModel, table=True):
    """
    Stores the category list, which is the classification applied on top of the transaction.

    Each category will belong to its own category group.
    """

    hidden: bool = Field(sa_column=Column("hidden", Boolean, nullable=False, server_default=text("0")))
    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    name: str | None = Field(default=None, sa_column=Column("name", Text))
    is_income: int | None = Field(default=None, sa_column=Column("is_income", Integer, server_default=text("0")))
    cat_group: str | None = Field(default=None, sa_column=Column("cat_group", Text, ForeignKey("category_groups.id")))
    sort_order: float | None = Field(default=None, sa_column=Column("sort_order", Float))
    tombstone: int | None = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))
    goal_def: str | None = Field(default=None, sa_column=Column("goal_def", Text, server_default=text("null")))
    template_settings: dict | None = Field(default=None, sa_column=Column("template_settings", JSON))

    zero_budgets: "ZeroBudgets" = Relationship(
        back_populates="category",
        sa_relationship_kwargs={
            "primaryjoin": "and_(ZeroBudgets.category_id == Categories.id)",
        },
    )
    reflect_budgets: "ReflectBudgets" = Relationship(
        back_populates="category",
        sa_relationship_kwargs={
            "primaryjoin": "and_(ReflectBudgets.category_id == Categories.id)",
        },
    )
    transactions: list["Transactions"] = Relationship(
        back_populates="category",
        sa_relationship_kwargs={
            "primaryjoin": (
                "and_(Categories.id == Transactions.category_id,Transactions.is_parent == 0, Transactions.tombstone==0)"
            )
        },
    )
    group: "CategoryGroups" = Relationship(
        back_populates="categories",
        sa_relationship_kwargs={
            "primaryjoin": "and_(Categories.cat_group == CategoryGroups.id, CategoryGroups.tombstone == 0)",
            "uselist": False,
        },
    )

    @property
    def balance(self) -> decimal.Decimal:
        """Returns the current balance of the category. Deleted transactions are ignored."""
        value = object_session(self).scalar(
            select(func.coalesce(func.sum(Transactions.amount), 0)).where(
                Transactions.category_id == self.id,
                Transactions.is_parent == 0,
                Transactions.tombstone == 0,
            )
        )
        return cents_to_decimal(value)


class CategoryGroups(BaseModel, table=True):
    """
    Stores the groups that the categories can belong to.
    """

    __tablename__ = "category_groups"

    hidden: bool = Field(sa_column=Column("hidden", Boolean, nullable=False, server_default=text("0")))
    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    name: str | None = Field(default=None, sa_column=Column("name", Text))
    is_income: int | None = Field(default=None, sa_column=Column("is_income", Integer, server_default=text("0")))
    sort_order: float | None = Field(default=None, sa_column=Column("sort_order", Float))
    tombstone: int | None = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))

    categories: list["Categories"] = Relationship(
        back_populates="group",
        sa_relationship_kwargs={
            "primaryjoin": "and_(CategoryGroups.id == Categories.cat_group, Categories.tombstone == 0)",
            "order_by": "Categories.sort_order",
        },
    )


class CategoryMapping(BaseModel, table=True):
    __tablename__ = "category_mapping"

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    transfer_id: str | None = Field(default=None, sa_column=Column("transferId", Text))


class CreatedBudgets(SQLModel, table=True):
    __tablename__ = "created_budgets"

    month: str | None = Field(default=None, sa_column=Column("month", Text, primary_key=True))


class CustomReports(BaseModel, table=True):
    """Metadata for all the custom reports available on the Actual frontend."""

    __tablename__ = "custom_reports"

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    name: str | None = Field(default=None, sa_column=Column("name", Text))
    start_date: str | None = Field(default=None, sa_column=Column("start_date", Text))
    end_date: str | None = Field(default=None, sa_column=Column("end_date", Text))
    date_static: int | None = Field(default=None, sa_column=Column("date_static", Integer, server_default=text("0")))
    date_range: str | None = Field(default=None, sa_column=Column("date_range", Text))
    mode: str | None = Field(default=None, sa_column=Column("mode", Text, server_default=text("'total'")))
    group_by: str | None = Field(default=None, sa_column=Column("group_by", Text, server_default=text("'Category'")))
    balance_type: str | None = Field(
        default=None, sa_column=Column("balance_type", Text, server_default=text("'Expense'"))
    )
    show_empty: int | None = Field(default=None, sa_column=Column("show_empty", Integer, server_default=text("0")))
    show_offbudget: int | None = Field(
        default=None, sa_column=Column("show_offbudget", Integer, server_default=text("0"))
    )
    show_hidden: int | None = Field(default=None, sa_column=Column("show_hidden", Integer, server_default=text("0")))
    show_uncategorized: int | None = Field(
        default=None, sa_column=Column("show_uncategorized", Integer, server_default=text("0"))
    )
    selected_categories: str | None = Field(default=None, sa_column=Column("selected_categories", Text))
    graph_type: str | None = Field(
        default=None, sa_column=Column("graph_type", Text, server_default=text("'BarGraph'"))
    )
    conditions: str | None = Field(default=None, sa_column=Column("conditions", Text))
    conditions_op: str | None = Field(
        default=None, sa_column=Column("conditions_op", Text, server_default=text("'and'"))
    )
    metadata_: str | None = Field(default=None, sa_column=Column("metadata", Text))
    interval: str | None = Field(default=None, sa_column=Column("interval", Text, server_default=text("'Monthly'")))
    color_scheme: str | None = Field(default=None, sa_column=Column("color_scheme", Text))
    tombstone: int | None = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))
    include_current: int | None = Field(
        default=None, sa_column=Column("include_current", Integer, server_default=text("0"))
    )
    sort_by: str | None = Field(default=None, sa_column=Column("sort_by", Text, server_default=text("'desc'")))
    trim_intervals: int | None = Field(default=None, sa_column=Column("trim_intervals", Integer))


class Dashboard(BaseModel, table=True):
    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    type: str | None = Field(default=None, sa_column=Column("type", Text))
    width: int | None = Field(default=None, sa_column=Column("width", Integer))
    height: int | None = Field(default=None, sa_column=Column("height", Integer))
    x: int | None = Field(default=None, sa_column=Column("x", Integer))
    y: int | None = Field(default=None, sa_column=Column("y", Integer))
    meta: str | None = Field(default=None, sa_column=Column("meta", Text))
    tombstone: int | None = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))


class Kvcache(SQLModel, table=True):
    key: str | None = Field(default=None, sa_column=Column("key", Text, primary_key=True))
    value: str | None = Field(default=None, sa_column=Column("value", Text))


class KvcacheKey(SQLModel, table=True):
    __tablename__ = "kvcache_key"

    id: int | None = Field(default=None, sa_column=Column("id", Integer, primary_key=True))
    key: float | None = Field(default=None, sa_column=Column("key", Float))


class MessagesClock(SQLModel, table=True):
    __tablename__ = "messages_clock"

    id: int | None = Field(default=None, sa_column=Column("id", Integer, primary_key=True))
    clock: str | None = Field(default=None, sa_column=Column("clock", Text))

    def get_clock(self) -> dict:
        """Gets the clock from JSON text to a dictionary with fields `timestamp` and `merkle`."""
        return json.loads(self.clock)

    def set_clock(self, value: dict):
        """Sets the clock from a dictionary and stores it in the correct format."""
        self.clock = json.dumps(value, separators=(",", ":"))

    def get_timestamp(self) -> HULC_Client:
        """Gets the timestamp from the clock value directly as a [HULC_Client][actual.protobuf_models.HULC_Client]."""
        clock = self.get_clock()
        return HULC_Client.from_timestamp(clock["timestamp"])

    def set_timestamp(self, client: HULC_Client) -> None:
        """Sets the timestamp on the clock value based on the [HULC_Client][actual.protobuf_models.HULC_Client]
        provided."""
        clock_message = self.get_clock()
        clock_message["timestamp"] = str(client)
        self.set_clock(clock_message)


class MessagesCrdt(SQLModel, table=True):
    __tablename__ = "messages_crdt"
    __table_args__ = (Index("messages_crdt_search", "dataset", "row", "column", "timestamp"),)

    timestamp: str = Field(sa_column=Column("timestamp", Text, nullable=False, unique=True))
    dataset: str = Field(sa_column=Column("dataset", Text, nullable=False))
    row: str = Field(sa_column=Column("row", Text, nullable=False))
    column: str = Field(sa_column=Column("column", Text, nullable=False))
    value: bytes = Field(sa_column=Column("value", LargeBinary, nullable=False))
    id: int | None = Field(default=None, sa_column=Column("id", Integer, primary_key=True))


class Notes(BaseModel, table=True):
    """Stores the description of each account."""

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    note: str | None = Field(default=None, sa_column=Column("note", Text))


class PayeeMapping(BaseModel, table=True):
    __tablename__ = "payee_mapping"

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    target_id: str | None = Field(default=None, sa_column=Column("targetId", Text))


class Payees(BaseModel, table=True):
    """
    Stores the individual payees.

    Each payee is a unique identifier that can be assigned to a transaction. Certain payees have empty names and are
    associated to the accounts themselves, representing the transfer between one account and another. These would
    have the field `account` not set to `None`.
    """

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    name: str | None = Field(default=None, sa_column=Column("name", Text))
    category: str | None = Field(default=None, sa_column=Column("category", Text))
    tombstone: int | None = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))
    transfer_acct: str | None = Field(default=None, sa_column=Column("transfer_acct", Text, ForeignKey("accounts.id")))
    favorite: int | None = Field(default=None, sa_column=Column("favorite", Integer, server_default=text("0")))
    learn_categories: bool | None = Field(sa_column=Column("learn_categories", Boolean, server_default=text("1")))

    account: Optional["Accounts"] = Relationship(back_populates="payee", sa_relationship_kwargs={"uselist": False})
    transactions: list["Transactions"] = Relationship(
        back_populates="payee",
        sa_relationship_kwargs={"primaryjoin": "and_(Transactions.payee_id == Payees.id, Transactions.tombstone==0)"},
    )

    @property
    def balance(self) -> decimal.Decimal:
        """Returns the current balance of the payee. Deleted transactions are ignored."""
        value = object_session(self).scalar(
            select(func.coalesce(func.sum(Transactions.amount), 0)).where(
                Transactions.payee_id == self.id,
                Transactions.is_parent == 0,
                Transactions.tombstone == 0,
            )
        )
        return cents_to_decimal(value)


class Preferences(BaseModel, table=True):
    """Stores the preferences for the user, using key/value pairs."""

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    value: str | None = Field(default=None, sa_column=Column("value", Text))


class Rules(BaseModel, table=True):
    """
    Stores all rules on the budget. The conditions and actions are stored separately using the JSON format.

    The conditions are stored as a text field, but can be retrieved as a model using
    [get_ruleset][actual.queries.get_ruleset].
    """

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    stage: str | None = Field(default=None, sa_column=Column("stage", Text))
    conditions: str | None = Field(default=None, sa_column=Column("conditions", Text))
    actions: str | None = Field(default=None, sa_column=Column("actions", Text))
    tombstone: int | None = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))
    conditions_op: str | None = Field(
        default=None,
        sa_column=Column("conditions_op", Text, server_default=text("'and'")),
    )


class Schedules(BaseModel, table=True):
    """Stores the schedules defined by the user. Is also linked to a rule that executes it."""

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    rule_id: str | None = Field(default=None, sa_column=Column("rule", Text, ForeignKey("rules.id")))
    active: int | None = Field(default=None, sa_column=Column("active", Integer, server_default=text("0")))
    completed: int | None = Field(default=None, sa_column=Column("completed", Integer, server_default=text("0")))
    posts_transaction: int | None = Field(
        default=None,
        sa_column=Column("posts_transaction", Integer, server_default=text("0")),
    )
    tombstone: int | None = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))
    name: str | None = Field(default=None, sa_column=Column("name", Text, server_default=text("NULL")))

    rule: "Rules" = Relationship(sa_relationship_kwargs={"uselist": False})
    transactions: list["Transactions"] = Relationship(back_populates="schedule")


class SchedulesJsonPaths(SQLModel, table=True):
    __tablename__ = "schedules_json_paths"

    schedule_id: str | None = Field(default=None, sa_column=Column("schedule_id", Text, primary_key=True))
    payee: str | None = Field(default=None, sa_column=Column("payee", Text))
    account: str | None = Field(default=None, sa_column=Column("account", Text))
    amount: str | None = Field(default=None, sa_column=Column("amount", Text))
    date: str | None = Field(default=None, sa_column=Column("date", Text))


class SchedulesNextDate(SQLModel, table=True):
    __tablename__ = "schedules_next_date"

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    schedule_id: str | None = Field(default=None, sa_column=Column("schedule_id", Text))
    local_next_date: int | None = Field(default=None, sa_column=Column("local_next_date", Integer))
    local_next_date_ts: int | None = Field(default=None, sa_column=Column("local_next_date_ts", Integer))
    base_next_date: int | None = Field(default=None, sa_column=Column("base_next_date", Integer))
    base_next_date_ts: int | None = Field(default=None, sa_column=Column("base_next_date_ts", Integer))
    tombstone: int | None = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))


class Tags(BaseModel, table=True):
    tag: str | None = Field(default=None, sa_column=Column("tag", Text, unique=True))
    color: str | None = Field(
        default=None, sa_column=Column("color", Text), description="Color in hex format (i.e. '#690CB0')"
    )
    description: str | None = Field(default=None, sa_column=Column("description", Text))
    tombstone: int | None = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))

    @property
    def transactions(self) -> Sequence["Transactions"]:
        """Returns all transactions with this tag associated to them."""
        return (
            object_session(self)
            .execute(
                select(Transactions).where(
                    Transactions.is_parent == 0,
                    Transactions.tombstone == 0,
                    or_(
                        # Either it has a space or is finishing the sentence
                        Transactions.notes.like(f"%#{self.tag} %"),
                        Transactions.notes.like(f"%#{self.tag}"),
                    ),
                )
            )
            .scalars()
            .all()
        )


class TransactionFilters(BaseModel, table=True):
    __tablename__ = "transaction_filters"

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    name: str | None = Field(default=None, sa_column=Column("name", Text))
    conditions: str | None = Field(default=None, sa_column=Column("conditions", Text))
    conditions_op: str | None = Field(
        default=None,
        sa_column=Column("conditions_op", Text, server_default=text("'and'")),
    )
    tombstone: int | None = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))


class Transactions(BaseModel, table=True):
    """
    Contains all transactions inserted into Actual.
    """

    __table_args__ = (
        Index("trans_category", "category"),
        Index("trans_category_date", "category", "date"),
        Index("trans_date", "date"),
        Index("trans_parent_id", "parent_id"),
        Index("trans_sorted", "date", "starting_balance_flag", "sort_order", "id"),
    )

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    is_parent: int | None = Field(default=None, sa_column=Column("isParent", Integer, server_default=text("0")))
    is_child: int | None = Field(default=None, sa_column=Column("isChild", Integer, server_default=text("0")))
    acct: str | None = Field(default=None, sa_column=Column("acct", Text, ForeignKey("accounts.id")))
    category_id: str | None = Field(default=None, sa_column=Column("category", Text, ForeignKey("categories.id")))
    amount: int | None = Field(default=None, sa_column=Column("amount", Integer))
    payee_id: str | None = Field(default=None, sa_column=Column("description", Text, ForeignKey("payees.id")))
    notes: str | None = Field(default=None, sa_column=Column("notes", Text))
    date: int | None = Field(default=None, sa_column=Column("date", Integer))
    financial_id: str | None = Field(default=None, sa_column=Column("financial_id", Text))
    type: str | None = Field(default=None, sa_column=Column("type", Text))
    location: str | None = Field(default=None, sa_column=Column("location", Text))
    error: str | None = Field(default=None, sa_column=Column("error", Text))
    imported_description: str | None = Field(default=None, sa_column=Column("imported_description", Text))
    starting_balance_flag: int | None = Field(
        default=None,
        sa_column=Column("starting_balance_flag", Integer, server_default=text("0")),
    )
    transferred_id: str | None = Field(
        default=None, sa_column=Column("transferred_id", Text, ForeignKey("transactions.id"))
    )
    sort_order: float | None = Field(default=None, sa_column=Column("sort_order", Float))
    tombstone: int | None = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))
    cleared: int | None = Field(default=None, sa_column=Column("cleared", Integer, server_default=text("1")))
    pending: int | None = Field(default=None, sa_column=Column("pending", Integer, server_default=text("0")))
    parent_id: str | None = Field(default=None, sa_column=Column("parent_id", Text, ForeignKey("transactions.id")))
    schedule_id: str | None = Field(default=None, sa_column=Column("schedule", Text, ForeignKey("schedules.id")))
    reconciled: int | None = Field(default=None, sa_column=Column("reconciled", Integer, server_default=text("0")))
    raw_synced_data: str | None = Field(default=None, sa_column=Column("raw_synced_data", Text))

    account: "Accounts" = Relationship(back_populates="transactions")
    category: Optional["Categories"] = Relationship(
        back_populates="transactions",
        sa_relationship_kwargs={
            "primaryjoin": "and_(Transactions.category_id == Categories.id, Categories.tombstone==0)"
        },
    )
    payee: Optional["Payees"] = Relationship(
        back_populates="transactions",
        sa_relationship_kwargs={"primaryjoin": "and_(Transactions.payee_id == Payees.id, Payees.tombstone==0)"},
    )
    schedule: Optional["Schedules"] = Relationship(
        back_populates="transactions",
        sa_relationship_kwargs={
            "primaryjoin": "and_(Transactions.schedule_id == Schedules.id, Schedules.tombstone==0)"
        },
    )
    parent: Optional["Transactions"] = Relationship(
        back_populates="splits",
        sa_relationship_kwargs={"remote_side": "Transactions.id", "foreign_keys": "Transactions.parent_id"},
    )
    splits: list["Transactions"] = Relationship(
        back_populates="parent",
        sa_relationship_kwargs={
            "primaryjoin": "and_(Transactions.id == remote(Transactions.parent_id), remote(Transactions.tombstone)==0)",
            "order_by": "remote(Transactions.sort_order.desc())",
        },
    )
    transfer: Optional["Transactions"] = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "and_("
            "Transactions.transferred_id == remote(Transactions.id), remote(Transactions.tombstone)==0)",
            "foreign_keys": "Transactions.transferred_id",
        }
    )

    def get_date(self) -> datetime.date:
        """Returns the transaction date as a datetime.date object, instead of as a string."""
        return int_to_date(self.date)

    def set_date(self, date: datetime.date):
        """Sets the transaction date as a datetime.date object, instead of as a string."""
        self.date = date_to_int(date)

    def set_amount(self, amount: decimal.Decimal | int | float):
        """Sets the amount as a decimal.Decimal object, instead of as an integer representing the number of cents."""
        self.amount = decimal_to_cents(amount)

    def get_amount(self) -> decimal.Decimal:
        """Returns the amount as a decimal.Decimal, instead of as an integer representing the number of cents."""
        return cents_to_decimal(self.amount)

    @validates("cleared")
    def validate_cleared(self, key, v):
        """Add an validator which ensures that clearing parent transactions also affects all splits"""

        # Validation only performed on parent transactions where cleared is changed
        if self.is_parent and self.cleared != v:
            session = object_session(self)
            splits = session.scalars(select(Transactions).where(Transactions.parent_id == self.id)).all()
            for s in splits:
                s.cleared = v

        # Return the input value unmodified as this is a validator for the parent
        return v

    def delete(self):
        """Overload the delete() from the BaseModel so that we can properly delete any children splits
        as well. Otherwise things will not add up in the Actual GUI when calling delete() on a parent.

        It is technically possible to call delete() a child transaction, that would probably also cause
        things to go out of sync ,but since that is not possible to do in the UI this case is not handled
        here and is left as undefined behaviour.

        Neither is it handled if the tombstone flag is set directly on an object without using the delete()
        metod. If you are into this direct attribute modification you will have to handle the splits yourself.
        """

        # Check if this is a parent transaction, if so iterate the children and call delete() on them as well
        if self.is_parent:
            session = object_session(self)
            splits = session.scalars(select(Transactions).where(Transactions.parent_id == self.id)).all()
            for s in splits:
                s.delete()

        # Utilise the BaseModel delete() for deleting the transaction
        super().delete()


class ZeroBudgetMonths(BaseModel, table=True):
    """
    Holds the amount of budget held for the next month for a specific budget id.

    Only applies to envelope budgets and is attached to a [ZeroBudgets][actual.database.ZeroBudgets] object.

    The month data is stored on the `id` field instead of the default uuid as id. Here, Actual actually ignores the
    previous models for the int dates and instead uses a string with "-" as separator (i.e., `'2025-09'`). The
    `buffered` represents the amount held for the next month in cents as usual.
    """

    __tablename__ = "zero_budget_months"

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    buffered: int | None = Field(default=None, sa_column=Column("buffered", Integer, server_default=text("0")))

    def get_month(self) -> datetime.date:
        """Returns the month as a datetime.date object, instead of as a string."""
        return int_to_date(self.id.replace("-", ""), month_only=True)

    def set_month(self, month: datetime.date):
        """Sets the month as a datetime.date object, instead of as a string."""
        self.id = datetime.date.strftime(month, "%Y-%m")

    def set_amount(self, amount: decimal.Decimal | int | float):
        """Sets the amount as a decimal.Decimal object, instead of as an integer representing the number of cents."""
        self.buffered = decimal_to_cents(amount)

    def get_amount(self) -> decimal.Decimal:
        """
        Returns the amount being held for next month for a budget as a `decimal.Decimal`."""
        return cents_to_decimal(self.buffered)


class BaseBudgets(BaseModel):
    """
    Hosts the shared code between both [ZeroBudgets][actual.database.ZeroBudgets] and
    [ReflectBudgets][actual.database.ReflectBudgets].

    Each budget will represent a certain month in a certain category. When a budget is missing on the frontend,
    frontend will assume this value is zero, but the entity will be missing from the database.
    """

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    month: int | None = Field(default=None, sa_column=Column("month", Integer))
    category_id: str | None = Field(default=None, sa_column=Column("category", Text))
    amount: int | None = Field(default=None, sa_column=Column("amount", Integer, server_default=text("0")))
    carryover: int | None = Field(default=None, sa_column=Column("carryover", Integer, server_default=text("0")))

    def get_date(self) -> datetime.date:
        """Returns the transaction date as a datetime.date object, instead of as a string."""
        return int_to_date(self.month, month_only=True)

    def set_date(self, date: datetime.date):
        """
        Sets the transaction date as a datetime.date object, instead of as a string.

        If the date value contains a day, it will be truncated and only the month and year will be inserted, as the
        budget applies to a month.
        """
        self.month = date_to_int(date, month_only=True)

    def set_amount(self, amount: decimal.Decimal | int | float):
        """Sets the amount as a decimal.Decimal object, instead of as an integer representing the number of cents."""
        self.amount = decimal_to_cents(amount)

    def get_amount(self) -> decimal.Decimal:
        """Returns the amount as a decimal.Decimal, instead of as an integer representing the number of cents."""
        return cents_to_decimal(self.amount)

    @property
    def range(self) -> tuple[datetime.date, datetime.date]:
        """
        Range of the budget as a tuple [start, end).

        The end date is not inclusive, as it represents the start of the next month.
        """
        return month_range(self.get_date())

    @property
    def balance(self) -> decimal.Decimal:
        """
        Returns the current **spent** balance of the budget.

        The evaluation will take into account the budget month and only selected transactions for the combination month
        and category. Deleted transactions are ignored.

        If you want to get the balance from the frontend, take a look at the query
        [get_accumulated_budgeted_balance][actual.queries.get_accumulated_budgeted_balance] instead.
        """
        budget_start, budget_end = (date_to_int(d) for d in self.range)
        value = object_session(self).scalar(
            select(func.coalesce(func.sum(Transactions.amount), 0)).where(
                Transactions.category_id == self.category_id,
                Transactions.date >= budget_start,
                Transactions.date < budget_end,
                Transactions.is_parent == 0,
                Transactions.tombstone == 0,
            )
        )
        return cents_to_decimal(value)


class ReflectBudgets(BaseBudgets, table=True):
    """
    Stores the budgets, when using tracking budget.

    This table will only contain data for the entries which are created. If a combination of category and budget month
    is not existing, it is assumed that the budget is 0.
    """

    __tablename__ = "reflect_budgets"

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    month: int | None = Field(default=None, sa_column=Column("month", Integer))
    category_id: str | None = Field(default=None, sa_column=Column("category", ForeignKey("categories.id")))
    amount: int | None = Field(default=None, sa_column=Column("amount", Integer, server_default=text("0")))
    carryover: int | None = Field(default=None, sa_column=Column("carryover", Integer, server_default=text("0")))
    goal: int | None = Field(default=None, sa_column=Column("goal", Integer, server_default=text("null")))
    long_goal: int | None = Field(default=None, sa_column=Column("long_goal", Integer, server_default=text("null")))

    category: "Categories" = Relationship(
        back_populates="reflect_budgets",
        sa_relationship_kwargs={
            "uselist": False,
            "primaryjoin": "and_(ReflectBudgets.category_id == Categories.id, Categories.tombstone == 0)",
        },
    )


class ZeroBudgets(BaseBudgets, table=True):
    """
    Stores the budgets, when using envelope budget (default).

    This table will only contain data for the entries which are created. If a combination of category and budget month
    is not existing, it is assumed that the budget is 0.
    """

    __tablename__ = "zero_budgets"

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    month: int | None = Field(default=None, sa_column=Column("month", Integer))
    category_id: str | None = Field(default=None, sa_column=Column("category", ForeignKey("categories.id")))
    amount: int | None = Field(default=None, sa_column=Column("amount", Integer, server_default=text("0")))
    carryover: int | None = Field(default=None, sa_column=Column("carryover", Integer, server_default=text("0")))
    goal: int | None = Field(default=None, sa_column=Column("goal", Integer, server_default=text("null")))
    long_goal: int | None = Field(default=None, sa_column=Column("long_goal", Integer, server_default=text("null")))

    category: "Categories" = Relationship(
        back_populates="zero_budgets",
        sa_relationship_kwargs={
            "uselist": False,
            "primaryjoin": "and_(ZeroBudgets.category_id == Categories.id, Categories.tombstone == 0)",
        },
    )


class PendingTransactions(SQLModel, table=True):
    __tablename__ = "pending_transactions"

    id: str | None = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    acct: int | None = Field(default=None, sa_column=Column("acct", ForeignKey("accounts.id")))
    amount: int | None = Field(default=None, sa_column=Column("amount", Integer))
    description: str | None = Field(default=None, sa_column=Column("description", Text))
    date: str | None = Field(default=None, sa_column=Column("date", Text))


for t_entry in SQLModel._sa_registry.mappers:
    t_name = t_entry.entity.__tablename__
    if t_name not in __TABLE_COLUMNS_MAP__:
        __TABLE_COLUMNS_MAP__[t_name] = {"entity": t_entry.entity, "columns": {}, "rev_columns": {}}
    table_columns = list(c.name for c in t_entry.columns)
    # the name and property name of the pydantic property and database column can be different
    for t_key, t_column in dict(t_entry.entity.__dict__).items():
        if hasattr(t_column, "name") and getattr(t_column, "name") in table_columns:
            __TABLE_COLUMNS_MAP__[t_name]["columns"][t_column.name] = t_key
            # add also rever columns mappings for reverse mapping
            __TABLE_COLUMNS_MAP__[t_name]["rev_columns"][t_key] = t_column.name
