"""현장 서버 메인 모듈 — 휴대폰(:8765) · 관리(127.0.0.1:8766) 두 FastAPI 앱."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
import uvicorn

from app.api import admin_api_router, mobile_api_router
from app.api.errors import register_error_handlers
from app.models import LOOPBACK_HOSTS, ServerSettings, create_session_factory, load_settings
from app.modules import load_categories, reader_names
from app.services import ensure_storage_dirs
from app.services.events import EventBroker, install_uvicorn_exit_hook, read_event_listener
from app.worker import ReadQueue

logger = logging.getLogger(__name__)
# 종료 신호 없이 should_exit만 세운 경우에도 열린 SSE가 종료를 무한정 붙잡지 않게 하는 상한
SHUTDOWN_GRACE_SECONDS = 5


class LoopbackOnlyMiddleware(BaseHTTPMiddleware):
    """REQ-B1-01 방어 기작: 관리 API는 루프백(127.0.0.1/::1) 외의 접근을 차단한다.

    1차 방어로 관리 서버는 127.0.0.1:8766 소켓에만 바인딩되어 타 기기의 TCP 접속이 OS 레벨에서 거부된다.
    2차 심층 방어로 프록시 오설정이나 ASGI 직접 호출 시에도 루프백 외 IP/Host 헤더를 403으로 차단한다.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        client_host = request.client.host if request.client else ""
        host_header = request.headers.get("host", "").split(":")[0].lower()

        if client_host not in LOOPBACK_HOSTS or (host_header and host_header not in LOOPBACK_HOSTS):
            # TODO(확인 필요): 02-API명세.md §1.1의 403 에러 코드는 CLIENT_REVOKED만 정의되어 있어 이를 사용함.
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "CLIENT_REVOKED",
                        "message": "관리 API는 로컬(127.0.0.1)에서만 접근할 수 있습니다.",
                        "detail": {"client_host": client_host},
                    }
                },
            )
        return await call_next(request)


def _configure_state(
    app: FastAPI, cfg: ServerSettings, *, default_actor: str, phone_only: bool, events: EventBroker | None
) -> None:
    """라우터가 의존성으로 꺼내 쓰는 앱별 설정·DB 세션 팩토리·SSE 알림 배달부를 둔다."""
    install_uvicorn_exit_hook()
    app.state.event_broker = events or EventBroker()
    app.state.settings = cfg
    app.state.session_factory = create_session_factory(cfg.database_url)
    app.state.default_actor = default_actor
    app.state.phone_only = phone_only


def create_read_queue() -> ReadQueue:
    """판독 작업 큐(REQ-B1-04). categories.yaml의 Reader들을 작업 프로세스에 미리 올린다."""
    try:
        preload = reader_names(load_categories())
    except Exception:  # noqa: BLE001 - 미리 올리기는 최적화일 뿐, 분류 파일 문제로 서버가 안 뜨면 안 된다
        logger.exception("categories.yaml을 읽지 못해 Reader를 미리 올리지 않습니다")
        preload = []
    return ReadQueue(preload=preload)


def create_admin_app(
    settings: ServerSettings | None = None, read_queue: ReadQueue | None = None, events: EventBroker | None = None
) -> FastAPI:
    """관리 전용 FastAPI 앱(127.0.0.1:8766)을 생성한다. read_queue · events는 휴대폰 앱과 같은 것을 공유한다."""
    cfg = settings or load_settings()
    admin = FastAPI(
        title="KTL Field Server - Admin API",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    _configure_state(admin, cfg, default_actor="admin", phone_only=False, events=events)
    register_error_handlers(admin)
    admin.state.read_queue = read_queue
    admin.add_middleware(LoopbackOnlyMiddleware)
    admin.include_router(admin_api_router)

    admin_static = cfg.static_dir / "admin"
    if admin_static.is_dir():
        admin.mount("/", StaticFiles(directory=str(admin_static), html=True), name="admin-static")
    return admin


def create_mobile_app(
    settings: ServerSettings | None = None, read_queue: ReadQueue | None = None, events: EventBroker | None = None
) -> FastAPI:
    """휴대폰 전용 FastAPI 앱(:8765)을 생성한다. read_queue를 주지 않으면 앱 수명 동안 큐를 직접 띄우고
    판독 알림을 이 앱의 SSE로 잇는다. read_queue를 주면 SSE 연결은 준 쪽(serve_both)이 한다."""
    cfg = settings or load_settings()
    broker = events or EventBroker()

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        ensure_storage_dirs(cfg.storage_dir)

        owned_queue = read_queue is None
        active_queue = read_queue or create_read_queue()
        if owned_queue:
            # TODO(#4): 판독 결과 DB 저장 리스너를 이것보다 먼저 subscribe 한다 (read_event_listener 설명 참고)
            active_queue.subscribe(read_event_listener(broker))
        _app.state.read_queue = active_queue
        active_queue.start()

        admin_server: uvicorn.Server | None = None
        admin_task: asyncio.Task[None] | None = None

        if os.getenv("KTL_ADMIN_SIDECAR", "1") == "1":
            admin_config = uvicorn.Config(
                create_admin_app(cfg, active_queue, broker),
                host=cfg.admin_host,
                port=cfg.admin_port,
                log_level="info",
                timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS,
            )
            admin_server = uvicorn.Server(admin_config)
            admin_task = asyncio.create_task(admin_server.serve())

        try:
            yield
        finally:
            if admin_server is not None and admin_task is not None:
                admin_server.should_exit = True
                await admin_task
            if owned_queue:
                await asyncio.to_thread(active_queue.stop)

    mobile = FastAPI(
        title="KTL Field Server - Mobile API",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=_lifespan,
    )
    # TODO(REQ-B1-10): 로그인 구현 후 created_by 등은 세션의 시험자 ID로 바뀐다 (app/api/deps.py get_actor)
    _configure_state(mobile, cfg, default_actor="anonymous", phone_only=True, events=broker)
    register_error_handlers(mobile)
    mobile.include_router(mobile_api_router)

    mobile_static = cfg.static_dir / "mobile"
    if mobile_static.is_dir():
        mobile.mount("/", StaticFiles(directory=str(mobile_static), html=True), name="mobile-static")
    return mobile


admin_app = create_admin_app()
mobile_app = create_mobile_app()
app = mobile_app


async def serve_both(settings: ServerSettings | None = None) -> None:
    """휴대폰(:8765)과 관리(127.0.0.1:8766) 두 서버를 단일 이벤트 루프에서 동시 실행한다."""
    cfg = settings or load_settings()
    os.environ["KTL_ADMIN_SIDECAR"] = "0"
    ensure_storage_dirs(cfg.storage_dir)

    events = EventBroker()
    read_queue = create_read_queue()
    # TODO(#4): 판독 결과 DB 저장 리스너를 이것보다 먼저 subscribe 한다
    read_queue.subscribe(read_event_listener(events))
    read_queue.start()
    mobile_server = uvicorn.Server(
        uvicorn.Config(
            create_mobile_app(cfg, read_queue, events),
            host=cfg.mobile_host,
            port=cfg.mobile_port,
            timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS,
        )
    )
    admin_server = uvicorn.Server(
        uvicorn.Config(
            create_admin_app(cfg, read_queue, events),
            host=cfg.admin_host,
            port=cfg.admin_port,
            timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS,
        )
    )
    try:
        await asyncio.gather(mobile_server.serve(), admin_server.serve())
    finally:
        await asyncio.to_thread(read_queue.stop)


if __name__ == "__main__":
    asyncio.run(serve_both())
