"""관리 설정 저장·조회 (`GET · PUT /api/admin/v1/settings`).

`system_settings` 표에 칸 하나당 한 행(key → JSON 문자열)으로 둔다. 저장된 적 없는 칸은 계약 기본값.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.api.schemas import Settings
from app.models import SystemSetting, local_now

logger = logging.getLogger(__name__)

# TODO(확인 필요): 이 값들을 실제로 쓰는 곳 — 판독 큐(#5)의 신뢰도 기준·제한 시간, 내보내기(#8)의 기본 인코딩,
#   사진 자동 삭제(보관 기간)는 각 이슈 병합 후 `read_settings()`로 읽어 연결한다.
_KEYS = tuple(Settings.model_fields)


def _valid(key: str, value: Any) -> bool:
    try:
        Settings.model_validate({key: value})
    except ValidationError:
        return False
    return True


def read_settings(db: Session) -> Settings:
    """저장된 칸을 읽는다. 손으로 고친 DB 등으로 틀린 칸이 있어도 관리 화면은 열리게 그 칸만 기본값."""
    values: dict[str, Any] = {}
    for row in db.scalars(select(SystemSetting).where(SystemSetting.key.in_(_KEYS))):
        try:
            value = json.loads(row.value)
        except ValueError:
            value = row.value
        if _valid(row.key, value):
            values[row.key] = value
        else:
            logger.warning("설정 %s 값이 올바르지 않아 기본값을 씁니다: %r", row.key, row.value)
    return Settings(**values)


def update_settings(db: Session, body: Settings) -> Settings:
    """전체 교체 (PUT) — 빠진 칸은 기본값. `photo_retention_days: null`은 '자동 삭제 안 함'."""
    now = local_now()
    rows = [{"key": key, "value": json.dumps(value), "updated_at": now} for key, value in body.model_dump().items()]
    stmt = insert(SystemSetting).values(rows)
    # 두 화면이 동시에 저장해도 기본키 충돌(500) 없이 나중 것이 남게 upsert
    stmt = stmt.on_conflict_do_update(
        index_elements=[SystemSetting.key],
        set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
    )
    try:
        db.execute(stmt)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return read_settings(db)
