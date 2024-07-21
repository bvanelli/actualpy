from unittest.mock import patch

import pytest

from actual import Actual
from actual.exceptions import ActualError, AuthorizationError, UnknownFileId
from actual.protobuf_models import Message
from tests.conftest import RequestsMock


def test_api_apply(mocker):
    actual = Actual.__new__(Actual)
    actual.engine = mocker.MagicMock()
    # not found table
    m = Message(dict(dataset="foo", row="foobar", column="bar"))
    m.set_value("foobar")
    with pytest.raises(ActualError, match="table 'foo' not found"):
        actual.apply_changes([m])
    m.dataset = "accounts"
    with pytest.raises(ActualError, match="column 'bar' at table 'accounts' not found"):
        actual.apply_changes([m])


def test_rename_delete_budget_without_file():
    actual = Actual.__new__(Actual)
    actual._file = None
    with pytest.raises(UnknownFileId, match="No current file loaded"):
        actual.delete_budget()
    with pytest.raises(UnknownFileId, match="No current file loaded"):
        actual.rename_budget("foo")


@patch("requests.post", return_value=RequestsMock({"status": "error", "reason": "proxy-not-trusted"}))
def test_api_login_unknown_error(_post):
    actual = Actual.__new__(Actual)
    actual.api_url = "localhost"
    with pytest.raises(AuthorizationError, match="Something went wrong on login"):
        actual.login("foo")
