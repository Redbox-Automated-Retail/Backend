"""Logins for the dashboard.

The bootstrap account comes from the environment (the git-ignored .env), set up with `scripts/rb.ps1
set-admin-password` / `scripts/set-admin-password.sh`, or with the hash this module prints
(`python -m redbox_backend.dashboard.auth`, password on stdin):
  ADMIN_USERNAME        default "admin"
  ADMIN_PASSWORD_HASH   pbkdf2_sha256$<iterations>$<salt b64>$<hash b64>
It is always an admin. Further accounts (admin / operator / viewer) live in dash_users and are managed on the
dashboard's Users & access page. Sessions are HMAC-signed cookies (key in CERT_DIR/dashboard.key), HttpOnly,
SameSite=Strict, valid for ADMIN_SESSION_HOURS (default 8); a password change or a disabled account ends them.
"""
import base64
import contextvars
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
ROLES = ("admin", "operator", "viewer")
MIN_PASSWORD = 10
# The signed-in user of the request being handled (set by the dashboard's middleware): {"user", "role"}.
current = contextvars.ContextVar("dashboard_user", default=None)


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
    """The bootstrap (environment) account's name."""
    return os.environ.get("ADMIN_USERNAME") or "admin"


def configured():
    return bool(os.environ.get("ADMIN_PASSWORD_HASH"))


def check_login(user, password):
    """The bootstrap account only; database accounts are checked by login()."""
    stored = os.environ.get("ADMIN_PASSWORD_HASH")
    return bool(stored) and hmac.compare_digest(user or "", username()) and verify_password(password or "", stored)


async def login(user, password):
    """The role of a valid user name / password, else None."""
    from .. import db
    if check_login(user, password):
        return "admin"
    if not user or user == username():
        return None
    row = await db.fetchrow("SELECT password_hash, role, enabled FROM dash_users WHERE username=%s", user)
    if row is None or not row["enabled"] or not verify_password(password or "", row["password_hash"]):
        return None
    await db.execute("UPDATE dash_users SET last_login_at=now() WHERE username=%s", user)
    return row["role"]


async def resolve(session):
    """{"user", "role"} for a valid session cookie's data, else None (account gone, disabled or password changed)."""
    from .. import db
    user = session.get("u")
    if user == username():
        return {"user": user, "role": "admin"} if configured() else None
    row = await db.fetchrow("SELECT role, enabled, password_changed_at FROM dash_users WHERE username=%s", user)
    if row is None or not row["enabled"] or row["password_changed_at"].timestamp() > session.get("iat", 0) + 1:
        return None
    return {"user": user, "role": row["role"]}


def _key():
    p = pathlib.Path(config.CERT_DIR) / "dashboard.key"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_hex(32))
    return p.read_text().strip().encode()


def make_session(user):
    hours = float(os.environ.get("ADMIN_SESSION_HOURS") or 8)
    now = time.time()
    body = base64.urlsafe_b64encode(json.dumps({"u": user, "iat": int(now), "exp": int(now + hours * 3600),
                                                "n": secrets.token_hex(8)}).encode()).decode()
    sig = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_session(token):
    """The signed cookie's data if the signature holds and it has not expired (the account is checked by resolve())."""
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
    except Exception:
        return None
    if data.get("exp", 0) < time.time() or not data.get("u"):
        return None
    return data


if __name__ == "__main__":  # python -m redbox_backend.dashboard.auth  (reads the password from stdin)
    print(hash_password(sys.stdin.readline().rstrip("\r\n")))
