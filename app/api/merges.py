"""가스·라돈 파일 · 시계열 · 선별/병합 라우터 (관리 API 전용, 02-API명세.md §2.5 · REQ-B1-09)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_category_lookup, get_settings
from app.api.schemas import DataFile, Merge, MergeCreate, Series
from app.models import ServerSettings, get_db
from app.services import merges as merge_service
from app.services.auth import parse_id
from app.services.cases import CategoryLookup

router = APIRouter(tags=["admin"])


def _loader(request: Request) -> str:
    """Selector를 불러올 함수 경로 (`모듈:함수`). 테스트는 app.state.selector_loader를 바꿔 끼운다."""
    return request.app.state.selector_loader


@router.post(
    "/cases/{case_id}/files",
    response_model=DataFile,
    status_code=201,
    summary="가스·라돈 파일 업로드 → 형식 판별",
    responses={409: {"description": "완료된 건"}, 413: {"description": "파일 50 MB 초과"}},
)
def upload_file(
    case_id: str,
    request: Request,
    file: UploadFile = File(description="로거 파일 (원본 그대로 읽기 전용 보관)"),
    db: Session = Depends(get_db),
    settings: ServerSettings = Depends(get_settings),
    lookup: CategoryLookup = Depends(get_category_lookup),
    actor: str = Depends(get_actor),
) -> DataFile:
    return merge_service.upload_file(
        db,
        settings.storage_dir,
        case_id,
        lookup,
        file=file.file,
        filename=file.filename,
        actor=actor,
        loader=_loader(request),
    )


@router.get("/files/{file_id}/series", response_model=Series, summary="그래프용 시계열 (B-4 series())")
def get_series(
    file_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: ServerSettings = Depends(get_settings),
) -> Series:
    return merge_service.file_series(db, settings.storage_dir, file_id, loader=_loader(request))


@router.post(
    "/cases/{case_id}/merges",
    response_model=Merge,
    status_code=202,
    summary="선별/병합 실행 (비동기)",
    responses={409: {"description": "완료된 건"}},
)
def create_merge(
    case_id: str,
    body: MergeCreate,
    request: Request,
    db: Session = Depends(get_db),
    lookup: CategoryLookup = Depends(get_category_lookup),
    actor: str = Depends(get_actor),
) -> Merge:
    merge = merge_service.create_merge(db, case_id, lookup, body, actor=actor)
    pk = parse_id(merge.id, merge_service.MERGE_ID_PREFIX)
    assert pk is not None
    request.app.state.merge_runner.submit(pk, loader=_loader(request))
    return merge


@router.get("/merges/{merge_id}", response_model=Merge, summary="선별/병합 결과")
def get_merge(merge_id: str, db: Session = Depends(get_db)) -> Merge:
    return merge_service.get_merge(db, merge_id)
