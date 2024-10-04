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


class BalanceType(enum.Enum):
    CLOSING_BOOKED = "closingBooked"
    EXPECTED = "expected"
    FORWARD_AVAILABLE = "forwardAvailable"
    INTERIM_AVAILABLE = "interimAvailable"
    INTERIM_BOOKED = "interimBooked"
    NON_INVOICED = "nonInvoiced"
    OPENING_BOOKED = "openingBooked"


class Balance(BaseModel):
    """An object containing the balance amount and currency."""

    balance_amount: BankSyncAmount = Field(..., alias="balanceAmount")
    balance_type: BalanceType = Field(..., alias="balanceType")
    reference_date: Optional[str] = Field(..., alias="referenceDate", description="The date of the balance")


class TransactionItem(BaseModel):
    transaction_id: str = Field(..., alias="transactionId")
    booking_date: str = Field(..., alias="bookingDate")
    booked: bool = True
    value_date: str = Field(..., alias="valueDate")
    transaction_amount: BankSyncAmount = Field(..., alias="transactionAmount")
    # this field will come as either debtorName or creditorName, depending on if it's a debt or credit
    payee: str = Field(None, validation_alias=AliasChoices("debtorName", "creditorName"))
    payee_account: Optional[DebtorAccount] = Field(
        None, validation_alias=AliasChoices("debtorAccount", "creditorAccount")
    )
    date: datetime.date
    remittance_information_unstructured: str = Field(None, alias="remittanceInformationUnstructured")
    remittance_information_unstructured_array: List[str] = Field(
        default_factory=list, alias="remittanceInformationUnstructuredArray"
    )
    additional_information: Optional[str] = Field(None, alias="additionalInformation")

    @property
    def imported_payee(self):
        name_parts = []
        name = self.payee or self.notes or self.additional_information
        if name:
            name_parts.append(title(name))
        if self.payee_account and self.payee_account.iban:
            iban = self.payee_account.iban
            name_parts.append(f"({iban[:4]} XXX {iban[-4:]})")
        return " ".join(name_parts).strip()

    @property
    def notes(self):
        notes = self.remittance_information_unstructured or ", ".join(
            self.remittance_information_unstructured_array or []
        )
        return notes.strip()


class Transactions(BaseModel):
    all: List[TransactionItem]
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


class BankSyncErrorData(BaseModel):
    error_type: str
    error_code: str
    status: Optional[str] = None
    reason: Optional[str] = None
