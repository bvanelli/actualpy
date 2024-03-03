import enum
import io
import pathlib
import tempfile
import zipfile
from typing import List, Union

import pydantic
import requests
import sqlalchemy
import sqlalchemy.orm
from sqlalchemy.orm import joinedload

from actual.database import Accounts, Categories, Transactions, get_class_by_table_name
from actual.models import RemoteFile
from actual.protobuf_models import Message, SyncRequest, SyncResponse


class Endpoints(enum.Enum):
    LOGIN = "account/login"
    INFO = "info"
    ACCOUNT_VALIDATE = "account/validate"
    NEEDS_BOOTSTRAP = "account/needs-bootstrap"
    SYNC = "sync/sync"
    LIST_USER_FILES = "sync/list-user-files"
    GET_USER_FILE_INFO = "sync/get-user-file-info"
    DOWNLOAD_USER_FILE = "sync/download-user-file"
    UPLOAD_USER_FILE = "sync/upload-user-file"
    RESET_USER_FILE = "sync/reset-user-file"
    # data related
    DATA_FILE_INDEX = "data-file-index.txt"
    DEFAULT_DB = "data/default-db.sqlite"
    MIGRATIONS = "data/migrations"

    def __str__(self):
        return self.value


class ActualError(Exception):
    pass


class AuthorizationError(ActualError):
    pass


class UnknownFileId(ActualError):
    pass


class InvalidZipFile(ActualError):
    pass


class Actual:
    def __init__(
        self,
        base_url: str = "http://localhost:5006",
        token: str = None,
        password: str = None,
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
        :param data_dir: where to store the downloaded files from the server. If not specified, a temporary folder will
            be created instead.
        """
        self.api_url = base_url
        self._token = token
        self._password = password
        self._file: RemoteFile | None = None
        self._data_dir = data_dir
        self._session_maker = None
        if token is None and password is None:
            raise ValueError("Either provide a valid token or a password.")

    def login(self) -> str:
        """Logs in on the Actual server using the password provided. Raises `AuthorizationError` if it fails to
        authenticate the user."""
        if not self._password:
            raise AuthorizationError("Trying to login but not password was provided.")
        response = requests.post(f"{self.api_url}/{Endpoints.LOGIN}", json={"password": self._password})
        response.raise_for_status()
        token = response.json()["data"]["token"]
        if token is None:
            raise AuthorizationError("Could not validate password on login.")
        self._password = None  # erase password
        self._token = token
        return self._token

    def headers(self, file_id: str = None, extra_headers: dict = None) -> dict:
        """Generates headers by retrieving a token, if one is not provided, and auto-filling the file id."""
        if not self._token:
            self.login()
        headers = {"X-ACTUAL-TOKEN": self._token}
        if self._file and self._file.file_id:
            headers["X-ACTUAL-FILE-ID"] = file_id or self._file.file_id
        if extra_headers:
            headers = headers | extra_headers
        return headers

    def user_files(self) -> List[RemoteFile]:
        """Lists user files from remote. Requires authentication to return all results."""
        response = requests.get(f"{self.api_url}/{Endpoints.LIST_USER_FILES}", headers=self.headers())
        response.raise_for_status()
        files = response.json()
        return pydantic.parse_obj_as(List[RemoteFile], files["data"])

    def set_file(self, file_id: Union[str, RemoteFile]) -> RemoteFile:
        """Sets the file id for the class for further requests. The file_id argument can be either a name or remote
        id from the file. If the name is provided, only the first match is taken."""
        if isinstance(file_id, RemoteFile):
            self._file = file_id
            return file_id
        else:
            user_files = self.user_files()
            for file in user_files:
                if (file.file_id == file_id or file.name == file_id) and file.deleted == 0:
                    return self.set_file(file)
            raise UnknownFileId(f"Could not find a file id or identifier '{file_id}'")

    def reset_file(self, file_id: Union[str, RemoteFile] = None) -> bool:
        if not self._file:
            if file_id is None:
                raise UnknownFileId("Could not reset the file without a valid 'file_id'")
            self.set_file(file_id)
        request = requests.post(
            f"{self.api_url}/{Endpoints.RESET_USER_FILE}", json={"fileId": self._file.file_id, "token": self._token}
        )
        request.raise_for_status()
        return request.json()["status"] == "ok"

    def apply_changes(self, messages: list[Message]):
        if not self._session_maker:
            raise UnknownFileId("No valid file available, download one with download_budget")
        with self._session_maker() as s:
            s: sqlalchemy.orm.Session
            for message in messages:
                table = get_class_by_table_name(message.dataset)
                entry = s.query(table).get(message.row)
                if not entry:
                    entry = table(id=message.row)
                setattr(entry, message.column, message.get_value())
                s.add(entry)
            s.commit()

    def download_budget(self):
        db = requests.get(f"{self.api_url}/{Endpoints.DOWNLOAD_USER_FILE}", headers=self.headers())
        db.raise_for_status()
        f = io.BytesIO(db.content)
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

    def sync(self, request: SyncRequest) -> SyncResponse:
        response = requests.post(
            f"{self.api_url}/{Endpoints.SYNC}",
            headers=self.headers(extra_headers={"Content-Type": "application/actual-sync"}),
            data=SyncRequest.serialize(request),
        )
        response.raise_for_status()
        parsed_response = SyncResponse.deserialize(response.content)
        return parsed_response  # noqa

    def get_transactions(self) -> List[Transactions]:
        with self._session_maker() as s:
            query = (
                s.query(Transactions)
                .options(joinedload(Transactions.account), joinedload(Transactions.category_))
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

    def get_categories(self) -> List[Categories]:
        with self._session_maker() as s:
            query = s.query(Categories)
            return query.all()

    def get_accounts(self) -> List[Accounts]:
        with self._session_maker() as s:
            query = s.query(Accounts)
            return query.all()
