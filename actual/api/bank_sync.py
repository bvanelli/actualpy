from __future__ import annotations

import datetime
import decimal
import enum

from pydantic import AliasChoices, BaseModel, Field

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
    holdings: list[dict]


class BankSyncAmount(BaseModel):
    amount: decimal.Decimal
    currency: str


class DebtorAccount(BaseModel):
    iban: str

    @property
    def masked_iban(self):
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


class Balance(BaseModel):
    """An object containing the balance amount and currency."""

    balance_amount: BankSyncAmount = Field(..., alias="balanceAmount")
    balance_type: BalanceType = Field(..., alias="balanceType")
    reference_date: str | None = Field(None, alias="referenceDate", description="The date of the balance")


class TransactionItem(BaseModel):
    transaction_id: str | None = Field(None, alias="transactionId")
    booked: bool | None = False
    transaction_amount: BankSyncAmount = Field(..., alias="transactionAmount")
    # these fields are generated on the server itself, so we can trust them as being correct
    payee_name: str | None = Field(None, alias="payeeName")
    date: datetime.date = Field(..., alias="date")
    notes: str | None = Field(None, alias="notes")
    # goCardless optional fields
    payee: str | None = Field(None, validation_alias=AliasChoices("debtorName", "creditorName"))
    payee_account: DebtorAccount | None = Field(None, validation_alias=AliasChoices("debtorAccount", "creditorAccount"))
    booking_date: datetime.date | None = Field(None, alias="bookingDate")
    value_date: datetime.date | None = Field(None, alias="valueDate")
    remittance_information_unstructured: str = Field(None, alias="remittanceInformationUnstructured")
    remittance_information_unstructured_array: list[str] = Field(
        default_factory=list, alias="remittanceInformationUnstructuredArray"
    )
    additional_information: str | None = Field(None, alias="additionalInformation")
    # simpleFin optional fields
    posted_date: datetime.date | None = Field(None, alias="postedDate")

    @property
    def imported_payee(self):
        """Deprecated method to convert the payee name. Use the payee_name instead."""
        name_parts = []
        name = self.payee or self.notes or self.additional_information
        if name:
            name_parts.append(title(name))
        if self.payee_account and self.payee_account.iban:
            name_parts.append(self.payee_account.masked_iban)
        return " ".join(name_parts).strip()


class Transactions(BaseModel):
    all: list[TransactionItem] = Field(..., description="List of all transactions, from newest to oldest.")
    booked: list[TransactionItem]
    pending: list[TransactionItem]


class BankSyncAccountData(BaseModel):
    accounts: list[BankSyncAccountDTO]


class BankSyncTransactionData(BaseModel):
    balances: list[Balance]
    starting_balance: int = Field(..., alias="startingBalance")
    transactions: Transactions
    # goCardless specific
    iban: str | None = None
    institution_id: str | None = Field(None, alias="institutionId")

    @property
    def balance(self) -> decimal.Decimal:
        """Starting balance of the account integration, converted to a decimal amount.

        For `simpleFin`, this will represent the current amount on the account, while for `goCardless` it will
        represent the actual initial amount before all transactions.
        """
        return decimal.Decimal(self.starting_balance) / 100


class BankSyncErrorData(BaseModel):
    error_type: str
    error_code: str
    status: str | None = None
    reason: str | None = None
