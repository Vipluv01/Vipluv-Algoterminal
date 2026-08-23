"""User identity for request handlers.

THIS IS A PHASE-1 PLACEHOLDER, not real authentication. It exists so
Phase 1 (order execution, strategies, dashboard) can be built and tested
end-to-end without being blocked on Supabase credentials -- which are
Phase 3's job (see the plan: verify a Supabase-issued JWT here instead,
provisioning a User row from its claims on first sign-in). Every request
in dev mode resolves to one fixed dev user, auto-created if missing.

Swapping this out for real auth means changing get_current_user's body
only -- every router already depends on it via FastAPI's dependency
injection (`user: User = Depends(get_current_user)`), so nothing calling
it needs to change.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.user import User

DEV_USER_GOOGLE_SUB = "dev-user-local"
DEV_USER_EMAIL = "dev@localhost"


def get_current_user(db: Session = Depends(get_db)) -> User:
    user = db.query(User).filter(User.google_sub == DEV_USER_GOOGLE_SUB).first()
    if user is None:
        user = User(google_sub=DEV_USER_GOOGLE_SUB, email=DEV_USER_EMAIL, display_name="Dev User")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user
