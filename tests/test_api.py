import ssl
import zipfile
from unittest.mock import patch

import pytest
from httpx import Client

from actual import Actual, reflect_model
from actual.api import ListUserFilesDTO
from actual.api.models import RemoteFileListDTO, StatusCode
from actual.exceptions import ActualError, AuthorizationError, UnknownFileId
from actual.protobuf_models import Message
from tests.conftest import RequestsMock


@pytest.fixture
def login_mocks(mocker):
    mocker.patch("actual.Actual.validate")
    mocker.patch("actual.Actual.is_open_id_owner_created", return_value=False)


def test_api_apply(login_mocks, session):
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


def test_rename_delete_budget_without_file(login_mocks):
    actual = Actual(token="foo")
    actual._file = None
    with pytest.raises(UnknownFileId, match="No current file loaded"):
        actual.delete_budget()
    with pytest.raises(UnknownFileId, match="No current file loaded"):
        actual.rename_budget("foo")


@patch.object(Client, "post", return_value=RequestsMock({"status": "error", "reason": "proxy-not-trusted"}))
def test_api_login_unknown_error(_post, login_mocks):
    actual = Actual(token="foo")
    actual.api_url = "localhost"
    actual.cert = False
    with pytest.raises(AuthorizationError, match="Something went wrong on login"):
        actual.login("foo")


@patch.object(Client, "post", return_value=RequestsMock({}, status_code=403))
def test_api_login_http_error(_post, login_mocks):
    actual = Actual(token="foo")
    actual.api_url = "localhost"
    actual.cert = False
    with pytest.raises(AuthorizationError, match="HTTP error '403'"):
        actual.login("foo")


def test_no_certificate(login_mocks, mocker):
    mock_client = mocker.patch("actual.api.httpx.Client")
    Actual(token="foo", cert=False)
    mock_client.assert_called_once()
    assert mock_client.call_args.kwargs["verify"] is False


def test_certificate_string(login_mocks, mocker):
    mock_client = mocker.patch("actual.api.httpx.Client")
    mocker.patch("actual.api.ssl.SSLContext.load_verify_locations")
    Actual(token="foo", cert="my-cert-string")
    mock_client.assert_called_once()
    assert isinstance(mock_client.call_args.kwargs["verify"], ssl.SSLContext)


def test_set_file_exceptions(login_mocks, mocker):
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


def test_zip_exceptions(login_mocks, mocker, tmp_path):
    mocker.patch("actual.Actual.create_engine")
    archive = tmp_path / "file.zip"
    with zipfile.ZipFile(archive, "w"):
        pass
    actual = Actual(token="foo")
    actual.import_zip(archive)
    # archive will use a normal temp folder since the cloudFileId is missing from metadata
    assert actual._data_dir.name.startswith("tmp")


def test_api_extra_headers(login_mocks):
    actual = Actual(token="foo", extra_headers={"foo": "bar"})
    assert actual._requests_session.headers["foo"] == "bar"
    assert actual._requests_session.headers["X-ACTUAL-TOKEN"] == "foo"


@patch.object(
    Client,
    "post",
    return_value=RequestsMock(
        {
            "status": "ok",
            "data": {
                "openId": {
                    "doc": "OpenID authentication settings.",
                    "discoveryURL": "",
                    "issuer": {
                        "doc": "OpenID issuer",
                        "name": "Friendly name for the issuer",
                        "authorization_endpoint": "https://example.com/login/oauth/authorize",
                        "token_endpoint": "https://example.com/login/oauth/access_token",
                        "userinfo_endpoint": "https://api.example.com/user",
                    },
                    "client_id": "my-client-id",
                    "client_secret": "my-client-secret",
                    "server_hostname": "http://localhost:5006",
                    "authMethod": "oauth2",
                }
            },
        }
    ),
)
def test_open_id_config(_post, login_mocks):
    actual = Actual(token="foo")
    config = actual.open_id_config("mypass")
    assert config.status == StatusCode.OK
    assert config.data["openId"].client_id == "my-client-id"
    assert config.data["openId"].auth_method == "oauth2"
