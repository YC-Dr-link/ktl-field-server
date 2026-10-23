"""단말 · 로그인 라우터 (휴대폰 API §2.1)."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["auth"])


@router.get("/health", summary="휴대폰 API 상태 확인")
def mobile_health() -> dict[str, str]:
    """휴대폰 API 서버 동작 상태를 반환한다."""
    return {"status": "ok", "app": "mobile"}
