from __future__ import annotations

import base64
import datetime
import io
import json
import pathlib
import re
import sqlite3
import tempfile
import uuid
import zipfile
from os import PathLike
from typing import IO, Union

from sqlmodel import Session, create_engine, select

from actual.api import ActualServer
from actual.api.models import RemoteFileListDTO
from actual.crypto import create_key_buffer, decrypt_from_meta, encrypt, make_salt
from actual.database import (
    Accounts,
    MessagesClock,
    Transactions,
    get_attribute_by_table_name,
    get_class_by_table_name,
    strong_reference_session,
)
from actual.exceptions import ActualError, InvalidZipFile, UnknownFileId
from actual.protobuf_models import HULC_Client, Message, SyncRequest
from actual.queries import (
    get_account,
    get_accounts,
    get_ruleset,
    get_transactions,
    reconcile_transaction,
)
from actual.version import __version__  # noqa: F401


class Actual(ActualServer):
    def __init__(
        self,
        base_url: str = "http://localhost:5006",
        token: str = None,
        password: str = None,
        file: str = None,
        encryption_password: str = None,
        data_dir: Union[str, pathlib.Path] = None,
        bootstrap: bool = False,
        sa_kwargs: dict = None,
    ):
        """
        Implements the Python API for the Actual Server in order to be able to read and modify information on Actual
        books using Python.

        Parts of the implementation are available at the following file:
        https://github.com/actualbudget/actual/blob/2178da0414958064337b2c53efc95ff1d3abf98a/packages/loot-core/src/server/cloud-storage.ts

        :param base_url: url of the running Actual server
        :param token: the token for authentication, if this is available (optional)
        :param password: the password for authentication. It will be used on the .login() method to retrieve the token.
        :param file: the name or id of the file to be set
        :param encryption_password: password used to configure encryption, if existing
        :param data_dir: where to store the downloaded files from the server. If not specified, a temporary folder will
        be created instead.
        :param bootstrap: if the server is not bootstrapped, bootstrap it with the password.
        :param sa_kwargs: additional kwargs passed to the SQLAlchemy session maker. Examples are `autoflush` (enabled
        by default), `autocommit` (disabled by default). For a list of all parameters, check the SQLAlchemy
        documentation: https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session.__init__
        """
        super().__init__(base_url, token, password, bootstrap)
        self._file: RemoteFileListDTO | None = None
        self._data_dir = pathlib.Path(data_dir) if data_dir else None
        self.engine = None
        self._session: Session | None = None
        self._client: HULC_Client | None = None
        # set the correct file
        if file:
            self.set_file(file)
        self._encryption_password = encryption_password
        self._master_key = None
        self._in_context = False
        self._sa_kwargs = sa_kwargs or {}
        if "autoflush" not in self._sa_kwargs:
            self._sa_kwargs["autoflush"] = True

    def __enter__(self) -> Actual:
        self._in_context = True
        if self._file:
            self.download_budget(self._encryption_password)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            self._session.close()
        self._in_context = False

    @property
    def session(self) -> Session:
        if not self._session:
            raise ActualError("No session defined. Use `with Actual() as actual:` construct to generate one.")
        return self._session

    def set_file(self, file_id: Union[str, RemoteFileListDTO]) -> RemoteFileListDTO:
        """
        Sets the file id for the class for further requests. The file_id argument can be either the name, the remote
        id or the group id (also known as sync_id) from the file. If there are duplicates for the name, this method
        will raise `UnknownFileId`.
        """
        if isinstance(file_id, RemoteFileListDTO):
            self._file = file_id
            return file_id
        else:
            selected_files = []
            user_files = self.list_user_files()
            for file in user_files.data:
                if (file.file_id == file_id or file.name == file_id or file.group_id == file_id) and file.deleted == 0:
                    selected_files.append(file)
            if len(selected_files) == 0:
                raise UnknownFileId(f"Could not find a file id or identifier '{file_id}'")
            elif len(selected_files) > 1:
                raise UnknownFileId(f"Multiple files found with identifier '{file_id}'")
            return self.set_file(selected_files[0])

    def run_migrations(self, migration_files: list[str]):
        """Runs the migration files, skipping the ones that have already been run. The files can be retrieved from
        .data_file_index() method. This first file is the base database, and the following files are migrations.
        Migrations can also be .js files. In this case, we have to extract and execute queries from the standard JS."""
        conn = sqlite3.connect(self._data_dir / "db.sqlite")
        for file in migration_files:
            if not file.startswith("migrations"):
                continue  # in case db.sqlite file gets passed as one of the migrations files
            file_id = file.split("_")[0].split("/")[1]
            if conn.execute(f"SELECT id FROM __migrations__ WHERE id = '{file_id}';").fetchall():
                continue  # skip migration as it was already ran
            migration = self.data_file(file)  # retrieves file from actual server
            sql_statements = migration.decode()
            if file.endswith(".js"):
                # there is one migration which is Javascript. All entries inside db.execQuery(`...`) must be executed
                exec_entries = re.findall(r"db\.execQuery\(`([^`]*)`\)", sql_statements, re.DOTALL)
                sql_statements = "\n".join(exec_entries)
            conn.executescript(sql_statements)
            conn.execute(f"INSERT INTO __migrations__ (id) VALUES ({file_id});")
        conn.commit()
        conn.close()

    def create_budget(self, budget_name: str):
        """Creates a budget using the remote server default database and migrations. If password is provided, the
        budget will be encrypted."""
        migration_files = self.data_file_index()
        # create folder for the files
        if not self._data_dir:
            self._data_dir = pathlib.Path(tempfile.mkdtemp())
        # first migration file is the default database
        migration = self.data_file(migration_files[0])
        (self._data_dir / "db.sqlite").write_bytes(migration)
        # also write the metadata file with default fields
        random_id = str(uuid.uuid4()).replace("-", "")[:7]
        file_id = str(uuid.uuid4())
        self.update_metadata(
            {
                "id": f"My-Finances-{random_id}",
                "budgetName": budget_name,
                "userId": self._token,
                "cloudFileId": file_id,
                "resetClock": True,
            }
        )
        self._file = RemoteFileListDTO(name=budget_name, fileId=file_id, groupId=None, deleted=0, encryptKeyId=None)
        # create engine for downloaded database and run migrations
        self.run_migrations(migration_files[1:])
        # generate a session
        self.engine = create_engine(f"sqlite:///{self._data_dir}/db.sqlite")
        if self._in_context:
            self._session = strong_reference_session(Session(self.engine, **self._sa_kwargs))
        # create a clock
        self.load_clock()

    def rename_budget(self, budget_name: str):
        if not self._file:
            raise UnknownFileId("No current file loaded.")
        self.update_user_file_name(self._file.file_id, budget_name)

    def delete_budget(self):
        if not self._file:
            raise UnknownFileId("No current file loaded.")
        self.delete_user_file(self._file.file_id)
        # reset group id, as file cannot be synced anymore
        self._file.group_id = None

    def export_data(self, output_file: str | PathLike[str] | IO[bytes] = None) -> bytes:
        """Export your data as a zip file containing db.sqlite and metadata.json files. It can be imported into another
        Actual instance by closing an open file (if any), then clicking the “Import file” button, then choosing
        “Actual.” Even when encryption is enabled, the exported zip file will not have any encryption."""
        temp_file = io.BytesIO()
        with zipfile.ZipFile(temp_file, "a", zipfile.ZIP_DEFLATED, False) as z:
            z.write(self._data_dir / "db.sqlite", "db.sqlite")
            z.write(self._data_dir / "metadata.json", "metadata.json")
        content = temp_file.getvalue()
        if output_file:
            with open(output_file, "wb") as f:
                f.write(content)
        return content

    def encrypt(self, encryption_password: str):
        """Encrypts the local database using a new key, and re-uploads to the server.

        WARNING: this resets the file on the server. Make sure you have a copy of the database before attempting this
        operation.
        """
        if encryption_password and not self._file.encrypt_key_id:
            # password was provided, but encryption key not, create one
            key_id = str(uuid.uuid4())
            salt = make_salt()
            self.user_create_key(self._file.file_id, key_id, encryption_password, salt)
            self.update_metadata({"encryptKeyId": key_id})
            self._file.encrypt_key_id = key_id
        elif self._file.encrypt_key_id:
            key_info = self.user_get_key(self._file.file_id)
            salt = key_info.data.salt
        else:
            raise ActualError("Budget is encrypted but password was not provided")
        self._master_key = create_key_buffer(encryption_password, salt)
        # encrypt binary data with
        encrypted = encrypt(self._file.encrypt_key_id, self._master_key, self.export_data())
        binary_data = io.BytesIO(base64.b64decode(encrypted["value"]))
        encryption_meta = encrypted["meta"]
        self.reset_user_file(self._file.file_id)
        self.upload_user_file(binary_data.getvalue(), self._file.file_id, self._file.name, encryption_meta)
        self.set_file(self._file.file_id)

    def upload_budget(self):
        """Uploads the current file to the Actual server."""
        if not self._data_dir:
            raise UnknownFileId("No current file loaded.")
        if not self._file:
            file_id = str(uuid.uuid4())
            metadata = self.get_metadata()
            budget_name = metadata.get("budgetName", "My Finances")
            self._file = RemoteFileListDTO(name=budget_name, fileId=file_id, groupId=None, deleted=0, encryptKeyId=None)
        binary_data = io.BytesIO()
        with zipfile.ZipFile(binary_data, "a", zipfile.ZIP_DEFLATED, False) as z:
            z.write(self._data_dir / "db.sqlite", "db.sqlite")
            z.write(self._data_dir / "metadata.json", "metadata.json")
        # we have to first upload the user file so the reference id can be used to generate a new encryption key
        self.upload_user_file(binary_data.getvalue(), self._file.file_id, self._file.name)
        # reset local file id to retrieve the grouping id
        self.set_file(self._file.file_id)
        # encrypt the file and re-upload
        if self._encryption_password or self._master_key or self._file.encrypt_key_id:
            self.encrypt(self._encryption_password)

    def reupload_budget(self):
        self.reset_user_file(self._file.file_id)
        self.upload_budget()

    def apply_changes(self, messages: list[Message]):
        """Applies a list of sync changes, based on what the sync method returned on the remote."""
        if not self.engine:
            raise UnknownFileId("No valid file available, download one with download_budget()")
        with Session(self.engine) as s:
            for message in messages:
                if message.dataset == "prefs":
                    # write it to metadata.json instead
                    self.update_metadata({message.row: message.get_value()})
                    continue
                table = get_class_by_table_name(message.dataset)
                if table is None:
                    raise ActualError(
                        f"Actual found a table not supported by the library: table '{message.dataset}' not found"
                    )
                column = get_attribute_by_table_name(message.dataset, message.column)
                if column is None:
                    raise ActualError(
                        f"Actual found a column not supported by the library: "
                        f"column '{message.column}' at table '{message.dataset}' not found"
                    )
                entry = s.get(table, message.row)
                if not entry:
                    entry = table(id=message.row)
                setattr(entry, column, message.get_value())
                s.add(entry)
                # this seems to be required for sqlmodel, remove if not needed anymore when querying from cache
                s.flush()
            s.commit()

    def get_metadata(self) -> dict:
        """Gets the content of metadata.json."""
        metadata_file = self._data_dir / "metadata.json"
        return json.loads(metadata_file.read_text())

    def update_metadata(self, patch: dict):
        """Updates the metadata.json from the Actual file with the patch fields. The patch is a dictionary that will
        then be merged on the metadata and written again to a file."""
        metadata_file = self._data_dir / "metadata.json"
        if metadata_file.is_file():
            config = self.get_metadata() | patch
        else:
            config = patch
        metadata_file.write_text(json.dumps(config, separators=(",", ":")))

    def download_budget(self, encryption_password: str = None):
        """Downloads the budget file from the remote. After the file is downloaded, the sync endpoint is queries
        for the list of pending changes. The changes are individual row updates, that are then applied on by one to
        the downloaded database state.

        If the budget is password protected, the password needs to be present to download the budget, otherwise it will
        fail.
        """
        file_bytes = self.download_user_file(self._file.file_id)
        encryption_password = encryption_password or self._encryption_password

        if self._file.encrypt_key_id and encryption_password is None:
            raise ActualError("File is encrypted but no encryption password provided.")
        if encryption_password is not None and self._file.encrypt_key_id:
            file_info = self.get_user_file_info(self._file.file_id)
            key_info = self.user_get_key(self._file.file_id)
            self._master_key = create_key_buffer(encryption_password, key_info.data.salt)
            # decrypt file bytes
            file_bytes = decrypt_from_meta(self._master_key, file_bytes, file_info.data.encrypt_meta)
        self.import_zip(io.BytesIO(file_bytes))
        # actual js always calls validation
        self.validate()
        # run migrations if needed
        migration_files = self.data_file_index()
        self.run_migrations(migration_files[1:])
        self.sync()
        # create session if not existing
        if self._in_context and not self._session:
            self._session = strong_reference_session(Session(self.engine, **self._sa_kwargs))

    def import_zip(self, file_bytes: str | PathLike[str] | IO[bytes]):
        try:
            zip_file = zipfile.ZipFile(file_bytes)
        except zipfile.BadZipfile as e:
            raise InvalidZipFile(f"Invalid zip file: {e}") from None
        if not self._data_dir:
            self._data_dir = pathlib.Path(tempfile.mkdtemp())
        # this should extract 'db.sqlite' and 'metadata.json' to the folder
        zip_file.extractall(self._data_dir)
        self.engine = create_engine(f"sqlite:///{self._data_dir}/db.sqlite")
        # load the client id
        self.load_clock()

    def sync(self):
        # after downloading the budget, some pending transactions still need to be retrieved using sync
        request = SyncRequest(
            {
                "messages": [],
                "fileId": self._file.file_id,
                "groupId": self._file.group_id,
                "keyId": self._file.encrypt_key_id,
            }
        )
        request.set_null_timestamp(client_id=self._client.client_id)  # using 0 timestamp to retrieve all changes
        changes = self.sync_sync(request)
        self.apply_changes(changes.get_messages(self._master_key))
        if changes.messages:
            self._client = HULC_Client.from_timestamp(changes.messages[-1].timestamp)

    def load_clock(self) -> MessagesClock:
        """See implementation at:
        https://github.com/actualbudget/actual/blob/5bcfc71be67c6e7b7c8b444e4c4f60da9ea9fdaa/packages/loot-core/src/server/db/index.ts#L81-L98
        """
        with Session(self.engine) as session:
            clock = session.exec(select(MessagesClock)).one_or_none()
            if not clock:
                clock_message = {
                    "timestamp": HULC_Client().timestamp(now=datetime.datetime(1970, 1, 1, 0, 0, 0, 0)),
                    "merkle": {},
                }
                clock = MessagesClock(id=1, clock=json.dumps(clock_message, separators=(",", ":")))
                session.add(clock)
            session.commit()
            # add clock id to client id
            self._client = HULC_Client.from_timestamp(json.loads(clock.clock)["timestamp"])
        return clock

    def commit(self):
        """Adds all pending entries to the local database, and sends a sync request to the remote server to synchronize
        the local changes. It's important to note that this process is not atomic, so if the process is interrupted
        before it completes successfully, the files would end up in a unknown state, leading you to have to redo
        the budget download."""
        if not self._session:
            raise ActualError("No session has been created for the file.")
        # create sync request based on the session reference that is tracked
        req = SyncRequest({"fileId": self._file.file_id, "groupId": self._file.group_id})
        if self._file.encrypt_key_id:
            req.keyId = self._file.encrypt_key_id
        req.set_null_timestamp(client_id=self._client.client_id)
        # flush to database, so that all data is evaluated on the database for consistency
        self._session.flush()
        # first we add all new entries and modify is required
        if "messages" in self._session.info:
            req.set_messages(self._session.info["messages"], self._client, master_key=self._master_key)
        # commit to local database to clear the current flush cache
        self._session.commit()
        # sync all changes to the server
        if self._file.group_id:  # only files with a group id can be synced
            self.sync_sync(req)

    def run_rules(self):
        ruleset = get_ruleset(self.session)
        transactions = get_transactions(self.session)
        ruleset.run(transactions)

    def _run_bank_sync_account(self, acct: Accounts, start_date: datetime.date) -> list[Transactions]:
        sync_method = acct.account_sync_source
        account_id = acct.account_id
        requisition_id = acct.bank.bank_id if sync_method == "goCardless" else None
        new_transactions_data = self.bank_sync_transactions(
            sync_method.lower(), account_id, start_date, requisition_id=requisition_id
        )
        new_transactions = new_transactions_data.data.transactions.all
        imported_transactions = []
        for transaction in new_transactions:
            payee = transaction.imported_payee or "" if sync_method == "goCardless" else transaction.notes
            reconciled = reconcile_transaction(
                self.session,
                transaction.date,
                acct,
                payee,
                transaction.notes,
                amount=transaction.transaction_amount.amount,
                imported_id=transaction.transaction_id,
                cleared=transaction.booked,
                imported_payee=payee,
                already_matched=imported_transactions,
            )
            if reconciled.changed():
                imported_transactions.append(reconciled)
        return imported_transactions

    def run_bank_sync(
        self, account: str | Accounts | None = None, start_date: datetime.date | None = None
    ) -> list[Transactions]:
        """
        Runs the bank synchronization for the selected account. If missing, all accounts are synchronized. If a
        start_date is provided, is used as a reference, otherwise, the last timestamp of each account will be used. If
        the account does not have any transaction, the last 90 days are considered instead.
        """
        # if no account is provided, sync all of them, otherwise just the account provided
        if account is None:
            accounts = get_accounts(self.session)
        else:
            account = get_account(self.session, account)
            accounts = [account]
        imported_transactions = []

        default_start_date = start_date
        for acct in accounts:
            sync_method = acct.account_sync_source
            account_id = acct.account_id
            if not (account_id and sync_method):
                continue
            status = self.bank_sync_status(sync_method.lower())
            if not status.data.configured:
                continue
            if start_date is None:
                all_transactions = get_transactions(self.session, account=acct)
                if all_transactions:
                    default_start_date = all_transactions[0].get_date()
                else:
                    default_start_date = datetime.date.today() - datetime.timedelta(days=90)
            transactions = self._run_bank_sync_account(acct, default_start_date)
            imported_transactions.extend(transactions)
        return imported_transactions
