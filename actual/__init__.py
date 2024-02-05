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

from actual.database import Accounts, Categories, Transactions
from actual.models import RemoteFile


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
        self._file_id = None
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

    def headers(self, file_id: str = None):
        if not self._token:
            self.login()
        headers = {"X-ACTUAL-TOKEN": self._token}
        if self._file_id:
            headers["X-ACTUAL-FILE-ID"] = file_id or self._file_id
        return headers

    def user_files(self) -> List[RemoteFile]:
        response = requests.get(f"{self.api_url}/{Endpoints.LIST_USER_FILES}", headers=self.headers())
        response.raise_for_status()
        files = response.json()
        return pydantic.parse_obj_as(List[RemoteFile], files["data"])

    def set_file(self, file_id: Union[str, RemoteFile]) -> RemoteFile:
        """Sets the file id for the class for further requests."""
        if isinstance(file_id, RemoteFile):
            self._file_id = file_id.file_id
            return file_id
        else:
            user_files = self.user_files()
            for file in user_files:
                if file.file_id == file_id or file.name == file_id:
                    self._file_id = file.file_id
                    return file
            raise UnknownFileId(f"Could not find a file id ofr identifier '{file_id}'")

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

    def get_transactions(self) -> List[Transactions]:
        with self._session_maker() as s:
            query = (
                s.query(Transactions)
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

    def get_accounts(self):
        with self._session_maker() as s:
            query = s.query(Accounts)
            return query.all()
