"""가스·라돈 파일 업로드 · 시계열 · 선별/병합 (REQ-B1-09, 02-API명세.md §2.5).

- 파일: 받은 그대로 읽기 전용 보관 + SHA-256 → B-4 `detect()`로 형식 판별 (못 하면 detected=null)
- 시계열: B-4 `series()` → `{ columns, unit, rows }`
- 병합: `202 queued`로 받고 작업 실행기(app.worker.merges)가 B-4 `run()`을 불러 결과 표를 저장
모듈 호출은 모두 별도 프로세스에서 시간 제한을 두고 한다 (app.worker.selector).
"""
from __future__ import annotations

from collections.abc import Mapping
import logging
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Any, BinaryIO

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import ApiError, not_found, validation_error
from app.api.schemas import DataFile, Merge, MergeCreate, Series
from app.models import Case as CaseRow
from app.models import local_now
from app.models.data import DataFile as DataFileRow
from app.models.data import Merge as MergeRow
from app.services import storage
from app.services.auth import parse_id
from app.services.cases import CategoryLookup, format_case_id, parse_case_id
from app.worker.selector import (
    DEFAULT_LOADER,
    DETECT_TIMEOUT_S,
    RUN_TIMEOUT_S,
    SERIES_TIMEOUT_S,
    SelectorFailed,
    call_selector,
)

logger = logging.getLogger(__name__)

FILE_ID_PREFIX = "file"
MERGE_ID_PREFIX = "mg"
RADON_ALIGN = ("sequence", "hour")
RESTART_ERROR = "서버가 다시 시작되어 병합이 중단되었습니다. 다시 실행하세요."
_SAFE_EXT = re.compile(r"^\.[a-z0-9]{1,8}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def format_file_id(pk: int) -> str:
    return f"{FILE_ID_PREFIX}_{pk}"


def format_merge_id(pk: int) -> str:
    return f"{MERGE_ID_PREFIX}_{pk}"


def to_data_file(row: DataFileRow) -> DataFile:
    return DataFile(
        id=format_file_id(row.id),
        case_id=format_case_id(row.case_id),
        name=row.name,
        sha256=row.sha256,
        detected=row.detected,
    )


def to_merge(row: MergeRow) -> Merge:
    return Merge(
        id=format_merge_id(row.id),
        case_id=format_case_id(row.case_id),
        selector=row.selector,
        status=row.status,  # type: ignore[arg-type]
        table=row.table,  # type: ignore[arg-type]
        meta=row.meta or {},
        warnings=row.warnings or [],  # type: ignore[arg-type]
        error=row.error,
    )


# ── 공통 확인 ────────────────────────────────────────────────────


def _open_case_with_selector(db: Session, case_id: str, lookup: CategoryLookup) -> tuple[CaseRow, str]:
    """건이 있고(404), 열려 있고(409), 분류에 selector가 있는지(400) 확인하고 selector 이름을 돌려준다."""
    case = db.get(CaseRow, parse_case_id(case_id))
    if case is None:
        raise not_found("접수 건을 찾을 수 없습니다.")
    if case.status != "open":
        raise ApiError(409, "CASE_COMPLETED", "완료된 건은 바꿀 수 없습니다. 관리 화면에서 다시 여세요.")
    selector = str((lookup(case.category) or {}).get("selector") or "")
    if not selector:
        raise validation_error({"case_id": "가스·라돈 건이 아닙니다 (분류에 selector가 없음)."})
    return case, selector


def _get_file(db: Session, file_id: str) -> DataFileRow:
    pk = parse_id(file_id, FILE_ID_PREFIX)
    row = db.get(DataFileRow, pk) if pk is not None else None
    if row is None:
        raise not_found("파일을 찾을 수 없습니다.")
    return row


def _file_name_parts(filename: str | None) -> tuple[str, str]:
    """받은 파일 이름에서 경로 부분을 빼고 (표시 이름, 저장 확장자)를 정한다."""
    raw = filename or ""
    name = PureWindowsPath(PurePosixPath(raw).name).name  # `C:\\…\\a.csv` · `../a.csv` 모두 이름만
    name = _CONTROL.sub("", name).strip()[:200] or "upload"
    ext = Path(name).suffix.lower()
    return name, ext if _SAFE_EXT.match(ext) else ".dat"


# ── 파일 업로드 · 시계열 ─────────────────────────────────────────────


def upload_file(
    db: Session,
    storage_dir: Path,
    case_id: str,
    lookup: CategoryLookup,
    *,
    file: BinaryIO,
    filename: str | None,
    actor: str,
    loader: str = DEFAULT_LOADER,
) -> DataFile:
    case, selector = _open_case_with_selector(db, case_id, lookup)
    name, ext = _file_name_parts(filename)
    now = local_now()
    try:
        stored = storage.store_data_file(file, storage_dir, format_case_id(case.id), f"{now:%Y%m%d_%H%M%S}_{selector}", ext)
    except storage.FileTooLarge:
        raise ApiError(413, "FILE_TOO_LARGE", storage.DATA_FILE_TOO_LARGE_MESSAGE) from None
    except storage.EmptyFile:
        raise validation_error({"file": "빈 파일입니다."}) from None

    try:
        detected = call_selector(
            selector, "detect", (str(storage_dir / stored.rel_path),), timeout=DETECT_TIMEOUT_S, loader=loader
        )
    except SelectorFailed as exc:
        # 판별 실패는 "모르는 형식"과 같게 본다 (계약: 모르는 파일이면 null). 원본은 남긴다.
        logger.warning("형식 판별 실패 (%s): %s", stored.rel_path, exc)
        detected = None

    try:
        row = DataFileRow(
            case_id=case.id,
            name=name,
            selector=selector,
            sha256=stored.sha256,
            stored_name=stored.stored_name,
            original_path=stored.rel_path,
            size_bytes=stored.size_bytes,
            detected=detected,
            uploaded_by=actor,
            uploaded_at=now,
        )
        db.add(row)
        # 판별하는 동안 건이 완료됐을 수 있다 — 같은 트랜잭션에서 open일 때만 갱신해 다시 확인
        still_open = db.execute(
            update(CaseRow).where(CaseRow.id == case.id, CaseRow.status == "open").values(updated_at=now)
        )
        if still_open.rowcount != 1:
            raise ApiError(409, "CASE_COMPLETED", "완료된 건은 바꿀 수 없습니다. 관리 화면에서 다시 여세요.")
        db.commit()
    except BaseException:
        db.rollback()
        storage.remove_original(storage_dir, stored.rel_path)
        raise
    return to_data_file(row)


def file_series(db: Session, storage_dir: Path, file_id: str, *, loader: str = DEFAULT_LOADER) -> Series:
    row = _get_file(db, file_id)
    try:
        payload = call_selector(
            row.selector, "series", ([str(storage_dir / row.original_path)],), timeout=SERIES_TIMEOUT_S, loader=loader
        )
    except SelectorFailed as exc:
        # TODO(확인 필요): 모듈이 파일을 못 읽을 때 쓸 에러 코드가 계약(§1.1)에 없어 400 VALIDATION으로 둠
        raise validation_error({"file": str(exc)}, "그래프용 시계열을 만들지 못했습니다.") from None
    unit = (row.detected or {}).get("unit")
    return Series(columns=payload["columns"], unit=str(unit) if unit is not None else None, rows=payload["rows"])


# ── 선별/병합 ──────────────────────────────────────────────────────


def create_merge(db: Session, case_id: str, lookup: CategoryLookup, body: MergeCreate, *, actor: str) -> Merge:
    case, selector = _open_case_with_selector(db, case_id, lookup)
    fields: dict[str, str] = {}
    if body.selector != selector:
        fields["selector"] = f"이 건의 분류는 {selector}를 씁니다."

    pks = [parse_id(fid, FILE_ID_PREFIX) for fid in body.file_ids]
    if len(set(body.file_ids)) != len(body.file_ids):
        fields["file_ids"] = "같은 파일이 두 번 들어 있습니다."
    else:
        found = set(db.scalars(select(DataFileRow.id).where(DataFileRow.case_id == case.id)))
        missing = [fid for fid, pk in zip(body.file_ids, pks, strict=True) if pk not in found]
        if missing:
            fields["file_ids"] = f"이 건의 파일이 아닙니다: {', '.join(missing)}"

    if body.selector == "radon_merge":
        reference = body.options.get("reference")
        if reference is not None and reference not in body.file_ids:
            fields["options.reference"] = "기준기는 file_ids 중 하나여야 합니다."
        align = body.options.get("align")
        if align is not None and align not in RADON_ALIGN:
            fields["options.align"] = "sequence 또는 hour여야 합니다."
    elif "ranges" in body.options and not isinstance(body.options["ranges"], Mapping):
        fields["options.ranges"] = "{ 구간코드: [시작, 끝] } 모양이어야 합니다."
    if fields:
        raise validation_error(fields)

    row = MergeRow(
        case_id=case.id,
        selector=body.selector,
        file_ids=[pk for pk in pks if pk is not None],
        options=body.options,
        status="queued",
        table=None,
        meta={},
        warnings=[],
        created_by=actor,
        created_at=local_now(),
    )
    db.add(row)
    db.commit()
    return to_merge(row)


def get_merge(db: Session, merge_id: str) -> Merge:
    pk = parse_id(merge_id, MERGE_ID_PREFIX)
    row = db.get(MergeRow, pk) if pk is not None else None
    if row is None:
        raise not_found("병합 결과를 찾을 수 없습니다.")
    return to_merge(row)


def _selector_options(options: Mapping[str, Any], paths_by_id: Mapping[str, str]) -> dict[str, Any]:
    """Selector는 파일 ID를 모르므로 라돈 기준기(`reference: file_11`)는 그 파일 경로로 바꿔 넘긴다.

    TODO(확인 필요): B-4가 reference를 경로로 받는지(또는 paths 안 순번인지) ktl-field-modules와 맞춰야 함.
    """
    converted = dict(options)
    reference = converted.get("reference")
    if isinstance(reference, str) and reference in paths_by_id:
        converted["reference"] = paths_by_id[reference]
    return converted


def run_merge(
    session_factory: sessionmaker[Session], storage_dir: Path, merge_pk: int, *, loader: str = DEFAULT_LOADER
) -> None:
    """작업 실행기 스레드에서 부른다: queued → running → done / failed. 어떤 경우에도 예외를 밖으로 내지 않는다."""
    with session_factory() as db:
        row = db.get(MergeRow, merge_pk)
        if row is None or row.status != "queued":
            return
        files = {f.id: f for f in db.scalars(select(DataFileRow).where(DataFileRow.id.in_(row.file_ids)))}
        paths_by_id = {format_file_id(pk): str(storage_dir / files[pk].original_path) for pk in row.file_ids if pk in files}
        paths = list(paths_by_id.values())
        options = _selector_options(row.options or {}, paths_by_id)
        selector, missing = row.selector, len(paths) != len(row.file_ids)
        row.status = "running"
        db.commit()

    result: dict[str, Any] = {"status": "failed", "finished_at": None}
    try:
        if missing:
            raise SelectorFailed("병합할 파일 일부를 찾을 수 없습니다.")
        payload = call_selector(selector, "run", (paths, options), timeout=RUN_TIMEOUT_S, loader=loader)
        result.update(status="done", table=payload["table"], warnings=payload["warnings"], meta=payload["meta"], error=None)
    except SelectorFailed as exc:
        result["error"] = str(exc)
    except Exception:  # noqa: BLE001 - 실행기 스레드가 죽지 않게
        logger.exception("병합 실행 중 서버 오류 (mg_%s)", merge_pk)
        result["error"] = "병합 중 서버 오류가 발생했습니다."
    result["finished_at"] = local_now()
    with session_factory() as db:
        db.execute(update(MergeRow).where(MergeRow.id == merge_pk, MergeRow.status == "running").values(**result))
        db.commit()
    # TODO(#6): SSE `merge.status` { id, case_id, status } 알림은 #6 병합 후 여기서 보낸다


def fail_unfinished_merges(session_factory: sessionmaker[Session]) -> int:
    """서버 시작 시: 이전 실행에서 끝나지 못한 병합(queued · running)을 failed로 바꾼다."""
    with session_factory() as db:
        result = db.execute(
            update(MergeRow)
            .where(MergeRow.status.in_(("queued", "running")))
            .values(status="failed", error=RESTART_ERROR, finished_at=local_now())
        )
        db.commit()
        return int(result.rowcount or 0)


def merge_raw_table(db: Session, case_pk: int, merge_id: str) -> dict[str, Any]:
    """`merge_raw` 내보내기용: 이 건의 끝난 병합 결과 표 (xlsm RAW 시트에 붙이는 배치 그대로).

    TODO(#8): 내보내기(app/services/exports.py)의 kind=merge_raw가 이 함수를 쓴다 — #8 병합 후 연결.
    TODO(확인 필요): RAW 시트에 붙일 때 머리글 행(columns)을 같이 넣는지 — 배치 기준은 B-4 표 그대로.
    """
    pk = parse_id(merge_id, MERGE_ID_PREFIX)
    row = db.get(MergeRow, pk) if pk is not None else None
    if row is None or row.case_id != case_pk:
        raise validation_error({"merge_id": "이 건의 병합 결과가 아닙니다."})
    if row.status != "done" or row.table is None:
        raise validation_error({"merge_id": "끝난 병합 결과만 내보낼 수 있습니다."})
    return row.table
