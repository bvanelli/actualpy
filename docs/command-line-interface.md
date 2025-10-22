# Command Line Interface

The `actualpy` command line interface (CLI) allows you to interact with your Actual Budget data directly
from the terminal without writing custom scripts.

## Installation

Install the CLI with the optional CLI dependencies:

```bash
pip install "actualpy[cli]"
```

## Initial setup

Before using any commands, you need to initialize a budget configuration:

```console
$ actualpy init
Please enter the URL of the actual server [http://localhost:5006]:
Please enter the Actual server password:
(1) Test
Please enter the budget index: 1
Name of the context for this budget [test]:
Initialized budget 'test'
```

Once initialized, you can export your budget data:

```console
$ actualpy export
Exported budget 'Test' (budget id 'My-Finances-0b46239') to '2024-10-04-1438-Test.zip'.
```

The configuration will be saved on the folder `.actualpy/config.yaml`. Check full help for more details:

```console
$ actualpy --help

 Usage: actualpy [OPTIONS] COMMAND [ARGS]...

╭─ Options ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --output              -o      [table|json]  Output format: table or json [default: table]                                                                  │
│ --install-completion                        Install completion for the current shell.                                                                      │
│ --show-completion                           Show completion for the current shell, to copy it or customize the installation.                               │
│ --help                                      Show this message and exit.                                                                                    │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ accounts         Show all accounts.                                                                                                                        │
│ export           Generates an export from the budget (for CLI backups).                                                                                    │
│ init             Initializes an actual budget config interactively if options are not provided.                                                            │
│ metadata         Displays all metadata for the current budget.                                                                                             │
│ payees           Show all payees.                                                                                                                          │
│ remove-context   Removes a configured context from the configuration.                                                                                      │
│ transactions     Show all transactions.                                                                                                                    │
│ use-context      Sets the default context for the CLI.                                                                                                     │
│ version          Shows the library and server version.                                                                                                     │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```
