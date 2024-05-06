import datetime

import pytest

from actual.protobuf_models import (
    HULC_Client,
    Message,
    MessageEnvelope,
    SyncRequest,
    SyncResponse,
)


def test_timestamp():
    now = datetime.datetime(2020, 10, 11, 12, 13, 14, 15 * 1000)
    ts = HULC_Client("foo").timestamp(now)
    assert ts == "2020-10-11T12:13:14.015Z-0000-foo"
    assert "foo" == HULC_Client.from_timestamp(ts).client_id


def test_message_envelope():
    me = MessageEnvelope()
    me.set_timestamp()
    assert isinstance(MessageEnvelope.serialize(me), bytes)


def test_sync_request():
    m = Message({"dataset": "foo", "row": "bar", "column": "foobar"})
    m.set_value("example")
    req = SyncRequest()
    req.set_null_timestamp()
    req.set_messages([m], HULC_Client())
    # create a sync response from the messages array
    sr = SyncResponse({"merkle": "", "messages": req.messages})
    messages_decoded = sr.get_messages()
    assert messages_decoded == [m]


def test_message_set_value():
    m = Message()
    for data in ["foo", 1, 1.5, None]:
        m.set_value(data)
        assert m.get_value() == data
    with pytest.raises(ValueError):
        m.set_value(object())  # noqa
    with pytest.raises(ValueError):
        m.value = "T:foo"
        m.get_value()
