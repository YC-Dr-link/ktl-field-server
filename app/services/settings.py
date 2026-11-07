"""관리 설정 저장·조회 (`GET · PUT /api/admin/v1/settings`).

`system_settings` 표에 칸 하나당 한 행(key → JSON 문자열)으로 둔다. 저장된 적 없는 칸은 계약 기본값.
"""
from __future__ import annotations

import json
import logging

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import Settings
from app.models import SystemSetting, local_now

logger = logging.getLogger(__name__)

# TODO(확인 필요): 이 값들을 실제로 쓰는 곳 — 판독 큐(#5)의 신뢰도 기준·제한 시간, 내보내기(#8)의 기본 인코딩,
#   사진 자동 삭제(보관 기간)는 각 이슈 병합 후 `read_settings()`로 읽어 연결한다.
_KEYS = tuple(Settings.model_fields)


def read_settings(db: Session) -> Settings:
    stored = {
        row.key: row.value for row in db.scalars(select(SystemSetting).where(SystemSetting.key.in_(_KEYS)))
    }
    values = {}
    for key, raw in stored.items():
        try:
            values[key] = json.loads(raw)
        except ValueError:
            logger.warning("설정 %s 값이 JSON이 아니라 기본값을 씁니다: %r", key, raw)
    try:
        return Settings(**values)
    except ValidationError:
        # 손으로 고친 DB 등으로 범위를 벗어난 값이 있어도 관리 화면은 열려야 한다 — 틀린 칸만 기본값
        logger.warning("저장된 설정 일부가 올바르지 않아 기본값을 씁니다: %r", values)
        good = {k: v for k, v in values.items() if _valid(k, v)}
        return Settings(**good)


def _valid(key: str, value: object) -> bool:
    try:
        Settings.model_validate({key: value})
    except ValidationError:
        return False
    return True


def update_settings(db: Session, body: Settings) -> Settings:
    """보낸 칸만 바꾼다 (빠진 칸은 그대로). `photo_retention_days: null`은 '자동 삭제 안 함'으로 저장."""
    changes = body.model_dump(exclude_unset=True)
    if changes:
        now = local_now()
        rows = {row.key: row for row in db.scalars(select(SystemSetting).where(SystemSetting.key.in_(changes)))}
        for key, value in changes.items():
            raw = json.dumps(value)
            row = rows.get(key)
            if row is None:
                db.add(SystemSetting(key=key, value=raw, updated_at=now))
            elif row.value != raw:
                row.value, row.updated_at = raw, now
        db.commit()
    return read_settings(db)
