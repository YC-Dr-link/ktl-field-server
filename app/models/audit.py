"""작업 로그 (REQ-B1-11) — 각 행이 이전 행의 해시를 품는 해시 체인. 쌓기만 하고 고치거나 지우지 않는다."""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class AuditLog(Base):
    __tablename__ = "audit_log"
    # 지운 행의 seq가 다시 쓰이지 않게 (끝 행 삭제 후 새 행이 그 자리를 메우면 안 됨)
    __table_args__ = {"sqlite_autoincrement": True}

    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[str] = mapped_column(String(32))  # ISO 8601 문자열 그대로 (해시 입력이 DB 왕복에도 같게)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(64))
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
