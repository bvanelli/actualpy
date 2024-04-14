import base64

from actual.crypto import create_key_buffer


def test_create_key_buffer():
    # Tested based on:
    # const crypto = require('crypto');
    # console.log(crypto.pbkdf2Sync('foo', 'bar', 10000, 32, 'sha512').toString("base64"))
    buffer = create_key_buffer("foo", base64.b64encode(b"bar").decode())
    assert base64.b64encode(buffer).decode() == "+Do1kTWpkRT0w4kl2suJLdbY1BLtyEpRCiImRtslNgQ="
