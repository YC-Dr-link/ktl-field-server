"""관리 화면 "확인 필요" 모아 보기 (REQ-B2-06, `GET /api/admin/v1/review-queue`).

열린 건의 판독 끝난(done) 사진에서 `needs_check` 값만 모은다. 다시 찍기로 지운 사진·완료된 건은 뺀다.
"""
from __future__ import annotations

import logging

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.schemas import ReviewQueueItem, Value
from app.models import Case as CaseRow
from app.models.photos import Photo as PhotoRow
from app.services.cases import CategoryLookup, format_case_id, format_photo_id

logger = logging.getLogger(__name__)

# 확인 필요 값이 하나라도 있는 사진만 (값 JSON을 파이썬으로 모두 풀지 않게 SQLite에서 거른다)
_HAS_NEEDS_CHECK = text(
    "EXISTS (SELECT 1 FROM json_each(photos.\"values\") AS v WHERE json_extract(v.value, '$.status') = 'needs_check')"
)


def review_queue(db: Session, category_lookup: CategoryLookup) -> list[ReviewQueueItem]:
    """건 · 기기 · 촬영 항목(categories.yaml 순서) 순. 같은 칸은 최신 사진 하나뿐 — 이전 사진은 deleted."""
    # TODO(확인 필요): 계약에 limit · cursor가 없어 전부 돌려준다. 현장 하루치 규모를 넘으면 페이지 나누기 필요
    rows = db.execute(
        select(
            PhotoRow.id,
            PhotoRow.case_id,
            PhotoRow.unit_index,
            PhotoRow.item_key,
            PhotoRow.values,
            CaseRow.receipt_no,
            CaseRow.category,
        )
        .join(CaseRow, CaseRow.id == PhotoRow.case_id)
        .where(CaseRow.status == "open", PhotoRow.status == "done", _HAS_NEEDS_CHECK)
        .order_by(PhotoRow.case_id, PhotoRow.unit_index, PhotoRow.id)
    ).all()

    item_order: dict[str, dict[str, int]] = {}
    for category in {row.category for row in rows}:
        items = (category_lookup(category) or {}).get("items") or []
        item_order[category] = {item["key"]: pos for pos, item in enumerate(items)}

    def sort_key(row) -> tuple[int, int, int, int]:  # 분류에 없는 항목(설정이 바뀐 경우)은 맨 뒤
        order = item_order[row.category]
        return row.case_id, row.unit_index, order.get(row.item_key, len(order)), row.id

    queue: list[ReviewQueueItem] = []
    for row in sorted(rows, key=sort_key):
        for raw in row.values or []:
            if raw.get("status") != "needs_check":
                continue
            try:
                value = Value.model_validate(raw)
            except ValidationError:
                # 모양이 틀린 값 하나 때문에 목록 전체가 500이 되지 않게 그 값만 뺀다
                logger.warning("확인 필요 값의 모양이 올바르지 않아 뺍니다: photo=%s value=%r", row.id, raw)
                continue
            queue.append(
                ReviewQueueItem(
                    case_id=format_case_id(row.case_id),
                    receipt_no=row.receipt_no,
                    photo_id=format_photo_id(row.id),
                    unit_index=row.unit_index,
                    item_key=row.item_key,
                    value=value,
                )
            )
    return queue
