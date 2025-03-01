from unittest.mock import patch

import pytest
from requests import Session

from actual import Actual, reflect_model
from actual.api import ListUserFilesDTO
from actual.api.models import RemoteFileListDTO, StatusCode
from actual.exceptions import ActualError, AuthorizationError, UnknownFileId
from actual.protobuf_models import Message
from tests.conftest import RequestsMock


def test_api_apply(mocker, session):
    mocker.patch("actual.Actual.validate")
    actual = Actual(token="foo")
    actual.engine = session.bind
    actual._meta = reflect_model(session.bind)
    # not found table
    m = Message(dict(dataset="foo", row="foobar", column="bar"))
    m.set_value("foobar")
    with pytest.raises(ActualError, match="table 'foo' not found"):
        actual.apply_changes([m])
    m.dataset = "accounts"
    with pytest.raises(ActualError, match="column 'bar' at table 'accounts' not found"):
        actual.apply_changes([m])


def test_rename_delete_budget_without_file(mocker):
    mocker.patch("actual.Actual.validate")
    actual = Actual(token="foo")
    actual._file = None
    with pytest.raises(UnknownFileId, match="No current file loaded"):
        actual.delete_budget()
    with pytest.raises(UnknownFileId, match="No current file loaded"):
        actual.rename_budget("foo")


@patch.object(Session, "post", return_value=RequestsMock({"status": "error", "reason": "proxy-not-trusted"}))
def test_api_login_unknown_error(_post, mocker):
    mocker.patch("actual.Actual.validate")
    actual = Actual(token="foo")
    actual.api_url = "localhost"
    actual.cert = False
    with pytest.raises(AuthorizationError, match="Something went wrong on login"):
        actual.login("foo")


def test_no_certificate(mocker):
    mocker.patch("actual.Actual.validate")
    actual = Actual(token="foo", cert=False)
    assert actual._requests_session.verify is False


def test_set_file_exceptions(mocker):
    mocker.patch("actual.Actual.validate")
    list_user_files = mocker.patch(
        "actual.Actual.list_user_files", return_value=ListUserFilesDTO(status=StatusCode.OK, data=[])
    )
    actual = Actual(token="foo")
    with pytest.raises(ActualError, match="Could not find a file id or identifier 'foo'"):
        actual.set_file("foo")
    list_user_files.return_value = ListUserFilesDTO(
        status=StatusCode.OK,
        data=[
            RemoteFileListDTO(deleted=False, fileId="foo", groupId="foo", name="foo", encryptKeyId=None),
            RemoteFileListDTO(deleted=False, fileId="foo", groupId="foo", name="foo", encryptKeyId=None),
        ],
    )
    with pytest.raises(ActualError, match="Multiple files found with identifier 'foo'"):
        actual.set_file("foo")
