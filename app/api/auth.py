"""단말 · 로그인 라우터 (휴대폰 API §2.1).

TODO(REQ-B1-10): 단말 등록(ktl_client 쿠키) 및 시험자 PIN 로그인(ktl_session 쿠키) 인증 구현 예정.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["auth"])


@router.get("/health", summary="휴대폰 API 상태 확인")
def mobile_health() -> dict[str, str]:
    """휴대폰 API 서버 동작 상태를 반환한다."""
    return {"status": "ok", "app": "mobile"}
