"""Signed keyset cursors.

A cursor names the last row of a page by its sort key (created_at, id), so
later pages start strictly after it however many rows arrive meanwhile, and
the query can seek through the index instead of counting past an offset.

Cursors are HMAC-signed and bound to the query they came from (org and
filters). Editing one, or reusing it with other filters, is rejected rather
than quietly paging from a made-up position.
"""

import base64
import binascii
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime


class InvalidCursorError(Exception):
    pass


@dataclass(frozen=True)
class Cursor:
    created_at: datetime
    id: uuid.UUID


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def query_fingerprint(*parts: object) -> str:
    """A short digest of what a cursor's query was, to bind the cursor to it."""
    encoded = json.dumps([str(part) for part in parts]).encode()
    return hashlib.sha256(encoded).hexdigest()[:32]


def _sign(secret: bytes, payload: bytes) -> bytes:
    return hmac.new(secret, payload, hashlib.sha256).digest()


def encode_cursor(cursor: Cursor, fingerprint: str, secret: bytes) -> str:
    payload = json.dumps(
        {"t": cursor.created_at.isoformat(), "i": str(cursor.id), "q": fingerprint},
        separators=(",", ":"),
    ).encode()
    return f"{_b64encode(payload)}.{_b64encode(_sign(secret, payload))}"


def decode_cursor(token: str, fingerprint: str, secret: bytes) -> Cursor:
    try:
        encoded_payload, encoded_signature = token.split(".")
        payload = _b64decode(encoded_payload)
        signature = _b64decode(encoded_signature)
    except (ValueError, binascii.Error) as exc:
        raise InvalidCursorError from exc
    if not hmac.compare_digest(signature, _sign(secret, payload)):
        raise InvalidCursorError
    # Signed by us, so well-formed; only the query binding can still differ.
    data = json.loads(payload)
    if data["q"] != fingerprint:
        raise InvalidCursorError
    return Cursor(datetime.fromisoformat(data["t"]), uuid.UUID(data["i"]))
