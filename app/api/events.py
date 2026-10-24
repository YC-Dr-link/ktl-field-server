"""실시간 알림 SSE 라우터 (§2.4)."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["events"])
