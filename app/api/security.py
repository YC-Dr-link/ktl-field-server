"""휴대폰 API 쿠키 인증 의존성 (02-API명세.md §1: `ktl_client` + `ktl_session`).

- 쿠키 없음·모르는 단말 → 401 CLIENT_UNKNOWN
- 해제된 단말 → 403 CLIENT_REVOKED
"""
from __future__ import annotations

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyCookie
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.models import get_db, local_now
from app.models.auth import Client
from app.services.auth import hash_token

CLIENT_COOKIE = "ktl_client"

# 계약 components.securitySchemes의 이름(client) 그대로 /docs에 나오게
_client_scheme = APIKeyCookie(name=CLIENT_COOKIE, scheme_name="client", auto_error=False)


def require_client(
    request: Request,
    ktl_client: str | None = Security(_client_scheme),
    db: Session = Depends(get_db),
) -> Client:
    client = db.scalars(select(Client).where(Client.token_hash == hash_token(ktl_client))).first() if ktl_client else None
    if client is None:
        raise ApiError(401, "CLIENT_UNKNOWN", "등록되지 않은 단말입니다. 노트북에서 등록 코드를 받아 등록하세요.")
    if client.revoked_at is not None:
        raise ApiError(403, "CLIENT_REVOKED", "해제된 단말입니다. 노트북 담당에게 다시 등록을 요청하세요.")
    client.last_seen_at = local_now()
    db.commit()
    request.state.client = client
    return client
