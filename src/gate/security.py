"""Password hashing and the opaque tokens used for sessions and API keys."""

import hashlib
import secrets
from functools import cache

import anyio.to_thread
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

API_KEY_PREFIX = "gk_"
# How much of a key is stored in the clear, so users can tell keys apart.
API_KEY_DISPLAY_LENGTH = 8
TOKEN_BYTES = 32

# argon2id with the library's defaults (RFC 9106's low-memory profile).
_hasher = PasswordHasher()


# Hashing takes tens of milliseconds of CPU on purpose, so it runs in a worker
# thread rather than stalling the event loop.


async def hash_password(password: str) -> str:
    return await anyio.to_thread.run_sync(_hasher.hash, password)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


async def verify_password(password_hash: str, password: str) -> bool:
    return await anyio.to_thread.run_sync(_verify, password_hash, password)


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@cache
def _dummy_hash() -> str:
    return _hasher.hash(secrets.token_hex(16))


async def spend_password_check_time(password: str) -> None:
    """Take as long as a real check, so unknown emails can't be told apart by
    how fast login fails."""
    await anyio.to_thread.run_sync(lambda: _verify(_dummy_hash(), password))


def generate_session_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 is enough here: tokens are 256 random bits, not guessable
    passwords, and lookups need a deterministic hash to use the index."""
    return hashlib.sha256(token.encode()).hexdigest()
