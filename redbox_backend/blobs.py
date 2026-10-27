"""Content-addressed file store under DATA_DIR/blobs, keyed by upper-case hex SHA-1."""
import base64
import hashlib
import os
import pathlib
import shutil
import tempfile

from . import config


def root():
    p = pathlib.Path(config.DATA_DIR) / "blobs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest().upper()


def sha1_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha1(data).digest()).decode()


def hex_to_b64(hex_hash: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_hash)).decode()


def path(key: str) -> pathlib.Path:
    key = key.upper()
    return root() / key[:2] / key


def exists(key: str) -> bool:
    return bool(key) and path(key).is_file()


def put_bytes(data: bytes) -> str:
    key = sha1_hex(data)
    target = path(key)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    return key


def put_file(src) -> str:
    h = hashlib.sha1()
    with open(src, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    key = h.hexdigest().upper()
    target = path(key)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, target)
    return key


def get_bytes(key: str) -> bytes:
    return path(key).read_bytes()


def size(key: str) -> int:
    return path(key).stat().st_size
