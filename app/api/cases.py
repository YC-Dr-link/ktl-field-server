"""접수 건 라우터 (§2.2 — 휴대폰·관리 API 공용 /cases)."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["cases"])
