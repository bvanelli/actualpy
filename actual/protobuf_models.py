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


class SyncResponse(proto.Message):
    messages = proto.RepeatedField(MessageEnvelope, number=1)
    merkle = proto.Field(proto.STRING, number=2)
