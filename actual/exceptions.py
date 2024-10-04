import requests


def get_exception_from_response(response: requests.Response):
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
    pass


class ActualInvalidOperationError(ActualError):
    pass


class AuthorizationError(ActualError):
    pass


class UnknownFileId(ActualError):
    pass


class InvalidZipFile(ActualError):
    pass


class InvalidFile(ActualError):
    pass


class ActualDecryptionError(ActualError):
    pass


class ActualSplitTransactionError(ActualError):
    pass


class ActualBankSyncError(ActualError):
    def __init__(self, error_type: str, status: str = None, reason: str = None):
        self.error_type, self.status, self.reason = error_type, status, reason
