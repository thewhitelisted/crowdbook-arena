"""Join codes and player tokens. A token is shown to its player once and kept only as a hash."""

from __future__ import annotations

import hashlib
import secrets

# No 0/O or 1/I/L, which are easy to misread when a code is read out.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_code(length: int = 6) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def new_token() -> str:
    return secrets.token_urlsafe(24)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
