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
from typing import TYPE_CHECKING, Union

import sqlalchemy
import sqlalchemy.orm

from actual.api import ActualServer, RemoteFileListDTO
from actual.crypto import create_key_buffer, decrypt
from actual.database import (
    MessagesClock,
    get_attribute_by_table_name,
    get_class_by_table_name,
)
from actual.exceptions import (
    ActualError,
    ActualInvalidOperationError,
    InvalidZipFile,
    UnknownFileId,
)
from actual.protobuf_models import HULC_Client, Message, SyncRequest

if TYPE_CHECKING:
    pass


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
    ):
        """
        Implements the Python API for the Actual Server in order to be able to read and modify information on Actual
        books using Python.

        Parts of the implementation are available at the following file:
        https://github.com/actualbudget/actual/blob/master/packages/loot-core/src/server/cloud-storage.ts

        :param base_url: url of the running Actual server
        :param token: the token for authentication, if this is available (optional)
        :param password: the password for authentication. It will be used on the .login() method to retrieve the token.
        :param file: the name or id of the file to be set
        :param encryption_password: password used to configure encryption, if existing
        :param data_dir: where to store the downloaded files from the server. If not specified, a temporary folder will
            be created instead.
        :param bootstrap: if the server is not bootstrapped, bootstrap it with the password.
        """
        super().__init__(base_url, token, password, bootstrap)
        self._file: RemoteFileListDTO | None = None
        self._data_dir = pathlib.Path(data_dir) if data_dir else None
        self.session_maker = None
        self._session: sqlalchemy.orm.Session | None = None
        self._client: HULC_Client | None = None
        # set the correct file
        if file:
            self.set_file(file)
        self._encryption_password = encryption_password
        self._master_key = None
        self._in_context = False

    def __enter__(self) -> Actual:
        if self._file:
            self.download_budget(self._encryption_password)
            self._session = self.session_maker()
        self._in_context = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            self._session.close()
        self._in_context = False

    @property
    def session(self) -> sqlalchemy.orm.Session:
        if not self._session:
            raise ActualError("No session defined. Use `with Actual() as actual:` construct to generate one.")
        return self._session

    def set_file(self, file_id: Union[str, RemoteFileListDTO]) -> RemoteFileListDTO:
        """Sets the file id for the class for further requests. The file_id argument can be either a name or remote
        id from the file. If the name is provided, only the first match is taken."""
        if isinstance(file_id, RemoteFileListDTO):
            self._file = file_id
            return file_id
        else:
            selected_files = []
            user_files = self.list_user_files()
            for file in user_files.data:
                if (file.file_id == file_id or file.name == file_id) and file.deleted == 0:
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
                continue  # in case db.sqlite file gets passed
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

    def create_budget(self, budget_name: str, encryption_password: str = None):
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
        (self._data_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "id": f"My-Finances-{random_id}",
                    "budgetName": budget_name,
                    "userId": self._token,
                    "cloudFileId": file_id,
                    "resetClock": True,
                }
            )
        )
        self._file = RemoteFileListDTO(name=budget_name, fileId=file_id, groupId=None, deleted=0, encryptKeyId=None)
        # create engine for downloaded database and run migrations
        self.run_migrations(migration_files[1:])
        # generate a session
        engine = sqlalchemy.create_engine(f"sqlite:///{self._data_dir}/db.sqlite")
        self.session_maker = sqlalchemy.orm.sessionmaker(engine, autoflush=False)
        if self._in_context:
            self._session = self.session_maker()
        # create a clock
        self.load_clock()

    def encrypt(self, encryption_password: str):
        key_id = str(uuid.uuid4())
        self.user_create_key(self._file.file_id, key_id, encryption_password)

    def upload_budget(self):
        """Uploads the current file to the Actual server."""
        if not self._data_dir:
            raise UnknownFileId("No current file loaded.")
        binary_data = io.BytesIO()
        with zipfile.ZipFile(binary_data, "a", zipfile.ZIP_DEFLATED, False) as z:
            z.write(self._data_dir / "db.sqlite", "db.sqlite")
            z.write(self._data_dir / "metadata.json", "metadata.json")
            binary_data.seek(0)
        return self.upload_user_file(binary_data.getvalue(), self._file.file_id, self._file.name)

    def reupload_budget(self):
        self.reset_user_file(self._file.file_id)
        self.upload_budget()

    def apply_changes(self, messages: list[Message]):
        """Applies a list of sync changes, based on what the sync method returned on the remote."""
        if not self.session_maker:
            raise UnknownFileId("No valid file available, download one with download_budget()")
        with self.session_maker() as s:
            for message in messages:
                if message.dataset == "prefs":
                    # write it to metadata.json instead
                    config = json.loads((self._data_dir / "metadata.json").read_text() or "{}")
                    config[message.row] = message.get_value()
                    (self._data_dir / "metadata.json").write_text(json.dumps(config))
                    continue
                table = get_class_by_table_name(message.dataset)
                column = get_attribute_by_table_name(message.dataset, message.column)
                entry = s.query(table).get(message.row)
                if not entry:
                    entry = table(id=message.row)
                setattr(entry, column, message.get_value())
                s.add(entry)
                # this seems to be required for sqlmodel, remove if not needed anymore when querying from cache
                s.flush()
            s.commit()

    def download_budget(self, encryption_password: str = None):
        """Downloads the budget file from the remote. After the file is downloaded, the sync endpoint is queries
        for the list of pending changes. The changes are individual row updates, that are then applied on by one to
        the downloaded database state.

        If the budget is password protected, the password needs to be present to download the budget, otherwise it will
        fail.
        """
        file_bytes = self.download_user_file(self._file.file_id)

        if self._file.encrypt_key_id and encryption_password is None:
            raise ActualError("File is encrypted but no encryption password provided.")
        if encryption_password is not None:
            file_info = self.get_user_file_info(self._file.file_id)
            key_info = self.user_get_key(self._file.file_id)
            self._master_key = create_key_buffer(encryption_password, key_info.data.salt)
            iv = base64.b64decode(file_info.data.encrypt_meta.iv)
            auth_tag = base64.b64decode(file_info.data.encrypt_meta.auth_tag)
            # decrypt file bytes
            file_bytes = decrypt(self._master_key, iv, file_bytes, auth_tag)
        f = io.BytesIO(file_bytes)
        try:
            zip_file = zipfile.ZipFile(f)
        except zipfile.BadZipfile as e:
            raise InvalidZipFile(f"Invalid zip file: {e}") from None
        if not self._data_dir:
            self._data_dir = pathlib.Path(tempfile.mkdtemp())
        # this should extract 'db.sqlite' and 'metadata.json' to the folder
        zip_file.extractall(self._data_dir)
        engine = sqlalchemy.create_engine(f"sqlite:///{self._data_dir}/db.sqlite")
        self.session_maker = sqlalchemy.orm.sessionmaker(engine, autoflush=False)
        # actual js always calls validation
        self.validate()
        # load the client id
        self.load_clock()
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
        changes = self.sync(request)
        self.apply_changes(changes.get_messages(self._master_key))
        if changes.messages:
            self._client = HULC_Client.from_timestamp(changes.messages[-1].timestamp)

    def load_clock(self) -> MessagesClock:
        """See implementation at:
        https://github.com/actualbudget/actual/blob/5bcfc71be67c6e7b7c8b444e4c4f60da9ea9fdaa/packages/loot-core/src/server/db/index.ts#L81-L98
        """
        with self.session_maker() as session:
            clock = session.query(MessagesClock).first()
            if not clock:
                clock_message = {
                    "timestamp": HULC_Client().timestamp(now=datetime.datetime(1970, 1, 1, 0, 0, 0, 0)),
                    "merkle": {},
                }
                clock = MessagesClock(id=0, clock=json.dumps(clock_message))
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
        if len(self._session.deleted):
            raise ActualInvalidOperationError(
                "Actual does not allow deleting entries, set the `tombstone` to 1 instead"
            )
        # create sync request based on the session reference that is tracked
        req = SyncRequest({"fileId": self._file.file_id, "groupId": self._file.group_id})
        req.set_null_timestamp(client_id=self._client.client_id)
        # first we add all new entries and modify is required
        for model in self._session.new:
            req.set_messages(model.convert(is_new=True), self._client)
        # modify if required
        for model in self._session.dirty:
            if self._session.is_modified(model):
                req.set_messages(model.convert(is_new=False), self._client)
        # make sure changes are valid before syncing
        self._session.commit()
        # sync all changes to the server
        self.sync(req)
