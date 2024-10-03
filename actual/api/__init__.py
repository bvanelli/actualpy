from __future__ import annotations

import datetime
import json
from typing import List, Literal

import requests

from actual.api.models import (
    BankSyncAccountResponseDTO,
    BankSyncResponseDTO,
    BankSyncStatusDTO,
    BootstrapInfoDTO,
    Endpoints,
    GetUserFileInfoDTO,
    InfoDTO,
    ListUserFilesDTO,
    LoginDTO,
    StatusDTO,
    UploadUserFileDTO,
    UserGetKeyDTO,
    ValidateDTO,
)
from actual.crypto import create_key_buffer, make_test_message
from actual.exceptions import (
    ActualInvalidOperationError,
    AuthorizationError,
    UnknownFileId,
)
from actual.protobuf_models import SyncRequest, SyncResponse


class ActualServer:
    def __init__(
        self,
        base_url: str = "http://localhost:5006",
        token: str = None,
        password: str = None,
        bootstrap: bool = False,
        cert: str | bool = None,
    ):
        """
        Implements the low-level API for interacting with the Actual server by just implementing the API calls and
        response models.

        :param base_url: url of the running Actual server
        :param token: the token for authentication, if this is available (optional)
        :param password: the password for authentication. It will be used on the .login() method to retrieve the token.
        be created instead.
        :param bootstrap: if the server is not bootstrapped, bootstrap it with the password.
        :param cert: if a custom certificate should be used (i.e. self-signed certificate), it's path can be provided
                     as a string. Set to `False` for no certificate check.
        """
        self.api_url = base_url
        self._token = token
        self.cert = cert
        if token is None and password is None:
            raise ValueError("Either provide a valid token or a password.")
        # already try to login if password was provided
        if password and bootstrap and not self.needs_bootstrap().data.bootstrapped:
            self.bootstrap(password)
        elif password:
            self.login(password)
        # finally call validate
        self.validate()

    def login(self, password: str, method: Literal["password", "header"] = "password") -> LoginDTO:
        """
        Logs in on the Actual server using the password provided. Raises `AuthorizationError` if it fails to
        authenticate the user.

        :param password: password of the Actual server.
        :param method: the method used to authenticate with the server. Check the [official auth header documentation](
        https://actualbudget.org/docs/advanced/http-header-auth/) for information.
        """
        if not password:
            raise AuthorizationError("Trying to login but not password was provided.")
        if method == "password":
            response = requests.post(f"{self.api_url}/{Endpoints.LOGIN}", json={"password": password}, verify=self.cert)
        else:
            response = requests.post(
                f"{self.api_url}/{Endpoints.LOGIN}",
                json={"loginMethod": method},
                headers={"X-ACTUAL-PASSWORD": password},
                verify=self.cert,
            )
        response_dict = response.json()
        if response.status_code == 400 and "invalid-password" in response.text:
            raise AuthorizationError("Could not validate password on login.")
        elif response.status_code == 200 and "invalid-header" in response.text:
            # try the same login with the header
            return self.login(password, "header")
        elif response_dict["status"] == "error":
            # for example, when not trusting the proxy
            raise AuthorizationError(f"Something went wrong on login: {response_dict['reason']}")
        login_response = LoginDTO.model_validate(response.json())
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
            headers.update(extra_headers)
        return headers

    def info(self) -> InfoDTO:
        """Gets the information from the Actual server, like the name and version."""
        response = requests.get(f"{self.api_url}/{Endpoints.INFO}", verify=self.cert)
        response.raise_for_status()
        return InfoDTO.model_validate(response.json())

    def validate(self) -> ValidateDTO:
        """Validates if the user is valid and logged in, and if the token is also valid and bound to a session."""
        response = requests.get(
            f"{self.api_url}/{Endpoints.ACCOUNT_VALIDATE}", headers=self.headers(), verify=self.cert
        )
        response.raise_for_status()
        return ValidateDTO.model_validate(response.json())

    def needs_bootstrap(self) -> BootstrapInfoDTO:
        """Checks if the Actual needs bootstrap, in other words, if it needs a master password for the server."""
        response = requests.get(f"{self.api_url}/{Endpoints.NEEDS_BOOTSTRAP}", verify=self.cert)
        response.raise_for_status()
        return BootstrapInfoDTO.model_validate(response.json())

    def bootstrap(self, password: str) -> LoginDTO:
        response = requests.post(f"{self.api_url}/{Endpoints.BOOTSTRAP}", json={"password": password}, verify=self.cert)
        response.raise_for_status()
        login_response = LoginDTO.model_validate(response.json())
        self._token = login_response.data.token
        return login_response

    def data_file_index(self) -> List[str]:
        """Gets all the migration file references for the actual server."""
        response = requests.get(f"{self.api_url}/{Endpoints.DATA_FILE_INDEX}", verify=self.cert)
        response.raise_for_status()
        return response.content.decode().splitlines()

    def data_file(self, file_path: str) -> bytes:
        """Gets the content of the individual migration file from server."""
        response = requests.get(f"{self.api_url}/data/{file_path}", verify=self.cert)
        response.raise_for_status()
        return response.content

    def reset_user_file(self, file_id: str) -> StatusDTO:
        """Resets the file. If the file_id is not provided, the current file set is reset. Usually used together with
        the upload_user_file() method."""
        if file_id is None:
            raise UnknownFileId("Could not reset the file without a valid 'file_id'")
        request = requests.post(
            f"{self.api_url}/{Endpoints.RESET_USER_FILE}",
            json={"fileId": file_id, "token": self._token},
            verify=self.cert,
        )
        request.raise_for_status()
        return StatusDTO.model_validate(request.json())

    def download_user_file(self, file_id: str) -> bytes:
        """Downloads the user file based on the file_id provided. Returns the `bytes` from the response, which is a
        zipped folder of the database `db.sqlite` and the `metadata.json`. If the database is encrypted, the key id
        has to be retrieved additionally using user_get_key()."""
        db = requests.get(
            f"{self.api_url}/{Endpoints.DOWNLOAD_USER_FILE}", headers=self.headers(file_id), verify=self.cert
        )
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
            verify=self.cert,
        )
        request.raise_for_status()
        return UploadUserFileDTO.model_validate(request.json())

    def list_user_files(self) -> ListUserFilesDTO:
        """Lists the user files. If the response item contains `encrypt_key_id` different from `None`, then the
        file must be decrypted on retrieval."""
        response = requests.get(f"{self.api_url}/{Endpoints.LIST_USER_FILES}", headers=self.headers(), verify=self.cert)
        response.raise_for_status()
        return ListUserFilesDTO.model_validate(response.json())

    def get_user_file_info(self, file_id: str) -> GetUserFileInfoDTO:
        """Gets the user file information, including the encryption metadata."""
        response = requests.get(
            f"{self.api_url}/{Endpoints.GET_USER_FILE_INFO}", headers=self.headers(file_id), verify=self.cert
        )
        response.raise_for_status()
        return GetUserFileInfoDTO.model_validate(response.json())

    def update_user_file_name(self, file_id: str, file_name: str) -> StatusDTO:
        """Updates the file name for the budget on the remote server."""
        response = requests.post(
            f"{self.api_url}/{Endpoints.UPDATE_USER_FILE_NAME}",
            json={"fileId": file_id, "name": file_name, "token": self._token},
            headers=self.headers(),
            verify=self.cert,
        )
        response.raise_for_status()
        return StatusDTO.model_validate(response.json())

    def delete_user_file(self, file_id: str):
        """Deletes the user file that is loaded from the remote server."""
        response = requests.post(
            f"{self.api_url}/{Endpoints.DELETE_USER_FILE}",
            json={"fileId": file_id, "token": self._token},
            headers=self.headers(),
            verify=self.cert,
        )
        return StatusDTO.model_validate(response.json())

    def user_get_key(self, file_id: str) -> UserGetKeyDTO:
        """Gets the key information associated with a user file, including the algorithm, key, salt and iv."""
        response = requests.post(
            f"{self.api_url}/{Endpoints.USER_GET_KEY}",
            json={
                "fileId": file_id,
                "token": self._token,
            },
            headers=self.headers(file_id),
            verify=self.cert,
        )
        response.raise_for_status()
        return UserGetKeyDTO.model_validate(response.json())

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
            verify=self.cert,
        )
        return StatusDTO.model_validate(response.json())

    def sync_sync(self, request: SyncRequest) -> SyncResponse:
        """Calls the sync endpoint with a request and returns the response. Both the request and response are
        protobuf models. The request and response are not standard REST, but rather protobuf binary serialized data.
        The server stores this serialized data to allow the user to replay all changes to the database and construct
        a local copy."""
        response = requests.post(
            f"{self.api_url}/{Endpoints.SYNC}",
            headers=self.headers(request.fileId, extra_headers={"Content-Type": "application/actual-sync"}),
            data=SyncRequest.serialize(request),
            verify=self.cert,
        )
        response.raise_for_status()
        parsed_response = SyncResponse.deserialize(response.content)
        return parsed_response  # noqa

    def bank_sync_status(self, bank_sync: Literal["gocardless", "simplefin"] | str) -> BankSyncStatusDTO:
        endpoint = Endpoints.BANK_SYNC_STATUS.value.format(bank_sync=bank_sync)
        response = requests.post(f"{self.api_url}/{endpoint}", headers=self.headers(), json={}, verify=self.cert)
        return BankSyncStatusDTO.model_validate(response.json())

    def bank_sync_accounts(self, bank_sync: Literal["gocardless", "simplefin"]) -> BankSyncAccountResponseDTO:
        endpoint = Endpoints.BANK_SYNC_ACCOUNTS.value.format(bank_sync=bank_sync)
        response = requests.post(f"{self.api_url}/{endpoint}", headers=self.headers(), json={}, verify=self.cert)
        return BankSyncAccountResponseDTO.model_validate(response.json())

    def bank_sync_transactions(
        self,
        bank_sync: Literal["gocardless", "simplefin"] | str,
        account_id: str,
        start_date: datetime.date,
        requisition_id: str = None,
    ) -> BankSyncResponseDTO:
        if bank_sync == "gocardless" and requisition_id is None:
            raise ActualInvalidOperationError("Retrieving transactions with goCardless requires `requisition_id`")
        endpoint = Endpoints.BANK_SYNC_TRANSACTIONS.value.format(bank_sync=bank_sync)
        payload = {"accountId": account_id, "startDate": start_date.strftime("%Y-%m-%d")}
        if requisition_id:
            payload["requisitionId"] = requisition_id
        response = requests.post(f"{self.api_url}/{endpoint}", headers=self.headers(), json=payload, verify=self.cert)
        return BankSyncResponseDTO.validate_python(response.json())
