"""작업 로그 해시 체인 (REQ-B1-11, `GET /audit`).

행마다 hash = SHA-256(seq · at · actor · action · target · case_id · detail · prev_hash).
가운데 행을 고치거나 지우면 그 행 또는 다음 행에서 체인이 끊겨 chain_ok=false가 된다.
TODO(확인 필요): 마지막 행(들)을 통째로 지우는 것은 체인만으로는 못 잡는다 — 최신 해시를 따로 보관(출력·서명)할지 결정 필요.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import local_now, to_iso
from app.models.audit import AuditLog

GENESIS_HASH = "0" * 64
SYSTEM_ACTOR = "system"


def entry_hash(row: AuditLog) -> str:
    payload = {
        "seq": row.seq,
        "at": row.at,
        "actor": row.actor,
        "action": row.action,
        "target": row.target,
        "case_id": row.case_id,
        "detail": row.detail,
        "prev_hash": row.prev_hash,
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def record(
    db: Session, action: str, target: str, *, actor: str, case_pk: int | None = None, detail: dict[str, Any] | None = None
) -> None:
    """변경과 같은 트랜잭션에 로그 한 줄을 붙인다. 커밋은 부르는 쪽이 한다 (변경이 롤백되면 로그도 같이)."""
    row = AuditLog(
        at=to_iso(local_now()), actor=actor, action=action, target=target, case_id=case_pk,
        detail=detail or {}, prev_hash="", hash="",
    )
    db.add(row)
    # INSERT로 쓰기 잠금을 먼저 잡은 뒤 직전 행을 읽는다 — 동시 요청이 같은 prev_hash로 갈라지지 않게
    db.flush()
    prev = db.scalar(select(AuditLog.hash).where(AuditLog.seq < row.seq).order_by(AuditLog.seq.desc()).limit(1))
    row.prev_hash = prev or GENESIS_HASH
    row.hash = entry_hash(row)


def read_log(db: Session, case_pk: int | None) -> tuple[bool, list[AuditLog]]:
    """(체인 전체 검증 결과, 건 필터를 적용한 행). 건만 골라 봐도 검증은 늘 처음부터 끝까지."""
    # shortcut: 요청마다 전체 행을 다시 해시한다 — 현장 노트북 규모(수만 행)에선 충분, 느려지면 검증 지점(checkpoint)을 둔다
    rows = list(db.scalars(select(AuditLog).order_by(AuditLog.seq)))
    prev, ok = GENESIS_HASH, True
    for row in rows:
        if row.prev_hash != prev or row.hash != entry_hash(row):
            ok = False
            break
        prev = row.hash
    return ok, [row for row in rows if case_pk is None or row.case_id == case_pk]
