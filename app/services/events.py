"""실시간 알림 (REQ-B1-07, 02 §2.4) — 서버 안에서 생긴 일을 열려 있는 SSE 연결들에 나눠 준다.

- `publish()`는 어느 스레드에서 불러도 된다 (판독 감독 스레드 · 동기 라우터의 스레드 풀 · 이벤트 루프).
  연결마다 자기 이벤트 루프로 `call_soon_threadsafe` 전달하므로 판독 완료 → 화면까지 기다림이 없다.
- 연결마다 쌓아 둘 수 있는 개수에 한도가 있다. 느린 연결이 넘치면 그 연결만 끊는다
  → `EventSource`가 다시 붙고, 화면은 `GET`으로 최신 상태를 다시 받는다. 다른 연결 · 서버는 영향 없음.
- 한 프로세스 안에서만 나눈다. uvicorn `--workers`는 1이어야 한다 (판독 큐도 같은 전제).
- 서버 종료(Ctrl+C) 때 열린 SSE 연결이 종료를 붙잡지 않도록 uvicorn 종료 신호에 맞춰 스트림을 닫는다.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import json
import logging
import threading
from typing import TYPE_CHECKING, Any
import weakref

if TYPE_CHECKING:
    from app.worker import ReadJob, ReadOutcome

logger = logging.getLogger(__name__)

PING_INTERVAL_SECONDS = 15.0
MAX_PENDING_EVENTS = 256
PING = b": ping\n\n"

PHOTO_STATUS = "photo.status"
PHOTO_VALUES = "photo.values"
CASE_UPDATED = "case.updated"
MERGE_STATUS = "merge.status"
EXPORT_DONE = "export.done"


@dataclass(frozen=True)
class Event:
    """SSE 한 건. data는 계약 `components/schemas/Event` 모양의 dict."""

    name: str
    data: dict[str, Any]

    def encode(self) -> bytes:
        payload = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))
        return f"event: {self.name}\ndata: {payload}\n\n".encode()


class Subscription:
    """SSE 연결 하나. 만든 이벤트 루프 안에서만 `get()`한다."""

    def __init__(self, loop: asyncio.AbstractEventLoop, max_pending: int) -> None:
        self.loop = loop
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue(max_pending)
        self.closed = False

    async def get(self, timeout: float) -> Event | None:
        """다음 이벤트. 연결이 닫혔으면 None, timeout 안에 없으면 TimeoutError."""
        if self.closed:
            return None
        item = await asyncio.wait_for(self._queue.get(), timeout)
        return None if self.closed else item

    # 아래는 self.loop 위에서만 불린다
    def _deliver(self, event: Event) -> None:
        if self.closed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("SSE 연결이 이벤트를 %d건 넘게 못 받아 끊습니다 (다시 연결하면 최신 상태를 받음)", self._queue.maxsize)
            self._close()

    def _close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._queue.put_nowait(None)  # 기다리는 get()을 깨운다
        except asyncio.QueueFull:
            pass  # 큐에 이벤트가 남아 있으니 get()이 곧 깨어나 closed를 본다


_brokers: weakref.WeakSet[EventBroker] = weakref.WeakSet()
_brokers_lock = threading.Lock()


class EventBroker:
    """휴대폰 앱 · 관리 앱이 함께 쓰는 알림 배달부 (판독 큐처럼 앱 사이에 공유)."""

    def __init__(self, ping_interval: float = PING_INTERVAL_SECONDS, max_pending: int = MAX_PENDING_EVENTS) -> None:
        self.ping_interval = ping_interval
        self.max_pending = max_pending
        self._subs: set[Subscription] = set()
        self._lock = threading.Lock()
        self._closed = False
        with _brokers_lock:
            _brokers.add(self)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    @contextmanager
    def subscribe(self) -> Iterator[Subscription]:
        """이벤트 루프 안에서 부른다. 블록을 벗어나면 구독이 풀린다."""
        sub = Subscription(asyncio.get_running_loop(), self.max_pending)
        with self._lock:
            if self._closed:
                sub._close()
            else:
                self._subs.add(sub)
        try:
            yield sub
        finally:
            with self._lock:
                self._subs.discard(sub)

    def publish(self, name: str, data: dict[str, Any]) -> None:
        """이벤트를 보낸다. 어느 스레드에서 불러도 되고, 받는 쪽 문제로 예외를 내지 않는다."""
        event = Event(name, data)
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            self._call(sub, sub._deliver, event)

    def close(self) -> None:
        """열린 스트림을 모두 끝낸다 (서버 종료). 이후 새 연결은 바로 끝난다."""
        with self._lock:
            self._closed = True
            subs = list(self._subs)
        for sub in subs:
            self._call(sub, sub._close)

    def _call(self, sub: Subscription, fn: Callable[..., None], *args: Any) -> None:
        try:
            sub.loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:  # 그 연결의 이벤트 루프가 이미 닫힘
            with self._lock:
                self._subs.discard(sub)


def close_all_brokers() -> None:
    with _brokers_lock:
        brokers = list(_brokers)
    for broker in brokers:
        broker.close()


_exit_hook_lock = threading.Lock()


def install_uvicorn_exit_hook() -> None:
    """uvicorn 종료 신호(Ctrl+C 등)를 받으면 SSE 스트림을 닫는다. 여러 번 불러도 한 번만 건다.

    uvicorn은 응답이 끝나기를 기다린 뒤에야 lifespan 종료를 부르므로, 끝나지 않는 SSE 응답이 있으면
    lifespan에서는 닫을 수 없다. 그래서 신호를 받는 `Server.handle_exit`에 건다
    (`uvicorn app.main:app` 한 줄 실행 · serve_both · 관리 앱 sidecar 모두 해당).
    """
    try:
        import uvicorn
    except ImportError:  # pragma: no cover - uvicorn은 필수 의존성
        return
    with _exit_hook_lock:
        original = uvicorn.Server.handle_exit
        if getattr(original, "_ktl_closes_sse", False):
            return

        def handle_exit(self: Any, sig: int, frame: Any) -> None:
            original(self, sig, frame)
            # 신호 처리기는 이벤트 루프 스레드에서 아무 때나 끼어든다. 그 스레드가 배달부 잠금을 쥔 순간이면
            # 여기서 잠금을 기다리다 영영 멈추므로, 닫기는 다른 스레드에 맡긴다 (close()는 스레드 안전).
            threading.Thread(target=close_all_brokers, name="ktl-sse-close", daemon=True).start()

        handle_exit._ktl_closes_sse = True  # type: ignore[attr-defined]
        uvicorn.Server.handle_exit = handle_exit  # type: ignore[method-assign]


def photo_status_data(
    photo_id: str, case_id: str | None, unit_index: int | None, item_key: str, status: str
) -> dict[str, Any]:
    return {"photo_id": photo_id, "case_id": case_id, "unit_index": unit_index, "item_key": item_key, "status": status}


def read_event_listener(broker: EventBroker) -> Callable[[ReadJob, ReadOutcome], None]:
    """판독 큐 알림 → SSE. reading · done · failed마다 `photo.status`, 판독이 끝나 값이 생기면 `photo.values`.

    판독 결과를 DB에 저장하는 리스너(#4)보다 **나중에** 구독해야 한다: 화면은 `photo.values`를 받자마자
    `GET /photos/{id}`로 다시 받으므로, 그때 DB에 값이 있어야 한다 (큐는 구독 순서대로 부른다).
    """

    def listener(job: ReadJob, outcome: ReadOutcome) -> None:
        if job.case_id is None or job.unit_index is None:  # 계약상 문자열 · 정수. null을 보내면 화면이 거른다
            logger.warning("case_id · unit_index 없는 판독 작업이라 SSE 알림을 보내지 않음: %s", job.photo_id)
            return
        broker.publish(
            PHOTO_STATUS, photo_status_data(outcome.photo_id, job.case_id, job.unit_index, job.item_key, outcome.status)
        )
        if outcome.status == "done":
            broker.publish(PHOTO_VALUES, {"photo_id": outcome.photo_id, "case_id": job.case_id})

    return listener
