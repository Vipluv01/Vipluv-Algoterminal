"""Per-user AngelOneAdapter reuse -- without this, every live order or
live-market request would decrypt the vault credential and do a fresh
SmartAPI login (a real TOTP-gated network round trip) from scratch, which
is both slow and wasteful given AngelOneAdapter already knows how to
refresh/re-login its OWN session lazily (see angelone.py's _call).

Keyed by (user_id, credential.updated_at), not user_id alone: rotating a
credential in the Vault (routers/vault.py's PUT) changes updated_at, so
the OLD cached adapter (built from the now-stale decrypted secret) simply
becomes an unreachable key and a fresh one gets built and logged in on
next use -- no explicit invalidation call needed from vault.py, and no
risk of silently continuing to use a credential the user just rotated
away from. The stale entry is never explicitly evicted (this is a small,
single-process dev deployment, not a long-running multi-tenant service --
one abandoned adapter object per historical rotation is not worth an LRU).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app import crypto
from app.broker.angelone import AngelOneAdapter, AngelOneCredentials
from app.models.trading import LiveBrokerCredential

_CACHE: dict[tuple[int, object], AngelOneAdapter] = {}


class NoBrokerCredentialError(Exception):
    pass


class IncompleteBrokerCredentialError(Exception):
    pass


def get_adapter_for_user(db: Session, user_id: int) -> AngelOneAdapter:
    cred = db.query(LiveBrokerCredential).filter(LiveBrokerCredential.user_id == user_id).first()
    if cred is None:
        raise NoBrokerCredentialError("no broker credential stored -- add one via POST /vault/credential first")
    if cred.encrypted_client_code is None or cred.encrypted_totp_secret is None:
        raise IncompleteBrokerCredentialError(
            "broker credential is missing client_code and/or totp_secret -- both are required for Angel One login"
        )

    key = (user_id, cred.updated_at)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    creds = AngelOneCredentials(
        api_key=crypto.decrypt(cred.encrypted_api_key),
        client_code=crypto.decrypt(cred.encrypted_client_code),
        password=crypto.decrypt(cred.encrypted_api_secret),
        totp_secret=crypto.decrypt(cred.encrypted_totp_secret),
    )
    adapter = AngelOneAdapter(creds)
    _CACHE[key] = adapter
    return adapter
