import base64

import pytest

from actual.api.models import EncryptionDTO, EncryptMetaDTO
from actual.crypto import (
    create_key_buffer,
    decrypt,
    decrypt_from_meta,
    encrypt,
    make_salt,
    make_test_message,
    random_bytes,
)
from actual.exceptions import ActualDecryptionError
from actual.protobuf_models import HULC_Client, Message, SyncRequest, SyncResponse


def test_create_key_buffer():
    # Tested based on:
    # const crypto = require('crypto');
    # console.log(crypto.pbkdf2Sync('foo', 'bar', 10000, 32, 'sha512').toString("base64"))
    buffer = create_key_buffer("foo", base64.b64encode(b"bar").decode())
    assert base64.b64encode(buffer).decode() == "+Do1kTWpkRT0w4kl2suJLdbY1BLtyEpRCiImRtslNgQ="


def test_encrypt_decrypt():
    key = create_key_buffer("foo", "bar")
    string_to_encrypt = b"foobar"
    encrypted = encrypt("foo", key, string_to_encrypt)
    decrypted_from_meta = decrypt_from_meta(key, base64.b64decode(encrypted.value), EncryptMetaDTO(**encrypted.meta))
    assert decrypted_from_meta == string_to_encrypt
    with pytest.raises(ActualDecryptionError):
        decrypt_from_meta(key[::-1], base64.b64decode(encrypted.value), EncryptMetaDTO(**encrypted.meta))


def test_encrypt_decrypt_message():
    key = create_key_buffer("foo", "bar")
    m = Message(dict(dataset=random_bytes(), row=random_bytes(), column=random_bytes(), value=random_bytes()))
    req = SyncRequest()
    req.set_messages([m], HULC_Client(), master_key=key)
    resp = SyncResponse()
    resp.messages = req.messages
    with pytest.raises(ActualDecryptionError):
        resp.get_messages()  # should fail to get messages without a key
    decrypted_messages = resp.get_messages(master_key=key)
    assert len(decrypted_messages) == 1
    assert decrypted_messages[0] == m


def test_create_test_message():
    key = create_key_buffer(make_salt(), make_salt())
    tm = make_test_message("", key)
    dfm = decrypt(key, base64.b64decode(tm.meta.iv), base64.b64decode(tm.value), base64.b64decode(tm.meta.auth_tag))
    m = Message.deserialize(dfm)
    assert isinstance(m, Message)


def test_encryption_dto_meta():
    # test is None, meta() should return None
    dto = EncryptionDTO(id="key-id", salt="salt", test=None)
    assert dto.meta() is None
    # test is set, meta() should parse and return EncryptionTestDTO
    test_json = '{"value": "encrypted", "meta": {"keyId": "k", "algorithm": "a", "iv": "i", "authTag": "t"}}'
    dto = EncryptionDTO(id="key-id", salt="salt", test=test_json)
    result = dto.meta()
    assert result is not None
    assert result.value == "encrypted"
    assert result.meta.key_id == "k"
