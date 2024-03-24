from __future__ import annotations

import datetime
import uuid

import proto

"""
Protobuf message definitions taken from:

https://github.com/actualbudget/actual/blob/029e2f09bf6caf386523bbfa944ab845271a3932/packages/crdt/src/proto/sync.proto

They should represent how the server take requests from the client. The server side implementation is available here:

https://github.com/actualbudget/actual-server/blob/master/src/app-sync.js#L32
"""


class HULC_Client:
    def __init__(self, client_id: str = None, initial_count: int = 0):
        self.client_id = client_id
        self.initial_count = initial_count

    @classmethod
    def from_timestamp(cls, ts: str) -> HULC_Client:
        segments = ts.split("-")
        return cls(segments[-1], int(segments[-2]))

    def timestamp(self, now: datetime.datetime = None) -> str:
        """Actual uses Hybrid Unique Logical Clock (HULC) timestamp generator.

        Timestamps serialize into a 46-character collatable string
         *    example: 2015-04-24T22:23:42.123Z-1000-0123456789ABCDEF
         *    example: 2015-04-24T22:23:42.123Z-1000-A219E7A71CC18912

        See https://github.com/actualbudget/actual/blob/a9362cc6f9b974140a760ad05816cac51c849769/packages/crdt/src/crdt/timestamp.ts
        for reference.
        """
        if not now:
            now = datetime.datetime.utcnow()
        count = str(self.initial_count).zfill(4)
        self.initial_count += 1
        return f"{now.isoformat(timespec='milliseconds')}Z-{count}-{self.client_id}"

    def get_client_id(self):
        """Creates a client id for the HULC request. Copied implementation from:

        https://github.com/actualbudget/actual/blob/a9362cc6f9b974140a760ad05816cac51c849769/packages/crdt/src/crdt/timestamp.ts#L80
        """
        return self.client_id if self.client_id is not None else str(uuid.uuid4()).replace("-", "")[-16:]


class EncryptedData(proto.Message):
    iv = proto.Field(proto.BYTES, number=1)
    authTag = proto.Field(proto.BYTES, number=2)
    data = proto.Field(proto.BYTES, number=3)


class Message(proto.Message):
    dataset = proto.Field(proto.STRING, number=1)
    row = proto.Field(proto.STRING, number=2)
    column = proto.Field(proto.STRING, number=3)
    value = proto.Field(proto.STRING, number=4)

    def get_value(self) -> str | int | float | None:
        """Serialization types from Actual. Source:

        https://github.com/actualbudget/actual/blob/998efb9447da6f8ce97956cbe83d6e8a3c18cf53/packages/loot-core/src/server/sync/index.ts#L154-L160
        """
        datatype, _, value = self.value.partition(":")
        if datatype == "S":
            return value
        elif datatype == "N":
            return float(value)
        elif datatype == "0":
            return None
        else:
            raise ValueError(f"Conversion not supported for datatype '{datatype}'")

    def set_value(self, value: str | int | float | None) -> str:
        if isinstance(value, str):
            datatype = "S"
        elif isinstance(value, int) or isinstance(value, float):
            datatype = "N"
        elif value is None:
            datatype = "0"
        else:
            raise ValueError(f"Conversion not supported for datatype '{type(value)}'")
        self.value = f"{datatype}:{value}"
        return self.value


class MessageEnvelope(proto.Message):
    timestamp = proto.Field(proto.STRING, number=1)
    isEncrypted = proto.Field(proto.BOOL, number=2)
    content = proto.Field(proto.BYTES, number=3)

    def set_timestamp(self, client_id: str = None, now: datetime.datetime = None) -> str:
        self.timestamp = HULC_Client(client_id).timestamp(now)
        return self.timestamp


class SyncRequest(proto.Message):
    messages = proto.RepeatedField(MessageEnvelope, number=1)
    fileId = proto.Field(proto.STRING, number=2)
    groupId = proto.Field(proto.STRING, number=3)
    keyId = proto.Field(proto.STRING, number=5)
    since = proto.Field(proto.STRING, number=6)

    def set_timestamp(self, client_id: str = None, now: datetime.datetime = None) -> str:
        self.since = HULC_Client(client_id).timestamp(now)
        return self.since

    def set_null_timestamp(self, client_id: str = None) -> str:
        return self.set_timestamp(client_id, datetime.datetime(1970, 1, 1, 0, 0, 0, 0))

    def set_messages(self, messages: list[Message], client: HULC_Client):
        if not self.messages:
            self.messages = []
        for message in messages:
            m = MessageEnvelope({"content": Message.serialize(message), "isEncrypted": False})
            m.timestamp = client.timestamp()
            self.messages.append(m)


class SyncResponse(proto.Message):
    messages = proto.RepeatedField(MessageEnvelope, number=1)
    merkle = proto.Field(proto.STRING, number=2)

    def get_messages(self) -> list[Message]:
        messages = []
        for message in self.messages:  # noqa
            messages.append(Message.deserialize(message.content))
        return messages
