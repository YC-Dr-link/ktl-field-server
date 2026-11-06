"""REQ-B1-07 실시간 알림 SSE (`GET /events`) — 이벤트 형식 · 판독 완료 1초 안 · 15초 ping · 끊김 · 종료.

TestClient는 응답이 끝날 때까지 모아서 돌려주므로 끝나지 않는 SSE를 못 읽는다. 그래서 ASGI 앱을 직접 부르는
작은 클라이언트(`SseClient`)로 읽는다. 가짜 모듈 판독은 경로에 'slow' · 'fail'이 있으면 반응하므로
테스트 이름(tmp_path 폴더 이름)에 그 단어를 쓰지 않는다.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
import json
from pathlib import Path
import signal
import threading
import time
from typing import Any

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import uvicorn

from app.main import create_admin_app, create_mobile_app
from app.models import BASE_DIR, load_settings
from app.services.events import PING, EventBroker, install_uvicorn_exit_hook, read_event_listener
from app.worker import ReadJob, ReadOutcome, ReadQueue

try:  # 휴대폰 로그인(#2)이 병합된 뒤에는 /events도 쿠키 인증을 받는다
    from app.api.security import require_session
except ImportError:  # pragma: no cover - #2 병합 전
    require_session = None

CATEGORIES_YAML = Path(__file__).parent / "fixtures" / "synthetic" / "categories.yaml"
MOBILE_EVENTS = "/api/v1/events"
ADMIN_EVENTS = "/api/admin/v1/events"
NEW_CASE = {"receipt_no": "26-000123-01", "category": "sampling", "unit_count": 1, "options": {"flowmeter_type": "defender"}}


class SseClient:
    """ASGI 앱에 SSE 요청 하나를 열어 두고 받은 조각을 이벤트(dict)로 나눠 준다."""

    def __init__(self, app: FastAPI, path: str, host: str = "testserver", cookie: str = "") -> None:
        self.app, self.path, self.host, self.cookie = app, path, host, cookie
        self.start: dict[str, Any] = {}
        self.frames: asyncio.Queue[str | None] = asyncio.Queue()
        self.disconnected = False
        self._started = asyncio.Event()
        self._buf = ""
        self.task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> SseClient:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": self.path,
            "raw_path": self.path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", self.host.encode()), (b"accept", b"text/event-stream")]
            + ([(b"cookie", self.cookie.encode())] if self.cookie else []),
            "client": ("127.0.0.1", 50000),
            "server": (self.host, 80),
        }
        self.task = asyncio.create_task(self.app(scope, self._receive, self._send))
        await asyncio.wait_for(self._started.wait(), 5)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.disconnected = True
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def _receive(self) -> dict[str, Any]:
        await asyncio.Event().wait()  # spec 2.4: 끊김은 send 실패로 알린다
        return {"type": "http.disconnect"}

    async def _send(self, message: dict[str, Any]) -> None:
        if self.disconnected:
            raise OSError("client disconnected")  # uvicorn: ClientDisconnected(OSError)
        if message["type"] == "http.response.start":
            self.start = message
            self._started.set()
        elif message["type"] == "http.response.body":
            self._buf += message.get("body", b"").decode()
            while "\n\n" in self._buf:
                frame, self._buf = self._buf.split("\n\n", 1)
                await self.frames.put(frame)
            if not message.get("more_body", False):
                await self.frames.put(None)  # 응답 끝

    @property
    def headers(self) -> dict[str, str]:
        return {k.decode(): v.decode() for k, v in self.start.get("headers", [])}

    async def next_frame(self, timeout: float = 5) -> str | None:
        return await asyncio.wait_for(self.frames.get(), timeout)

    async def next_event(self, timeout: float = 5) -> tuple[str, dict[str, Any]]:
        """ping은 건너뛰고 다음 이벤트 (event 이름, data)."""
        deadline = time.monotonic() + timeout
        while True:
            frame = await self.next_frame(max(0.01, deadline - time.monotonic()))
            assert frame is not None, "이벤트 전에 스트림이 끝남"
            if frame.startswith(":"):
                continue
            fields = dict(line.split(": ", 1) for line in frame.split("\n"))
            return fields["event"], json.loads(fields["data"])

    async def ended(self, timeout: float = 5) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.next_frame(deadline - time.monotonic()) is None:
                return True
        return False


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("KTL_ADMIN_SIDECAR", "0")
    monkeypatch.setenv("KTL_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("KTL_DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("KTL_CATEGORIES_PATH", str(CATEGORIES_YAML))
    command.upgrade(Config(str(BASE_DIR / "alembic.ini")), "head")
    return load_settings()


@pytest.fixture()
def apps(settings: Any) -> Iterator[tuple[FastAPI, FastAPI, EventBroker]]:
    """휴대폰 · 관리 앱이 알림 배달부를 공유한다. 로그인 검사는 건너뛴다 (로그인은 test_phone_events_need_login)."""
    broker = EventBroker(ping_interval=0.3)
    mobile = create_mobile_app(settings, events=broker)
    if require_session is not None:
        mobile.dependency_overrides[require_session] = lambda: None
    yield mobile, create_admin_app(settings, events=broker), broker
    broker.close()


def test_stream_headers_and_event_format(apps: tuple[FastAPI, FastAPI, EventBroker]) -> None:
    mobile, _, broker = apps

    async def scenario() -> None:
        async with SseClient(mobile, MOBILE_EVENTS) as sse:
            assert sse.start["status"] == 200
            assert sse.headers["content-type"].startswith("text/event-stream")
            assert sse.headers["cache-control"] == "no-cache"
            assert broker.subscriber_count == 1

            data = {"photo_id": "ph_301", "case_id": "case_12", "unit_index": 1, "item_key": "prec_1", "status": "done"}
            broker.publish("photo.status", data)
            frame = await sse.next_frame()
            assert frame == 'event: photo.status\ndata: {"photo_id":"ph_301","case_id":"case_12","unit_index":1,"item_key":"prec_1","status":"done"}'
        await asyncio.sleep(0)
        assert broker.subscriber_count == 0

    asyncio.run(scenario())


def test_ping_on_interval(apps: tuple[FastAPI, FastAPI, EventBroker]) -> None:
    mobile, _, broker = apps
    assert EventBroker().ping_interval == 15.0  # 계약: 15초마다 `: ping`

    async def scenario() -> None:
        async with SseClient(mobile, MOBILE_EVENTS) as sse:
            began = time.monotonic()
            assert await sse.next_frame() == PING.decode().strip()
            first = time.monotonic()
            broker.publish("case.updated", {"case_id": "case_1"})  # 이벤트가 있어도 ping 주기는 그대로
            assert (await sse.next_event())[0] == "case.updated"
            assert await sse.next_frame() == ": ping"
            second = time.monotonic()
        assert 0.2 <= first - began <= 1.0
        assert 0.2 <= second - first <= 1.0

    asyncio.run(scenario())


def test_read_done_reaches_screen_within_1s(
    apps: tuple[FastAPI, FastAPI, EventBroker], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """가짜 Reader 판독 → photo.status(reading · done) + photo.values. 판독 완료 알림 후 1초 안에 스트림에 나온다."""
    monkeypatch.setenv("KTL_MODULES", "fake")
    mobile, admin, broker = apps
    photo = tmp_path / "ph_7.jpg"
    photo.write_bytes(b"synthetic photo")
    done_at: list[float] = []

    def mark_done(_job: ReadJob, outcome: ReadOutcome) -> None:  # DB 저장 리스너 자리 (SSE보다 먼저)
        if outcome.status == "done":
            done_at.append(time.monotonic())

    q = ReadQueue(workers=1, timeout=5, preload=["flowmeter"])
    q.subscribe(mark_done)
    q.subscribe(read_event_listener(broker))
    q.start()

    async def scenario() -> None:
        async with SseClient(mobile, MOBILE_EVENTS) as phone, SseClient(admin, ADMIN_EVENTS, "127.0.0.1") as pc:
            q.submit(ReadJob("ph_7", str(photo), "prec_1", "flowmeter", ("flow",), case_id="case_3", unit_index=2))
            base = {"photo_id": "ph_7", "case_id": "case_3", "unit_index": 2, "item_key": "prec_1"}
            for sse in (phone, pc):
                assert await sse.next_event(30) == ("photo.status", {**base, "status": "reading"})
                assert await sse.next_event(30) == ("photo.status", {**base, "status": "done"})
                if sse is phone:
                    received = time.monotonic()
                assert await sse.next_event() == ("photo.values", {"photo_id": "ph_7", "case_id": "case_3"})
            assert received - done_at[0] < 1.0

    try:
        asyncio.run(scenario())
    finally:
        q.stop()


def test_failed_read_sends_status_only(apps: tuple[FastAPI, FastAPI, EventBroker]) -> None:
    mobile, _, broker = apps
    listener = read_event_listener(broker)
    job = ReadJob("ph_9", "x.jpg", "cert", "cert", case_id="case_1", unit_index=1)

    async def scenario() -> None:
        async with SseClient(mobile, MOBILE_EVENTS) as sse:
            await asyncio.to_thread(listener, job, ReadOutcome("ph_9", "failed", error="화면을 찾지 못함"))
            broker.publish("case.updated", {"case_id": "case_1"})  # 다음 이벤트로 photo.values가 없음을 확인
            assert (await sse.next_event())[1]["status"] == "failed"
            assert (await sse.next_event())[0] == "case.updated"

    asyncio.run(scenario())


def test_case_created_event_on_phone_and_admin_streams(apps: tuple[FastAPI, FastAPI, EventBroker]) -> None:
    mobile, admin, _ = apps

    async def scenario() -> None:
        async with SseClient(mobile, MOBILE_EVENTS) as phone, SseClient(admin, ADMIN_EVENTS, "127.0.0.1") as pc:
            with TestClient(mobile) as http:
                res = await asyncio.to_thread(http.post, "/api/v1/cases", json=NEW_CASE)
            assert res.status_code == 201
            for sse in (phone, pc):
                assert await sse.next_event() == ("case.updated", {"case_id": res.json()["id"]})

    asyncio.run(scenario())


@pytest.mark.skipif(require_session is None, reason="휴대폰 로그인(#2) 병합 후")
def test_phone_events_need_login(settings: Any) -> None:
    """쿠키 없으면 401로 구독하지 않는다. 로그인 후 열린 스트림은 DB 연결을 붙잡지 않는다 (몇 시간씩 열려 있음)."""
    broker = EventBroker()
    mobile, admin = create_mobile_app(settings, events=broker), create_admin_app(settings, events=broker)
    with TestClient(mobile) as phone, TestClient(admin, client=("127.0.0.1", 50000), headers={"Host": "127.0.0.1:8766"}) as pc:
        code = pc.post("/api/admin/v1/pair-codes").json()["code"]
        assert phone.post("/api/v1/clients/pair", json={"pair_code": code, "device_name": "현장폰-1"}).status_code == 200
        tester = pc.post("/api/admin/v1/testers", json={"name": "시험자B", "pin": "1234"}).json()["id"]
        assert phone.post("/api/v1/sessions", json={"tester_id": tester, "pin": "1234"}).status_code == 200
        cookie = "; ".join(f"{k}={v}" for k, v in phone.cookies.items())
    pool = mobile.state.session_factory.kw["bind"].pool

    async def scenario() -> None:
        async with SseClient(mobile, MOBILE_EVENTS) as anonymous:
            assert anonymous.start["status"] == 401
            assert broker.subscriber_count == 0
        async with SseClient(mobile, MOBILE_EVENTS, cookie=cookie) as sse:
            assert sse.start["status"] == 200
            assert broker.subscriber_count == 1
            assert pool.checkedout() == 0
            broker.publish("case.updated", {"case_id": "case_1"})
            assert await sse.next_event() == ("case.updated", {"case_id": "case_1"})

    asyncio.run(scenario())
    broker.close()


def test_admin_events_blocked_from_other_devices(apps: tuple[FastAPI, FastAPI, EventBroker]) -> None:
    _, admin, broker = apps

    async def scenario() -> None:
        async with SseClient(admin, ADMIN_EVENTS, "192.168.8.10") as sse:
            assert sse.start["status"] == 403
        assert broker.subscriber_count == 0

    asyncio.run(scenario())


def test_disconnected_phone_is_unsubscribed(apps: tuple[FastAPI, FastAPI, EventBroker]) -> None:
    mobile, _, broker = apps

    async def scenario() -> None:
        sse = SseClient(mobile, MOBILE_EVENTS)
        await sse.__aenter__()
        assert broker.subscriber_count == 1
        sse.disconnected = True  # 다음 쓰기(늦어도 다음 ping)에서 끝나야 한다
        assert sse.task is not None
        await asyncio.wait_for(asyncio.gather(sse.task, return_exceptions=True), 3)
        assert broker.subscriber_count == 0

    asyncio.run(scenario())


def test_lagging_client_is_cut_others_keep_going() -> None:
    broker = EventBroker(max_pending=2)

    async def scenario() -> None:
        with broker.subscribe() as lagging, broker.subscribe() as fine:
            for i in range(3):
                broker.publish("case.updated", {"case_id": f"case_{i}"})
                assert (await fine.get(1)).data == {"case_id": f"case_{i}"}  # type: ignore[union-attr]
            assert await lagging.get(1) is None  # 넘쳐서 끊김 → EventSource가 다시 연결
        assert broker.subscriber_count == 0

    asyncio.run(scenario())


def test_publish_from_worker_threads_is_safe() -> None:
    broker = EventBroker()

    async def scenario() -> None:
        with broker.subscribe() as sub:
            threads = [
                threading.Thread(target=lambda n=n: [broker.publish("x", {"n": n, "i": i}) for i in range(20)])
                for n in range(5)
            ]
            for t in threads:
                t.start()
            got = [await sub.get(5) for _ in range(100)]
            for t in threads:
                t.join()
        assert len({(e.data["n"], e.data["i"]) for e in got if e is not None}) == 100

    asyncio.run(scenario())


def test_server_shutdown_signal_ends_open_streams(apps: tuple[FastAPI, FastAPI, EventBroker]) -> None:
    """Ctrl+C 때 열린 SSE가 uvicorn 종료를 붙잡지 않는다 (uvicorn은 응답이 끝나길 기다린 뒤 lifespan을 끝냄)."""
    mobile, admin, _ = apps
    hooked = uvicorn.Server.handle_exit
    install_uvicorn_exit_hook()  # 앱을 만들 때 이미 걸었다. 다시 불러도 겹쳐 감싸지 않는다
    assert uvicorn.Server.handle_exit is hooked and getattr(hooked, "_ktl_closes_sse", False)
    server = uvicorn.Server(uvicorn.Config(mobile))

    async def scenario() -> None:
        async with SseClient(mobile, MOBILE_EVENTS) as phone, SseClient(admin, ADMIN_EVENTS, "127.0.0.1") as pc:
            server.handle_exit(signal.SIGINT, None)
            assert server.should_exit
            assert await phone.ended(2) and await pc.ended(2)
        async with SseClient(mobile, MOBILE_EVENTS) as late:  # 종료 중 새로 붙은 연결도 바로 끝
            assert await late.ended(2)

    asyncio.run(scenario())


def test_openapi_documents_events_on_both_apps(apps: tuple[FastAPI, FastAPI, EventBroker]) -> None:
    mobile, admin, _ = apps
    for app, path in ((mobile, MOBILE_EVENTS), (admin, ADMIN_EVENTS)):
        op = app.openapi()["paths"][path]["get"]
        assert op["tags"] == ["events"]
        content = op["responses"]["200"]["content"]
        assert list(content) == ["text/event-stream"]
        props = content["text/event-stream"]["schema"]["properties"]
        assert set(props) == {"photo_id", "case_id", "unit_index", "item_key", "id", "status"}
