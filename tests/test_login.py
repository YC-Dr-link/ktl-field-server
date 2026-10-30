"""REQ-B1-10 시험자 PIN 로그인: ktl_session 쿠키 · PIN 5회 잠금 · 관리 API(시험자)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
import re

from fastapi.testclient import TestClient

from app.services import auth as auth_service

ISO_WITH_OFFSET = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
A = "/api/admin/v1"
M = "/api/v1"
Clients = tuple[TestClient, TestClient]


def _login(mobile: TestClient, tid: str, pin: str) -> int:
    return mobile.post(f"{M}/sessions", json={"tester_id": tid, "pin": pin}).status_code


def test_unpaired_client_gets_401(clients: Clients) -> None:
    mobile, _ = clients
    for method, path in [("POST", "/sessions"), ("GET", "/sessions/me"), ("DELETE", "/sessions")]:
        res = mobile.request(method, f"{M}{path}", json={"tester_id": "ts_1", "pin": "1234"})
        assert res.status_code == 401, path
        assert res.json()["error"]["code"] == "CLIENT_UNKNOWN"


def test_login_me_logout(clients: Clients, pair: Callable[..., dict], add_tester: Callable[..., str]) -> None:
    mobile, _ = clients
    client = pair()
    tid = add_tester()

    assert mobile.get(f"{M}/testers").json() == {"items": [{"id": tid, "name": "시험자B"}]}
    assert mobile.get(f"{M}/sessions/me").json()["error"]["code"] == "SESSION_REQUIRED"

    res = mobile.post(f"{M}/sessions", json={"tester_id": tid, "pin": "1234"})
    assert res.status_code == 200
    assert res.json()["tester"] == {"id": tid, "name": "시험자B"}
    assert ISO_WITH_OFFSET.match(res.json()["expires_at"])
    cookie = res.headers["set-cookie"]
    assert cookie.startswith("ktl_session=") and "Max-Age=43200" in cookie and "HttpOnly" in cookie

    me = mobile.get(f"{M}/sessions/me").json()
    assert me["client"]["client_id"] == client["client_id"]
    assert ISO_WITH_OFFSET.match(me["client"]["last_seen_at"])
    assert me["tester"] == {"id": tid, "name": "시험자B"}

    assert mobile.delete(f"{M}/sessions").status_code == 204
    assert mobile.get(f"{M}/sessions/me").json()["error"]["code"] == "SESSION_REQUIRED"


def test_pin_locks_after_5_failures(clients: Clients, pair: Callable[..., dict], add_tester: Callable[..., str]) -> None:
    mobile, admin = clients
    pair()
    tid = add_tester()

    for remaining in (4, 3, 2, 1):
        res = mobile.post(f"{M}/sessions", json={"tester_id": tid, "pin": "0000"})
        assert res.status_code == 401
        assert res.json()["error"]["detail"] == {"remaining": remaining}

    res = mobile.post(f"{M}/sessions", json={"tester_id": tid, "pin": "0000"})
    assert res.status_code == 423
    err = res.json()["error"]
    assert err["code"] == "PIN_LOCKED"
    assert 590 <= err["detail"]["retry_after"] <= 600

    assert _login(mobile, tid, "1234") == 423  # 잠긴 동안은 맞는 PIN도 거부
    assert admin.get(f"{A}/testers").json()["items"][0]["locked"] is True

    res = admin.patch(f"{A}/testers/{tid}", json={"unlock": True})  # 관리 화면에서 잠금 해제
    assert res.json() == {"id": tid, "name": "시험자B", "locked": False, "active": True}
    assert _login(mobile, tid, "1234") == 200


def test_pin_lock_expires_after_10_minutes(
    clients: Clients,
    pair: Callable[..., dict],
    add_tester: Callable[..., str],
    shift_clock: Callable[[timedelta], None],
) -> None:
    mobile, _ = clients
    pair()
    tid = add_tester()
    for _ in range(5):
        _login(mobile, tid, "0000")
    shift_clock(timedelta(minutes=10, seconds=1))
    assert _login(mobile, tid, "1234") == 200


def test_parallel_wrong_pins_still_lock(
    clients: Clients, pair: Callable[..., dict], add_tester: Callable[..., str], parallel: Callable
) -> None:
    """동시에 틀린 PIN을 보내도 PIN 확인은 잠금 전 최대 5번 (나머지는 423)."""
    mobile, admin = clients
    pair()
    tid = add_tester()
    codes = parallel(lambda: _login(mobile, tid, "0000"), 12)
    assert codes.count(401) <= 4 and codes.count(423) >= 8
    assert _login(mobile, tid, "1234") == 423
    assert admin.get(f"{A}/testers").json()["items"][0]["locked"] is True


def test_revoked_client_cannot_use_session(
    clients: Clients, pair: Callable[..., dict], add_tester: Callable[..., str]
) -> None:
    mobile, admin = clients
    client = pair()
    tid = add_tester()
    _login(mobile, tid, "1234")
    admin.delete(f"{A}/clients/{client['client_id']}")
    for method, path in [("GET", "/sessions/me"), ("POST", "/sessions")]:
        res = mobile.request(method, f"{M}{path}", json={"tester_id": tid, "pin": "1234"})
        assert res.status_code == 403, path
        assert res.json()["error"]["code"] == "CLIENT_REVOKED"


def test_admin_tester_management(clients: Clients, pair: Callable[..., dict], add_tester: Callable[..., str]) -> None:
    mobile, admin = clients
    pair()
    tid = add_tester(pin="1234")
    assert admin.post(f"{A}/testers", json={"name": "시험자C", "pin": "12"}).status_code == 400

    res = admin.patch(f"{A}/testers/{tid}", json={"pin": "567890", "name": "시험자B2"})
    assert res.status_code == 200 and res.json()["name"] == "시험자B2"
    assert _login(mobile, tid, "1234") == 401
    assert _login(mobile, tid, "567890") == 200

    # 비활성 시험자: 휴대폰 목록에서 빠지고, 로그인돼 있던 세션도 끊기며 다시 활성화해도 살아나지 않는다
    admin.patch(f"{A}/testers/{tid}", json={"active": False})
    assert mobile.get(f"{M}/testers").json() == {"items": []}
    assert mobile.get(f"{M}/sessions/me").json()["error"]["code"] == "SESSION_REQUIRED"
    admin.patch(f"{A}/testers/{tid}", json={"active": True})
    assert mobile.get(f"{M}/sessions/me").json()["error"]["code"] == "SESSION_REQUIRED"

    huge = "9" * 20  # SQLite INTEGER 범위 밖도 500이 아니라 404/401
    assert admin.patch(f"{A}/testers/ts_999", json={"unlock": True}).status_code == 404
    assert admin.patch(f"{A}/testers/ts_{huge}", json={"unlock": True}).status_code == 404
    assert _login(mobile, f"ts_{huge}", "1234") == 401


def test_pin_reset_ends_existing_sessions(
    clients: Clients, pair: Callable[..., dict], add_tester: Callable[..., str]
) -> None:
    mobile, admin = clients
    pair()
    tid = add_tester()
    _login(mobile, tid, "1234")
    admin.patch(f"{A}/testers/{tid}", json={"pin": "5678"})
    assert mobile.get(f"{M}/sessions/me").json()["error"]["code"] == "SESSION_REQUIRED"


def test_openapi_has_session_cookie_scheme(clients: Clients) -> None:
    mobile, _ = clients
    schemes = mobile.get("/openapi.json").json()["components"]["securitySchemes"]
    assert schemes["session"] == {"type": "apiKey", "in": "cookie", "name": "ktl_session"}


def test_pin_is_hashed() -> None:
    stored = auth_service.hash_pin("1234")
    assert "1234" not in stored
    assert auth_service.verify_pin("1234", stored) and not auth_service.verify_pin("4321", stored)
