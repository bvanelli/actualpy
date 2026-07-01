import copy
import datetime
import decimal

import pytest
from httpx import Client

from actual import Actual, ActualBankSyncError, ActualError
from actual.api.bank_sync import BalanceType, TransactionItem
from actual.database import Banks
from actual.queries import create_account
from tests.conftest import RequestsMock

response = {
    "iban": "DE123",
    "balances": [
        {
            "balanceType": "expected",
            "lastChangeDateTime": "2024-06-13T14:56:06.092039915Z",
            "referenceDate": "2024-06-13",
            "balanceAmount": {"amount": "1.49", "currency": "EUR"},
        }
    ],
    "institutionId": "My Bank",
    "startingBalance": 149,
    "transactions": {
        "all": [
            {
                "transactionId": "208584e9-343f-4831-8095-7b9f4a34a77e",
                "bookingDate": "2024-06-13",
                "valueDate": "2024-06-13",
                "date": "2024-06-13",
                "transactionAmount": {"amount": "9.26", "currency": "EUR"},
                "payeeName": "John Doe",
                "debtorName": "John Doe",
                "notes": "Transferring Money",
                "remittanceInformationUnstructured": "Transferring Money",
                "booked": True,
            },
            # some have creditor name
            {
                "transactionId": "a2c2fafe-334a-46a6-8d05-200c2e41397b",
                "mandateId": "FOOBAR",
                "creditorId": "FOOBAR",
                "bookingDate": "2024-06-13",
                "valueDate": "2024-06-13",
                "date": "2024-06-13",
                "transactionAmount": {"amount": "-7.77", "currency": "EUR"},
                "payeeName": "Institution Gmbh (DE12 XXX 6789)",
                "notes": "Payment",
                "creditorName": "Institution GmbH",
                "creditorAccount": {"iban": "DE123456789"},
                "remittanceInformationUnstructured": "Payment",
                "remittanceInformationUnstructuredArray": ["Payment"],
                "bankTransactionCode": "FOO-BAR",
                "internalTransactionId": "6118268af4dc45039a7ca21b0fdcbe96",
                "booked": True,
            },
            # ignored since booked is set to false, but all required fields are also missing
            {
                "date": "2024-06-13",
                "transactionAmount": {"amount": "0.00", "currency": "EUR"},
                "booked": False,
            },
        ],
        "booked": [],
        "pending": [],
    },
}

fail_response = {
    "error_type": "ACCOUNT_NEEDS_ATTENTION",
    "error_code": "ACCOUNT_NEEDS_ATTENTION",
    "reason": "The account needs your attention.",
}

# Enable Banking returns ISO 20022 balance-type codes (e.g. "ITBD") and a snake_case
# transaction shape with a positive amount plus a separate credit/debit indicator.
# All values below are synthetic.
response_enable_banking = {
    "balances": [
        {
            "balanceAmount": {"amount": 50000, "currency": "EUR"},
            "balanceType": "ITBD",
            "referenceDate": None,
        }
    ],
    "startingBalance": 50000,
    "transactions": {
        "all": [
            {
                "entry_reference": "EB0000000000000001",
                "transaction_amount": {"currency": "EUR", "amount": "25.50"},
                "credit_debit_indicator": "DBIT",
                "status": "BOOK",
                "booking_date": "2024-06-13",
                "transaction_date": "2024-06-13",
                "creditor": {"name": "Example Shop"},
                "creditor_account": {"iban": "NL00BANK0123456789"},
                "remittance_information": ["Card payment"],
            },
            {
                "entry_reference": "EB0000000000000002",
                "transaction_amount": {"currency": "EUR", "amount": "100.00"},
                "credit_debit_indicator": "CRDT",
                "status": "BOOK",
                "booking_date": "2024-06-12",
                "transaction_date": "2024-06-12",
                "debtor": {"name": "Jane Example"},
                "remittance_information": ["Incoming transfer"],
            },
            # pending entry, should be ignored (status is not BOOK); amount kept at 0.00
            # so it does not affect the deduced starting balance
            {
                "entry_reference": "EB0000000000000003",
                "transaction_amount": {"currency": "EUR", "amount": "0.00"},
                "credit_debit_indicator": "DBIT",
                "status": "PDNG",
                "transaction_date": "2024-06-13",
            },
        ],
        "booked": [],
        "pending": [],
    },
}


def create_accounts(session, protocol: str):
    bank = create_account(session, "Bank")
    create_account(session, "Not related")
    bank.account_sync_source = protocol
    bank.bank_id = bank.account_id = "foobar"
    session.add(Banks(id="foobar", bank_id="foobar", name="test"))
    session.commit()
    return bank


def generate_bank_sync_data(mocker, starting_balance: int | None = None, base: dict | None = None):
    response_full = copy.deepcopy(base if base is not None else response)
    if starting_balance:
        response_full["startingBalance"] = starting_balance
    response_empty: dict = copy.deepcopy(base if base is not None else response)
    response_empty["transactions"]["all"] = []
    mocker.patch.object(Client, "get").return_value = RequestsMock({"status": "ok", "data": {"validated": True}})
    main_mock = mocker.patch.object(Client, "post")
    main_mock.side_effect = [
        RequestsMock({"status": "ok", "data": {"configured": True}}),
        RequestsMock({"status": "ok", "data": response_full}),
        RequestsMock({"status": "ok", "data": {"configured": True}}),  # in case it gets called again
        RequestsMock({"status": "ok", "data": response_empty}),
    ]
    return main_mock


@pytest.fixture
def bank_sync_data_match(mocker):
    # call for validate
    return generate_bank_sync_data(mocker)


@pytest.fixture
def bank_sync_data_no_match(mocker):
    return generate_bank_sync_data(mocker, 2500)


def test_full_bank_sync_go_cardless(session, bank_sync_data_match):
    with Actual(token="foo") as actual:
        actual._session = session
        create_accounts(session, "goCardless")

        # now try to run the bank sync
        imported_transactions = actual.run_bank_sync()
        session.commit()
        assert len(imported_transactions) == 3
        assert imported_transactions[0].financial_id is None
        assert imported_transactions[0].get_date() == datetime.date(2024, 6, 13)
        # goCardless provides the correct starting balance
        assert imported_transactions[0].get_amount() == decimal.Decimal("1.49")
        assert imported_transactions[0].payee.name == "Starting Balance"
        assert imported_transactions[0].notes is None

        assert imported_transactions[1].financial_id == "a2c2fafe-334a-46a6-8d05-200c2e41397b"
        assert imported_transactions[1].get_date() == datetime.date(2024, 6, 13)
        assert imported_transactions[1].get_amount() == decimal.Decimal("-7.77")
        # the name of the payee was normalized (from GmbH to Gmbh) and the masked iban is included
        assert imported_transactions[1].payee.name == "Institution Gmbh (DE12 XXX 6789)"
        assert imported_transactions[1].notes == "Payment"
        # also test the iban generation functions
        loaded_transaction = TransactionItem.model_validate(response["transactions"]["all"][1])
        assert imported_transactions[1].payee.name == loaded_transaction.imported_payee

        assert imported_transactions[2].financial_id == "208584e9-343f-4831-8095-7b9f4a34a77e"
        assert imported_transactions[2].get_date() == datetime.date(2024, 6, 13)
        assert imported_transactions[2].get_amount() == decimal.Decimal("9.26")
        assert imported_transactions[2].payee.name == "John Doe"
        assert imported_transactions[2].notes == "Transferring Money"

        # the next call should do nothing
        new_imported_transactions = actual.run_bank_sync()
        assert new_imported_transactions == []
        # assert that the call date is correctly set
        assert bank_sync_data_match.call_args_list[3][1]["json"]["startDate"] == "2024-06-13"


def test_full_bank_sync_go_simplefin(session, bank_sync_data_match):
    with Actual(token="foo") as actual:
        actual._session = session
        create_accounts(session, "simpleFin")

        # now try to run the bank sync
        imported_transactions = actual.run_bank_sync("Bank")
        assert len(imported_transactions) == 2
        assert imported_transactions[0].financial_id == "a2c2fafe-334a-46a6-8d05-200c2e41397b"
        assert imported_transactions[0].get_date() == datetime.date(2024, 6, 13)
        assert imported_transactions[0].get_amount() == decimal.Decimal("-7.77")
        assert imported_transactions[0].payee.name == "Institution Gmbh (DE12 XXX 6789)"
        assert imported_transactions[0].notes == "Payment"

        assert imported_transactions[1].financial_id == "208584e9-343f-4831-8095-7b9f4a34a77e"
        assert imported_transactions[1].get_date() == datetime.date(2024, 6, 13)
        assert imported_transactions[1].get_amount() == decimal.Decimal("9.26")
        assert imported_transactions[1].payee.name == "John Doe"
        assert imported_transactions[1].notes == "Transferring Money"


def test_bank_sync_with_starting_balance(session, bank_sync_data_no_match):
    with Actual(token="foo") as actual:
        actual._session = session
        create_accounts(session, "simpleFin")
        # now try to run the bank sync
        imported_transactions = actual.run_bank_sync("Bank", run_rules=True)
        assert len(imported_transactions) == 3
        # first transaction should be the amount
        assert imported_transactions[0].get_date() == datetime.date(2024, 6, 13)
        # final amount is 2500 - (926 - 777) = 2351
        assert imported_transactions[0].get_amount() == decimal.Decimal("23.51")


def test_bank_sync_unconfigured(mocker, session):
    mocker.patch.object(Client, "get").return_value = RequestsMock({"status": "ok", "data": {"validated": True}})
    main_mock = mocker.patch.object(Client, "post")
    main_mock.return_value = RequestsMock({"status": "ok", "data": {"configured": False}})

    with Actual(token="foo") as actual:
        actual._session = session
        create_accounts(session, "simplefin")
        assert actual.run_bank_sync() == []


def test_bank_sync_failed_response_exception(session, mocker):
    mocker.patch.object(Client, "get").return_value = RequestsMock({"status": "ok", "data": {"validated": True}})
    main_mock = mocker.patch.object(Client, "post")
    main_mock.side_effect = [
        RequestsMock({"status": "ok", "data": {"configured": True}}),
        RequestsMock({"status": "ok", "data": fail_response}),
    ]
    with Actual(token="foo") as actual:
        actual._session = session
        create_accounts(session, "simplefin")

        # now try to run the bank sync
        with pytest.raises(ActualBankSyncError):
            actual.run_bank_sync()


@pytest.fixture
def bank_sync_data_enable_banking(mocker):
    return generate_bank_sync_data(mocker, base=response_enable_banking)


def test_full_bank_sync_enable_banking(session, bank_sync_data_enable_banking):
    with Actual(token="foo") as actual:
        actual._session = session
        create_accounts(session, "enableBanking")

        imported_transactions = actual.run_bank_sync()
        session.commit()
        # starting balance + 2 booked transactions (the pending entry is skipped)
        assert len(imported_transactions) == 3

        # Enable Banking's startingBalance is the *current* balance (like simpleFin), so the
        # starting-balance transaction is deduced: 500.00 - (100.00 - 25.50) = 425.50
        assert imported_transactions[0].payee.name == "Starting Balance"
        assert imported_transactions[0].get_amount() == decimal.Decimal("425.50")

        # CRDT -> positive amount, payee taken from the nested debtor object
        assert imported_transactions[1].financial_id == "EB0000000000000002"
        assert imported_transactions[1].get_date() == datetime.date(2024, 6, 12)
        assert imported_transactions[1].get_amount() == decimal.Decimal("100.00")
        assert imported_transactions[1].payee.name == "Jane Example"

        # DBIT -> negated amount, payee taken from the nested creditor object
        assert imported_transactions[2].financial_id == "EB0000000000000001"
        assert imported_transactions[2].get_date() == datetime.date(2024, 6, 13)
        assert imported_transactions[2].get_amount() == decimal.Decimal("-25.50")
        assert imported_transactions[2].payee.name == "Example Shop"

        # the next call should do nothing
        assert actual.run_bank_sync() == []


def test_enable_banking_transaction_item_parsing():
    # ISO 20022 balance-type code must parse instead of raising
    assert BalanceType("ITBD") is BalanceType.ISO_INTERIM_BOOKED
    assert BalanceType("some-unknown-code") is BalanceType.INFORMATION
    # a DBIT entry is normalized to a negative signed amount with a derived date
    item = TransactionItem.model_validate(response_enable_banking["transactions"]["all"][0])
    assert item.transaction_id == "EB0000000000000001"
    assert item.booked is True
    assert item.date == datetime.date(2024, 6, 13)
    assert item.transaction_amount.amount == decimal.Decimal("-25.50")
    assert item.payee_name == "Example Shop"


def test_bank_sync_invalid_input(session, mocker):
    mocker.patch.object(Client, "get").return_value = RequestsMock({"status": "ok", "data": {"validated": True}})

    account = create_account(session, "notSync")
    with Actual(token="foo") as actual:
        actual._session = session
        with pytest.raises(ActualError, match="Account 'notExistingAccount' not found"):
            actual.run_bank_sync("notExistingAccount")
        with pytest.raises(ActualError, match="Account is missing sync source"):
            actual._run_bank_sync_account(account, datetime.date.today(), False)
