import pytest

from actual import Actual
from actual.exceptions import ActualError
from actual.protobuf_models import Message


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
