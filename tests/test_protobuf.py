import datetime

import pytest
from freezegun import freeze_time

from actual.exceptions import ActualOverflowError
from actual.protobuf_models import (
    HULC_Client,
    Message,
    MessageEnvelope,
    SyncRequest,
    SyncResponse,
)


@freeze_time("2020-10-11 12:13:14.015")
def test_timestamp():
    client = HULC_Client("foo")
    ts = client.timestamp()
    assert ts == "2020-10-11T12:13:14.015Z-0000-foo"
    assert "foo" == HULC_Client.from_timestamp(ts).client_id
    # Next ts should have advanced the counter
    next_ts = client.timestamp()
    assert next_ts == "2020-10-11T12:13:14.015Z-0001-foo"


@freeze_time("2020-10-11 12:13:14.015")
def test_timestamp_client_string():
    client = HULC_Client("foo")
    assert str(client) == "1970-01-01T00:00:00.000Z-0000-foo"
    assert client.timestamp(datetime.datetime.fromtimestamp(0)) == "1970-01-01T00:00:00.000Z-0000-foo"


def test_timestamp_counter_reset_on_clock_advance():
    now = datetime.datetime(2020, 10, 11, 12, 13, 14, 15_000)
    client = HULC_Client("foo", initial_count=5, ts=now)
    # same timestamp: counter advances
    ts = client.timestamp(now)
    assert ts == "2020-10-11T12:13:14.015Z-0006-foo"
    # clock advances by 1ms: counter resets to 0
    later = datetime.datetime(2020, 10, 11, 12, 13, 14, 16_000)
    ts = client.timestamp(later)
    assert ts == "2020-10-11T12:13:14.016Z-0000-foo"


def test_timestamp_counter_overflow():
    now = datetime.datetime(2020, 10, 11, 12, 13, 14, 15_000)
    client = HULC_Client("foo", initial_count=0xFFFF, ts=now)
    with pytest.raises(ActualOverflowError, match="Timestamp counter overflow"):
        client.timestamp(now)  # tries 0xFFFF + 1, overflows


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
