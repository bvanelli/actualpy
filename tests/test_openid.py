import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

from actual import Actual
from actual.exceptions import ActualInvalidOperationError


@pytest.fixture()
def actual_server(request):
    # we test integration with the 5 latest versions of actual server
    with DockerContainer("actualbudget/actual-server:25.7.1").with_exposed_ports(5006) as container:
        wait_for_logs(container, "Listening on :::5006...")
        yield container


def test_openid_endpoints(actual_server):
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
        actual.update_open_id_user(user.id, display_name="foobar")
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
        # delete user does not work due to some internal exception
        # actual.delete_open_id_user(user.id)
