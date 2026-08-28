"""Live broker credential storage -- STORE-ONLY. A decrypted secret must
NEVER be returned by any endpoint here, appear in any log line, or appear
in any error body: this router only ever hands back last-4 + a rotation
timestamp, the same convention a real broker dashboard uses for API keys
it doesn't trust the browser to hold in full again after the moment
they're entered.

app/crypto.py (Fernet, encrypt/decrypt) and LiveBrokerCredential
(app/models/trading.py) both existed before this router and were wired
into no application code -- this is that wiring.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crypto
from app.auth import get_current_user
from app.db import get_db
from app.models.trading import LiveBrokerCredential
from app.models.user import User

router = APIRouter(prefix="/vault", tags=["vault"])

LAST_N = 4


def _last4(plaintext: str) -> str:
    # Only ever the last LAST_N characters ever leave this function -- the
    # decrypted plaintext itself is a local variable in the caller, never
    # assigned to anything this router returns, logs, or raises.
    return plaintext[-LAST_N:] if len(plaintext) >= LAST_N else plaintext


class CredentialIn(BaseModel):
    broker: str
    api_key: str
    api_secret: str
    access_token: str | None = None


class CredentialOut(BaseModel):
    broker: str
    api_key_last4: str
    api_secret_last4: str
    has_access_token: bool
    # "Rotation timestamp" -- LiveBrokerCredential.updated_at, which bumps
    # on every POST (create OR overwrite, via the model's own onupdate=).
    rotated_at: object

    model_config = {"from_attributes": True}


def _to_out(cred: LiveBrokerCredential) -> CredentialOut:
    # Decrypts ONLY to slice the last 4 characters -- the full plaintext
    # never gets assigned to a field this function returns.
    api_key_plain = crypto.decrypt(cred.encrypted_api_key)
    api_secret_plain = crypto.decrypt(cred.encrypted_api_secret)
    return CredentialOut(
        broker=cred.broker,
        api_key_last4=_last4(api_key_plain),
        api_secret_last4=_last4(api_secret_plain),
        has_access_token=cred.encrypted_access_token is not None,
        rotated_at=cred.updated_at,
    )


@router.get("/credential", response_model=CredentialOut | None)
def get_credential(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cred = db.query(LiveBrokerCredential).filter(LiveBrokerCredential.user_id == user.id).first()
    if cred is None:
        return None
    return _to_out(cred)


@router.post("/credential", response_model=CredentialOut)
def put_credential(body: CredentialIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not body.api_key or not body.api_secret:
        raise HTTPException(status_code=400, detail="api_key and api_secret are required")

    cred = db.query(LiveBrokerCredential).filter(LiveBrokerCredential.user_id == user.id).first()
    encrypted_access_token = crypto.encrypt(body.access_token) if body.access_token else None
    if cred is None:
        cred = LiveBrokerCredential(
            user_id=user.id, broker=body.broker,
            encrypted_api_key=crypto.encrypt(body.api_key),
            encrypted_api_secret=crypto.encrypt(body.api_secret),
            encrypted_access_token=encrypted_access_token,
        )
        db.add(cred)
    else:
        # A real rotation, not a fresh row -- updated_at (this credential's
        # own "rotated_at") bumps via the model's onupdate=, which is
        # exactly the signal a Vault screen wants to show ("rotated 3 days
        # ago") without ever having stored a separate plaintext history.
        cred.broker = body.broker
        cred.encrypted_api_key = crypto.encrypt(body.api_key)
        cred.encrypted_api_secret = crypto.encrypt(body.api_secret)
        cred.encrypted_access_token = encrypted_access_token
    db.commit()
    db.refresh(cred)
    return _to_out(cred)


@router.delete("/credential")
def delete_credential(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cred = db.query(LiveBrokerCredential).filter(LiveBrokerCredential.user_id == user.id).first()
    if cred is None:
        raise HTTPException(status_code=404, detail="no credential stored")
    db.delete(cred)
    db.commit()
    return {"ok": True}
