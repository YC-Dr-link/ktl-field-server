"""내보내기 라우터 (§2.5 관리 API 전용 — `/cases/{id}/exports`, `/exports/{id}`, `/exports/{id}/download`)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_category_lookup, get_settings
from app.api.schemas import Export, ExportCreate
from app.models import ServerSettings, get_db
from app.services import exports as export_service
from app.services.cases import CategoryLookup

router = APIRouter(tags=["admin"])


@router.post(
    "/cases/{case_id}/exports",
    response_model=Export,
    status_code=202,
    summary="내보내기 생성 (확정값만)",
    responses={400: {"description": "지원하지 않는 형식 · 사진 업무가 아닌 분류 · cp949로 못 쓰는 값"}},
)
def create_export(
    case_id: str,
    body: ExportCreate,
    db: Session = Depends(get_db),
    settings: ServerSettings = Depends(get_settings),
    lookup: CategoryLookup = Depends(get_category_lookup),
    actor: str = Depends(get_actor),
) -> Export:
    return export_service.create_export(db, settings.storage_dir, case_id, body, lookup, actor=actor)


@router.get("/exports/{export_id}", response_model=Export, summary="내보내기 상태 + 미리보기(앞 20행)")
def get_export(export_id: str, db: Session = Depends(get_db)) -> Export:
    return export_service.to_export(export_service.get_export(db, export_id))


@router.get(
    "/exports/{export_id}/download",
    response_class=FileResponse,
    summary="파일 받기 (csv/txt/xlsx 또는 zip)",
    responses={200: {"description": "파일", "content": {"application/octet-stream": {}}}},
)
def download_export(
    export_id: str,
    db: Session = Depends(get_db),
    settings: ServerSettings = Depends(get_settings),
) -> FileResponse:
    row = export_service.get_export(db, export_id)
    path = export_service.export_file(settings.storage_dir, row)
    return FileResponse(path, media_type="application/octet-stream", filename=row.file_name)
