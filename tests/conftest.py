"""단말·로그인 테스트 공용 fixture — 임시 DB로 휴대폰·관리 앱을 함께 띄운다."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest

from app.main import create_admin_app, create_mobile_app
from app.models import BASE_DIR, load_settings, local_now
from app.services import auth as auth_service

ADMIN_HEADERS = {"Host": "127.0.0.1:8766"}


@pytest.fixture()
def clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, TestClient]]:
    """(휴대폰 앱, 관리 앱) 테스트 클라이언트."""
    monkeypatch.setenv("KTL_ADMIN_SIDECAR", "0")
    monkeypatch.setenv("KTL_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("KTL_DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("KTL_PUBLIC_HOST", "192.0.2.10")
    command.upgrade(Config(str(BASE_DIR / "alembic.ini")), "head")

    settings = load_settings()
    with TestClient(create_mobile_app(settings)) as mobile, TestClient(
        create_admin_app(settings), client=("127.0.0.1", 50000), headers=ADMIN_HEADERS
    ) as admin:
        yield mobile, admin


@pytest.fixture()
def shift_clock(monkeypatch: pytest.MonkeyPatch) -> Callable[[timedelta], None]:
    """서비스가 보는 현재 시각을 delta만큼 뒤로 민다."""

    def shift(delta: timedelta) -> None:
        later = local_now() + delta
        monkeypatch.setattr(auth_service, "local_now", lambda: later)

    return shift


@pytest.fixture()
def pair(clients: tuple[TestClient, TestClient]) -> Callable[..., dict]:
    """관리 API로 코드를 받아 휴대폰을 등록하고 응답(Client)을 돌려준다."""
    mobile, admin = clients

    def do_pair(name: str = "현장폰-1") -> dict:
        code = admin.post("/api/admin/v1/pair-codes").json()["code"]
        res = mobile.post("/api/v1/clients/pair", json={"pair_code": code, "device_name": name})
        assert res.status_code == 200
        return res.json()

    return do_pair


@pytest.fixture()
def parallel() -> Callable[[Callable[[], int], int], list[int]]:
    """fn을 n개 스레드로 동시에 돌려 결과(상태 코드)를 정렬해 돌려준다."""

    def run(fn: Callable[[], int], n: int) -> list[int]:
        with ThreadPoolExecutor(max_workers=n) as pool:
            return sorted(pool.map(lambda _: fn(), range(n)))

    return run


@pytest.fixture()
def add_tester(clients: tuple[TestClient, TestClient]) -> Callable[..., str]:
    """관리 API로 시험자를 추가하고 ID(`ts_N`)를 돌려준다."""
    _, admin = clients

    def do_add(name: str = "시험자B", pin: str = "1234") -> str:
        res = admin.post("/api/admin/v1/testers", json={"name": name, "pin": pin})
        assert res.status_code == 201
        return res.json()["id"]

    return do_add
