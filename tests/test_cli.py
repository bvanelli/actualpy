import datetime
import pathlib
from typing import List

import pytest
from click.testing import Result
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs
from typer.testing import CliRunner

from actual import Actual, __version__
from actual.cli.config import Config, default_config_path
from actual.queries import create_account, create_transaction

runner = CliRunner()
server_version = "24.9.0"


def base_dataset(actual: Actual):
    actual.create_budget("Test")
    bank = create_account(actual.session, "Bank")
    create_transaction(
        actual.session, datetime.date(2024, 9, 5), bank, "Starting Balance", category="Starting", amount=150
    )
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


def invoke(command: List[str]) -> Result:
    from actual.cli.main import app

    return runner.invoke(app, command)


def test_load_config(actual_server):
    cfg = Config.load()
    assert cfg.default_context == "test"
    assert str(default_config_path()).endswith(".actual/config.yaml")


def test_app(actual_server):
    result = invoke(["version"])
    assert result.exit_code == 0
    assert result.stdout == f"Library Version: {__version__}\nServer Version: {server_version}\n"


def test_metadata(actual_server):
    result = invoke(["metadata"])
    assert result.exit_code == 0
    assert "budgetName" in result.stdout


def test_accounts(actual_server):
    result = invoke(["accounts"])
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
    result = invoke(["transactions"])
    assert result.exit_code == 0
    assert result.stdout == (
        "                              Transactions                              \n"
        "┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┓\n"
        "┃ Date       ┃ Payee            ┃ Notes           ┃ Category ┃  Amount ┃\n"
        "┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━┩\n"
        "│ 2024-12-24 │ Shopping Center  │ Christmas Gifts │ Gifts    │ -100.00 │\n"
        "│ 2024-09-05 │ Starting Balance │                 │ Starting │  150.00 │\n"
        "└────────────┴──────────────────┴─────────────────┴──────────┴─────────┘\n"
    )


def test_payees(actual_server):
    result = invoke(["payees"])
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


def test_export(actual_server, mocker):
    export_data = mocker.patch("actual.Actual.export_data")
    invoke(["export"])
    export_data.assert_called_once()
    assert export_data.call_args[0][0].name.endswith("Test.zip")

    # test normal file name
    invoke(["export", "Test.zip"])
    assert export_data.call_count == 2
    assert export_data.call_args[0][0].name == "Test.zip"
