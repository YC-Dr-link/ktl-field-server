"""단말 · 시험자 · 등록 코드 · 로그인 세션 테이블 (REQ-B1-10).

토큰과 PIN은 원문을 저장하지 않는다 (NFR-04: 해시 저장).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, local_now


class Tester(Base):
    """시험자. 외부 ID는 `ts_<id>`."""

    __tablename__ = "testers"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    pin_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=local_now)


class Client(Base):
    """등록된 단말(휴대폰). 외부 ID는 `cl_<id>`. 해제되면 `revoked_at`이 찍힌다."""

    __tablename__ = "clients"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_name: Mapped[str] = mapped_column(String(40), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    paired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=local_now)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PairCode(Base):
    """1회용 단말 등록 코드 (6자리, 5분 만료)."""

    __tablename__ = "pair_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 이 코드가 살아 있는 동안 들어온 틀린 코드 수 (무차별 대입 막기)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class LoginSession(Base):
    """시험자 로그인 세션 (`ktl_session` 쿠키, 12시간)."""

    __tablename__ = "login_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    tester_id: Mapped[int] = mapped_column(ForeignKey("testers.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
