"""Client-side secrets of the Update Client that the IoT certificate service must reproduce.

* HashService (software/src/UpdateClient/.../IoT/Certificate/Security/HashService.cs):
    password(id) = Base64(SHA512(UTF8(Base64(SHA512(UTF8(id))) + salt)))
  The salt helpers are async methods called without `await`, so the salt is the ToString() of a Task<string>:
  "System.Threading.Tasks.Task`1[System.String]" for both the kiosk password and the PFX password.
* EncryptionService "EncryptType1": Base64(salt[32] || AES-256-CBC(PKCS7)), key/IV = PBKDF2-HMAC-SHA1
  (1000 iterations) of a passphrase hard-coded in EncryptionService.cs. The certificate service needs it to
  read the encrypted kiosk id. It is NOT stored in this backend: set UCS_ENCRYPTION_PASSPHRASE, or mount the
  decompiled EncryptionService.cs and set UCS_ENCRYPTION_SOURCE to its path (the run script does this).
"""
import base64
import hashlib
import os
import re

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

TASK_SALT = "System.Threading.Tasks.Task`1[System.String]"


def _hash_password(kiosk_id):
    inner = base64.b64encode(hashlib.sha512(str(kiosk_id).encode("utf-8")).digest()).decode()
    return base64.b64encode(hashlib.sha512((inner + TASK_SALT).encode("utf-8")).digest()).decode()


def kiosk_password(kiosk_id):
    return _hash_password(kiosk_id)


def certificate_password(kiosk_id):
    return _hash_password(kiosk_id)


_passphrase = None


def _csharp_literal(source):
    m = re.search(r'_key\s*=\s*"((?:[^"\\]|\\.)*)"', source)
    if not m:
        return None
    return re.sub(r'\\(.)', lambda x: {"n": "\n", "r": "\r", "t": "\t", "0": "\0"}.get(x.group(1), x.group(1)),
                  m.group(1))


def passphrase():
    global _passphrase
    if _passphrase is None:
        value = os.environ.get("UCS_ENCRYPTION_PASSPHRASE")
        src = os.environ.get("UCS_ENCRYPTION_SOURCE")
        if not value and src and os.path.isfile(src):
            with open(src, encoding="utf-8-sig") as f:
                value = _csharp_literal(f.read())
        _passphrase = value or ""
    return _passphrase or None


def _derive(salt):
    material = hashlib.pbkdf2_hmac("sha1", passphrase().encode("utf-8"), salt, 1000, 48)
    return material[:32], material[32:]


def decrypt(ciphertext):
    if not passphrase():
        raise RuntimeError("UCS encryption passphrase not configured")
    raw = base64.b64decode(ciphertext)
    salt, body = raw[:32], raw[32:]
    key, iv = _derive(salt)
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    data = dec.update(body) + dec.finalize()
    unpad = padding.PKCS7(128).unpadder()
    text = (unpad.update(data) + unpad.finalize()).decode("utf-8")
    return text.lstrip("﻿")  # StreamWriter does not write a BOM, but be lenient


def encrypt(plaintext):
    if not passphrase():
        raise RuntimeError("UCS encryption passphrase not configured")
    salt = os.urandom(32)
    key, iv = _derive(salt)
    pad = padding.PKCS7(128).padder()
    data = pad.update(plaintext.encode("utf-8")) + pad.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(salt + enc.update(data) + enc.finalize()).decode()
