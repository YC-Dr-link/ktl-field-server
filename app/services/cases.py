"""접수 건 생성·목록·상세 (REQ-B1-02, 02-API명세.md §2.2)."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import re
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.errors import not_found, validation_error
from app.api.schemas import Case, CaseCreate, CaseSummary, ItemStatus, Progress, Unit, UnitItem
from app.models import Case as CaseRow
from app.models import CaseReopen, local_now, to_iso
from app.models.photos import Photo as PhotoRow
from app.services.text import text_problem

CASE_ID_PREFIX = "case_"
PHOTO_ID_PREFIX = "ph_"
_CASE_ID_RE = re.compile(r"^case_([1-9]\d{0,17})$")

# (unit_index, item_key) → (photo_id, 칸 상태)
SlotMap = Mapping[tuple[int, str], tuple[str, ItemStatus]]
CategoryLookup = Callable[[str], Mapping[str, Any] | None]


def format_case_id(pk: int) -> str:
    return f"{CASE_ID_PREFIX}{pk}"


def format_photo_id(pk: int) -> str:
    return f"{PHOTO_ID_PREFIX}{pk}"


def parse_case_id(case_id: str) -> int:
    """`case_12` → 12. 형식이 다르면 존재하지 않는 건과 같게 404."""
    m = _CASE_ID_RE.match(case_id)
    if m is None:
        raise not_found("접수 건을 찾을 수 없습니다.")
    return int(m.group(1))


def item_status(photo_status: str | None, value_statuses: list[str]) -> ItemStatus:
    """사진 상태(§1.2)와 그 사진 값들의 상태로 칸(`units[].items[].status`) 상태를 정한다.

    - 사진 없음(또는 다시 찍기로 deleted) → empty
    - queued · reading · failed → 그대로
    - done: 확인 필요 값이 하나라도 있으면 needs_check, 값이 있고 모두 확정(confirmed · edited)이면 confirmed,
      그 밖(자동 판독 값 · 판독 없는 사진)은 done
    """
    if photo_status is None or photo_status == "deleted":
        return "empty"
    if photo_status in ("queued", "reading", "failed"):
        return photo_status  # type: ignore[return-value]
    if "needs_check" in value_statuses:
        return "needs_check"
    if value_statuses and all(s in ("confirmed", "edited") for s in value_statuses):
        return "confirmed"
    return "done"


def build_units(category: Mapping[str, Any] | None, unit_count: int, slots: SlotMap) -> list[Unit]:
    """기기(units) × 촬영 항목(items) 표를 만든다. 기기 번호는 1부터."""
    items = (category or {}).get("items") or []
    units: list[Unit] = []
    for index in range(1, unit_count + 1):
        unit_items = []
        for item in items:
            photo_id, status = slots.get((index, item["key"]), (None, "empty"))
            unit_items.append(
                UnitItem(
                    key=item["key"],
                    label=item["label"],
                    required=bool(item.get("required", False)),
                    photo_id=photo_id,
                    status=status,
                )
            )
        units.append(Unit(index=index, items=unit_items))
    return units


def compute_progress(units: list[Unit]) -> Progress:
    # TODO(확인 필요): total을 필수(required) 칸만 셀지, 값(Value) 단위로 셀지 — 지금은 모든 칸(기기 × 항목)
    statuses = [item.status for unit in units for item in unit.items]
    return Progress(
        total=len(statuses),
        confirmed=statuses.count("confirmed"),
        needs_check=statuses.count("needs_check"),
    )


def _photo_slots(db: Session, case_ids: list[int]) -> dict[int, dict[tuple[int, str], tuple[str, ItemStatus]]]:
    """건별로 올라온 사진의 칸별 상태 — 칸마다 최신(deleted 아닌) 사진 하나. 여러 건을 쿼리 한 번에."""
    result: dict[int, dict[tuple[int, str], tuple[str, ItemStatus]]] = {pk: {} for pk in case_ids}
    if not case_ids:
        return result
    photos = db.scalars(
        select(PhotoRow)
        .where(PhotoRow.case_id.in_(case_ids), PhotoRow.status != "deleted")
        .order_by(PhotoRow.id)
    )
    for photo in photos:  # id 오름차순이므로 칸마다 마지막 사진이 남는다
        statuses = [str(v.get("status")) for v in photo.values or []]
        result[photo.case_id][(photo.unit_index, photo.item_key)] = (
            format_photo_id(photo.id),
            item_status(photo.status, statuses),
        )
    return result


def _validate_create(body: CaseCreate, category: Mapping[str, Any] | None, phone_only: bool) -> None:
    if category is None or (phone_only and not category.get("phone", False)):
        raise validation_error({"category": "알 수 없는 분류입니다."}, "알 수 없는 분류입니다.")
    allowed = {opt["key"]: opt.get("choices") or [] for opt in category.get("options") or []}
    fields: dict[str, str] = {}
    for key, value in body.options.items():
        if key not in allowed:
            fields[f"options.{key}"] = "이 분류에 없는 선택 항목입니다."
        elif allowed[key] and value not in allowed[key]:
            fields[f"options.{key}"] = f"다음 중 하나여야 합니다: {', '.join(allowed[key])}"
    # TODO(확인 필요): 분류 options(예: flowmeter_type)를 생성 시 필수로 받을지 — 지금은 선택
    if fields:
        raise validation_error(fields)


def to_summary(row: CaseRow, units: list[Unit]) -> CaseSummary:
    return CaseSummary(
        id=format_case_id(row.id),
        receipt_no=row.receipt_no,
        category=row.category,
        unit_count=row.unit_count,
        status=row.status,  # type: ignore[arg-type]
        progress=compute_progress(units),
        updated_at=to_iso(row.updated_at),
    )


def to_case(row: CaseRow, units: list[Unit]) -> Case:
    return Case(
        **to_summary(row, units).model_dump(),
        options=dict(row.options or {}),
        created_by=row.created_by,
        created_at=to_iso(row.created_at),
        units=units,
    )


def create_case(
    db: Session, body: CaseCreate, category: Mapping[str, Any] | None, actor: str, *, phone_only: bool
) -> Case:
    """새 접수 건. `phone_only`면 휴대폰에 보이는 분류(phone: true)만 허용한다."""
    _validate_create(body, category, phone_only)
    # TODO(확인 필요): 같은 접수번호로 건을 여러 번 만들 수 있는지 (중복 금지 여부) — 지금은 허용
    now = local_now()  # 새 건은 created_at == updated_at (따로 부르면 초 경계에서 달라질 수 있음)
    row = CaseRow(
        created_at=now,
        updated_at=now,
        receipt_no=body.receipt_no,
        category=body.category,
        unit_count=body.unit_count,
        options=dict(body.options),
        status="open",
        created_by=actor,
    )
    db.add(row)
    db.commit()
    return to_case(row, build_units(category, row.unit_count, {}))


def get_case(db: Session, case_id: str, category_lookup: CategoryLookup) -> Case:
    row = db.get(CaseRow, parse_case_id(case_id))
    if row is None:
        raise not_found("접수 건을 찾을 수 없습니다.")
    units = build_units(category_lookup(row.category), row.unit_count, _photo_slots(db, [row.id])[row.id])
    return to_case(row, units)


def list_cases(
    db: Session,
    category_lookup: CategoryLookup,
    *,
    status: str | None,
    q: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[CaseSummary], str | None]:
    """최근 건부터. `cursor`는 이전 페이지 마지막 건의 ID(`case_N`)."""
    stmt = select(CaseRow).order_by(CaseRow.id.desc())
    if status:
        stmt = stmt.where(CaseRow.status == status)
    if q:
        stmt = stmt.where(CaseRow.receipt_no.contains(q, autoescape=True))
    if cursor:
        m = _CASE_ID_RE.match(cursor)
        if m is None:
            raise validation_error({"cursor": "올바르지 않은 cursor입니다."})
        stmt = stmt.where(CaseRow.id < int(m.group(1)))
    rows = list(db.scalars(stmt.limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    slots = _photo_slots(db, [row.id for row in rows])
    items = [
        to_summary(row, build_units(category_lookup(row.category), row.unit_count, slots[row.id]))
        for row in rows
    ]
    next_cursor = format_case_id(rows[-1].id) if has_more and rows else None
    return items, next_cursor


def reopen_case(db: Session, case_id: str, reason: str, category_lookup: CategoryLookup, *, actor: str) -> Case:
    """완료 건을 다시 연다 (관리 화면). 사유는 필수이고 `case_reopens`에 이력으로 남는다."""
    pk = parse_case_id(case_id)
    reason = reason.strip()
    if problem := ("다시 여는 사유를 적어야 합니다." if not reason else text_problem(reason)):
        raise validation_error({"reason": problem})
    now = local_now()
    try:
        # 조건부 UPDATE 한 번으로 상태를 바꿔 동시에 두 번 열어도 이력이 한 번만 남게 한다
        reopened = db.execute(
            update(CaseRow).where(CaseRow.id == pk, CaseRow.status == "completed").values(status="open", updated_at=now)
        )
        if reopened.rowcount != 1:
            if db.get(CaseRow, pk) is None:
                raise not_found("접수 건을 찾을 수 없습니다.")
            # TODO(확인 필요): 이미 열린 건을 다시 열기 — 계약에 코드가 없어 400 VALIDATION
            raise validation_error({"status": "완료된 건만 다시 열 수 있습니다."}, "이미 열려 있는 건입니다.")
        db.add(CaseReopen(case_id=pk, reason=reason, reopened_by=actor, reopened_at=now))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    # TODO(#6): SSE case.updated 알림은 #6 병합 후 여기서 보낸다.
    return get_case(db, case_id, category_lookup)
