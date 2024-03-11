from __future__ import annotations

import contextlib
import io
import pathlib
import tempfile
import zipfile
from typing import TYPE_CHECKING, List, Union

import sqlalchemy
import sqlalchemy.orm
from sqlalchemy.orm import joinedload

from actual.api import ActualServer, RemoteFileListDTO
from actual.database import (
    Accounts,
    Categories,
    Payees,
    Transactions,
    get_class_by_table_name,
)
from actual.exceptions import InvalidZipFile, UnknownFileId
from actual.models import RemoteFile
from actual.protobuf_models import Message, SyncRequest

if TYPE_CHECKING:
    from actual.database import BaseModel


class Actual(ActualServer):
    def __init__(
        self,
        base_url: str = "http://localhost:5006",
        token: str = None,
        password: str = None,
        file: str = None,
        data_dir: Union[str, pathlib.Path] = None,
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
        :param data_dir: where to store the downloaded files from the server. If not specified, a temporary folder will
            be created instead.
        """
        super().__init__(base_url, token, password)
        self._file: RemoteFile | None = None
        self._data_dir = pathlib.Path(data_dir)
        self._session_maker = None
        self._session: sqlalchemy.orm.Session | None = None
        # set the correct file
        if file:
            self.set_file(file)

    def __enter__(self) -> Actual:
        self.download_budget()
        self._session = self._session_maker()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._session.close()

    @contextlib.contextmanager
    def with_session(self) -> sqlalchemy.orm.Session:
        s = self._session if self._session else self._session_maker()
        try:
            yield s
        finally:
            if not self._session:
                s.close()

    def set_file(self, file_id: Union[str, RemoteFileListDTO]) -> RemoteFileListDTO:
        """Sets the file id for the class for further requests. The file_id argument can be either a name or remote
        id from the file. If the name is provided, only the first match is taken."""
        if isinstance(file_id, RemoteFileListDTO):
            self._file = file_id
            return file_id
        else:
            user_files = self.list_user_files()
            for file in user_files.data:
                if (file.file_id == file_id or file.name == file_id) and file.deleted == 0:
                    return self.set_file(file)
            raise UnknownFileId(f"Could not find a file id or identifier '{file_id}'")

    def upload_file(self):
        """Uploads the current file to the Actual server."""
        if not self._data_dir:
            raise UnknownFileId("No current file loaded.")
        binary_data = io.BytesIO()
        z = zipfile.ZipFile(binary_data)
        z.write(self._data_dir / "db.sqlite", "db.sqlite")
        z.write(self._data_dir / "metadata.json", "metadata.json")
        binary_data.seek(0)
        return self.upload_user_file(binary_data.read(), self._file.name)

    def apply_changes(self, messages: list[Message]):
        """Applies a list of sync changes, based on what the sync method returned on the remote."""
        if not self._session_maker:
            raise UnknownFileId("No valid file available, download one with download_budget()")
        with self.with_session() as s:
            for message in messages:
                if message.dataset == "prefs":
                    # ignore because it's an internal preference from actual
                    continue
                table = get_class_by_table_name(message.dataset)
                entry = s.query(table).get(message.row)
                if not entry:
                    entry = table(id=message.row)
                setattr(entry, message.column, message.get_value())
                s.add(entry)
            s.commit()

    def download_budget(self):
        """Downloads the budget file from the remote. After the file is downloaded, the sync endpoint is queries
        for the list of pending changes. The changes are individual row updates, that are then applied on by one to
        the downloaded database state."""
        file_bytes = self.download_user_file(self._file.file_id)
        f = io.BytesIO(file_bytes)
        try:
            zip_file = zipfile.ZipFile(f)
        except zipfile.BadZipfile as e:
            raise InvalidZipFile(f"Invalid zip file: {e}")
        if not self._data_dir:
            self._data_dir = pathlib.Path(tempfile.mkdtemp())
        # this should extract 'db.sqlite' and 'metadata.json' to the folder
        zip_file.extractall(self._data_dir)
        engine = sqlalchemy.create_engine(f"sqlite:///{self._data_dir}/db.sqlite")
        self._session_maker = sqlalchemy.orm.sessionmaker(engine)
        # after downloading the budget, some pending transactions still need to be retrieved using sync
        request = SyncRequest({"messages": [], "fileId": self._file.file_id, "groupId": self._file.group_id})
        request.set_null_timestamp()  # using 0 timestamp to retrieve all changes
        changes = self.sync(request)
        self.apply_changes(changes.get_messages())

    def get_transactions(self) -> List[Transactions]:
        with self._session_maker() as s:
            query = (
                s.query(Transactions)
                .options(
                    joinedload(Transactions.account),
                    joinedload(Transactions.category_),
                    joinedload(Transactions.payee),
                )
                .filter(
                    Transactions.date.isnot(None),
                    Transactions.acct.isnot(None),
                    sqlalchemy.or_(Transactions.isChild == 0, Transactions.parent_id.isnot(None)),
                    sqlalchemy.func.coalesce(Transactions.tombstone, 0) == 0,
                )
                .order_by(
                    Transactions.date.desc(),
                    Transactions.starting_balance_flag,
                    Transactions.sort_order.desc(),
                    Transactions.id,
                )
            )
            return query.all()

    def add(self, model: BaseModel):
        """Adds a new entry to the local database, sends a sync request to the remote server to synchronize
        the local changes, then commits the change on the local database. It's important to note that this process
        is not atomic, so if the process is interrupted before it completes successfully, the files would end up in
        a unknown state."""
        with self.with_session() as s:
            # add to database and see if all works well
            s.add(model)
            # generate a sync request and sync it to the server
            req = SyncRequest({"fileId": self._file.file_id, "groupId": self._file.group_id})
            req.set_timestamp()
            req.set_messages(model.convert())
            self.sync(req)
            s.commit()
            if not self._session:
                s.close()

    def get_categories(self) -> List[Categories]:
        with self.with_session() as s:
            query = s.query(Categories)
            return query.all()

    def get_accounts(self) -> List[Accounts]:
        with self.with_session() as s:
            query = s.query(Accounts)
            return query.all()

    def get_payees(self) -> List[Payees]:
        with self.with_session() as s:
            query = s.query(Payees)
            return query.all()
