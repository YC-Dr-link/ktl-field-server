"""가스·라돈 로거 파일 · 선별/병합 테이블 (REQ-B1-09, 02-API명세.md §2.5).

외부 ID: `file_<id>` · `mg_<id>`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, local_now

_TS = DateTime(timezone=True)


class DataFile(Base):
    """업로드한 로거 파일. 원본은 읽기 전용으로 보관하고 SHA-256을 남긴다."""

    __tablename__ = "data_files"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))  # 받은 파일 이름 (경로 부분은 뺀다)
    selector: Mapped[str] = mapped_column(String(32))  # categories.yaml의 selector
    sha256: Mapped[str] = mapped_column(String(64))
    stored_name: Mapped[str] = mapped_column(String(128))
    original_path: Mapped[str] = mapped_column(String(256))  # storage_dir 기준 상대 경로
    size_bytes: Mapped[int] = mapped_column(Integer)
    detected: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # B-4 detect() 결과, 모르는 형식이면 None
    uploaded_by: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(_TS, default=local_now)


class Merge(Base):
    """선별/병합 실행 (queued → running → done / failed)."""

    __tablename__ = "merges"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    selector: Mapped[str] = mapped_column(String(32))
    file_ids: Mapped[list[int]] = mapped_column(JSON)  # DataFile.id 순서 그대로 (Selector에 넘기는 순서)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    table: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # {"columns": [...], "rows": [[...]]}
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_TS, default=local_now)
    finished_at: Mapped[datetime | None] = mapped_column(_TS)

