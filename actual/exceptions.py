class ActualError(Exception):
    pass


class AuthorizationError(ActualError):
    pass


class UnknownFileId(ActualError):
    pass


class InvalidZipFile(ActualError):
    pass
