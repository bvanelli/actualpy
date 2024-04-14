import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from actual.protobuf_models import Message


def random_bytes(size: int = 12) -> str:
    return str(os.urandom(size))


def create_key_buffer(password: str, key_salt: str = None) -> bytes:
    if key_salt is None:
        # reference generates 32 bytes of random data
        # github.com/actualbudget/actual/blob/70e37c0119f4ba95ccf6549f0df4aac770f1bb8f/packages/loot-core/src/server/main.ts#L1489
        key_salt = base64.b64encode(os.urandom(32)).decode()
    kdf = PBKDF2HMAC(algorithm=hashes.SHA512(), length=32, salt=key_salt.encode(), iterations=10_000)
    return kdf.derive(password.encode())


def encrypt(key_id: str, master_key: bytes, plaintext: bytes) -> dict:
    cypher = AESGCM(master_key)
    iv = os.urandom(12)
    auth_tag = os.urandom(12)
    value = cypher.encrypt(iv, plaintext, auth_tag)
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
    return decryptor.update(ciphertext) + decryptor.finalize()


def make_test_message(key_id: str, key: bytes) -> dict:
    """Reference
    https://github.com/actualbudget/actual/blob/70e37c0119f4ba95ccf6549f0df4aac770f1bb8f/packages/loot-core/src/server/sync/make-test-message.ts#L10
    """
    m = Message(dict(dataset=random_bytes(), row=random_bytes(), column=random_bytes(), value=random_bytes()))
    binary_message = Message.serialize(m)
    # return encrypted binary message
    return encrypt(key_id, key, binary_message)
