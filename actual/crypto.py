from __future__ import annotations

import base64
import os
import uuid

import cryptography.exceptions
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from actual.exceptions import ActualDecryptionError


def random_bytes(size: int = 12) -> str:
    return str(os.urandom(size))


def make_salt(length: int = 32) -> str:
    # reference generates 32 bytes of random data
    # github.com/actualbudget/actual/blob/70e37c0119f4ba95ccf6549f0df4aac770f1bb8f/packages/loot-core/src/server/main.ts#L1489
    return base64.b64encode(os.urandom(length)).decode()


def create_key_buffer(password: str, key_salt: str) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA512(), length=32, salt=key_salt.encode(), iterations=10_000)
    return kdf.derive(password.encode())


def encrypt(key_id: str, master_key: bytes, plaintext: bytes) -> dict:
    iv = os.urandom(12)
    encryptor = Cipher(algorithms.AES(master_key), modes.GCM(iv)).encryptor()
    value = encryptor.update(plaintext) + encryptor.finalize()
    auth_tag = encryptor.tag
    return {
        "value": base64.b64encode(value).decode(),
        "meta": {
            "keyId": key_id,
            "algorithm": "aes-256-gcm",
            "iv": base64.b64encode(iv).decode(),
            "authTag": base64.b64encode(auth_tag).decode(),
        },
    }


def decrypt(master_key: bytes, iv: bytes, ciphertext: bytes, auth_tag: bytes = None) -> bytes:
    decryptor = Cipher(algorithms.AES(master_key), modes.GCM(iv, auth_tag)).decryptor()
    try:
        return decryptor.update(ciphertext) + decryptor.finalize()
    except cryptography.exceptions.InvalidTag:
        raise ActualDecryptionError("Error decrypting file. Is the encryption key correct?") from None


def decrypt_from_meta(master_key: bytes, ciphertext: bytes, encrypt_meta) -> bytes:
    iv = base64.b64decode(encrypt_meta.iv)
    auth_tag = base64.b64decode(encrypt_meta.auth_tag)
    return decrypt(master_key, iv, ciphertext, auth_tag)


def make_test_message(key_id: str, key: bytes) -> dict:
    """Reference
    https://github.com/actualbudget/actual/blob/70e37c0119f4ba95ccf6549f0df4aac770f1bb8f/packages/loot-core/src/server/sync/make-test-message.ts#L10
    """
    from actual.protobuf_models import Message

    m = Message(dict(dataset=random_bytes(), row=random_bytes(), column=random_bytes(), value=random_bytes()))
    binary_message = Message.serialize(m)
    # return encrypted binary message
    return encrypt(key_id, key, binary_message)


def is_uuid(text: str, version: int = 4):
    """
    Check if uuid_to_test is a valid UUID. Taken from [this thread](https://stackoverflow.com/a/54254115/12681470)

    Examples:

    >>> is_uuid('c9bf9e57-1685-4c89-bafb-ff5af830be8a')
    True
    >>> is_uuid('c9bf9e58')
    False

    :param text: UUID string to test
    :param version: expected version for the UUID
    :return: `True` if `text` is a valid UUID, otherwise `False`.
    """
    try:
        uuid.UUID(str(text), version=version)
        return True
    except ValueError:
        return False
