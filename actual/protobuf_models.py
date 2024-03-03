import datetime
import uuid

import proto

"""
Protobuf message definitions taken from:

https://github.com/actualbudget/actual/blob/029e2f09bf6caf386523bbfa944ab845271a3932/packages/crdt/src/proto/sync.proto

They should represent how the server take requests from the client. The server side implementation is available here:

https://github.com/actualbudget/actual-server/blob/master/src/app-sync.js#L32
"""


class EncryptedData(proto.Message):
    iv = proto.Field(proto.BYTES, number=1)
    authTag = proto.Field(proto.BYTES, number=2)
    data = proto.Field(proto.BYTES, number=3)


class Message(proto.Message):
    dataset = proto.Field(proto.STRING, number=1)
    row = proto.Field(proto.STRING, number=2)
    column = proto.Field(proto.STRING, number=3)
    value = proto.Field(proto.STRING, number=4)

    def get_value(self) -> str | int:
        datatype, _, value = self.value.partition(":")
        if datatype == "S":
            return value
        elif datatype == "N":
            return int(value)
        else:
            raise ValueError(f"Conversion not supported for datatype '{datatype}'")


class MessageEnvelope(proto.Message):
    timestamp = proto.Field(proto.STRING, number=1)
    isEncrypted = proto.Field(proto.BOOL, number=2)
    content = proto.Field(proto.BYTES, number=3)


class SyncRequest(proto.Message):
    messages = proto.RepeatedField(MessageEnvelope, number=1)
    fileId = proto.Field(proto.STRING, number=2)
    groupId = proto.Field(proto.STRING, number=3)
    keyId = proto.Field(proto.STRING, number=5)
    since = proto.Field(proto.STRING, number=6)

    def set_timestamp(self, client_id: str = None, now: datetime.datetime = None) -> str:
        """Actual uses Hybrid Unique Logical Clock (HULC) timestamp generator.

        Timestamps serialize into a 46-character collatable string
         *    example: 2015-04-24T22:23:42.123Z-1000-0123456789ABCDEF
         *    example: 2015-04-24T22:23:42.123Z-1000-A219E7A71CC18912

        See https://github.com/actualbudget/actual/blob/a9362cc6f9b974140a760ad05816cac51c849769/packages/crdt/src/crdt/timestamp.ts
        for reference.
        """
        if not now:
            now = datetime.datetime.utcnow()
        if not client_id:
            client_id = self.client_id()
        self.since = f"{now.isoformat(timespec='milliseconds')}Z-0000-{client_id}"
        return self.since

    def set_null_timestamp(self) -> str:
        return self.set_timestamp(None, datetime.datetime(1970, 1, 1, 0, 0, 0, 0))

    def client_id(self):
        """Creates a client id for the HULC request. Copied implementation from:

        https://github.com/actualbudget/actual/blob/a9362cc6f9b974140a760ad05816cac51c849769/packages/crdt/src/crdt/timestamp.ts#L80
        """
        return str(uuid.uuid4()).replace("-", "")[-16:]


class SyncResponse(proto.Message):
    messages = proto.RepeatedField(MessageEnvelope, number=1)
    merkle = proto.Field(proto.STRING, number=2)

    def get_messages(self) -> list[Message]:
        messages = []
        for message in self.messages:  # noqa
            messages.append(Message.deserialize(message.content))
        return messages
