import threading
import time

import pytest
from requests import Session, get
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

from actual import Actual
from actual.exceptions import ActualInvalidOperationError, AuthorizationError
from tests.conftest import RequestsMock


@pytest.fixture()
def actual_server(request):
    # we test integration with the 5 latest versions of actual server
    with DockerContainer("actualbudget/actual-server:25.7.1").with_exposed_ports(5006) as container:
        wait_for_logs(container, "Listening on :::5006...")
        yield container


def test_openid_endpoints(actual_server, mocker):
    port = actual_server.get_exposed_port(5006)
    with Actual(f"http://localhost:{port}", password="mypass", bootstrap=True) as actual:
        actual.create_budget("My Budget")
        actual.upload_budget()
        assert actual.is_open_id_owner_created() is False
        methods = actual.login_methods()
        assert len(methods.methods) == 1
        assert methods.methods[0].method == "password"
        assert actual.open_id_users() == []
        # somehow those are not really validating anything, so we can test those endpoints
        user = actual.create_open_id_user("foo")
        actual.update_open_id_user(user.id, display_name="foobar", owner=False, enabled=True)
        with pytest.raises(ActualInvalidOperationError):
            actual.update_open_id_user("not_existing", display_name="foobar")
        users = actual.open_id_users()
        assert len(users) == 1
        assert user.id == users[0].id
        assert users[0].display_name == "foobar"
        # get permissions of file per user
        permissions = actual.list_file_users_allowed(actual._file.file_id)
        assert len(permissions) == 1
        assert all(user.owner is False for user in permissions)
        # Delete user does not work due to some internal exception (when not set), so we mock the response for now
        mocker.patch.object(Session, "delete").return_value = RequestsMock(
            {"status": "ok", "data": {"someDeletionsFailed": False}}
        )
        actual.delete_open_id_user(user.id)


def test_login_handshake(mocker):

    def _threading_call(url: str):
        # This thread will do the interaction of the user logging in via browser
        # We just wait a second then call the endpoint passing the token from the open id callback to the API
        time.sleep(1)
        get(url, params={"token": "mytoken"})

    def _login_fn(_url: str, json: dict):
        assert "returnUrl" in json
        url = json.get("returnUrl")
        threading.Thread(target=_threading_call, args=(url,)).start()
        return RequestsMock({"status": "ok", "data": {}})

    mocker.patch.object(Actual, "validate")
    mocker.patch.object(Actual, "is_open_id_owner_created", return_value=True)
    mocker.patch.object(Actual, "needs_bootstrap", return_value=True)
    mocker.patch.object(Session, "post").side_effect = _login_fn

    # If the handshake is successful, the token would be set
    with Actual("http://localhost:123") as actual:
        assert actual._token == "mytoken"


def test_login_exceptions(mocker):
    mocker.patch.object(Actual, "validate")
    mocker.patch.object(Actual, "is_open_id_owner_created", return_value=False)

    with pytest.raises(ValueError, match="provide a valid token or a password"):
        with Actual("http://localhost:123"):
            pass
    with pytest.raises(AuthorizationError, match="OpenID server is not set-up"):
        actual = Actual("http://localhost:123", token="foo")
        actual.login(None, "openid")
