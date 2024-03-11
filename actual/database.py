import datetime
import decimal
import uuid
from typing import List, Optional, Union

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    text,
)
from sqlmodel import Field, Relationship, SQLModel

from actual.protobuf_models import Message


def get_class_by_table_name(table_name: str) -> Union[SQLModel, None]:
    """
    Returns, based on the defined tables __tablename__ the corresponding SQLModel object. If not found, returns None.
    """
    for entry in SQLModel._sa_registry.mappers:
        if entry.entity.__tablename__ == table_name:
            return entry.entity
    return None


class BaseModel(SQLModel):
    def convert(self) -> List[Message]:
        """Convert the object into distinct entries for sync method. Based on the original implementation:

        https://github.com/actualbudget/actual/blob/98c17bd5e0f13e27a09a7f6ac176510530572be7/packages/loot-core/src/server/aql/schema-helpers.ts#L146
        """
        dataset = self.__tablename__
        changes = []
        row = getattr(self, "id", None)  # also helps lazy loading the instance
        if row is None:
            raise AttributeError(f"Cannot convert model {self.__name__} because it misses the 'id' attribute.")
        for column, value in self.model_dump().items():
            if value is None or column == "id":
                continue
            m = Message(dict(dataset=dataset, row=row, column=column))
            m.set_value(value)
            changes.append(m)
        return changes


class Meta(SQLModel, table=True):
    __tablename__ = "__meta__"

    key: Optional[str] = Field(default=None, sa_column=Column("key", Text, primary_key=True))
    value: Optional[str] = Field(default=None, sa_column=Column("value", Text))


class Migrations(SQLModel, table=True):
    __tablename__ = "__migrations__"

    id: Optional[int] = Field(default=None, sa_column=Column("id", Integer, primary_key=True))


class Accounts(SQLModel, table=True):
    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    account_id: Optional[str] = Field(default=None, sa_column=Column("account_id", Text))
    name: Optional[str] = Field(default=None, sa_column=Column("name", Text))
    balance_current: Optional[int] = Field(default=None, sa_column=Column("balance_current", Integer))
    balance_available: Optional[int] = Field(default=None, sa_column=Column("balance_available", Integer))
    balance_limit: Optional[int] = Field(default=None, sa_column=Column("balance_limit", Integer))
    mask: Optional[str] = Field(default=None, sa_column=Column("mask", Text))
    official_name: Optional[str] = Field(default=None, sa_column=Column("official_name", Text))
    subtype: Optional[str] = Field(default=None, sa_column=Column("subtype", Text))
    bank: Optional[str] = Field(default=None, sa_column=Column("bank", Text))
    offbudget: Optional[int] = Field(default=None, sa_column=Column("offbudget", Integer, server_default=text("0")))
    closed: Optional[int] = Field(default=None, sa_column=Column("closed", Integer, server_default=text("0")))
    tombstone: Optional[int] = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))
    sort_order: Optional[float] = Field(default=None, sa_column=Column("sort_order", Float))
    type: Optional[str] = Field(default=None, sa_column=Column("type", Text))

    pending_transactions: List["PendingTransactions"] = Relationship(back_populates="account")
    transactions: List["Transactions"] = Relationship(back_populates="account")


class Banks(SQLModel, table=True):
    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    bank_id: Optional[str] = Field(default=None, sa_column=Column("bank_id", Text))
    name: Optional[str] = Field(default=None, sa_column=Column("name", Text))
    tombstone: Optional[int] = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))


class Categories(SQLModel, table=True):
    hidden: bool = Field(sa_column=Column("hidden", Boolean, nullable=False, server_default=text("0")))
    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    name: Optional[str] = Field(default=None, sa_column=Column("name", Text))
    is_income: Optional[int] = Field(default=None, sa_column=Column("is_income", Integer, server_default=text("0")))
    cat_group: Optional[str] = Field(default=None, sa_column=Column("cat_group", Text))
    sort_order: Optional[float] = Field(default=None, sa_column=Column("sort_order", Float))
    tombstone: Optional[int] = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))
    goal_def: Optional[str] = Field(default=None, sa_column=Column("goal_def", Text, server_default=text("null")))

    transactions: List["Transactions"] = Relationship(back_populates="category_")


class CategoryGroups(SQLModel, table=True):
    __tablename__ = "category_groups"

    hidden: bool = Field(sa_column=Column("hidden", Boolean, nullable=False, server_default=text("0")))
    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    name: Optional[str] = Field(default=None, sa_column=Column("name", Text))
    is_income: Optional[int] = Field(default=None, sa_column=Column("is_income", Integer, server_default=text("0")))
    sort_order: Optional[float] = Field(default=None, sa_column=Column("sort_order", Float))
    tombstone: Optional[int] = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))


class CategoryMapping(SQLModel, table=True):
    __tablename__ = "category_mapping"

    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    transferId: Optional[str] = Field(default=None, sa_column=Column("transferId", Text))


class CreatedBudgets(SQLModel, table=True):
    __tablename__ = "created_budgets"

    month: Optional[str] = Field(default=None, sa_column=Column("month", Text, primary_key=True))


class Kvcache(SQLModel, table=True):
    key: Optional[str] = Field(default=None, sa_column=Column("key", Text, primary_key=True))
    value: Optional[str] = Field(default=None, sa_column=Column("value", Text))


class KvcacheKey(SQLModel, table=True):
    __tablename__ = "kvcache_key"

    id: Optional[int] = Field(default=None, sa_column=Column("id", Integer, primary_key=True))
    key: Optional[float] = Field(default=None, sa_column=Column("key", Float))


class MessagesClock(SQLModel, table=True):
    __tablename__ = "messages_clock"

    id: Optional[int] = Field(default=None, sa_column=Column("id", Integer, primary_key=True))
    clock: Optional[str] = Field(default=None, sa_column=Column("clock", Text))


class MessagesCrdt(SQLModel, table=True):
    __tablename__ = "messages_crdt"
    __table_args__ = (Index("messages_crdt_search", "dataset", "row", "column", "timestamp"),)

    timestamp: str = Field(sa_column=Column("timestamp", Text, nullable=False, unique=True))
    dataset: str = Field(sa_column=Column("dataset", Text, nullable=False))
    row: str = Field(sa_column=Column("row", Text, nullable=False))
    column: str = Field(sa_column=Column("column", Text, nullable=False))
    value: bytes = Field(sa_column=Column("value", LargeBinary, nullable=False))
    id: Optional[int] = Field(default=None, sa_column=Column("id", Integer, primary_key=True))


class Notes(SQLModel, table=True):
    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    note: Optional[str] = Field(default=None, sa_column=Column("note", Text))


class PayeeMapping(SQLModel, table=True):
    __tablename__ = "payee_mapping"

    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    targetId: Optional[str] = Field(default=None, sa_column=Column("targetId", Text))


class Payees(SQLModel, table=True):
    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    name: Optional[str] = Field(default=None, sa_column=Column("name", Text))
    category: Optional[str] = Field(default=None, sa_column=Column("category", Text))
    tombstone: Optional[int] = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))
    transfer_acct: Optional[str] = Field(default=None, sa_column=Column("transfer_acct", Text))

    transactions: List["Transactions"] = Relationship(back_populates="payee")


class ReflectBudgets(SQLModel, table=True):
    __tablename__ = "reflect_budgets"

    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    month: Optional[int] = Field(default=None, sa_column=Column("month", Integer))
    category: Optional[str] = Field(default=None, sa_column=Column("category", Text))
    amount: Optional[int] = Field(default=None, sa_column=Column("amount", Integer, server_default=text("0")))
    carryover: Optional[int] = Field(default=None, sa_column=Column("carryover", Integer, server_default=text("0")))
    goal: Optional[int] = Field(default=None, sa_column=Column("goal", Integer, server_default=text("null")))


class Rules(SQLModel, table=True):
    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    stage: Optional[str] = Field(default=None, sa_column=Column("stage", Text))
    conditions: Optional[str] = Field(default=None, sa_column=Column("conditions", Text))
    actions: Optional[str] = Field(default=None, sa_column=Column("actions", Text))
    tombstone: Optional[int] = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))
    conditions_op: Optional[str] = Field(
        default=None,
        sa_column=Column("conditions_op", Text, server_default=text("'and'")),
    )


class Schedules(SQLModel, table=True):
    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    rule: Optional[str] = Field(default=None, sa_column=Column("rule", Text))
    active: Optional[int] = Field(default=None, sa_column=Column("active", Integer, server_default=text("0")))
    completed: Optional[int] = Field(default=None, sa_column=Column("completed", Integer, server_default=text("0")))
    posts_transaction: Optional[int] = Field(
        default=None,
        sa_column=Column("posts_transaction", Integer, server_default=text("0")),
    )
    tombstone: Optional[int] = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))
    name: Optional[str] = Field(default=None, sa_column=Column("name", Text, server_default=text("NULL")))


class SchedulesJsonPaths(SQLModel, table=True):
    __tablename__ = "schedules_json_paths"

    schedule_id: Optional[str] = Field(default=None, sa_column=Column("schedule_id", Text, primary_key=True))
    payee: Optional[str] = Field(default=None, sa_column=Column("payee", Text))
    account: Optional[str] = Field(default=None, sa_column=Column("account", Text))
    amount: Optional[str] = Field(default=None, sa_column=Column("amount", Text))
    date: Optional[str] = Field(default=None, sa_column=Column("date", Text))


class SchedulesNextDate(SQLModel, table=True):
    __tablename__ = "schedules_next_date"

    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    schedule_id: Optional[str] = Field(default=None, sa_column=Column("schedule_id", Text))
    local_next_date: Optional[int] = Field(default=None, sa_column=Column("local_next_date", Integer))
    local_next_date_ts: Optional[int] = Field(default=None, sa_column=Column("local_next_date_ts", Integer))
    base_next_date: Optional[int] = Field(default=None, sa_column=Column("base_next_date", Integer))
    base_next_date_ts: Optional[int] = Field(default=None, sa_column=Column("base_next_date_ts", Integer))
    tombstone: Optional[int] = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))


class TransactionFilters(SQLModel, table=True):
    __tablename__ = "transaction_filters"

    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    name: Optional[str] = Field(default=None, sa_column=Column("name", Text))
    conditions: Optional[str] = Field(default=None, sa_column=Column("conditions", Text))
    conditions_op: Optional[str] = Field(
        default=None,
        sa_column=Column("conditions_op", Text, server_default=text("'and'")),
    )
    tombstone: Optional[int] = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))


class Transactions(BaseModel, table=True):
    __table_args__ = (
        Index("trans_category", "category"),
        Index("trans_category_date", "category", "date"),
        Index("trans_date", "date"),
        Index("trans_parent_id", "parent_id"),
        Index("trans_sorted", "date", "starting_balance_flag", "sort_order", "id"),
    )

    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    isParent: Optional[int] = Field(default=None, sa_column=Column("isParent", Integer, server_default=text("0")))
    isChild: Optional[int] = Field(default=None, sa_column=Column("isChild", Integer, server_default=text("0")))
    acct: Optional[str] = Field(default=None, sa_column=Column("acct", Text, ForeignKey("accounts.id")))
    category: Optional[str] = Field(default=None, sa_column=Column("category", Text, ForeignKey("categories.id")))
    amount: Optional[int] = Field(default=None, sa_column=Column("amount", Integer))
    description: Optional[str] = Field(default=None, sa_column=Column("description", Text, ForeignKey("payees.id")))
    notes: Optional[str] = Field(default=None, sa_column=Column("notes", Text))
    date: Optional[int] = Field(default=None, sa_column=Column("date", Integer))
    financial_id: Optional[str] = Field(default=None, sa_column=Column("financial_id", Text))
    type: Optional[str] = Field(default=None, sa_column=Column("type", Text))
    location: Optional[str] = Field(default=None, sa_column=Column("location", Text))
    error: Optional[str] = Field(default=None, sa_column=Column("error", Text))
    imported_description: Optional[str] = Field(default=None, sa_column=Column("imported_description", Text))
    starting_balance_flag: Optional[int] = Field(
        default=None,
        sa_column=Column("starting_balance_flag", Integer, server_default=text("0")),
    )
    transferred_id: Optional[str] = Field(default=None, sa_column=Column("transferred_id", Text))
    sort_order: Optional[float] = Field(default=None, sa_column=Column("sort_order", Float))
    tombstone: Optional[int] = Field(default=None, sa_column=Column("tombstone", Integer, server_default=text("0")))
    cleared: Optional[int] = Field(default=None, sa_column=Column("cleared", Integer, server_default=text("1")))
    pending: Optional[int] = Field(default=None, sa_column=Column("pending", Integer, server_default=text("0")))
    parent_id: Optional[str] = Field(default=None, sa_column=Column("parent_id", Text))
    schedule: Optional[str] = Field(default=None, sa_column=Column("schedule", Text))
    reconciled: Optional[int] = Field(default=None, sa_column=Column("reconciled", Integer, server_default=text("0")))

    account: Optional["Accounts"] = Relationship(back_populates="transactions")
    category_: Optional["Categories"] = Relationship(back_populates="transactions")
    payee: Optional["Payees"] = Relationship(back_populates="transactions")

    @classmethod
    def new(
        cls,
        account_id: str,
        amount: decimal.Decimal,
        date: datetime.date,
        category: Optional[Categories] = None,
        payee: Optional[Payees] = None,
        notes: Optional[str] = None,
    ):
        date_int = int(datetime.date.strftime(date, "%Y%m%d"))
        return cls(
            id=str(uuid.uuid4()),
            acct=account_id,
            date=date_int,
            amount=int(amount * 100),
            category=category,
            payee=payee,
            notes=notes,
        )


class ZeroBudgetMonths(SQLModel, table=True):
    __tablename__ = "zero_budget_months"

    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    buffered: Optional[int] = Field(default=None, sa_column=Column("buffered", Integer, server_default=text("0")))


class ZeroBudgets(SQLModel, table=True):
    __tablename__ = "zero_budgets"

    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    month: Optional[int] = Field(default=None, sa_column=Column("month", Integer))
    category: Optional[str] = Field(default=None, sa_column=Column("category", Text))
    amount: Optional[int] = Field(default=None, sa_column=Column("amount", Integer, server_default=text("0")))
    carryover: Optional[int] = Field(default=None, sa_column=Column("carryover", Integer, server_default=text("0")))
    goal: Optional[int] = Field(default=None, sa_column=Column("goal", Integer, server_default=text("null")))


class PendingTransactions(SQLModel, table=True):
    __tablename__ = "pending_transactions"

    id: Optional[str] = Field(default=None, sa_column=Column("id", Text, primary_key=True))
    acct: Optional[int] = Field(default=None, sa_column=Column("acct", ForeignKey("accounts.id")))
    amount: Optional[int] = Field(default=None, sa_column=Column("amount", Integer))
    description: Optional[str] = Field(default=None, sa_column=Column("description", Text))
    date: Optional[str] = Field(default=None, sa_column=Column("date", Text))

    account: Optional["Accounts"] = Relationship(back_populates="pending_transactions")
