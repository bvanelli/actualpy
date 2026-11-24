from __future__ import annotations

import datetime
import decimal
import enum
import typing
from typing import Annotated, Any, ClassVar, Generic, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from actual.exceptions import ActualInvalidOperationError
from actual.utils.title import title


class BankSyncTransactionDTO(BaseModel):
    id: str
    posted: int
    amount: str
    description: str
    payee: str
    memo: str


class BankSyncOrgDTO(BaseModel):
    domain: str
    sfin_url: str = Field(..., alias="sfin-url")


class BankSyncAccountDTO(BaseModel):
    org: BankSyncOrgDTO
    id: str
    name: str
    currency: str
    balance: str
    available_balance: str = Field(..., alias="available-balance")
    balance_date: int = Field(..., alias="balance-date")
    transactions: list[BankSyncTransactionDTO]
    holdings: list[dict[str, typing.Any]]


class BankSyncModel(BaseModel):
    """
    Base model for the bank sync responses.

    Fields that are not declared on the model are kept, and can be accessed with their snake_case name, i.e. a
    `debtorName` sent by the server is available as `transaction.debtor_name`. Undeclared fields are returned as they
    were sent by the server, without any type conversion, and are also available under `model_extra`.
    """

    model_config = ConfigDict(extra="allow", alias_generator=to_camel)

    def __getattr__(self, item: str) -> Any:
        try:
            return super().__getattr__(item)  # type: ignore[misc]
        except AttributeError:
            if not item.startswith("_"):
                extra = self.__pydantic_extra__ or {}
                for key in (to_camel(item), item):
                    if key in extra:
                        return extra[key]
            raise


class BankSyncAmount(BaseModel):
    amount: decimal.Decimal
    currency: str


class DebtorAccount(BaseModel):
    iban: str | None = None

    @property
    def masked_iban(self) -> str:
        if not self.iban:
            return ""
        return f"({self.iban[:4]} XXX {self.iban[-4:]})"


class BalanceType(enum.Enum):
    # See https://developer.gocardless.com/bank-account-data/balance#balance_type for full documentation
    CLOSING_AVAILABLE = "closingAvailable"
    CLOSING_BOOKED = "closingBooked"
    CLOSING_CLEARED = "closingCleared"
    EXPECTED = "expected"
    FORWARD_AVAILABLE = "forwardAvailable"
    INTERIM_AVAILABLE = "interimAvailable"
    INTERIM_CLEARED = "interimCleared"
    INFORMATION = "information"
    INTERIM_BOOKED = "interimBooked"
    NON_INVOICED = "nonInvoiced"
    OPENING_BOOKED = "openingBooked"
    OPENING_AVAILABLE = "openingAvailable"
    OPENING_CLEARED = "openingCleared"
    PREVIOUSLY_CLOSED_BOOKED = "previouslyClosedBooked"


class Balance(BankSyncModel):
    """An object containing the balance amount and currency."""

    balance_amount: BankSyncAmount
    balance_type: Annotated[BalanceType | str, Field(union_mode="left_to_right")] = Field(
        ..., description="The balance type. Types not known by `BalanceType` are kept as strings."
    )
    reference_date: str | None = Field(None, description="The date of the balance")


class BankSyncTransaction(BankSyncModel):
    """
    Transaction fields that are common to all bank sync providers.

    The Actual server normalizes the transactions of every provider into these fields. Provider specific fields are
    either declared on the provider subclasses, or can be accessed as undeclared fields (see `BankSyncModel`).
    """

    transaction_id: str | None = None
    booked: bool | None = False
    transaction_amount: BankSyncAmount
    # these fields are generated on the server itself, so we can trust them as being correct
    payee_name: str | None = None
    date: datetime.date
    notes: str | None = None


class GoCardlessTransaction(BankSyncTransaction):
    payee: str | None = Field(None, validation_alias=AliasChoices("debtorName", "creditorName"))
    payee_account: DebtorAccount | None = Field(None, validation_alias=AliasChoices("debtorAccount", "creditorAccount"))
    booking_date: datetime.date | None = None
    value_date: datetime.date | None = None
    remittance_information_unstructured: str | None = None
    remittance_information_unstructured_array: list[str] = Field(default_factory=list)
    additional_information: str | None = None

    @property
    def imported_payee(self) -> str:
        """Deprecated method to convert the payee name. Use the payee_name instead."""
        name_parts = []
        name = self.payee or self.notes or self.additional_information
        if name:
            name_parts.append(title(name))
        if self.payee_account and self.payee_account.iban:
            name_parts.append(self.payee_account.masked_iban)
        return " ".join(name_parts).strip()


class SimpleFinTransaction(BankSyncTransaction):
    posted_date: datetime.date | None = None
    transacted_date: datetime.date | None = None


T = TypeVar("T", bound=BankSyncTransaction)


class Transactions(BankSyncModel, Generic[T]):
    all: list[T] = Field(..., description="List of all transactions, from newest to oldest.")
    booked: list[T]
    pending: list[T]


class BankSyncAccountData(BaseModel):
    accounts: list[BankSyncAccountDTO]


class BankSyncTransactionData(BankSyncModel, Generic[T]):
    balances: list[Balance]
    starting_balance: int
    transactions: Transactions[T]

    @property
    def balance(self) -> decimal.Decimal:
        """Starting balance of the account integration, converted to a decimal amount.

        For `goCardless`, this represents the actual initial amount before all transactions, while for all other
        providers it represents the current amount on the account. Use `BankSyncProvider.initial_balance` to get the
        initial amount regardless of the provider.
        """
        return decimal.Decimal(self.starting_balance) / 100


class GoCardlessTransactionData(BankSyncTransactionData[GoCardlessTransaction]):
    iban: str | None = None
    institution_id: str | None = None


class SimpleFinTransactionData(BankSyncTransactionData[SimpleFinTransaction]):
    pass


class BankSyncErrorData(BaseModel):
    error_type: str
    error_code: str
    status: str | None = None
    reason: str | None = None


class BankSyncProvider:
    """
    Describes how to retrieve and interpret the transactions of a bank sync provider.

    This default implementation is used for any provider without a dedicated implementation, and parses the
    transactions into the common `BankSyncTransaction` model.

    :param name: name of the provider, as used by the server endpoints (i.e. `gocardless`).
    """

    data_model: ClassVar[type[BankSyncTransactionData[Any]]] = BankSyncTransactionData

    def __init__(self, name: str):
        self.name = name.lower()

    def payload(self, account_id: str, start_date: datetime.date, requisition_id: str | None = None) -> dict[str, str]:
        """
        Builds the request payload to retrieve the transactions from the server.

        :param account_id: the account id of the provider, stored as `account_id` on the account.
        :param start_date: the date from which the transactions should be retrieved.
        :param requisition_id: the requisition id, only used by providers that require it.
        :return: the payload to be sent to the server.
        """
        return {"accountId": account_id, "startDate": start_date.strftime("%Y-%m-%d")}

    def initial_balance(self, data: BankSyncTransactionData[Any]) -> decimal.Decimal:
        """
        Returns the balance of the account before all transactions retrieved.

        By default, the server returns the current balance of the account, so the retrieved transactions are deducted
        from it.

        :param data: the transaction data returned by the server.
        :return: the initial balance of the account, as a decimal amount.
        """
        return data.balance - sum((t.transaction_amount.amount for t in data.transactions.all), decimal.Decimal(0))


class GoCardlessProvider(BankSyncProvider):
    data_model = GoCardlessTransactionData

    def payload(self, account_id: str, start_date: datetime.date, requisition_id: str | None = None) -> dict[str, str]:
        if requisition_id is None:
            raise ActualInvalidOperationError("Retrieving transactions with goCardless requires `requisition_id`")
        return {**super().payload(account_id, start_date), "requisitionId": requisition_id}

    def initial_balance(self, data: BankSyncTransactionData[Any]) -> decimal.Decimal:
        # goCardless already provides the correct starting balance
        # https://github.com/actualbudget/actual/blob/f09f4af667ddd57e031dcdb0d428ae935aa2afad/packages/loot-core/src/server/accounts/sync.ts#L740-L752
        return data.balance


class SimpleFinProvider(BankSyncProvider):
    data_model = SimpleFinTransactionData


def get_bank_sync_provider(
    name: str,
    providers: dict[str, type[BankSyncProvider]] | None = None,
) -> BankSyncProvider:
    """
    Returns the provider implementation for the bank sync name. If the provider has no dedicated implementation, the
    default `BankSyncProvider` is returned instead.

    :param name: name of the provider, case-insensitive (i.e. `goCardless` or `gocardless`).
    :param providers: mapping of the lowercase provider name to its implementation.
    :return: the provider implementation.
    """
    if providers is None:
        providers = {"gocardless": GoCardlessProvider, "simplefin": SimpleFinProvider}
    return providers.get(name.lower(), BankSyncProvider)(name)
