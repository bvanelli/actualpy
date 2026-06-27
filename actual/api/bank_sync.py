from __future__ import annotations

import datetime
import decimal
import enum
import typing

from pydantic import AliasChoices, BaseModel, Field, model_validator

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


class BankSyncAmount(BaseModel):
    amount: decimal.Decimal
    currency: str


class DebtorAccount(BaseModel):
    iban: str

    @property
    def masked_iban(self):
        return f"({self.iban[:4]} XXX {self.iban[-4:]})"


class BalanceType(enum.Enum):
    # goCardless/Nordigen camelCase names.
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
    # Enable Banking returns ISO 20022 external balance-type codes instead of the
    # camelCase names above. https://enablebanking.com/docs/api/reference/#balance
    ISO_CLOSING_BOOKED = "CLBD"
    ISO_CLOSING_AVAILABLE = "CLAV"
    ISO_INTERIM_BOOKED = "ITBD"
    ISO_INTERIM_AVAILABLE = "ITAV"
    ISO_OPENING_BOOKED = "OPBD"
    ISO_OPENING_AVAILABLE = "OPAV"
    ISO_FORWARD_AVAILABLE = "FWAV"
    ISO_EXPECTED = "XPCD"
    ISO_PREVIOUSLY_CLOSED_BOOKED = "PRCD"
    ISO_INFORMATION = "INFO"

    @classmethod
    def _missing_(cls, value: object) -> BalanceType:
        # Be lenient with unknown/unmapped balance-type codes: the balance type is
        # informational only and not used in the sync reconciliation, so an unknown
        # code from any provider should not break parsing of the whole response.
        return cls.INFORMATION


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
    remittance_information_unstructured: str | None = Field(None, alias="remittanceInformationUnstructured")
    remittance_information_unstructured_array: list[str] = Field(
        default_factory=list, alias="remittanceInformationUnstructuredArray"
    )
    additional_information: str | None = Field(None, alias="additionalInformation")
    # simpleFin optional fields
    posted_date: datetime.date | None = Field(None, alias="postedDate")

    @model_validator(mode="before")
    @classmethod
    def _normalize_enable_banking(cls, data: typing.Any) -> typing.Any:
        """Map Enable Banking's native transaction shape onto the goCardless/simpleFin
        field names this model already understands.

        Enable Banking differs in several ways: the id is ``entry_reference``, the amount is
        always positive with a separate ``credit_debit_indicator`` (``DBIT``/``CRDT``) carrying
        the sign, there is no ``date`` field (only ``booking_date``/``transaction_date``), the
        booked flag is a ``status`` string (``BOOK``), and the counterparty is a nested
        ``creditor``/``debtor`` object rather than a flat ``creditorName``/``debtorName``.
        """
        if not isinstance(data, dict):
            return data
        if "entry_reference" not in data and "credit_debit_indicator" not in data:
            return data  # not an Enable Banking payload, leave untouched
        data = dict(data)
        data.setdefault("transactionId", data.get("entry_reference"))
        # amount: positive value + credit_debit_indicator -> signed transactionAmount
        amount = data.get("transaction_amount") or data.get("transactionAmount")
        if isinstance(amount, dict) and amount.get("amount") is not None:
            value = decimal.Decimal(str(amount["amount"]))
            if str(data.get("credit_debit_indicator", "")).upper() == "DBIT":
                value = -value
            data["transactionAmount"] = {"amount": str(value), "currency": amount.get("currency")}
        # date: no dedicated field, derive from booking/transaction/value date
        data.setdefault("date", data.get("booking_date") or data.get("transaction_date") or data.get("value_date"))
        data.setdefault("bookingDate", data.get("booking_date"))
        data.setdefault("valueDate", data.get("value_date"))
        # booked flag from ISO status string
        if "booked" not in data and "status" in data:
            data["booked"] = str(data.get("status", "")).upper() == "BOOK"
        # counterparty name from nested creditor/debtor
        if "creditorName" not in data and "debtorName" not in data:
            name = (data.get("creditor") or {}).get("name") or (data.get("debtor") or {}).get("name")
            if name:
                data["payeeName"] = data.get("payeeName") or name
                data["debtorName"] = name
        # counterparty account (iban) from nested creditor/debtor account
        if "creditorAccount" not in data and "debtorAccount" not in data:
            account = data.get("creditor_account") or data.get("debtor_account") or {}
            if isinstance(account, dict) and account.get("iban"):
                data["debtorAccount"] = {"iban": account["iban"]}
        # remittance info comes as a list under a different key
        remittance = data.get("remittance_information")
        if "remittanceInformationUnstructuredArray" not in data and isinstance(remittance, list):
            data["remittanceInformationUnstructuredArray"] = remittance
        return data

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
