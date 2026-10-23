"""REQ-B1-01 실행 뼈대 검증 테스트 (두 포트 분리, 관리 API 외부 차단, /docs, DB/Alembic)."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from app.main import admin_app, mobile_app
from app.models import ServerSettings, create_db_engine, load_settings


def test_two_ports_and_admin_loopback_config() -> None:
    """휴대폰(:8765)과 관리(127.0.0.1:8766) 두 포트 설정 및 비루프백 바인딩 차단 확인."""
    cfg = load_settings()
    assert cfg.mobile_host == "0.0.0.0"
    assert cfg.mobile_port == 8765
    assert cfg.admin_host == "127.0.0.1"
    assert cfg.admin_port == 8766

    with pytest.raises(ValueError, match="REQ-B1-01"):
        ServerSettings(admin_host="0.0.0.0")


def test_mobile_and_admin_docs_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """휴대폰 앱과 관리 앱 모두 /docs 및 /openapi.json이 정상적으로 열리는지 확인."""
    monkeypatch.setenv("KTL_ADMIN_SIDECAR", "0")
    monkeypatch.setenv("KTL_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("KTL_DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")

    with TestClient(mobile_app) as mobile_client:
        docs_res = mobile_client.get("/docs")
        assert docs_res.status_code == 200
        assert "swagger-ui" in docs_res.text.lower()

        openapi_res = mobile_client.get("/openapi.json")
        assert openapi_res.status_code == 200
        assert "/api/v1/health" in openapi_res.json()["paths"]

        health_res = mobile_client.get("/api/v1/health")
        assert health_res.status_code == 200
        assert health_res.json()["app"] == "mobile"

    with TestClient(admin_app, client=("127.0.0.1", 50000)) as admin_client:
        docs_res = admin_client.get("/docs", headers={"Host": "127.0.0.1:8766"})
        assert docs_res.status_code == 200
        assert "swagger-ui" in docs_res.text.lower()

        openapi_res = admin_client.get("/openapi.json", headers={"Host": "127.0.0.1:8766"})
        assert openapi_res.status_code == 200
        assert "/api/admin/v1/health" in openapi_res.json()["paths"]

        health_res = admin_client.get("/api/admin/v1/health", headers={"Host": "127.0.0.1:8766"})
        assert health_res.status_code == 200
        assert health_res.json()["app"] == "admin"


def test_admin_api_blocks_external_access() -> None:
    """관리 API(127.0.0.1:8766)에 다른 기기(외부 IP 또는 외부 Host 헤더)로 접속 시 403 차단 확인."""
    with TestClient(admin_app, client=("192.168.1.50", 51234)) as external_client:
        res = external_client.get("/api/admin/v1/health", headers={"Host": "127.0.0.1:8766"})
        assert res.status_code == 403
        body = res.json()
        assert body["error"]["code"] == "CLIENT_REVOKED"

    with TestClient(admin_app, client=("127.0.0.1", 51234)) as spoofed_host_client:
        res = spoofed_host_client.get("/api/admin/v1/health", headers={"Host": "192.168.1.10:8766"})
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "CLIENT_REVOKED"


def test_sqlite_wal_and_alembic_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SQLite WAL 모드 활성화 및 Alembic 마이그레이션(upgrade) 동작 확인."""
    db_file = tmp_path / "test_ktl.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    monkeypatch.setenv("KTL_DATABASE_URL", db_url)

    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")

    test_engine = create_db_engine(db_url)
    with test_engine.connect() as conn:
        journal_mode = conn.execute(text("PRAGMA journal_mode;")).scalar()
        assert str(journal_mode).lower() == "wal"

        tables = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='system_settings';")
        ).scalar()
        assert tables == "system_settings"
    test_engine.dispose()
