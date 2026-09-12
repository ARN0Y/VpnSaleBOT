"""Who may sign in to the panel, and how that is stored.

The password is kept as a salted scrypt hash, never in readable form. The old
build compared a plaintext password out of the environment file, which meant the
operator's password sat in a file on disk and in every backup of it.

Nothing here needs a configuration file. On a fresh install there is no admin
yet, so the panel prints a one-time setup code to its own service log and serves
a page that accepts it once — the operator chooses their username and password
there, and from then on the credentials live in the database like everything
else.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets

LOG = logging.getLogger(__name__)

# scrypt at these parameters costs ~100 ms and ~16 MB per attempt, which is
# irrelevant for a login and expensive for anyone working through a stolen hash.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32
_PREFIX = "scrypt"

MIN_PASSWORD_LENGTH = 10
SETTING_USERNAME = "panel_admin_username"
SETTING_PASSWORD = "panel_admin_password_hash"
SETTING_SECRET = "panel_session_secret"
SETTING_TTL = "panel_session_ttl_seconds"
SETTING_COOKIE_SECURE = "panel_cookie_secure"


def hash_password(password: str) -> str:
    """`scrypt$<salt>$<key>`, both halves base64 without padding."""
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                         n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_KEY_LEN)
    encode = lambda raw: base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")  # noqa: E731
    return f"{_PREFIX}${encode(salt)}${encode(key)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. A malformed or empty hash never verifies."""
    parts = str(stored or "").split("$")
    if len(parts) != 3 or parts[0] != _PREFIX:
        return False
    try:
        pad = lambda raw: raw + "=" * (-len(raw) % 4)  # noqa: E731
        salt = base64.urlsafe_b64decode(pad(parts[1]))
        expected = base64.urlsafe_b64decode(pad(parts[2]))
    except Exception:
        return False
    if not salt or not expected:
        return False
    candidate = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                               n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=len(expected))
    return hmac.compare_digest(candidate, expected)


def password_problems(password: str) -> list[str]:
    """Why this password is not acceptable. Empty list = fine."""
    problems: list[str] = []
    text = str(password or "")
    if len(text) < MIN_PASSWORD_LENGTH:
        problems.append(f"رمز باید حداقل {MIN_PASSWORD_LENGTH} کاراکتر باشد.")
    if text and text.strip() != text:
        problems.append("رمز نباید با فاصله شروع یا تمام شود.")
    if text.isdigit():
        problems.append("رمز نباید فقط عدد باشد.")
    return problems


class PanelCredentials:
    """The panel's own account, read from the database and cached in memory.

    Cached because it is consulted on every request; refreshed explicitly when
    the operator changes it, so a password change takes effect immediately
    without a restart.
    """

    def __init__(self) -> None:
        self.username = ""
        self.password_hash = ""
        self.secret = ""
        self.ttl_seconds = 28800
        self.cookie_secure = False
        # Held only in memory: a restart issues a new one, and it is never
        # written anywhere an attacker could read it back.
        self.setup_token = ""

    @property
    def configured(self) -> bool:
        return bool(self.username and self.password_hash)

    async def load(self, db) -> "PanelCredentials":
        self.username = str(await db.get_setting(SETTING_USERNAME, "") or "").strip()
        self.password_hash = str(await db.get_setting(SETTING_PASSWORD, "") or "")
        secret = str(await db.get_setting(SETTING_SECRET, "") or "")
        if not secret:
            # Generated once, on first start. Sessions signed with it survive
            # restarts; regenerating it would sign every open session out.
            secret = secrets.token_urlsafe(48)
            await db.set_setting(SETTING_SECRET, secret)
        self.secret = secret
        try:
            self.ttl_seconds = max(900, int(await db.get_setting(SETTING_TTL, "28800") or 28800))
        except (TypeError, ValueError):
            self.ttl_seconds = 28800
        self.cookie_secure = str(
            await db.get_setting(SETTING_COOKIE_SECURE, "0") or "0"
        ).strip().lower() in {"1", "true", "yes", "on"}

        if not self.configured:
            self.setup_token = secrets.token_urlsafe(24)
            LOG.warning(
                "\n"
                "  ────────────────────────────────────────────────────────────\n"
                "   No panel administrator exists yet.\n"
                "   Open  /admin  and enter this one-time setup code:\n\n"
                "       %s\n\n"
                "   It is valid until this service restarts, and is shown only\n"
                "   in this log.\n"
                "  ────────────────────────────────────────────────────────────",
                self.setup_token,
            )
        else:
            self.setup_token = ""
        return self

    def verify_setup_token(self, token: str) -> bool:
        if self.configured or not self.setup_token:
            return False
        return hmac.compare_digest(str(token or ""), self.setup_token)

    def verify(self, username: str, password: str) -> bool:
        if not self.configured:
            return False
        # Both halves are checked in constant time, and the password hash is
        # computed even when the username is wrong, so a wrong username cannot
        # be told apart from a wrong password by timing.
        user_ok = hmac.compare_digest(str(username or "").strip(), self.username)
        pass_ok = verify_password(str(password or ""), self.password_hash)
        return user_ok and pass_ok

    async def store(self, db, *, username: str, password: str) -> None:
        clean = str(username or "").strip()
        if not clean:
            raise ValueError("نام کاربری نمی‌تواند خالی باشد.")
        problems = password_problems(password)
        if problems:
            raise ValueError(problems[0])
        await db.admin_update_settings({
            SETTING_USERNAME: clean,
            SETTING_PASSWORD: hash_password(password),
        })
        await self.load(db)
