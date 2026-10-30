"""단말 등록 규칙 (REQ-B1-10, 02-API명세.md §1 · §2.1 · §2.5)."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import os
import re
import secrets
import socket
from typing import Any

import segno
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.api.errors import ApiError, validation_error
from app.models import local_now
from app.models.auth import Client, LoginSession, PairCode

PAIR_CODE_TTL = timedelta(minutes=5)
CLIENT_COOKIE_MAX_AGE = 90 * 24 * 3600
# TODO(확인 필요): 틀린 등록 코드를 몇 번까지 허용할지 — 넘으면 살아 있는 코드를 모두 만료시킨다
MAX_PAIR_FAILURES = 10
_ID_RE = re.compile(r"^([a-z]+)_([1-9]\d{0,17})$")  # SQLite INTEGER 범위 안


def _write(db: Session, stmt: Any) -> int:
    """일괄 UPDATE/DELETE. 세션 안 객체는 필요한 곳에서 refresh하므로 ORM 동기화는 끈다
    (SQLite에서 읽은 naive 시각과 aware 시각을 파이썬에서 비교하다 깨지는 것을 막음)."""
    return db.execute(stmt, execution_options={"synchronize_session": False}).rowcount


def as_local(dt: datetime) -> datetime:
    """DB에서 읽은 naive 시각(로컬 벽시계)을 오프셋 있는 로컬 시각으로."""
    return dt.astimezone()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    """(쿠키에 넣을 원문, DB에 넣을 해시)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def parse_id(external_id: str, prefix: str) -> int | None:
    """`ts_2` → 2. 형식이 다르면 None."""
    m = _ID_RE.match(external_id)
    return int(m.group(2)) if m and m.group(1) == prefix else None


# ── 등록 코드 · 단말 ──────────────────────────────────────────────


def issue_pair_code(db: Session) -> PairCode:
    now = local_now()
    # 다 쓴 코드·만료된 코드·만료된 세션은 발급할 때 같이 치운다
    _write(db, delete(PairCode).where((PairCode.used_at.is_not(None)) | (PairCode.expires_at <= now)))
    _write(db, delete(LoginSession).where(LoginSession.expires_at <= now))
    active = set(db.scalars(select(PairCode.code)))
    code = f"{secrets.randbelow(1_000_000):06d}"
    while code in active:
        code = f"{secrets.randbelow(1_000_000):06d}"
    row = PairCode(code=code, expires_at=now + PAIR_CODE_TTL)
    db.add(row)
    db.commit()
    return row


def pair_client(db: Session, code: str, device_name: str) -> tuple[Client, str]:
    """1회용 코드를 소비하고 단말을 만든다. 반환: (단말, 쿠키 토큰 원문)."""
    now = local_now()
    row = db.scalars(
        select(PairCode).where(PairCode.code == code, PairCode.used_at.is_(None)).order_by(PairCode.id.desc())
    ).first()
    # TODO(확인 필요): 없는 코드·이미 쓴 코드의 오류 코드가 계약에 없어 400 VALIDATION으로 응답함
    if row is None:
        _count_pair_failure(db, now)
        raise validation_error({"pair_code": "등록 코드가 맞지 않습니다."})
    if as_local(row.expires_at) <= now:
        raise ApiError(410, "PAIR_CODE_EXPIRED", "등록 코드가 만료되었습니다. 노트북에서 새 코드를 발급하세요.")
    # 조건부 UPDATE로 소비: 같은 코드로 동시에 들어와도 한 요청만 성공한다
    claimed = _write(
        db, update(PairCode).where(PairCode.id == row.id, PairCode.used_at.is_(None)).values(used_at=now)
    )
    if claimed != 1:
        db.rollback()
        raise validation_error({"pair_code": "등록 코드가 맞지 않습니다."})
    token, token_hash = new_token()
    client = Client(device_name=device_name, token_hash=token_hash, paired_at=now)
    db.add(client)
    db.commit()
    return client, token


def _count_pair_failure(db: Session, now: datetime) -> None:
    """틀린 코드가 MAX_PAIR_FAILURES번 들어오면 살아 있는 코드를 모두 만료시킨다 (6자리 무차별 대입 막기)."""
    alive = (PairCode.used_at.is_(None)) & (PairCode.expires_at > now)
    _write(db, update(PairCode).where(alive).values(failed_attempts=PairCode.failed_attempts + 1))
    _write(db, update(PairCode).where(alive, PairCode.failed_attempts >= MAX_PAIR_FAILURES).values(expires_at=now))
    db.commit()


def pair_url(code: str, port: int) -> str:
    """QR 내용 (03-UI시나리오 B-1: `http://<IP>:8765/pair?code=483920`)."""
    return f"http://{lan_host()}:{port}/pair?code={code}"


def lan_host() -> str:
    """휴대폰이 접속할 노트북 IP.

    TODO(확인 필요): 에그 Wi-Fi 대역에서 노트북 IP를 고정할지 — 그 전에는 KTL_PUBLIC_HOST로 지정하거나 자동 감지.
    """
    if host := os.getenv("KTL_PUBLIC_HOST"):
        return host
    # UDP connect는 패킷을 보내지 않고 기본 경로의 로컬 주소만 정한다 (오프라인에서도 동작)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def qr_svg(text: str) -> str:
    return segno.make(text, error="m").svg_inline(scale=4)


def revoke_client(db: Session, client: Client) -> None:
    client.revoked_at = local_now()
    _write(db, delete(LoginSession).where(LoginSession.client_id == client.id))
    db.commit()
