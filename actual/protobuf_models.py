from __future__ import annotations

import base64
import datetime
import uuid
from typing import List

import proto

from actual.crypto import decrypt, encrypt
from actual.exceptions import ActualDecryptionError

"""
Protobuf message definitions taken from the [sync.proto file](
https://github.com/actualbudget/actual/blob/029e2f09bf6caf386523bbfa944ab845271a3932/packages/crdt/src/proto/sync.proto).

They should represent how the server take requests from the client. The server side implementation is available [here](
https://github.com/actualbudget/actual-server/blob/master/src/app-sync.js#L32).
"""


class HULC_Client:
    def __init__(self, client_id: str = None, initial_count: int = 0):
        self.client_id = client_id or self.get_client_id()
        self.initial_count = initial_count

    @classmethod
    def from_timestamp(cls, ts: str) -> HULC_Client:
        segments = ts.split("-")
        return cls(segments[-1], int(segments[-2], 16))

    def timestamp(self, now: datetime.datetime = None) -> str:
        """Actual uses Hybrid Unique Logical Clock (HULC) timestamp generator.

        Timestamps serialize into a 46-character collatable string. Examples:

         - `2015-04-24T22:23:42.123Z-1000-0123456789ABCDEF`
         - `2015-04-24T22:23:42.123Z-1000-A219E7A71CC18912`

        See [original source code](
        https://github.com/actualbudget/actual/blob/a9362cc6f9b974140a760ad05816cac51c849769/packages/crdt/src/crdt/timestamp.ts)
        for reference.
        """
        if not now:
            now = datetime.datetime.utcnow()
        count = f"{self.initial_count:0>4X}"
        self.initial_count += 1
        return f"{now.isoformat(timespec='milliseconds')}Z-{count}-{self.client_id}"

    def get_client_id(self):
        """Creates a client id for the HULC request. Implementation copied [from the source code](
        https://github.com/actualbudget/actual/blob/a9362cc6f9b974140a760ad05816cac51c849769/packages/crdt/src/crdt/timestamp.ts#L80)
        """
        return (
            self.client_id if getattr(self, "client_id", None) is not None else str(uuid.uuid4()).replace("-", "")[-16:]
        )


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
        """Serialization types from Actual. [Original source code](
        https://github.com/actualbudget/actual/blob/998efb9447da6f8ce97956cbe83d6e8a3c18cf53/packages/loot-core/src/server/sync/index.ts#L154-L160)
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

    def set_messages(self, messages: List[Message], client: HULC_Client, master_key: bytes = None):
        if not self.messages:
            self.messages = []
        for message in messages:
            content = Message.serialize(message)
            is_encrypted = False
            if master_key is not None:
                encrypted_content = encrypt("", master_key, content)
                encrypted_data = EncryptedData(
                    {
                        "iv": base64.b64decode(encrypted_content["meta"]["iv"]),
                        "authTag": base64.b64decode(encrypted_content["meta"]["authTag"]),
                        "data": base64.b64decode(encrypted_content["value"]),
                    }
                )
                content = EncryptedData.serialize(encrypted_data)
                is_encrypted = True
            m = MessageEnvelope({"content": content, "isEncrypted": is_encrypted})
            m.timestamp = client.timestamp()
            self.messages.append(m)


class SyncResponse(proto.Message):
    messages = proto.RepeatedField(MessageEnvelope, number=1)
    merkle = proto.Field(proto.STRING, number=2)

    def get_messages(self, master_key: bytes = None) -> List[Message]:
        messages = []
        for message in self.messages:  # noqa
            if message.isEncrypted:
                if not master_key:
                    raise ActualDecryptionError("Master key not provided and data is encrypted.")
                encrypted = EncryptedData.deserialize(message.content)
                content = decrypt(master_key, encrypted.iv, encrypted.data, encrypted.authTag)
            else:
                content = message.content
            messages.append(Message.deserialize(content))
        return messages
