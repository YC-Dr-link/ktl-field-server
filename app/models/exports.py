"""내보내기 테이블 (REQ-B1-08, 02-API명세.md §2.5 `/exports`). 외부 ID `ex_<id>`."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, local_now


class Export(Base):
    __tablename__ = "exports"
    # 삭제 후에도 ID가 재사용되지 않도록 (내려받은 파일 · 이력이 ID를 참조)
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # photo_values | merge_raw
    format: Mapped[str] = mapped_column(String(8))  # csv | txt | xlsx
    encoding: Mapped[str] = mapped_column(String(8))  # cp949 | utf-8
    include_photos: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued · running · done · failed
    file_name: Mapped[str | None] = mapped_column(String(128))
    file_path: Mapped[str | None] = mapped_column(String(256))  # storage_dir 기준 상대 경로
    # 미리보기(앞 20행) — 내려받을 파일과 같은 칼럼 · 값
    preview: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    skipped_unconfirmed: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=local_now)
