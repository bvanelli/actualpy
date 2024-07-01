import datetime

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

from actual import Actual
from actual.exceptions import ActualDecryptionError, ActualError, AuthorizationError
from actual.queries import (
    create_transaction,
    get_accounts,
    get_categories,
    get_or_create_account,
    get_or_create_category,
    get_or_create_payee,
    get_payees,
    get_rules,
    get_ruleset,
    get_schedules,
    get_transactions,
)


@pytest.fixture
def actual_server():
    with DockerContainer("actualbudget/actual-server:24.5.0").with_exposed_ports(5006) as container:
        wait_for_logs(container, "Listening on :::5006...")
        yield container


def test_create_user_file(actual_server):
    port = actual_server.get_exposed_port(5006)
    with Actual(f"http://localhost:{port}", password="mypass", bootstrap=True) as actual:
        assert len(actual.list_user_files().data) == 0
        actual.create_budget("My Budget")
        actual.upload_budget()
        # add some entries to the budget
        acct = get_or_create_account(actual.session, "Bank")
        assert acct.balance == 0
        payee = get_or_create_payee(actual.session, "Landlord")
        category = get_or_create_category(actual.session, "Rent", "Fixed Costs")
        create_transaction(actual.session, datetime.date(2024, 5, 22), acct, payee, "Paying rent", category, -500)
        actual.commit()
        assert acct.balance == -500
        # list user files
        new_user_files = actual.list_user_files().data
        assert len(new_user_files) == 1
        assert new_user_files[-1].name == "My Budget"
        assert actual.info().build is not None
        # run rules
        actual.run_rules()
        # run bank sync
        actual.run_bank_sync()

    # make sure a new instance can now retrieve the budget info
    with Actual(f"http://localhost:{port}", password="mypass", file="My Budget"):
        assert len(get_accounts(actual.session)) == 1
        assert len(get_payees(actual.session)) == 2  # one is the account payee
        assert len(get_categories(actual.session)) > 6  # there are 6 default categories
        assert len(get_transactions(actual.session)) == 1
        assert len(get_schedules(actual.session)) == 0
        assert len(get_rules(actual.session)) == 0
        assert get_ruleset(actual.session).rules == []

    with pytest.raises(AuthorizationError):
        Actual(actual.api_url, password="mywrongpass", file="My Budget")


def test_encrypted_file(actual_server):
    port = actual_server.get_exposed_port(5006)
    with Actual(f"http://localhost:{port}", password="mypass", encryption_password="mypass", bootstrap=True) as actual:
        actual.create_budget("My Encrypted Budget")
        actual.upload_budget()
        user_files = actual.list_user_files().data
        assert user_files[0].encrypt_key_id is not None
    # re-download budget
    with Actual(
        f"http://localhost:{port}", password="mypass", encryption_password="mypass", file="My Encrypted Budget"
    ) as actual:
        assert actual.session is not None
    with pytest.raises(ActualDecryptionError):
        Actual(
            f"http://localhost:{port}", password="mypass", encryption_password="mywrongpass", file="My Encrypted Budget"
        ).download_budget()


def test_update_file_name(actual_server):
    port = actual_server.get_exposed_port(5006)
    with Actual(f"http://localhost:{port}", password="mypass", bootstrap=True) as actual:
        assert len(actual.list_user_files().data) == 0
        actual.create_budget("My Budget")
        actual.upload_budget()
        actual.rename_budget("Other name")
        files = actual.list_user_files().data
        assert len(files) == 1
        assert files[0].name == "Other name"
    # should raise an error if budget does not exist
    with Actual(f"http://localhost:{port}", password="mypass") as actual:
        with pytest.raises(ActualError):
            actual.rename_budget("Failing name")
