from __future__ import annotations

import datetime
import decimal
import enum
from typing import List, Optional

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
    transactions: List[BankSyncTransactionDTO]
    holdings: List[dict]


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
    reference_date: Optional[str] = Field(None, alias="referenceDate", description="The date of the balance")


class TransactionItem(BaseModel):
    transaction_id: Optional[str] = Field(None, alias="transactionId")
    booked: Optional[bool] = False
    transaction_amount: BankSyncAmount = Field(..., alias="transactionAmount")
    # these fields are generated on the server itself, so we can trust them as being correct
    payee_name: Optional[str] = Field(None, alias="payeeName")
    date: datetime.date = Field(..., alias="date")
    notes: Optional[str] = Field(None, alias="notes")
    # goCardless optional fields
    payee: Optional[str] = Field(None, validation_alias=AliasChoices("debtorName", "creditorName"))
    payee_account: Optional[DebtorAccount] = Field(
        None, validation_alias=AliasChoices("debtorAccount", "creditorAccount")
    )
    booking_date: Optional[datetime.date] = Field(None, alias="bookingDate")
    value_date: Optional[datetime.date] = Field(None, alias="valueDate")
    remittance_information_unstructured: str = Field(None, alias="remittanceInformationUnstructured")
    remittance_information_unstructured_array: List[str] = Field(
        default_factory=list, alias="remittanceInformationUnstructuredArray"
    )
    additional_information: Optional[str] = Field(None, alias="additionalInformation")
    # simpleFin optional fields
    posted_date: Optional[datetime.date] = Field(None, alias="postedDate")

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
    all: List[TransactionItem] = Field(..., description="List of all transactions, from newest to oldest.")
    booked: List[TransactionItem]
    pending: List[TransactionItem]


class BankSyncAccountData(BaseModel):
    accounts: List[BankSyncAccountDTO]


class BankSyncTransactionData(BaseModel):
    balances: List[Balance]
    starting_balance: int = Field(..., alias="startingBalance")
    transactions: Transactions
    # goCardless specific
    iban: Optional[str] = None
    institution_id: Optional[str] = Field(None, alias="institutionId")

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
    status: Optional[str] = None
    reason: Optional[str] = None
