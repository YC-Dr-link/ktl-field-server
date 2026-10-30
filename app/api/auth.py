"""단말 · 로그인 라우터 (휴대폰 API §2.1, REQ-B1-10)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.security import CLIENT_COOKIE, require_client
from app.models import get_db, to_iso
from app.models.auth import Client, Tester
from app.services import auth as auth_service

router = APIRouter(tags=["auth"])


class PairRequest(BaseModel):
    pair_code: str = Field(pattern=r"^\d{6}$", examples=["483920"])
    device_name: str = Field(min_length=1, max_length=40, examples=["현장폰-1"])


class ClientOut(BaseModel):
    client_id: str
    device_name: str
    paired_at: str
    last_seen_at: str | None = None

    @classmethod
    def of(cls, row: Client) -> ClientOut:
        return cls(
            client_id=f"cl_{row.id}",
            device_name=row.device_name,
            paired_at=to_iso(row.paired_at),
            last_seen_at=to_iso(row.last_seen_at) if row.last_seen_at else None,
        )


class TesterOut(BaseModel):
    id: str
    name: str

    @classmethod
    def of(cls, row: Tester) -> TesterOut:
        return cls(id=f"ts_{row.id}", name=row.name)  # 휴대폰에는 이름만


class TesterList(BaseModel):
    items: list[TesterOut]


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(name, value, max_age=max_age, path="/", httponly=True, samesite="strict")


@router.get("/health", summary="휴대폰 API 상태 확인")
def mobile_health() -> dict[str, str]:
    """휴대폰 API 서버 동작 상태를 반환한다."""
    return {"status": "ok", "app": "mobile"}


@router.post("/clients/pair", response_model=ClientOut, summary="등록 코드로 단말 등록 (ktl_client 쿠키 발급)")
def pair(body: PairRequest, response: Response, db: Session = Depends(get_db)) -> ClientOut:
    client, token = auth_service.pair_client(db, body.pair_code, body.device_name)
    _set_cookie(response, CLIENT_COOKIE, token, auth_service.CLIENT_COOKIE_MAX_AGE)
    return ClientOut.of(client)


@router.get(
    "/testers",
    response_model=TesterList,
    summary="시험자 목록 (이름만)",
    dependencies=[Depends(require_client)],
)
def list_testers(db: Session = Depends(get_db)) -> TesterList:
    rows = db.scalars(select(Tester).where(Tester.active.is_(True)).order_by(Tester.id))
    return TesterList(items=[TesterOut.of(row) for row in rows])
