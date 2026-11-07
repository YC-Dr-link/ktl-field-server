"""내보내기 photo_values (REQ-B1-08, 02-API명세.md §2.5 · §3.4).

- 기기 1대 = 1행, 칼럼 = categories.yaml `export` 순서 그대로.
- `항목.칸`(예: `prec_1.flow`) = 그 기기 · 그 항목의 현재 사진(다시 찍기로 지운 사진 제외)에서 읽은 칸 값.
  확정된 값(confirmed · edited)만 쓰고, 나머지 칸은 비워 둔 뒤 `skipped_unconfirmed`로 센다.
- `photo_first` = 그 기기 첫 사진의 저장 파일명 (값이 아니므로 확정과 상관없이 쓴다).
- 파일은 요청 안에서 바로 만든다 (기기 수십 대 × 칼럼 수십 개라 금방 끝난다) → 응답 status는 `done`.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import csv
from dataclasses import dataclass
import io
import logging
from pathlib import Path
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import not_found, validation_error
from app.api.schemas import Export, ExportCreate, ExportPreview
from app.models import Case as CaseRow
from app.models import local_now
from app.models.exports import Export as ExportRow
from app.models.photos import Photo as PhotoRow
from app.services.cases import CategoryLookup, format_case_id, parse_case_id
from app.services.values import CONFIRMED

logger = logging.getLogger(__name__)

EXPORT_ID_PREFIX = "ex_"
PHOTO_FIRST = "photo_first"
PREVIEW_ROWS = 20
_DELIMITERS = {"csv": ",", "txt": "\t"}  # 02 §2.5: txt는 탭 구분
_EXPORT_ID_RE = re.compile(rf"^{EXPORT_ID_PREFIX}([1-9]\d{{0,17}})$")
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True)
class Table:
    columns: list[str]
    rows: list[list[str]]
    skipped_unconfirmed: int


def format_export_id(pk: int) -> str:
    return f"{EXPORT_ID_PREFIX}{pk}"


def parse_export_id(export_id: str) -> int:
    """`ex_5` → 5. 형식이 다르면 없는 내보내기와 같게 404."""
    m = _EXPORT_ID_RE.match(export_id)
    if m is None:
        raise not_found("내보내기를 찾을 수 없습니다.")
    return int(m.group(1))


def to_export(row: ExportRow) -> Export:
    return Export(
        id=format_export_id(row.id),
        case_id=format_case_id(row.case_id),
        kind=row.kind,
        format=row.format,
        status=row.status,  # type: ignore[arg-type]
        file_name=row.file_name,
        preview=ExportPreview(**row.preview) if row.preview else None,
        skipped_unconfirmed=row.skipped_unconfirmed,
    )


def _confirmed_value(photo: PhotoRow | None, value_key: str) -> str | None:
    """사진에서 확정된 칸 값. 사진이 없거나 판독이 안 끝났거나 미확정이면 None."""
    if photo is None or photo.status != "done":
        return None
    for value in photo.values or []:
        if value.get("key") == value_key:
            return str(value.get("value") or "") if value.get("status") in CONFIRMED else None
    return None


def _parse_spec(export_spec: Sequence[str]) -> list[tuple[str, str] | None]:
    """export 칼럼마다 (항목 key, 칸 key). `photo_first`는 ("", ""), 알 수 없는 표기는 None."""
    parsed: list[tuple[str, str] | None] = []
    for column in export_spec:
        item_key, dot, value_key = column.partition(".")
        if column == PHOTO_FIRST:
            parsed.append(("", ""))
        elif dot and item_key and value_key:
            parsed.append((item_key, value_key))
        else:
            # TODO(확인 필요): categories.yaml export에 photo_first · 항목.칸 말고 다른 표기가 생기는지
            logger.warning("categories.yaml export의 알 수 없는 칼럼: %s (빈 칸으로 출력)", column)
            parsed.append(None)
    return parsed


def build_photo_table(export_spec: Sequence[str], unit_count: int, photos: Sequence[PhotoRow]) -> Table:
    """기기마다 한 행. `photos`는 다시 찍기로 지우지 않은 사진, id 오름차순."""
    current: dict[tuple[int, str], PhotoRow] = {}
    first_name: dict[int, str] = {}
    for photo in photos:  # id 오름차순 — 칸마다 마지막 사진이 남고, 기기마다 처음 사진 이름이 남는다
        current[(photo.unit_index, photo.item_key)] = photo
        first_name.setdefault(photo.unit_index, photo.stored_name)

    columns = _parse_spec(export_spec)
    rows: list[list[str]] = []
    skipped = 0
    for unit in range(1, unit_count + 1):
        row: list[str] = []
        for column in columns:
            if column is None:
                row.append("")
            elif column == ("", ""):
                # TODO(확인 필요): "첫 사진" = 그 기기에서 가장 먼저 올린 사진(지금) vs 촬영 항목 순서상 첫 칸 사진
                row.append(first_name.get(unit, ""))
            else:
                value = _confirmed_value(current.get((unit, column[0])), column[1])
                if value is None:
                    skipped += 1
                row.append(value or "")
        rows.append(row)
    return Table(columns=list(export_spec), rows=rows, skipped_unconfirmed=skipped)


def render(table: Table, fmt: str) -> str:
    """CSV(쉼표) · TXT(탭). 줄바꿈은 엑셀에 맞게 CRLF."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=_DELIMITERS[fmt], lineterminator="\r\n")
    # TODO(확인 필요): xlsm에 붙여 넣을 때 머리글(칼럼 이름) 행이 있어야 하는지 — 지금은 첫 줄에 export 칼럼 이름
    writer.writerow(table.columns)
    writer.writerows(table.rows)
    return buf.getvalue()


def encode(table: Table, fmt: str, encoding: str) -> bytes:
    """파일 내용. cp949로 못 쓰는 글자가 있으면 깨진 파일 대신 400 — 어느 기기 · 칼럼인지 알려 준다."""
    try:
        return render(table, fmt).encode(encoding)
    except UnicodeEncodeError:
        pass
    fields: dict[str, str] = {}
    for unit, row in enumerate(table.rows, start=1):
        for column, cell in zip(table.columns, row):
            try:
                cell.encode(encoding)
            except UnicodeEncodeError:
                fields[f"rows.{unit}.{column}"] = f"{encoding}로 쓸 수 없는 글자가 있습니다. utf-8로 내보내세요."
    raise validation_error(fields, f"{encoding}로 쓸 수 없는 값이 있습니다.")


def _validate(body: ExportCreate, category: Mapping[str, Any] | None) -> list[str]:
    """photo_values로 내보낼 수 있는지 확인하고 export 칼럼 목록을 돌려준다."""
    fields: dict[str, str] = {}
    if body.kind != "photo_values":
        fields["kind"] = "merge_raw 내보내기는 아직 지원하지 않습니다."  # TODO(#9): merge_raw
    if body.format == "xlsx":
        fields["format"] = "xlsx는 아직 지원하지 않습니다. csv 또는 txt를 쓰세요."  # TODO(확인 필요): xlsx 담당 이슈
    if body.include_photos:
        fields["include_photos"] = "사진 묶음(zip)은 아직 지원하지 않습니다."  # TODO(#14): 워터마크 사진 zip
    category = category or {}
    export_spec = [str(c) for c in category.get("export") or []]
    if body.kind == "photo_values" and (not category.get("items") or "merge_raw" in export_spec):
        fields["kind"] = "이 분류는 사진 업무가 아닙니다."
    if fields:
        raise validation_error(fields)
    return export_spec


def _write_new(target_dir: Path, base_name: str, ext: str, data: bytes) -> Path:
    """같은 이름이 있으면 `_2`, `_3` …을 붙여 새 파일로 쓴다 (이전 내보내기 파일을 덮어쓰지 않게)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        path = target_dir / (f"{base_name}{ext}" if n == 1 else f"{base_name}_{n}{ext}")
        try:
            with open(path, "xb") as out:
                out.write(data)
            return path
        except FileExistsError:
            continue
    raise RuntimeError("내보내기 파일 이름을 정하지 못했습니다")


def create_export(
    db: Session,
    storage_dir: Path,
    case_id: str,
    body: ExportCreate,
    category_lookup: CategoryLookup,
    *,
    actor: str,
) -> Export:
    case = db.get(CaseRow, parse_case_id(case_id))
    if case is None:
        raise not_found("접수 건을 찾을 수 없습니다.")
    export_spec = _validate(body, category_lookup(case.category))

    photos = db.scalars(
        select(PhotoRow).where(PhotoRow.case_id == case.id, PhotoRow.status != "deleted").order_by(PhotoRow.id)
    ).all()
    table = build_photo_table(export_spec, case.unit_count, photos)
    # TODO(확인 필요): utf-8 파일에 BOM을 붙일지 (엑셀에서 바로 열면 BOM이 없을 때 한글이 깨진다) — 지금은 BOM 없음
    data = encode(table, body.format, body.encoding)

    now = local_now()
    base_name = f"{case.receipt_no}_{_UNSAFE_NAME.sub('_', case.category)}_{now:%Y%m%d-%H%M%S}"
    path = _write_new(storage_dir / "exports" / format_case_id(case.id), base_name, f".{body.format}", data)
    row = ExportRow(
        case_id=case.id,
        kind=body.kind,
        format=body.format,
        encoding=body.encoding,
        include_photos=body.include_photos,
        status="done",
        file_name=path.name,
        file_path=path.relative_to(storage_dir).as_posix(),
        preview={"columns": table.columns, "rows": table.rows[:PREVIEW_ROWS]},
        skipped_unconfirmed=table.skipped_unconfirmed,
        created_by=actor,
        created_at=now,
    )
    try:
        db.add(row)
        db.commit()
    except BaseException:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    # TODO(#6): SSE export.done 알림은 #6 병합 후 여기서 보낸다. TODO(#12): 작업 로그(해시 체인) 기록.
    return to_export(row)


def get_export(db: Session, export_id: str) -> ExportRow:
    row = db.get(ExportRow, parse_export_id(export_id))
    if row is None:
        raise not_found("내보내기를 찾을 수 없습니다.")
    return row


def export_file(storage_dir: Path, row: ExportRow) -> Path:
    """내려받을 파일 경로. 아직 안 만들어졌거나 지워졌으면 404."""
    path = storage_dir / row.file_path if row.status == "done" and row.file_path else None
    if path is None or not path.is_file():
        raise not_found("내보내기 파일이 없습니다.")
    return path
