"""Admin login for the dashboard.

The credentials come from the environment, set up with `scripts/rb.ps1 set-admin-password` (which stores only
a PBKDF2 hash in the git-ignored local/backend.env):
  ADMIN_USERNAME        default "admin"
  ADMIN_PASSWORD_HASH   pbkdf2_sha256$<iterations>$<salt b64>$<hash b64>
Without ADMIN_PASSWORD_HASH nobody can log in. Sessions are HMAC-signed cookies (key in CERT_DIR/dashboard.key),
HttpOnly, SameSite=Strict, valid for ADMIN_SESSION_HOURS (default 8).
"""
import base64
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import sys
import time

from .. import config

COOKIE = "rb_admin"


def hash_password(password, iterations=310000, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password, stored):
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def username():
    return os.environ.get("ADMIN_USERNAME") or "admin"


def configured():
    return bool(os.environ.get("ADMIN_PASSWORD_HASH"))


def check_login(user, password):
    stored = os.environ.get("ADMIN_PASSWORD_HASH")
    return bool(stored) and hmac.compare_digest(user or "", username()) and verify_password(password or "", stored)


def _key():
    p = pathlib.Path(config.CERT_DIR) / "dashboard.key"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_hex(32))
    return p.read_text().strip().encode()


def make_session(user):
    hours = float(os.environ.get("ADMIN_SESSION_HOURS") or 8)
    body = base64.urlsafe_b64encode(json.dumps({"u": user, "exp": int(time.time() + hours * 3600),
                                                "n": secrets.token_hex(8)}).encode()).decode()
    sig = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_session(token):
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
    except Exception:
        return None
    if data.get("exp", 0) < time.time() or data.get("u") != username():
        return None
    return data


if __name__ == "__main__":  # python -m redbox_backend.dashboard.auth  (reads the password from stdin)
    print(hash_password(sys.stdin.readline().rstrip("\r\n")))
