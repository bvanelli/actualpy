import base64

from actual.api import EncryptMetaDTO
from actual.crypto import create_key_buffer, decrypt_from_meta, encrypt, random_bytes
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
    decrypted_from_meta = decrypt_from_meta(
        key, base64.b64decode(encrypted["value"]), EncryptMetaDTO(**encrypted["meta"])
    )
    assert decrypted_from_meta == string_to_encrypt


def test_encrypt_decrypt_message():
    key = create_key_buffer("foo", "bar")
    m = Message(dict(dataset=random_bytes(), row=random_bytes(), column=random_bytes(), value=random_bytes()))
    req = SyncRequest()
    req.set_messages([m], HULC_Client(), master_key=key)
    resp = SyncResponse()
    resp.messages = req.messages
    decrypted_messages = resp.get_messages(master_key=key)
    assert len(decrypted_messages) == 1
    assert decrypted_messages[0] == m
