"""실시간 알림 SSE 라우터 (§2.4) — `GET /events`, 휴대폰 · 관리 API 공용."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.types import Receive, Scope, Send

from app.api.deps import get_event_broker
from app.models import get_db
from app.services.events import PING, EventBroker

router = APIRouter(tags=["events"])

_EVENT_SCHEMA = {
    "type": "object",
    "description": "SSE data. event 이름은 photo.status | photo.values | case.updated | merge.status | export.done",
    "properties": {
        "photo_id": {"type": "string"},
        "case_id": {"type": "string"},
        "unit_index": {"type": "integer"},
        "item_key": {"type": "string"},
        "id": {"type": "string"},
        "status": {"type": "string"},
    },
}


class EventStreamResponse(StreamingResponse):
    """연결이 끝나면(끊김 · 서버 종료) 스트림을 바로 닫아 구독을 푼다.

    Starlette는 끊긴 뒤 제너레이터를 닫지 않고 GC에 맡기므로, 그대로 두면 구독이 한동안 남는다.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.body_iterator.aclose()  # type: ignore[attr-defined]


async def event_stream(broker: EventBroker) -> AsyncIterator[bytes]:
    """이벤트가 오면 바로 보내고, `ping_interval`(15초)마다 `: ping`을 보낸다.

    휴대폰이 끊기면 다음 쓰기(늦어도 다음 ping)에서 끝나고 구독이 풀린다.
    """
    loop = asyncio.get_running_loop()
    with broker.subscribe() as sub:
        next_ping = loop.time() + broker.ping_interval
        while True:
            wait = next_ping - loop.time()
            if wait <= 0:
                next_ping = loop.time() + broker.ping_interval
                yield PING
                continue
            try:
                event = await sub.get(wait)
            except TimeoutError:
                continue
            if event is None:  # 서버 종료 · 너무 밀려서 끊김
                return
            yield event.encode()


@router.get(
    "/events",
    summary="SSE 스트림. event = photo.status | photo.values | case.updated | merge.status | export.done",
    response_class=EventStreamResponse,
    responses={200: {"description": "text/event-stream", "content": {"text/event-stream": {"schema": _EVENT_SCHEMA}}}},
)
async def events(broker: EventBroker = Depends(get_event_broker), db: Session = Depends(get_db)) -> EventStreamResponse:
    # 인증 의존성(쿠키 확인, #2)이 같은 요청 세션을 쓴다. 스트림은 몇 시간씩 열려 있으므로 여기서 닫아
    # DB 연결(풀)과 SQLite 읽기 트랜잭션(WAL 체크포인트를 막음)을 붙잡지 않게 한다.
    db.close()
    return EventStreamResponse(
        event_stream(broker),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
