"""REQ-B1-10 기반: 단말·시험자 테이블 마이그레이션과 공통 에러 형식(02 §1.1)."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
import pytest
from sqlalchemy import inspect

from app.api.errors import ApiError, register_error_handlers
from app.models import BASE_DIR, create_db_engine


def test_migration_creates_auth_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("KTL_DATABASE_URL", db_url)
    command.upgrade(Config(str(BASE_DIR / "alembic.ini")), "head")

    engine = create_db_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert {"testers", "clients", "pair_codes", "login_sessions"} <= tables
    assert "failed_attempts" in {c["name"] for c in inspect(engine).get_columns("pair_codes")}
    engine.dispose()


class _Body(BaseModel):
    pin: str = Field(pattern=r"^\d{4,6}$")


def test_error_format() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/locked")
    def locked(body: _Body) -> None:
        raise ApiError(423, "PIN_LOCKED", "잠겼습니다.", {"retry_after": 600})

    client = TestClient(app)
    res = client.post("/locked", json={"pin": "1234"})
    assert res.status_code == 423
    assert res.json() == {"error": {"code": "PIN_LOCKED", "message": "잠겼습니다.", "detail": {"retry_after": 600}}}

    res = client.post("/locked", json={"pin": "12"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION"
    assert set(res.json()["error"]["detail"]["fields"]) == {"pin"}
    assert client.get("/nope").json()["error"]["code"] == "NOT_FOUND"
