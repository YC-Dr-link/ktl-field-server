"""값 수정 · 확정 · 수정 이력 (REQ-B1-06, 02-API명세.md §2.3 PATCH /photos/{id}/values).

- 값을 바꾸면 reason 필수 (없으면 400). 바꾼 값마다 이력(이전 값 · 새 값 · 사유 · 수정자 · 시각)을 남긴다.
- confirm: true면 보낸 값 전부 confirmed (바뀐 값은 edited). needs_check 값을 보내지 않고 확정하면 400.
- 이미 확정된 값을 다시 확정하거나 고치면 409 ALREADY_CONFIRMED. 완료된 건의 사진은 409 CASE_COMPLETED.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from app.api.errors import ApiError, not_found, validation_error
from app.api.schemas import Photo, ValuesPatch
from app.models import Case as CaseRow
from app.models import local_now, to_iso
from app.models.photos import Photo as PhotoRow
from app.models.photos import ValueEdit
from app.services.cases import PHOTO_ID_PREFIX
from app.services.photos import to_photo

CONFIRMED = frozenset({"confirmed", "edited"})
MAX_TEXT = 500  # 값 · 사유 길이 상한 (DB · 화면 보호용)
_PHOTO_ID_RE = re.compile(rf"^{PHOTO_ID_PREFIX}([1-9]\d{{0,17}})$")


def parse_photo_id(photo_id: str) -> int:
    """`ph_301` → 301. 형식이 다르면 존재하지 않는 사진과 같게 404."""
    m = _PHOTO_ID_RE.match(photo_id)
    if m is None:
        raise not_found("사진을 찾을 수 없습니다.")
    return int(m.group(1))


def _lock_photo(db: Session, photo_pk: int) -> PhotoRow:
    """사진 행을 쓰기 잠금한 뒤 최신 상태로 읽는다.

    SQLite는 첫 쓰기에서 쓰기 잠금을 잡으므로, 읽기 전에 빈 UPDATE를 해 두면
    같은 사진의 동시 PATCH(또는 판독 결과 저장)가 읽고-고치고-쓰는 사이에 끼어들지 못한다.
    """
    locked = db.execute(update(PhotoRow).where(PhotoRow.id == photo_pk).values(id=PhotoRow.id))
    row = db.get(PhotoRow, photo_pk, populate_existing=True) if locked.rowcount == 1 else None
    if row is None or row.status == "deleted":
        # TODO(확인 필요): 다시 찍기로 지워진 사진의 값 수정 — 계약에 코드가 없어 없는 사진과 같게 404
        raise not_found("사진을 찾을 수 없습니다.")
    return row


def _was_edited(db: Session, photo_pk: int, key: str) -> bool:
    return bool(
        db.scalar(select(exists().where(ValueEdit.photo_id == photo_pk, ValueEdit.value_key == key)))
    )


def patch_values(db: Session, photo_id: str, body: ValuesPatch, *, actor: str) -> Photo:
    photo_pk = parse_photo_id(photo_id)
    try:
        photo = _lock_photo(db, photo_pk)
        case = db.get(CaseRow, photo.case_id, populate_existing=True)
        if case is None or case.status != "open":
            raise ApiError(409, "CASE_COMPLETED", "완료된 건의 값은 수정할 수 없습니다.")
        if photo.status != "done":
            # TODO(확인 필요): 판독 실패(failed) 사진에 값을 손으로 넣는 흐름 — 계약에 없어 지금은 판독이 끝난 사진만
            raise validation_error({"photo": "판독이 끝난 사진만 값을 수정·확정할 수 있습니다."})

        current: dict[str, dict[str, Any]] = {str(v["key"]): dict(v) for v in photo.values or []}
        fields: dict[str, str] = {}
        already: list[str] = []
        seen: set[str] = set()
        for i, change in enumerate(body.values):
            where = f"values.{i}"
            if change.key in seen:
                fields[f"{where}.key"] = "같은 값을 두 번 보냈습니다."
                continue
            seen.add(change.key)
            old = current.get(change.key)
            if old is None:
                fields[f"{where}.key"] = "이 사진에 없는 값입니다."
                continue
            changed = change.value != old.get("value")
            if len(change.value) > MAX_TEXT:
                fields[f"{where}.value"] = f"{MAX_TEXT}자 이하여야 합니다."
            if changed and not (change.reason or "").strip():
                fields[f"{where}.reason"] = "값을 바꾸면 사유를 적어야 합니다."
            elif change.reason is not None and len(change.reason) > MAX_TEXT:
                fields[f"{where}.reason"] = f"{MAX_TEXT}자 이하여야 합니다."
            if body.confirm and not change.value.strip():
                # TODO(확인 필요): 빈 값을 확정해도 되는지 (해당 없음 칸 등) — 지금은 거부
                fields[f"{where}.value"] = "빈 값은 확정할 수 없습니다."
            if old.get("status") in CONFIRMED and (changed or body.confirm):
                already.append(change.key)
        if body.confirm:
            missing = [k for k, v in current.items() if v.get("status") == "needs_check" and k not in seen]
            for key in missing:
                fields[f"values[{key}]"] = "확인 필요 값을 확인하지 않고 확정할 수 없습니다."
        if fields:
            raise validation_error(fields)
        if already:
            raise ApiError(
                409, "ALREADY_CONFIRMED", "이미 확정된 값입니다. 관리자에게 문의하세요.", {"keys": already}
            )

        now = local_now()
        for change in body.values:
            value = current[change.key]
            changed = change.value != value.get("value")
            if changed:
                db.add(
                    ValueEdit(
                        photo_id=photo.id,
                        case_id=photo.case_id,
                        value_key=change.key,
                        old_value=str(value.get("value") or ""),
                        new_value=change.value,
                        reason=(change.reason or "").strip(),
                        edited_by=actor,
                        edited_at=now,
                    )
                )
                value["value"] = change.value
            if body.confirm:
                edited = changed or _was_edited(db, photo.id, change.key)
                value["status"] = "edited" if edited else "confirmed"
                value["confirmed_by"] = actor
                value["confirmed_at"] = to_iso(now)
        # JSON 칸은 새 목록을 넣어야 바뀐 것으로 저장된다 (순서는 판독 결과 그대로)
        photo.values = [current[str(v["key"])] for v in photo.values or []]
        case.updated_at = now
        db.commit()
    except BaseException:
        db.rollback()
        raise
    # TODO(#6): SSE photo.values 알림은 #6 병합 후 여기서 보낸다.
    return to_photo(photo)
