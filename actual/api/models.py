from __future__ import annotations

import enum
from typing import List, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter

from actual.api.bank_sync import (
    BankSyncAccountData,
    BankSyncErrorData,
    BankSyncTransactionData,
)


class Endpoints(enum.Enum):
    LOGIN = "account/login"
    INFO = "info"
    ACCOUNT_VALIDATE = "account/validate"
    NEEDS_BOOTSTRAP = "account/needs-bootstrap"
    BOOTSTRAP = "account/bootstrap"
    SYNC = "sync/sync"
    LIST_USER_FILES = "sync/list-user-files"
    GET_USER_FILE_INFO = "sync/get-user-file-info"
    UPDATE_USER_FILE_NAME = "sync/update-user-filename"
    DOWNLOAD_USER_FILE = "sync/download-user-file"
    UPLOAD_USER_FILE = "sync/upload-user-file"
    RESET_USER_FILE = "sync/reset-user-file"
    DELETE_USER_FILE = "sync/delete-user-file"
    # encryption related
    USER_GET_KEY = "sync/user-get-key"
    USER_CREATE_KEY = "sync/user-create-key"
    # data related
    DATA_FILE_INDEX = "data-file-index.txt"
    DEFAULT_DB = "data/default-db.sqlite"
    MIGRATIONS = "data/migrations"
    # bank sync related
    SECRET = "secret"
    BANK_SYNC_STATUS = "{bank_sync}/status"
    BANK_SYNC_ACCOUNTS = "{bank_sync}/accounts"
    BANK_SYNC_TRANSACTIONS = "{bank_sync}/transactions"

    def __str__(self):
        return self.value


class BankSyncs(enum.Enum):
    GOCARDLESS = "gocardless"
    SIMPLEFIN = "simplefin"


class StatusCode(enum.Enum):
    OK = "ok"
    ERROR = "error"


class StatusDTO(BaseModel):
    status: StatusCode


class ErrorStatusDTO(BaseModel):
    status: StatusCode
    reason: Optional[str] = None


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


class IsConfiguredDTO(BaseModel):
    configured: bool


class BankSyncStatusDTO(StatusDTO):
    data: IsConfiguredDTO


class BankSyncAccountResponseDTO(StatusDTO):
    data: BankSyncAccountData


class BankSyncTransactionResponseDTO(StatusDTO):
    data: BankSyncTransactionData


class BankSyncErrorDTO(StatusDTO):
    data: BankSyncErrorData


BankSyncResponseDTO = TypeAdapter(Union[BankSyncErrorDTO, BankSyncTransactionResponseDTO])
