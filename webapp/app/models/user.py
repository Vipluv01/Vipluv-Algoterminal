from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    """Identity comes from Google OAuth only -- there is no password to
    steal or leak, which matters more here than for a typical app because
    this one is eventually meant to hold live broker credentials per user.
    google_sub (the OAuth subject claim) is the real unique identity;
    email is stored for display and is NOT treated as the primary key,
    since Google's own docs warn email can change/be reused while sub
    cannot."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
