"""관리 전용 API 라우터 (127.0.0.1:8766/api/admin/v1 §2.5)."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["admin"])


@router.get("/health", summary="관리 API 상태 확인")
def admin_health() -> dict[str, str]:
    """관리 API 서버 동작 상태를 반환한다."""
    return {"status": "ok", "app": "admin"}
