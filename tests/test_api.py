import ssl
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import Client
from pytest_mock import MockerFixture
from sqlalchemy import Engine
from sqlmodel import Session, create_engine, text

from actual import Actual
from actual.api.models import ListUserFilesDTO, RemoteFileListDTO, StatusCode
from actual.database import Accounts, reflect_model
from actual.exceptions import ActualError, AuthorizationError, UnknownFileId
from actual.protobuf_models import Message
from tests.conftest import RequestsMock


@pytest.fixture
def login_mocks(mocker: MockerFixture) -> None:
    mocker.patch("actual.Actual.validate")
    mocker.patch("actual.Actual.is_open_id_owner_created", return_value=False)


def test_api_apply(login_mocks: None, session: Session) -> None:
    actual = Actual(token="foo")
    assert isinstance(session.bind, Engine)  # type check for correctness of session type
    actual.engine = session.bind
    actual._database_metadata = reflect_model(session.bind)
    # not found table
    m = Message(dict(dataset="foo", row="foobar", column="bar"))
    m.set_value("foobar")
    with pytest.raises(ActualError, match="table 'foo' not found"):
        actual.apply_changes([m])
    m.dataset = "accounts"
    with pytest.raises(ActualError, match="column 'bar' at table 'accounts' not found"):
        actual.apply_changes([m])


def test_api_apply_table_missing_on_model(login_mocks: None, session: Session) -> None:
    # a table can exist on the remote database but not be mapped on the models, for example on a newer server version
    session.execute(text("CREATE TABLE unmapped (id TEXT PRIMARY KEY, foo TEXT)"))
    session.commit()
    actual = Actual(token="foo")
    assert isinstance(session.bind, Engine)  # type check for correctness of session type
    actual.engine = session.bind
    actual._database_metadata = reflect_model(session.bind)
    unmapped_message = Message(dict(dataset="unmapped", row="foobar", column="foo"))
    unmapped_message.set_value("bar")
    account_message = Message(dict(dataset="accounts", row="account-id", column="name"))
    account_message.set_value("Bank")
    with pytest.warns(UserWarning, match="Table 'unmapped' not found on the model"):
        changes = actual.apply_changes([unmapped_message, account_message])
    # the unmapped table is skipped, but the remaining changes are still returned
    assert len(changes) == 1
    assert changes[0].table is Accounts
    assert changes[0].id == "account-id"
    # the change itself is applied to the local database regardless
    assert session.execute(text("select foo from unmapped where id = 'foobar'")).scalar_one() == "bar"


def test_rename_delete_budget_without_file(login_mocks: None) -> None:
    actual = Actual(token="foo")
    actual._file = None
    with pytest.raises(UnknownFileId, match="No file set"):
        actual.delete_budget()
    with pytest.raises(UnknownFileId, match="No file set"):
        actual.rename_budget("foo")


@patch.object(Client, "post", return_value=RequestsMock({"status": "error", "reason": "proxy-not-trusted"}))
def test_api_login_unknown_error(_post: MagicMock, login_mocks: None) -> None:
    actual = Actual(token="foo")
    with pytest.raises(AuthorizationError, match="Something went wrong on login"):
        actual.login("foo")


@patch.object(Client, "post", return_value=RequestsMock({}, status_code=403))
def test_api_login_http_error(_post: MagicMock, login_mocks: None) -> None:
    actual = Actual(token="foo")
    with pytest.raises(AuthorizationError, match="HTTP error '403'"):
        actual.login("foo")


def test_no_certificate(login_mocks: None, mocker: MockerFixture) -> None:
    mock_client = mocker.patch("actual.api.httpx.Client")
    Actual(token="foo", cert=False)
    mock_client.assert_called_once()
    assert mock_client.call_args.kwargs["verify"] is False


def test_certificate_string(login_mocks: None, mocker: MockerFixture) -> None:
    mock_client = mocker.patch("actual.api.httpx.Client")
    mocker.patch("actual.api.ssl.SSLContext.load_verify_locations")
    Actual(token="foo", cert="my-cert-string")
    mock_client.assert_called_once()
    assert isinstance(mock_client.call_args.kwargs["verify"], ssl.SSLContext)


def test_set_file_exceptions(login_mocks: None, mocker: MockerFixture) -> None:
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


def test_zip_exceptions(login_mocks: None, mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch("actual.Actual.create_engine")
    archive = tmp_path / "file.zip"
    with zipfile.ZipFile(archive, "w"):
        pass
    actual = Actual(token="foo")
    actual.import_zip(archive)
    # archive will use a normal temp folder since the cloudFileId is missing from metadata
    assert actual.data_dir.name.startswith("tmp")


def test_property_exceptions(login_mocks: None) -> None:
    actual = Actual(token="foo")
    with pytest.raises(ActualError, match="No file set"):
        getattr(actual, "file")
    with pytest.raises(ActualError, match="No data directory set"):
        getattr(actual, "data_dir")
    with pytest.raises(ActualError, match="Metadata not loaded"):
        getattr(actual, "_reflected_metadata")
    with pytest.raises(ActualError, match="Client not initialized"):
        getattr(actual, "_sync_client")


def test_login_exceptions(login_mocks: None) -> None:
    actual = Actual(token="foo")
    with pytest.raises(AuthorizationError, match="no password was provided"):
        # passing no password with the "header" method is the condition under test
        actual.login(None, method="header")  # type: ignore[call-overload]


def test_api_extra_headers(login_mocks: None) -> None:
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
def test_open_id_config(_post: MagicMock, login_mocks: None) -> None:
    actual = Actual(token="foo")
    config = actual.open_id_config("mypass")
    assert config.status == StatusCode.OK
    assert config.data["openId"].client_id == "my-client-id"
    assert config.data["openId"].auth_method == "oauth2"


def test_run_migrations_skips_non_migration_files(tmp_path: Path, login_mocks: None, mocker: MockerFixture) -> None:
    data_file = mocker.patch("actual.Actual.data_file", return_value=b"CREATE TABLE foo (id TEXT PRIMARY KEY);")
    actual = Actual(token="foo", data_dir=tmp_path)
    actual.engine = create_engine(f"sqlite:///{tmp_path}/db.sqlite")
    with actual.engine.begin() as conn:
        conn.execute(text("CREATE TABLE __migrations__ (id INT PRIMARY KEY NOT NULL);"))
    actual.run_migrations(["default-db.sqlite", "migrations/1722804019000_create_foo.sql"])
    data_file.assert_called_once_with("migrations/1722804019000_create_foo.sql")
    with actual.engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM __migrations__")).scalars().all() == [1722804019000]
