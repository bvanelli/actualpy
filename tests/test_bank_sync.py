import copy
import datetime
import decimal

import pytest
from requests import Session

from actual import Actual, ActualBankSyncError
from actual.api.bank_sync import TransactionItem
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


def create_accounts(session, protocol: str):
    bank = create_account(session, "Bank")
    create_account(session, "Not related")
    bank.account_sync_source = protocol
    bank.bank_id = bank.account_id = "foobar"
    session.add(Banks(id="foobar", bank_id="foobar", name="test"))
    session.commit()
    return bank


def generate_bank_sync_data(mocker, starting_balance: int = None):
    response_full = copy.deepcopy(response)
    if starting_balance:
        response_full["startingBalance"] = starting_balance
    response_empty = copy.deepcopy(response)
    response_empty["transactions"]["all"] = []
    mocker.patch.object(Session, "get").return_value = RequestsMock({"status": "ok", "data": {"validated": True}})
    main_mock = mocker.patch.object(Session, "post")
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
        assert imported_transactions[0].payee.name == "Payment"  # simplefin uses the wrong field
        assert imported_transactions[0].notes == "Payment"

        assert imported_transactions[1].financial_id == "208584e9-343f-4831-8095-7b9f4a34a77e"
        assert imported_transactions[1].get_date() == datetime.date(2024, 6, 13)
        assert imported_transactions[1].get_amount() == decimal.Decimal("9.26")
        assert imported_transactions[1].payee.name == "Transferring Money"  # simplefin uses the wrong field
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
    mocker.patch.object(Session, "get").return_value = RequestsMock({"status": "ok", "data": {"validated": True}})
    main_mock = mocker.patch.object(Session, "post")
    main_mock.return_value = RequestsMock({"status": "ok", "data": {"configured": False}})

    with Actual(token="foo") as actual:
        actual._session = session
        create_accounts(session, "simplefin")
        assert actual.run_bank_sync() == []


def test_bank_sync_exception(session, mocker):
    mocker.patch.object(Session, "get").return_value = RequestsMock({"status": "ok", "data": {"validated": True}})
    main_mock = mocker.patch.object(Session, "post")
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
