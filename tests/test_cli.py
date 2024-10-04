import datetime
import json
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


def base_dataset(actual: Actual, budget_name: str = "Test", encryption_password: str = None):
    actual.create_budget(budget_name)
    bank = create_account(actual.session, "Bank")
    create_transaction(
        actual.session, datetime.date(2024, 9, 5), bank, "Starting Balance", category="Starting", amount=150
    )
    create_transaction(
        actual.session, datetime.date(2024, 12, 24), bank, "Shopping Center", "Christmas Gifts", "Gifts", -100
    )
    actual.commit()
    actual.upload_budget()
    if encryption_password:
        actual.encrypt(encryption_password)


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
        result = invoke(
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
            ]
        )
        assert result.exit_code == 0
        yield container


def invoke(command: List[str]) -> Result:
    from actual.cli.main import app

    return runner.invoke(app, command)


def test_init_interactive(actual_server, mocker):
    # create a new encrypted file
    port = actual_server.get_exposed_port(5006)
    with Actual(f"http://localhost:{port}", password="mypass") as actual:
        base_dataset(actual, "Extra", "mypass")
    # test full prompt
    mock_prompt = mocker.patch("typer.prompt")
    mock_prompt.side_effect = [f"http://localhost:{port}", "mypass", 2, "mypass", "myextra"]
    assert invoke(["init"]).exit_code == 0
    assert invoke(["use-context", "myextra"]).exit_code == 0
    assert invoke(["use-context", "test"]).exit_code == 0
    # remove extra context
    assert invoke(["remove-context", "myextra"]).exit_code == 0
    # different context should not succeed
    assert invoke(["use-context", "myextra"]).exit_code != 0
    assert invoke(["remove-context", "myextra"]).exit_code != 0


def test_load_config(actual_server):
    cfg = Config.load()
    assert cfg.default_context == "test"
    assert str(default_config_path()).endswith(".actualpy/config.yaml")
    # if the context does not exist, it should fail to load the server
    cfg.default_context = "foo"
    with pytest.raises(ValueError, match="Could not find budget with context"):
        cfg.actual()


def test_app(actual_server):
    result = invoke(["version"])
    assert result.exit_code == 0
    assert result.stdout == f"Library Version: {__version__}\nServer Version: {server_version}\n"
    # make sure json is valid
    result = invoke(["-o", "json", "version"])
    assert json.loads(result.stdout) == {"library_version": __version__, "server_version": server_version}


def test_metadata(actual_server):
    result = invoke(["metadata"])
    assert result.exit_code == 0
    assert "" in result.stdout
    # make sure json is valid
    result = invoke(["-o", "json", "metadata"])
    assert "budgetName" in json.loads(result.stdout)


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
    # make sure json is valid
    result = invoke(["-o", "json", "accounts"])
    assert json.loads(result.stdout) == [{"name": "Bank", "balance": 50.00}]


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
    # make sure json is valid
    result = invoke(["-o", "json", "transactions"])
    assert {
        "date": "2024-12-24",
        "payee": "Shopping Center",
        "notes": "Christmas Gifts",
        "category": "Gifts",
        "amount": -100.00,
    } in json.loads(result.stdout)


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
    # make sure json is valid
    result = invoke(["-o", "json", "payees"])
    assert {"name": "Shopping Center", "balance": -100.00} in json.loads(result.stdout)


def test_export(actual_server, mocker):
    export_data = mocker.patch("actual.Actual.export_data")
    invoke(["export"])
    export_data.assert_called_once()
    assert export_data.call_args[0][0].name.endswith("Test.zip")

    # test normal file name
    invoke(["export", "Test.zip"])
    assert export_data.call_count == 2
    assert export_data.call_args[0][0].name == "Test.zip"
