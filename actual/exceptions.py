import requests


def get_exception_from_response(response: requests.Response) -> Exception:
    text = response.content.decode()
    if text == "internal-error" or response.status_code == 500:
        return ActualError(text)
    # taken from
    # https://github.com/actualbudget/actual-server/blob/6e9eddeb561b0d9f2bbb6301c3e2c30b4effc522/src/app-sync.js#L107
    elif text == "file-has-new-key":
        return ActualError(f"{text}: The data is encrypted with a different key")
    elif text == "file-has-reset":
        return InvalidFile(
            f"{text}: The changes being synced are part of an old group, which means the file has been reset. "
            f"User needs to re-download."
        )
    elif text in ("file-not-found", "file-needs-upload"):
        raise UnknownFileId(text)
    elif text == "file-old-version":
        raise InvalidFile(f"{text}: SYNC_FORMAT_VERSION was generated with an old format")


class ActualError(Exception):
    """General error with Actual. The error message should provide more information."""

    pass


class ActualInvalidOperationError(ActualError):
    """Invalid operation requested. Happens usually when a request has been done, but it's missing a required
    parameters."""

    pass


class AuthorizationError(ActualError):
    """When the login fails due to invalid credentials, or a request has been done with the wrong credentials
    (i.e. invalid token)"""

    pass


class UnknownFileId(ActualError):
    """When the file id that has been set does not exist on the server."""

    pass


class InvalidZipFile(ActualError):
    """
    The validation fails when loading a zip file, either because it's an invalid zip file or the file is corrupted.
    """

    pass


class InvalidFile(ActualError):

    pass


class ActualDecryptionError(ActualError):
    """
    The decryption for the file failed. This can happen for a multitude or reasons, like the password is wrong, the file
    is corrupted, or when the password is not provided but the file is encrypted.
    """

    pass


class ActualSplitTransactionError(ActualError):
    """The split transaction is invalid, most likely because the sum of splits is not equal the full amount of the
    transaction."""

    pass


class ActualBankSyncError(ActualError):
    """The bank sync had an error, due to the service being unavailable or due to authentication issues with the
    third-party service. This likely indicates a problem with the configuration of the bank sync, not an issue with
    this library."""

    def __init__(self, error_type: str, status: str = None, reason: str = None):
        self.error_type, self.status, self.reason = error_type, status, reason
