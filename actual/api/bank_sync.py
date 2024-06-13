from __future__ import annotations

import datetime
import decimal
import enum
from typing import List, Optional

from pydantic import BaseModel, Field


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
    booked: bool
    booking_date: str = Field(..., alias="bookingDate")
    date: datetime.date
    debtor_name: str = Field(..., alias="debtorName")
    remittance_information_unstructured: str = Field(..., alias="remittanceInformationUnstructured")
    transaction_amount: BankSyncAmount = Field(..., alias="transactionAmount")
    transaction_id: str = Field(..., alias="transactionId")
    value_date: str = Field(..., alias="valueDate")


class Transactions(BaseModel):
    all: List[TransactionItem]
    booked: List[TransactionItem]
    pending: List


class BankSyncAccountData(BaseModel):
    accounts: List[BankSyncAccountDTO]


class BankSyncTransactionData(BaseModel):
    balances: List[Balance]
    starting_balance: int = Field(..., alias="startingBalance")
    transactions: Transactions
