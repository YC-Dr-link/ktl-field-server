"""현장 서버 메인 모듈 — 휴대폰(:8765) · 관리(127.0.0.1:8766) 두 FastAPI 앱."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
import uvicorn

from app.api import admin_api_router, mobile_api_router
from app.models import Base, LOOPBACK_HOSTS, ServerSettings, engine, load_settings
from app.services import ensure_storage_dirs


class LoopbackOnlyMiddleware(BaseHTTPMiddleware):
    """REQ-B1-01 방어 기작: 관리 API는 루프백(127.0.0.1/::1) 외의 접근을 차단한다."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        client_host = request.client.host if request.client else ""
        host_header = request.headers.get("host", "").split(":")[0].lower()

        if client_host not in LOOPBACK_HOSTS or (host_header and host_header not in LOOPBACK_HOSTS):
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


def create_admin_app(settings: ServerSettings | None = None) -> FastAPI:
    """관리 전용 FastAPI 앱(127.0.0.1:8766)을 생성한다."""
    cfg = settings or load_settings()
    admin = FastAPI(
        title="KTL Field Server - Admin API",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    admin.add_middleware(LoopbackOnlyMiddleware)
    admin.include_router(admin_api_router)

    admin_static = cfg.static_dir / "admin"
    if admin_static.is_dir():
        admin.mount("/", StaticFiles(directory=str(admin_static), html=True), name="admin-static")
    return admin


@asynccontextmanager
async def _mobile_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """서버 시작 시 DB/보관소를 초기화하고, 필요 시 관리 API(127.0.0.1:8766)를 함께 기동한다."""
    cfg = load_settings()
    ensure_storage_dirs(cfg.storage_dir)
    Base.metadata.create_all(bind=engine)

    admin_server: uvicorn.Server | None = None
    admin_task: asyncio.Task[None] | None = None

    if os.getenv("KTL_ADMIN_SIDECAR", "1") == "1":
        admin_config = uvicorn.Config(
            admin_app,
            host=cfg.admin_host,
            port=cfg.admin_port,
            log_level="info",
        )
        admin_server = uvicorn.Server(admin_config)
        admin_task = asyncio.create_task(admin_server.serve())

    try:
        yield
    finally:
        if admin_server is not None and admin_task is not None:
            admin_server.should_exit = True
            await admin_task


def create_mobile_app(settings: ServerSettings | None = None) -> FastAPI:
    """휴대폰 전용 FastAPI 앱(:8765)을 생성한다."""
    cfg = settings or load_settings()
    mobile = FastAPI(
        title="KTL Field Server - Mobile API",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=_mobile_lifespan,
    )
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
    Base.metadata.create_all(bind=engine)

    mobile_server = uvicorn.Server(uvicorn.Config(mobile_app, host=cfg.mobile_host, port=cfg.mobile_port))
    admin_server = uvicorn.Server(uvicorn.Config(admin_app, host=cfg.admin_host, port=cfg.admin_port))
    await asyncio.gather(mobile_server.serve(), admin_server.serve())


if __name__ == "__main__":
    asyncio.run(serve_both())
