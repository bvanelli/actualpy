import copy
import datetime
import decimal

import pytest

from actual import Actual, ActualBankSyncError
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
            "balanceAmount": {"amount": "0.00", "currency": "EUR"},
        }
    ],
    "institutionId": "My Bank",
    "startingBalance": 0,
    "transactions": {
        "all": [
            {
                "transactionId": "208584e9-343f-4831-8095-7b9f4a34a77e",
                "bookingDate": "2024-06-13",
                "valueDate": "2024-06-13",
                "date": "2024-06-13",
                "transactionAmount": {"amount": "9.26", "currency": "EUR"},
                "debtorName": "John Doe",
                "remittanceInformationUnstructured": "Transferring Money",
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
                "creditorName": "Institution GmbH",
                "creditorAccount": {"iban": "DE123456789"},
                "remittanceInformationUnstructured": "Payment",
                "remittanceInformationUnstructuredArray": ["Payment"],
                "bankTransactionCode": "FOO-BAR",
                "internalTransactionId": "6118268af4dc45039a7ca21b0fdcbe96",
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


@pytest.fixture
def set_mocks(mocker):
    # call for validate
    response_empty = copy.deepcopy(response)
    response_empty["transactions"]["all"] = []
    mocker.patch("requests.get").return_value = RequestsMock({"status": "ok", "data": {"validated": True}})
    main_mock = mocker.patch("requests.post")
    main_mock.side_effect = [
        RequestsMock({"status": "ok", "data": {"configured": True}}),
        RequestsMock({"status": "ok", "data": response}),
        RequestsMock({"status": "ok", "data": {"configured": True}}),  # in case it gets called again
        RequestsMock({"status": "ok", "data": response_empty}),
    ]
    return main_mock


def test_full_bank_sync_go_cardless(session, set_mocks):
    with Actual(token="foo") as actual:
        actual._session = session
        create_accounts(session, "goCardless")

        # now try to run the bank sync
        imported_transactions = actual.run_bank_sync()
        session.commit()
        assert len(imported_transactions) == 2
        assert imported_transactions[0].financial_id == "208584e9-343f-4831-8095-7b9f4a34a77e"
        assert imported_transactions[0].get_date() == datetime.date(2024, 6, 13)
        assert imported_transactions[0].get_amount() == decimal.Decimal("9.26")
        assert imported_transactions[0].payee.name == "John Doe"
        assert imported_transactions[0].notes == "Transferring Money"

        assert imported_transactions[1].financial_id == "a2c2fafe-334a-46a6-8d05-200c2e41397b"
        assert imported_transactions[1].get_date() == datetime.date(2024, 6, 13)
        assert imported_transactions[1].get_amount() == decimal.Decimal("-7.77")
        # the name of the payee was normalized (from GmbH to Gmbh) and the masked iban is included
        assert imported_transactions[1].payee.name == "Institution Gmbh (DE12 XXX 6789)"
        assert imported_transactions[1].notes == "Payment"

        # the next call should do nothing
        new_imported_transactions = actual.run_bank_sync()
        assert new_imported_transactions == []
        # assert that the call date is correctly set
        assert set_mocks.call_args_list[3][1]["json"]["startDate"] == "2024-06-13"


def test_full_bank_sync_go_simplefin(session, set_mocks):
    with Actual(token="foo") as actual:
        actual._session = session
        create_accounts(session, "simplefin")

        # now try to run the bank sync
        imported_transactions = actual.run_bank_sync("Bank")
        assert len(imported_transactions) == 2
        assert imported_transactions[0].financial_id == "208584e9-343f-4831-8095-7b9f4a34a77e"
        assert imported_transactions[0].get_date() == datetime.date(2024, 6, 13)
        assert imported_transactions[0].get_amount() == decimal.Decimal("9.26")
        assert imported_transactions[0].payee.name == "Transferring Money"  # simplefin uses the wrong field
        assert imported_transactions[0].notes == "Transferring Money"

        assert imported_transactions[1].financial_id == "a2c2fafe-334a-46a6-8d05-200c2e41397b"
        assert imported_transactions[1].get_date() == datetime.date(2024, 6, 13)
        assert imported_transactions[1].get_amount() == decimal.Decimal("-7.77")
        assert imported_transactions[1].payee.name == "Payment"  # simplefin uses the wrong field
        assert imported_transactions[1].notes == "Payment"


def test_bank_sync_unconfigured(mocker, session):
    mocker.patch("requests.get").return_value = RequestsMock({"status": "ok", "data": {"validated": True}})
    main_mock = mocker.patch("requests.post")
    main_mock.return_value = RequestsMock({"status": "ok", "data": {"configured": False}})

    with Actual(token="foo") as actual:
        actual._session = session
        create_accounts(session, "simplefin")
        assert actual.run_bank_sync() == []


def test_bank_sync_exception(session, mocker):
    mocker.patch("requests.get").return_value = RequestsMock({"status": "ok", "data": {"validated": True}})
    main_mock = mocker.patch("requests.post")
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
