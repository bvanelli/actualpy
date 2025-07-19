from __future__ import annotations

import enum
from typing import Dict, List, Literal, Optional, Union

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
    # OpenID related
    OPEN_ID_OWNER_CREATED = "admin/owner-created/"  # returns a bool, no model required
    OPEN_ID_CONFIG = "openid/config"
    OPEN_ID_CALLBACK = "openid-cb"
    OPEN_ID_USERS = "admin/users/"

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
    """Here, if you try to log in with a password, you will get a token, and if you try to log in with an OpenID,
    you will get a return_url."""

    token: Optional[str] = None
    return_url: Optional[str] = Field(None, alias="returnUrl")


class LoginDTO(StatusDTO):
    data: TokenDTO


class UploadUserFileDTO(StatusDTO):
    group_id: str = Field(..., alias="groupId")


class IsValidatedDTO(BaseModel):
    validated: Optional[bool]
    # optional OpenID fields
    user_name: Optional[str] = Field(None, alias="userName")
    permission: Optional[str] = None
    user_id: Optional[str] = Field(None, alias="userId")
    display_name: Optional[str] = Field(None, alias="displayName")
    login_method: Optional[str] = Field(default="password", alias="loginMethod")


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
    # optional OpenId fields
    owner: Optional[str] = None
    users_with_access: Optional[List[str]] = Field(default_factory=list, alias="usersWithAccess")


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


class LoginMethodDTO(BaseModel):
    method: str
    active: bool
    display_name: str = Field(..., alias="displayName")


class IsBootstrapedDTO(BaseModel):
    bootstrapped: bool
    login_method: Optional[str] = Field(default="password", alias="loginMethod")
    multi_user: Optional[bool] = Field(default=False, alias="multiuser")
    available_login_methods: Optional[List[LoginMethodDTO]] = Field(default=None, alias="availableLoginMethods")


class BootstrapInfoDTO(StatusDTO):
    data: IsBootstrapedDTO


class IsConfiguredDTO(BaseModel):
    configured: bool


class BankSyncStatusDTO(StatusDTO):
    data: IsConfiguredDTO


class BankSyncAccountDTO(StatusDTO):
    data: BankSyncAccountData


class BankSyncTransactionResponseDTO(StatusDTO):
    data: BankSyncTransactionData


class BankSyncErrorDTO(StatusDTO):
    data: BankSyncErrorData


class IssuerConfig(BaseModel):
    name: str = Field(..., description="Friendly name for the issuer")
    authorization_endpoint: str = Field(..., description="Authorization endpoint URL")
    token_endpoint: str = Field(..., description="Token endpoint URL")
    userinfo_endpoint: str = Field(..., description="User info endpoint URL")


class OpenIDConfigDTO(BaseModel):
    doc: str = Field(default="OpenID authentication settings.", description="Documentation string")
    discovery_url: Optional[str]
    issuer: Optional[IssuerConfig]
    client_id: str
    client_secret: str
    server_hostname: str
    auth_method: Literal["openid", "oauth2"]


class OpenIDConfigResponseDTO(StatusDTO):
    data: Dict[str, OpenIDConfigDTO]


class OpenIDBootstrapDTO(BaseModel):
    client_id: str = Field(..., description="OAuth2 client ID")
    client_secret: str = Field(..., description="OAuth2 client secret")
    discovery_url: Optional[IssuerConfig] = Field(
        default=None, alias="discoveryURL", description="OpenID discovery URL"
    )
    server_hostname: str


BankSyncAccountResponseDTO = TypeAdapter(Union[BankSyncErrorDTO, BankSyncAccountDTO])
BankSyncResponseDTO = TypeAdapter(Union[BankSyncErrorDTO, BankSyncTransactionResponseDTO])
