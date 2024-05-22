from __future__ import annotations

import enum
import json
from typing import List, Optional

import requests
from pydantic import BaseModel, Field

from actual.crypto import create_key_buffer, make_test_message
from actual.exceptions import AuthorizationError, UnknownFileId
from actual.protobuf_models import SyncRequest, SyncResponse


class Endpoints(enum.Enum):
    LOGIN = "account/login"
    INFO = "info"
    ACCOUNT_VALIDATE = "account/validate"
    NEEDS_BOOTSTRAP = "account/needs-bootstrap"
    BOOTSTRAP = "account/bootstrap"
    SYNC = "sync/sync"
    LIST_USER_FILES = "sync/list-user-files"
    GET_USER_FILE_INFO = "sync/get-user-file-info"
    UPDATE_USER_FILE_NAME = "sync/update-user-file-name"
    DOWNLOAD_USER_FILE = "sync/download-user-file"
    UPLOAD_USER_FILE = "sync/upload-user-file"
    RESET_USER_FILE = "sync/reset-user-file"
    # encryption related
    USER_GET_KEY = "sync/user-get-key"
    USER_CREATE_KEY = "sync/user-create-key"
    # data related
    DATA_FILE_INDEX = "data-file-index.txt"
    DEFAULT_DB = "data/default-db.sqlite"
    MIGRATIONS = "data/migrations"

    def __str__(self):
        return self.value


class StatusCode(enum.Enum):
    OK = "ok"


class StatusDTO(BaseModel):
    status: StatusCode


class TokenDTO(BaseModel):
    token: Optional[str]


class LoginDTO(StatusDTO):
    data: TokenDTO


class UploadUserFileDTO(StatusDTO):
    group_id: str = Field(..., alias="groupId")


class IsValidatedDTO(BaseModel):
    validated: Optional[bool]


class ValidateDTO(StatusDTO):
    data: IsValidatedDTO


class EncryptMetaDTO(BaseModel):
    key_id: Optional[str] = Field(..., alias="keyId")
    algorithm: Optional[str]
    iv: Optional[str]
    auth_tag: Optional[str] = Field(..., alias="authTag")


class EncryptionTestDTO(BaseModel):
    value: str
    meta: EncryptMetaDTO


class EncryptionDTO(BaseModel):
    id: Optional[str]
    salt: Optional[str]
    test: Optional[str]

    def meta(self) -> EncryptionTestDTO:
        return EncryptionTestDTO.parse_raw(self.test)


class FileDTO(BaseModel):
    deleted: Optional[int]
    file_id: Optional[str] = Field(..., alias="fileId")
    group_id: Optional[str] = Field(..., alias="groupId")
    name: Optional[str]


class RemoteFileListDTO(FileDTO):
    encrypt_key_id: Optional[str] = Field(..., alias="encryptKeyId")


class RemoteFileDTO(FileDTO):
    encrypt_meta: Optional[EncryptMetaDTO] = Field(..., alias="encryptMeta")


class GetUserFileInfoDTO(StatusDTO):
    data: RemoteFileDTO


class ListUserFilesDTO(StatusDTO):
    data: List[RemoteFileListDTO]


class UserGetKeyDTO(StatusDTO):
    data: EncryptionDTO


class BuildDTO(BaseModel):
    name: str
    description: Optional[str]
    version: Optional[str]


class InfoDTO(BaseModel):
    build: BuildDTO


class IsBootstrapedDTO(BaseModel):
    bootstrapped: bool


class BootstrapInfoDTO(StatusDTO):
    data: IsBootstrapedDTO


class ActualServer:
    def __init__(
        self,
        base_url: str = "http://localhost:5006",
        token: str = None,
        password: str = None,
        bootstrap: bool = False,
    ):
        self.api_url = base_url
        self._token = token
        if token is None and password is None:
            raise ValueError("Either provide a valid token or a password.")
        # already try to login if password was provided
        if password and bootstrap and not self.needs_bootstrap().data.bootstrapped:
            self.bootstrap(password)
        elif password:
            self.login(password)
        # finally call validate
        self.validate()

    def login(self, password: str) -> LoginDTO:
        """Logs in on the Actual server using the password provided. Raises `AuthorizationError` if it fails to
        authenticate the user."""
        if not password:
            raise AuthorizationError("Trying to login but not password was provided.")
        response = requests.post(f"{self.api_url}/{Endpoints.LOGIN}", json={"password": password})
        if response.status_code == 400 and "invalid-password" in response.text:
            raise AuthorizationError("Could not validate password on login.")
        response.raise_for_status()
        login_response = LoginDTO.parse_obj(response.json())
        # older versions do not return 400 but rather return empty tokens
        if login_response.data.token is None:
            raise AuthorizationError("Could not validate password on login.")
        self._token = login_response.data.token
        return login_response

    def headers(self, file_id: str = None, extra_headers: dict = None) -> dict:
        """Generates headers by retrieving a token, if one is not provided, and auto-filling the file id."""
        if not self._token:
            raise AuthorizationError("Token not available for requests. Use the login() method or provide a token.")
        headers = {"X-ACTUAL-TOKEN": self._token}
        if file_id:
            headers["X-ACTUAL-FILE-ID"] = file_id
        if extra_headers:
            headers = headers | extra_headers
        return headers

    def info(self) -> InfoDTO:
        """Gets the information from the Actual server, like the name and version."""
        response = requests.get(f"{self.api_url}/{Endpoints.INFO}")
        response.raise_for_status()
        return InfoDTO.parse_obj(response.json())

    def validate(self) -> ValidateDTO:
        """Validates"""
        response = requests.get(f"{self.api_url}/{Endpoints.ACCOUNT_VALIDATE}", headers=self.headers())
        response.raise_for_status()
        return ValidateDTO.parse_obj(response.json())

    def needs_bootstrap(self) -> BootstrapInfoDTO:
        """Checks if the Actual needs bootstrap, in other words, if it needs a master password for the server."""
        response = requests.get(f"{self.api_url}/{Endpoints.NEEDS_BOOTSTRAP}")
        response.raise_for_status()
        return BootstrapInfoDTO.parse_obj(response.json())

    def bootstrap(self, password: str) -> LoginDTO:
        response = requests.post(f"{self.api_url}/{Endpoints.BOOTSTRAP}", json={"password": password})
        response.raise_for_status()
        login_response = LoginDTO.parse_obj(response.json())
        self._token = login_response.data.token
        return login_response

    def data_file_index(self) -> List[str]:
        """Gets all the migration file references for the actual server."""
        response = requests.get(f"{self.api_url}/{Endpoints.DATA_FILE_INDEX}")
        response.raise_for_status()
        return response.content.decode().splitlines()

    def data_file(self, file_path: str) -> bytes:
        """Gets the content of the individual migration file from server."""
        response = requests.get(f"{self.api_url}/data/{file_path}")
        response.raise_for_status()
        return response.content

    def reset_user_file(self, file_id: str) -> StatusDTO:
        """Resets the file. If the file_id is not provided, the current file set is reset. Usually used together with
        the upload_user_file() method."""
        if file_id is None:
            raise UnknownFileId("Could not reset the file without a valid 'file_id'")
        request = requests.post(
            f"{self.api_url}/{Endpoints.RESET_USER_FILE}", json={"fileId": file_id, "token": self._token}
        )
        request.raise_for_status()
        return StatusDTO.parse_obj(request.json())

    def download_user_file(self, file_id: str) -> bytes:
        """Downloads the user file based on the file_id provided. Returns the `bytes` from the response, which is a
        zipped folder of the database `db.sqlite` and the `metadata.json`. If the database is encrypted, the key id
        has to be retrieved additionally using user_get_key()."""
        db = requests.get(f"{self.api_url}/{Endpoints.DOWNLOAD_USER_FILE}", headers=self.headers(file_id))
        db.raise_for_status()
        return db.content

    def upload_user_file(
        self, binary_data: bytes, file_id: str, file_name: str = "My Finances", encryption_meta: dict = None
    ) -> UploadUserFileDTO:
        """Uploads the binary data, which is a zip folder containing the `db.sqlite` and the `metadata.json`. If the
        file is encrypted, the encryption_meta has to be provided with fields `keyId`, `algorithm`, `iv` and `authTag`
        """
        base_headers = {
            "X-ACTUAL-FORMAT": "2",
            "X-ACTUAL-FILE-ID": file_id,
            "X-ACTUAL-NAME": file_name,
            "Content-Type": "application/encrypted-file",
        }
        if encryption_meta:
            base_headers["X-ACTUAL-ENCRYPT-META"] = json.dumps(encryption_meta)
        request = requests.post(
            f"{self.api_url}/{Endpoints.UPLOAD_USER_FILE}",
            data=binary_data,
            headers=self.headers(extra_headers=base_headers),
        )
        request.raise_for_status()
        return UploadUserFileDTO.parse_obj(request.json())

    def list_user_files(self) -> ListUserFilesDTO:
        """Lists the user files. If the response item contains `encrypt_key_id` different from `None`, then the
        file must be decrypted on retrieval."""
        response = requests.get(f"{self.api_url}/{Endpoints.LIST_USER_FILES}", headers=self.headers())
        response.raise_for_status()
        return ListUserFilesDTO.parse_obj(response.json())

    def get_user_file_info(self, file_id: str) -> GetUserFileInfoDTO:
        """Gets the user file information, including the encryption metadata."""
        response = requests.get(f"{self.api_url}/{Endpoints.GET_USER_FILE_INFO}", headers=self.headers(file_id))
        response.raise_for_status()
        return GetUserFileInfoDTO.parse_obj(response.json())

    def update_user_file_name(self, file_id: str, file_name: str) -> StatusDTO:
        """Updates the file name for the budget on the remote server."""
        response = requests.post(
            f"{self.api_url}/{Endpoints.UPDATE_USER_FILE_NAME}",
            json={"fileId": file_id, "name": file_name, "token": self._token},
            headers=self.headers(),
        )
        response.raise_for_status()
        return StatusDTO.parse_obj(response.json())

    def user_get_key(self, file_id: str) -> UserGetKeyDTO:
        """Gets the key information associated with a user file, including the algorithm, key, salt and iv."""
        response = requests.post(
            f"{self.api_url}/{Endpoints.USER_GET_KEY}",
            json={
                "fileId": file_id,
                "token": self._token,
            },
            headers=self.headers(file_id),
        )
        response.raise_for_status()
        return UserGetKeyDTO.parse_obj(response.json())

    def user_create_key(self, file_id: str, key_id: str, password: str, key_salt: str) -> StatusDTO:
        """Creates a new key for the user file. The key has to be used then to encrypt the local file, and this file
        still needs to be uploaded."""
        key = create_key_buffer(password, key_salt)
        test_content = make_test_message(key_id, key)
        response = requests.post(
            f"{self.api_url}/{Endpoints.USER_CREATE_KEY}",
            headers=self.headers(),
            json={
                "fileId": file_id,
                "keyId": key_id,
                "keySalt": key_salt,
                "testContent": json.dumps(test_content),
                "token": self._token,
            },
        )
        return StatusDTO.parse_obj(response.json())

    def sync_sync(self, request: SyncRequest) -> SyncResponse:
        """Calls the sync endpoint with a request and returns the response. Both the request and response are
        protobuf models. The request and response are not standard REST, but rather protobuf binary serialized data.
        The server stores this serialized data to allow the user to replay all changes to the database and construct
        a local copy."""
        response = requests.post(
            f"{self.api_url}/{Endpoints.SYNC}",
            headers=self.headers(request.fileId, extra_headers={"Content-Type": "application/actual-sync"}),
            data=SyncRequest.serialize(request),
        )
        response.raise_for_status()
        parsed_response = SyncResponse.deserialize(response.content)
        return parsed_response  # noqa
