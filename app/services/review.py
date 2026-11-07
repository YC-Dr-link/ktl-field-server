"""관리 화면 "확인 필요" 모아 보기 (REQ-B2-06, `GET /api/admin/v1/review-queue`).

열린 건의 판독 끝난(done) 사진에서 `needs_check` 값만 모은다. 다시 찍기로 지운 사진·완료된 건은 뺀다.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import ReviewQueueItem, Value
from app.models import Case as CaseRow
from app.models.photos import Photo as PhotoRow
from app.services.cases import format_case_id, format_photo_id


def review_queue(db: Session) -> list[ReviewQueueItem]:
    """건 · 기기 · 촬영 항목 순서 (같은 칸은 최신 사진 하나뿐 — 이전 사진은 deleted)."""
    # TODO(확인 필요): 계약에 limit · cursor가 없어 전부 돌려준다. 현장 하루치 규모를 넘으면 페이지 나누기 필요
    rows = db.execute(
        select(PhotoRow, CaseRow.receipt_no)
        .join(CaseRow, CaseRow.id == PhotoRow.case_id)
        .where(CaseRow.status == "open", PhotoRow.status == "done")
        .order_by(PhotoRow.case_id, PhotoRow.unit_index, PhotoRow.item_key, PhotoRow.id)
    )
    items: list[ReviewQueueItem] = []
    for photo, receipt_no in rows:
        for value in photo.values or []:
            if value.get("status") != "needs_check":
                continue
            items.append(
                ReviewQueueItem(
                    case_id=format_case_id(photo.case_id),
                    receipt_no=receipt_no,
                    photo_id=format_photo_id(photo.id),
                    unit_index=photo.unit_index,
                    item_key=photo.item_key,
                    value=Value.model_validate(value),
                )
            )
    return items
