"""현장 서버 API 라우터 패키지 (auth, cases, photos, events, admin)."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.cases import router as cases_router
from app.api.events import router as events_router
from app.api.photos import router as photos_router

mobile_api_router = APIRouter(prefix="/api/v1")
mobile_api_router.include_router(auth_router)
mobile_api_router.include_router(cases_router)
mobile_api_router.include_router(photos_router)
mobile_api_router.include_router(events_router)

admin_api_router = APIRouter(prefix="/api/admin/v1")
admin_api_router.include_router(cases_router)
admin_api_router.include_router(photos_router)
admin_api_router.include_router(events_router)
admin_api_router.include_router(admin_router)

__all__ = ["admin_api_router", "mobile_api_router"]
