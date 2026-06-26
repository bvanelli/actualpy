from __future__ import annotations

import base64
import datetime
import uuid

import proto

from actual.crypto import decrypt, encrypt
from actual.exceptions import ActualDecryptionError, ActualOverflowError

"""
Protobuf message definitions taken from the [sync.proto file](
https://github.com/actualbudget/actual/blob/029e2f09bf6caf386523bbfa944ab845271a3932/packages/crdt/src/proto/sync.proto).

They should represent how the server take requests from the client. The server side implementation is available [here](
https://github.com/actualbudget/actual-server/blob/master/src/app-sync.js#L32).
"""


class HULC_Client:
    """
    A Hybrid Unique Logical Clock (HULC) timestamp generator.

    The generator makes sure that change timestamps are consistent across multiple clients that could have different
    clocks.
    """

    MAX_COUNTER: int = 0xFFFF

    def __init__(self, client_id: str | None = None, initial_count: int = 0, ts: datetime.datetime | None = None):
        self.client_id = client_id or self.random_client_id()
        self.initial_count = initial_count
        self.ts = ts

    @classmethod
    def from_timestamp(cls, ts: str) -> HULC_Client:
        """Generates a HULC_Client from a timestamp string."""
        ts_string, _, rest = ts.partition("Z")
        segments = rest.split("-")
        parsed_ts = datetime.datetime.fromisoformat(ts_string)
        return cls(segments[-1], int(segments[-2], 16), parsed_ts)

    def __str__(self):
        ts = self.ts or datetime.datetime(1970, 1, 1, 0, 0, 0)
        return f"{ts.isoformat(timespec='milliseconds')}Z-{self.initial_count:0>4X}-{self.client_id}"

    def timestamp(self, now: datetime.datetime | None = None) -> str:
        """Actual uses Hybrid Unique Logical Clock (HULC) timestamp generator.

        Timestamps serialize into a 46-character collatable string. Examples:

         - `2015-04-24T22:23:42.123Z-1000-0123456789ABCDEF`
         - `2015-04-24T22:23:42.123Z-1000-A219E7A71CC18912`

        See [original source code](
        https://github.com/actualbudget/actual/blob/a9362cc6f9b974140a760ad05816cac51c849769/packages/crdt/src/crdt/timestamp.ts)
        for reference.
        """
        current_time = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) if now is None else now
        # truncate to millisecond precision to match the Node.js Date.now() behavior
        current_time = current_time.replace(microsecond=current_time.microsecond // 1000 * 1000)
        # ensure that the logical time never goes backward
        new_logical_time = current_time if self.ts is None else max(self.ts, current_time)
        # advance the counter if same millisecond, otherwise reset to 0
        new_counter = self.initial_count + 1 if (self.ts is not None and self.ts == new_logical_time) else 0

        if new_counter > self.MAX_COUNTER:
            raise ActualOverflowError(
                f"Timestamp counter overflow (>{self.MAX_COUNTER}). "
                "Too many sync messages were generated without the clock advancing."
            )

        self.ts = new_logical_time
        self.initial_count = new_counter
        return str(self)

    @staticmethod
    def random_client_id() -> str:
        """Creates a client id for the HULC request.

        Implementation copied [from the source code](
        https://github.com/actualbudget/actual/blob/a9362cc6f9b974140a760ad05816cac51c849769/packages/crdt/src/crdt/timestamp.ts#L80).
        """
        return str(uuid.uuid4()).replace("-", "")[-16:]


class EncryptedData(proto.Message):
    """The encrypted data information, namely the iv, authTag and data."""

    iv = proto.Field(proto.BYTES, number=1)
    authTag = proto.Field(proto.BYTES, number=2)
    data = proto.Field(proto.BYTES, number=3)


class Message(proto.Message):
    """A change message from Actual, containing the dataset (table), row (primary key), column and value."""

    dataset = proto.Field(proto.STRING, number=1)
    row = proto.Field(proto.STRING, number=2)
    column = proto.Field(proto.STRING, number=3)
    value = proto.Field(proto.STRING, number=4)

    def get_value(self) -> str | float | None:
        """Serialization types from Actual.

        [Original source code](
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
        """
        Sets the value of the message based on the Actual spec for datatypes.

        [Original source code](
        https://github.com/actualbudget/actual/blob/998efb9447da6f8ce97956cbe83d6e8a3c18cf53/packages/loot-core/src/server/sync/index.ts#L154-L160)
        """
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
    """Envelopes a message while including the timestamp and if the message is encrypted or not."""

    timestamp = proto.Field(proto.STRING, number=1)
    isEncrypted = proto.Field(proto.BOOL, number=2)
    content = proto.Field(proto.BYTES, number=3)

    def set_timestamp(
        self, client_id: str | None = None, now: datetime.datetime | None = None, initial_count: int = 0
    ) -> str:
        self.timestamp = HULC_Client(client_id, initial_count).timestamp(now)
        return self.timestamp

    def message(self, master_key: bytes | None = None) -> Message:
        if self.isEncrypted:
            if not master_key:
                raise ActualDecryptionError("Master key not provided and data is encrypted.")
            encrypted = EncryptedData.deserialize(self.content)
            content = decrypt(master_key, encrypted.iv, encrypted.data, encrypted.authTag)
        else:
            content = self.content
        return Message.deserialize(content)


class SyncRequest(proto.Message):
    """Sync request message that is sent to the server for retrieving new messages since the last synchronization."""

    messages = proto.RepeatedField(MessageEnvelope, number=1)
    fileId = proto.Field(proto.STRING, number=2)
    groupId = proto.Field(proto.STRING, number=3)
    keyId = proto.Field(proto.STRING, number=5)
    since = proto.Field(proto.STRING, number=6)

    def set_timestamp(
        self, client_id: str | None = None, now: datetime.datetime | None = None, initial_count: int = 0
    ) -> str:
        self.since = HULC_Client(client_id, initial_count).timestamp(now)
        return self.since

    def set_null_timestamp(self, client_id: str | None = None) -> str:
        return self.set_timestamp(client_id, datetime.datetime(1970, 1, 1, 0, 0, 0, 0))

    def set_messages(self, messages: list[Message], client: HULC_Client, master_key: bytes | None = None):
        if not self.messages:
            self.messages = []
        for message in messages:
            content = Message.serialize(message)
            is_encrypted = False
            if master_key is not None:
                encrypted_content = encrypt("", master_key, content)
                # encrypt() always populates iv and auth_tag; the Optional is only for server responses
                if encrypted_content.meta.iv is None or encrypted_content.meta.auth_tag is None:
                    raise ActualDecryptionError(
                        f"EncryptionTestDTO does not contain the required encryption data: {encrypted_content}"
                    )
                encrypted_data = EncryptedData(
                    {
                        "iv": base64.b64decode(encrypted_content.meta.iv),
                        "authTag": base64.b64decode(encrypted_content.meta.auth_tag),
                        "data": base64.b64decode(encrypted_content.value),
                    }
                )
                content = EncryptedData.serialize(encrypted_data)
                is_encrypted = True
            m = MessageEnvelope({"content": content, "isEncrypted": is_encrypted})
            m.timestamp = client.timestamp()
            self.messages.append(m)


class SyncResponse(proto.Message):
    """Sync response that is sent to the client with the new messages."""

    messages = proto.RepeatedField(MessageEnvelope, number=1)
    merkle = proto.Field(proto.STRING, number=2)

    def get_messages(self, master_key: bytes | None = None) -> list[Message]:
        messages = []
        for message in self.messages:  # noqa
            messages.append(message.message(master_key))
        return messages
