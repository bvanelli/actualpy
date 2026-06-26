from typing import TypedDict


class AccountData(TypedDict):
    name: str
    balance: float


class TransactionData(TypedDict):
    date: str
    payee: str | None
    notes: str
    category: str | None
    amount: float


class PayeeData(TypedDict):
    name: str
    balance: float
