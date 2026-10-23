"""분류 · 접수 건 라우터 (§2.2)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.modules import load_categories

router = APIRouter(tags=["cases"])


@router.get("/categories", summary="categories.yaml 분류·항목·출력 칼럼 조회")
def get_categories() -> dict[str, Any]:
    """categories.yaml 내용을 JSON으로 반환한다."""
    return load_categories()
