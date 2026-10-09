"""
Dashboard access: a random link per business plus a one-off password sent in Telegram.
Only a scrypt hash of the password is stored. A browser's session cookie is an HMAC
keyed by that hash, so a new password signs out every browser using the old one.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
MAX_TRIES = 5
LOCKOUT = timedelta(minutes=5)
SESSION_DAYS = 30

def new_token() -> str:
    return secrets.token_urlsafe(18)

def new_password() -> str:
    groups = ["".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3)]
    return "-".join(groups)

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(_normalize(password), salt)
    return f"scrypt${salt.hex()}${digest.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    actual = _scrypt(_normalize(password), bytes.fromhex(salt))
    return hmac.compare_digest(actual.hex(), digest)

def session_value(stored_hash: str, token: str) -> str:
    return hmac.new(stored_hash.encode(), token.encode(), hashlib.sha256).hexdigest()

def _normalize(password: str) -> str:
    return password.strip().lower()

def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)

@dataclass
class Attempts:
    """
    Wrong-password counter per link and password; locks for LOCKOUT after MAX_TRIES.
    """

    failures: dict[str, int] = field(default_factory=dict)
    locked_until: dict[str, datetime] = field(default_factory=dict)

    def locked(self, key: str, now: datetime) -> bool:
        until = self.locked_until.get(key)
        if until is None:
            return False
        if now >= until:
            del self.locked_until[key]
            self.failures.pop(key, None)
            return False
        return True

    def fail(self, key: str, now: datetime) -> int:
        """
        Records a wrong password and returns how many tries are left.
        """
        count = self.failures.get(key, 0) + 1
        self.failures[key] = count
        if count >= MAX_TRIES:
            self.locked_until[key] = now + LOCKOUT
            return 0
        return MAX_TRIES - count

    def clear(self, key: str) -> None:
        self.failures.pop(key, None)
        self.locked_until.pop(key, None)
