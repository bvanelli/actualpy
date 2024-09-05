import datetime
import pathlib

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs
from typer.testing import CliRunner

from actual import Actual, __version__
from actual.queries import create_account, create_transaction

runner = CliRunner()
server_version = "24.9.0"


def base_dataset(actual: Actual):
    actual.create_budget("Test")
    bank = create_account(actual.session, "Bank", 150)
    create_transaction(
        actual.session, datetime.date(2024, 12, 24), bank, "Shopping Center", "Christmas Gifts", "Gifts", -100
    )
    actual.commit()
    actual.upload_budget()


@pytest.fixture(scope="module")
def actual_server(request, module_mocker, tmp_path_factory):
    path = pathlib.Path(tmp_path_factory.mktemp("config"))
    module_mocker.patch("actual.cli.config.default_config_path", return_value=path / "config.yaml")
    with DockerContainer(f"actualbudget/actual-server:{server_version}").with_exposed_ports(5006) as container:
        wait_for_logs(container, "Listening on :::5006...")
        # create a new budget
        port = container.get_exposed_port(5006)
        with Actual(f"http://localhost:{port}", password="mypass", bootstrap=True) as actual:
            base_dataset(actual)
        # init configuration
        from actual.cli.main import app

        result = runner.invoke(
            app,
            [
                "init",
                "--url",
                f"http://localhost:{port}",
                "--password",
                "mypass",
                "--file",
                "Test",
                "--context",
                "test",
            ],
        )
        assert result.exit_code == 0
        yield container


def test_app(actual_server):
    from actual.cli.main import app

    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout == f"Library Version: {__version__}\nServer Version: {server_version}\n"


def test_metadata(actual_server):
    from actual.cli.main import app

    result = runner.invoke(app, ["metadata"])
    assert result.exit_code == 0
    assert "budgetName" in result.stdout


def test_accounts(actual_server):
    from actual.cli.main import app

    result = runner.invoke(app, ["accounts"])
    assert result.exit_code == 0
    assert result.stdout == (
        "         Accounts         \n"
        "┏━━━━━━━━━━━━━━┳━━━━━━━━━┓\n"
        "┃ Account Name ┃ Balance ┃\n"
        "┡━━━━━━━━━━━━━━╇━━━━━━━━━┩\n"
        "│ Bank         │   50.00 │\n"
        "└──────────────┴─────────┘\n"
    )


def test_transactions(actual_server):
    from actual.cli.main import app

    result = runner.invoke(app, ["transactions"])
    assert result.exit_code == 0
    assert result.stdout == (
        "                                  "
        "Transactions                                  \n"
        "┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓\n"
        "┃ Date       ┃ Payee            ┃ Notes           ┃ Category         ┃  Amount ┃\n"
        "┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩\n"
        "│ 2024-12-24 │ Shopping Center  │ Christmas Gifts │ Gifts            │ -100.00 │\n"
        "│ 2024-09-05 │ Starting Balance │                 │ Starting         │  150.00 │\n"
        "│            │                  │                 │ Balances         │         │\n"
        "└────────────┴──────────────────┴─────────────────┴──────────────────┴─────────┘\n"
    )


def test_payees(actual_server):
    from actual.cli.main import app

    result = runner.invoke(app, ["payees"])
    assert result.exit_code == 0
    assert result.stdout == (
        "            Payees            \n"
        "┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓\n"
        "┃ Name             ┃ Balance ┃\n"
        "┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩\n"
        "│                  │    0.00 │\n"  # this is the payee for the account
        "│ Starting Balance │  150.00 │\n"
        "│ Shopping Center  │ -100.00 │\n"
        "└──────────────────┴─────────┘\n"
    )
