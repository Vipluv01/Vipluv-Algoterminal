"""Encrypts broker API credentials at rest.

Fernet (symmetric, authenticated encryption) via the `cryptography` package
-- not a hand-rolled scheme. The key comes from an environment variable
that must be generated once per deployment and never committed:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

then set BROKER_CREDENTIAL_KEY to that value. Losing this key means every
stored broker credential becomes permanently undecryptable (by design --
there is no recovery path that doesn't also give an attacker one).
"""

from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet


class MissingCredentialKeyError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = os.environ.get("BROKER_CREDENTIAL_KEY")
    if not key:
        raise MissingCredentialKeyError(
            "BROKER_CREDENTIAL_KEY is not set -- refusing to store or read broker "
            "credentials without it. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and set it as an env var."
        )
    return Fernet(key.encode())


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode()
