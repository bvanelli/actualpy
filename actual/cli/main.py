import datetime
import pathlib
import warnings
from typing import Optional

import typer
from rich.console import Console
from rich.json import JSON
from rich.table import Table

from actual import Actual, get_accounts, get_transactions
from actual.cli.config import BudgetConfig, Config, OutputType, State
from actual.queries import get_payees
from actual.version import __version__

# avoid displaying warnings on a CLI
warnings.filterwarnings("ignore")

app = typer.Typer()

console = Console()
config: Config = Config.load()
state: State = State()


@app.callback()
def main(output: OutputType = typer.Option("table", "--output", "-o", help="Output format: table or json")):
    if output:
        state.output = output


@app.command()
def init(
    url: str = typer.Option(None, "--url", help="URL of the actual server"),
    password: str = typer.Option(None, "--password", help="Password for the budget"),
    encryption_password: str = typer.Option(None, "--encryption-password", help="Encryption password for the budget"),
    context: str = typer.Option(None, "--context", help="Context for this budget context"),
    file_id: str = typer.Option(None, "--file", help="File ID or name on the remote server"),
):
    """
    Initializes an actual budget config interactively if options are not provided.
    """
    if not url:
        url = typer.prompt("Please enter the URL of the actual server", default="http://localhost:5006")

    if not password:
        password = typer.prompt("Please enter the Actual server password", hide_input=True)

    # test the login
    server = Actual(url, password=password)

    if not file_id:
        files = server.list_user_files()
        options = [file for file in files.data if not file.deleted]
        for idx, option in enumerate(options):
            console.print(f"[purple]({idx + 1}) {option.name}[/purple]")
        file_id_idx = typer.prompt("Please enter the budget index", type=int)
        assert file_id_idx - 1 in range(len(options)), "Did not select one of the options, exiting."
        server.set_file(options[file_id_idx - 1])
    else:
        server.set_file(file_id)
    file_id = server._file.file_id

    if not encryption_password and server._file.encrypt_key_id:
        encryption_password = typer.prompt("Please enter the encryption password for the budget", hide_input=True)
        # test the file
        server.download_budget(encryption_password)
    else:
        encryption_password = None

    if not context:
        # take the default context name as the file name in lowercase
        default_context = server._file.name.lower().replace(" ", "-")
        context = typer.prompt("Name of the context for this budget", default=default_context)

    config.budgets[context] = BudgetConfig(
        url=url,
        password=password,
        encryption_password=encryption_password,
        file_id=file_id,
    )
    if not config.default_context:
        config.default_context = context
    config.save()
    console.print(f"[green]Initialized budget '{context}'[/green]")


@app.command()
def use_context(context: str = typer.Argument(..., help="Context for this budget context")):
    """Sets the default context for the CLI."""
    if context not in config.budgets:
        raise ValueError(f"Context '{context}' is not registered. Choose one from {list(config.budgets.keys())}")
    config.default_context = context
    config.save()


@app.command()
def remove_context(context: str = typer.Argument(..., help="Context to be removed")):
    """Removes a configured context from the configuration."""
    if context not in config.budgets:
        raise ValueError(f"Context '{context}' is not registered. Choose one from {list(config.budgets.keys())}")
    config.budgets.pop(context)
    config.default_context = list(config.budgets.keys())[0] if len(config.budgets) == 1 else ""
    config.save()


@app.command()
def version():
    """
    Shows the library and server version.
    """
    actual = config.actual()
    info = actual.info()
    if state.output == OutputType.table:
        console.print(f"Library Version: {__version__}")
        console.print(f"Server Version: {info.build.version}")
    else:
        console.print(JSON.from_data({"library_version": __version__, "server_version": info.build.version}))


@app.command()
def accounts():
    """
    Show all accounts.
    """
    # Mock data for demonstration purposes
    accounts_data = []
    with config.actual() as actual:
        accounts_raw_data = get_accounts(actual.session)
        for account in accounts_raw_data:
            accounts_data.append(
                {
                    "name": account.name,
                    "balance": float(account.balance),
                }
            )

    if state.output == OutputType.table:
        table = Table(title="Accounts")
        table.add_column("Account Name", justify="left", style="cyan", no_wrap=True)
        table.add_column("Balance", justify="right", style="green")

        for account in accounts_data:
            table.add_row(account["name"], f"{account['balance']:.2f}")

        console.print(table)
    else:
        console.print(JSON.from_data(accounts_data))


@app.command()
def transactions():
    """
    Show all transactions.
    """
    transactions_data = []
    with config.actual() as actual:
        transactions_raw_data = get_transactions(actual.session)
        for transaction in transactions_raw_data:
            transactions_data.append(
                {
                    "date": transaction.get_date().isoformat(),
                    "payee": transaction.payee.name,
                    "notes": transaction.notes or "",
                    "category": (transaction.category.name if transaction.category else None),
                    "amount": round(float(transaction.get_amount()), 2),
                }
            )

    if state.output == OutputType.table:
        table = Table(title="Transactions")
        table.add_column("Date", justify="left", style="cyan", no_wrap=True)
        table.add_column("Payee", justify="left", style="magenta")
        table.add_column("Notes", justify="left", style="yellow")
        table.add_column("Category", justify="left", style="cyan")
        table.add_column("Amount", justify="right", style="green")

        for transaction in transactions_data:
            color = "green" if transaction["amount"] >= 0 else "red"
            table.add_row(
                transaction["date"],
                transaction["payee"],
                transaction["notes"],
                transaction["category"],
                f"[{color}]{transaction['amount']:.2f}[/]",
            )

        console.print(table)
    else:
        console.print(JSON.from_data(transactions_data))


@app.command()
def payees():
    """
    Show all payees.
    """
    payees_data = []
    with config.actual() as actual:
        payees_raw_data = get_payees(actual.session)
        for payee in payees_raw_data:
            payees_data.append({"name": payee.name, "balance": round(float(payee.balance), 2)})

    if state.output == OutputType.table:
        table = Table(title="Payees")
        table.add_column("Name", justify="left", style="cyan", no_wrap=True)
        table.add_column("Balance", justify="right")

        for payee in payees_data:
            color = "green" if payee["balance"] >= 0 else "red"
            table.add_row(
                payee["name"],
                f"[{color}]{payee['balance']:.2f}[/]",
            )
        console.print(table)
    else:
        console.print(JSON.from_data(payees_data))


@app.command()
def export(
    filename: Optional[pathlib.Path] = typer.Argument(
        default=None,
        help="Name of the file to export, in zip format. "
        "Leave it empty to export it to the current folder with default name.",
    ),
):
    """
    Generates an export from the budget (for CLI backups).
    """
    with config.actual() as actual:
        if filename is None:
            current_date = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
            budget_name = actual.get_metadata().get("budgetName", "My Finances")
            filename = pathlib.Path(f"{current_date}-{budget_name}.zip")
        actual.export_data(filename)
        actual_metadata = actual.get_metadata()
        budget_name = actual_metadata["budgetName"]
        budget_id = actual_metadata["id"]
    console.print(
        f"[green]Exported budget '{budget_name}' (budget id '{budget_id}') to [bold]'{filename}'[/bold].[/green]"
    )


@app.command()
def metadata():
    """Displays all metadata for the current budget."""
    with config.actual() as actual:
        actual_metadata = actual.get_metadata()
    if state.output == OutputType.table:
        table = Table(title="Metadata")
        table.add_column("Key", justify="left", style="cyan", no_wrap=True)
        table.add_column("Value", justify="left")
        for key, value in actual_metadata.items():
            table.add_row(key, str(value))
        console.print(table)
    else:
        console.print(JSON.from_data(actual_metadata))


if __name__ == "__main__":
    app()
