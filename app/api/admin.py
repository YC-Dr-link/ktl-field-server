"""관리 전용 API 라우터 (127.0.0.1:8766/api/admin/v1 §2.5)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import ClientOut
from app.api.errors import not_found
from app.models import get_db, to_iso
from app.models.auth import Client
from app.services import auth as auth_service

router = APIRouter(tags=["admin"])


class PairCodeOut(BaseModel):
    code: str
    expires_at: str
    qr_svg: str


class ClientList(BaseModel):
    items: list[ClientOut]


@router.get("/health", summary="관리 API 상태 확인")
def admin_health() -> dict[str, str]:
    """관리 API 서버 동작 상태를 반환한다."""
    return {"status": "ok", "app": "admin"}


@router.post("/pair-codes", status_code=201, response_model=PairCodeOut, summary="단말 등록 코드 발급 (5분 만료)")
def create_pair_code(request: Request, db: Session = Depends(get_db)) -> PairCodeOut:
    row = auth_service.issue_pair_code(db)
    url = auth_service.pair_url(row.code, request.app.state.settings.mobile_port)
    return PairCodeOut(code=row.code, expires_at=to_iso(row.expires_at), qr_svg=auth_service.qr_svg(url))


@router.get("/clients", response_model=ClientList, summary="등록 단말 목록")
def list_clients(db: Session = Depends(get_db)) -> ClientList:
    rows = db.scalars(select(Client).where(Client.revoked_at.is_(None)).order_by(Client.id))
    return ClientList(items=[ClientOut.of(row) for row in rows])


@router.delete("/clients/{client_id}", status_code=204, summary="단말 해제 (이후 요청 403)")
def revoke_client(client_id: str, db: Session = Depends(get_db)) -> Response:
    pk = auth_service.parse_id(client_id, "cl")
    client = db.get(Client, pk) if pk is not None else None
    if client is None or client.revoked_at is not None:
        raise not_found("단말을 찾을 수 없습니다.")
    auth_service.revoke_client(db, client)
    return Response(status_code=204)
