import datetime
import json
import os
import pathlib
from typing import Literal

import pytest
from click.testing import Result
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs
from typer.testing import CliRunner

from actual import Actual, __version__
from actual.cli.config import Config, default_config_path
from actual.queries import (
    create_account,
    create_budget,
    create_transaction,
    get_or_create_category,
    get_or_create_category_group,
    get_or_create_preference,
)
from tests.conftest import ACTUAL_SERVER_INTEGRATION_VERSIONS

runner = CliRunner()
server_version = ACTUAL_SERVER_INTEGRATION_VERSIONS[-1]  # use latest version


def base_dataset(
    actual: Actual,
    budget_name: str = "Test",
    encryption_password: str | None = None,
    budget_type: Literal["envelope", "tracking"] = "envelope",
):
    actual.create_budget(budget_name)
    bank = create_account(actual.session, "Bank")
    income_group = get_or_create_category_group(actual.session, "Income")
    income_group.is_income = 1
    starting = get_or_create_category(actual.session, "Starting", "Income")
    starting.is_income = 1
    create_transaction(
        actual.session, datetime.date(2024, 9, 5), bank, "Starting Balance", category="Starting", amount=150
    )
    create_transaction(
        actual.session, datetime.date(2024, 12, 24), bank, "Shopping Center", "Christmas Gifts", "Gifts", -100
    )
    if budget_type == "tracking":
        get_or_create_preference(actual.session, "budgetType", budget_type)
        create_budget(actual.session, datetime.date(2024, 12, 1), "Starting", 150)

    create_budget(actual.session, datetime.date(2024, 12, 1), "Gifts", 120)

    # create negative transaction for today
    create_transaction(actual.session, datetime.date.today(), bank, "Shopping Center", "Other things", "Gifts", -10)

    actual.commit()
    actual.upload_budget()

    if encryption_password:
        actual.encrypt(encryption_password)


def init_actual_server(module_mocker, path, budget_type: Literal["envelope", "tracking"] = "envelope"):
    module_mocker.patch("actual.cli.config.default_config_path", return_value=path / "config.yaml")
    with DockerContainer(f"actualbudget/actual-server:{server_version}").with_exposed_ports(5006) as container:
        wait_for_logs(container, "Listening on :::5006...")
        # create a new budget
        port = container.get_exposed_port(5006)
        with Actual(f"http://localhost:{port}", password="mypass", bootstrap=True) as actual:
            base_dataset(actual, budget_type=budget_type)
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


@pytest.fixture()
def actual_server(request, module_mocker, tmp_path_factory):
    path = pathlib.Path(tmp_path_factory.mktemp("config"))
    yield from init_actual_server(module_mocker, path)


def invoke(command: list[str]) -> Result:
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
    # you can show contexts
    assert invoke(["get-contexts"]).exit_code == 0
    assert invoke(["-o", "json", "get-contexts"]).exit_code == 0
    # you can use context
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
    assert str(default_config_path()).endswith(".actualpy" + os.sep + "config.yaml")
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

    # Split the output by line and assert that all expected data is present
    lines = result.stdout.split("\n")
    assert "Accounts" in lines[0]
    assert "Account Name" in lines[2] and "Balance" in lines[2]
    assert "Bank" in lines[4] and "40.00" in lines[4]

    # make sure json is valid
    result = invoke(["-o", "json", "accounts"])
    assert json.loads(result.stdout) == [{"name": "Bank", "balance": 40.00}]


def test_transactions(actual_server):
    result = invoke(["transactions"])
    assert result.exit_code == 0

    # Split the output by line and assert that all expected data is present
    lines = result.stdout.split("\n")
    assert "Transactions" in lines[0]
    for f in ["Date", "Payee", "Notes", "Category", "Amount"]:
        assert f in lines[2]
    for f in ["2024-12-24", "Shopping Center", "Christmas Gifts", "Gifts", "-100.00"]:
        assert f in lines[5]
    for f in ["2024-09-05", "Starting Balance", "Starting", "150.00"]:
        assert f in lines[6]

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

    # Split the output by line and assert that all expected data is present
    lines = result.stdout.split("\n")
    assert "Payees" in lines[0]
    assert "Name" in lines[2] and "Balance" in lines[2]
    assert "0.00" in lines[4]
    assert "Starting Balance" in lines[5] and "150.00" in lines[5]
    assert "Shopping Center" in lines[6] and "-110.00" in lines[6]

    # make sure json is valid
    result = invoke(["-o", "json", "payees"])
    assert {"name": "Shopping Center", "balance": -110.00} in json.loads(result.stdout)


def test_envelope_budget(actual_server):
    result = invoke(["budget", "2024-12-01"])
    assert result.exit_code == 0

    lines = result.stdout.split("\n")
    assert "Available funds" in lines[2] and "150.00" in lines[2]
    assert "Overspent in previous month" in lines[3] and "0.00" in lines[3]
    assert "Budgeted" in lines[4] and "120.00" in lines[4]
    assert "For next month" in lines[5] and "0.00" in lines[5]
    assert "To Budget" in lines[7] and "30.00" in lines[8]
    assert "Gifts" in lines[16] and "120.00" in lines[16] and "-100.00" in lines[16]

    # make sure json is valid
    result = invoke(["-o", "json", "budget", "2024-12-01"])
    response = json.loads(result.stdout)
    assert "incomeAvailable" in response and response["incomeAvailable"] == 150.0
    assert "lastMonthOverspent" in response and response["lastMonthOverspent"] == 0.0
    assert "totalBudgeted" in response and response["totalBudgeted"] == 120.0
    assert "forNextMonth" in response and response["forNextMonth"] == 0.0
    assert "toBudget" in response and response["toBudget"] == 30.0
    assert "categoryGroups" in response
    # find the gift group
    gifts = [c for cg in response["categoryGroups"] for c in cg["categories"] if c["name"] == "Gifts"]
    assert len(gifts) == 1
    assert gifts[0]["budgeted"] == 120.0
    assert gifts[0]["spent"] == -100.0
    assert gifts[0]["balance"] == 20.0

    # make sure calling it without arguments also works
    result = invoke(["budget"])
    assert result.exit_code == 0


def test_tracking_budget(module_mocker, tmp_path_factory):
    path = pathlib.Path(tmp_path_factory.mktemp("config"))
    # we do a little trick here to initialize a tracking budget
    for _server in init_actual_server(module_mocker, path, budget_type="tracking"):
        result = invoke(["budget", "2024-12-01"])
        assert result.exit_code == 0

        lines = result.stdout.split("\n")
        assert "Income" in lines[2] and "0.00 of 150.00" in lines[3]
        assert "Expenses" in lines[5] and "-100.00 of 120.00" in lines[6]
        assert "Saved" in lines[8] and "-100.00" in lines[9]
        assert "Gifts" in lines[17] and "120.00" in lines[17] and "-100.00" in lines[17]

        # make sure json is valid
        result = invoke(["-o", "json", "budget", "2024-12-01"])
        response = json.loads(result.stdout)
        assert "totalIncome" in response and response["totalIncome"] == 0.0
        assert "totalSpent" in response and response["totalSpent"] == -100
        assert "totalBalance" in response and response["totalBalance"] == 20
        assert "budgeted" in response and response["budgeted"] == 120.0
        assert "budgetedIncome" in response and response["budgetedIncome"] == 150.0
        assert "overspent" in response and response["overspent"] == -100.0
        assert "categoryGroups" in response
        # find the gift group
        gifts = [c for cg in response["categoryGroups"] for c in cg["categories"] if c["name"] == "Gifts"]
        assert len(gifts) == 1
        assert gifts[0]["budgeted"] == 120.0
        assert gifts[0]["spent"] == -100.0

        # make sure calling it without arguments also works
        result = invoke(["budget"])
        assert result.exit_code == 0


def test_export(actual_server, mocker):
    export_data = mocker.patch("actual.Actual.export_data")
    invoke(["export"])
    export_data.assert_called_once()
    assert export_data.call_args[0][0].name.endswith("Test.zip")

    # test normal file name
    invoke(["export", "Test.zip"])
    assert export_data.call_count == 2
    assert export_data.call_args[0][0].name == "Test.zip"
