import datetime

import pytest
from sqlalchemy import delete, select
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

from actual import Actual, js_migration_statements
from actual.database import __TABLE_COLUMNS_MAP__, Dashboard, Migrations, reflect_model
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


@pytest.fixture(params=["24.8.0", "24.9.0", "24.10.0"])  # todo: support multiple versions at once
def actual_server(request):
    # we test integration with the 5 latest versions of actual server
    with DockerContainer(f"actualbudget/actual-server:{request.param}").with_exposed_ports(5006) as container:
        wait_for_logs(container, "Listening on :::5006...")
        yield container


def test_create_user_file(actual_server):
    port = actual_server.get_exposed_port(5006)
    with Actual(f"http://localhost:{port}", password="mypass", bootstrap=True) as actual:
        assert len(actual.list_user_files().data) == 0
        actual.create_budget("My Budget")
        actual.upload_budget()
        assert "userId" in actual.get_metadata()
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


def test_reimport_file_from_zip(actual_server, tmp_path):
    port = actual_server.get_exposed_port(5006)
    backup_file = f"{tmp_path}/backup.zip"
    # create one file
    with Actual(f"http://localhost:{port}", password="mypass", bootstrap=True) as actual:
        # add some entries to the budget
        actual.create_budget("My Budget")
        get_or_create_account(actual.session, "Bank")
        actual.commit()
        actual.upload_budget()
    # re-download file and save as a backup
    with Actual(f"http://localhost:{port}", password="mypass", file="My Budget") as actual:
        actual.export_data(backup_file)
        actual.delete_budget()
    # re-upload the file
    with Actual(f"http://localhost:{port}", password="mypass") as actual:
        actual.import_zip(backup_file)
        actual.upload_budget()
    # check if the account can be retrieved
    with Actual(f"http://localhost:{port}", password="mypass", file="My Budget") as actual:
        assert len(get_accounts(actual.session)) == 1


def test_models(actual_server):
    port = actual_server.get_exposed_port(5006)
    with Actual(f"http://localhost:{port}", password="mypass", encryption_password="mypass", bootstrap=True) as actual:
        actual.create_budget("My Budget")
        # check if the models are matching
        metadata = reflect_model(actual.session.bind)
        # check first if all tables are present
        for table_name, table in metadata.tables.items():
            assert table_name in __TABLE_COLUMNS_MAP__, f"Missing table '{table_name}' on models."
            # then assert if all columns are matching the model
            for column_name in table.columns.keys():
                assert (
                    column_name in __TABLE_COLUMNS_MAP__[table_name]["columns"]
                ), f"Missing column '{column_name}' at table '{table_name}'."


def test_header_login():
    with DockerContainer("actualbudget/actual-server:24.7.0").with_env(
        "ACTUAL_LOGIN_METHOD", "header"
    ).with_exposed_ports(5006) as container:
        port = container.get_exposed_port(5006)
        wait_for_logs(container, "Listening on :::5006...")
        with Actual(f"http://localhost:{port}", password="mypass", bootstrap=True):
            pass
        # make sure we can log in
        actual = Actual(f"http://localhost:{port}", password="mypass")
        response_login = actual.login("mypass")
        response_header_login = actual.login("mypass", "header")
        assert response_login.data.token == response_header_login.data.token


def test_session_reflection_after_migrations():
    with DockerContainer("actualbudget/actual-server:24.9.0").with_exposed_ports(5006) as container:
        port = container.get_exposed_port(5006)
        wait_for_logs(container, "Listening on :::5006...")
        with Actual(f"http://localhost:{port}", password="mypass", bootstrap=True) as actual:
            actual.create_budget("My Budget")
            actual.upload_budget()
            # add a dashboard entry
            actual.session.add(Dashboard(id="123", x=1, y=2))
            actual.commit()
            # revert the last migration like it never happened
            Dashboard.__table__.drop(actual.engine)
            actual.session.exec(delete(Migrations).where(Migrations.id == 1722804019000))
            actual.session.commit()
        # now try to download the budget, it should not fail
        with Actual(f"http://localhost:{port}", file="My Budget", password="mypass") as actual:
            assert len(actual.session.exec(select(Dashboard)).all()) > 2  # there are two default dashboards


def test_empty_query_migrations():
    # empty queries should not fail
    assert js_migration_statements("await db.runQuery('');") == []
    # malformed entries should not fail
    assert js_migration_statements("await db.runQuery(") == []
    # weird formats neither
    assert js_migration_statements("db.runQuery\n('update 1')") == ["update 1;"]
