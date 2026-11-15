from __future__ import annotations

import hashlib
import hmac
import secrets


def make_token(document_id: str, secret: str) -> str:
    """Token deterministik dari document_id + secret.

    Deterministik supaya QR bisa dibuat ulang tanpa menyimpan token mentah.
    """
    digest = hmac.new(
        secret.encode("utf-8"), document_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return digest[:16]


def verify_token(document_id: str, token: str, secret: str) -> bool:
    expected = make_token(document_id, secret)
    return hmac.compare_digest(expected, token)


def new_short_id() -> str:
    return secrets.token_hex(4).upper()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
