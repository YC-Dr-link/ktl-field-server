"""REQ-B1-10 단말 등록: 1회용 코드(5분 만료) · ktl_client 쿠키 · 미등록 401 · 해제 403 · 관리 API(등록 코드·단말)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.auth import PairCode
from app.services import auth as auth_service

ISO_WITH_OFFSET = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
A = "/api/admin/v1"
M = "/api/v1"
Clients = tuple[TestClient, TestClient]


def test_pair_code_issue_and_pair(clients: Clients) -> None:
    mobile, admin = clients
    res = admin.post(f"{A}/pair-codes")
    assert res.status_code == 201
    body = res.json()
    assert re.fullmatch(r"\d{6}", body["code"])
    assert ISO_WITH_OFFSET.match(body["expires_at"])
    assert body["qr_svg"].startswith("<svg")

    res = mobile.post(f"{M}/clients/pair", json={"pair_code": body["code"], "device_name": "현장폰-1"})
    assert res.status_code == 200
    out = res.json()
    assert re.fullmatch(r"cl_\d+", out["client_id"])
    assert out["device_name"] == "현장폰-1"
    assert ISO_WITH_OFFSET.match(out["paired_at"])
    cookie = res.headers["set-cookie"]
    assert cookie.startswith("ktl_client=")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Max-Age=7776000" in cookie

    # 1회용: 같은 코드로 다시 등록할 수 없다
    again = mobile.post(f"{M}/clients/pair", json={"pair_code": body["code"], "device_name": "현장폰-2"})
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "VALIDATION"

    listed = admin.get(f"{A}/clients").json()["items"]
    assert [c["client_id"] for c in listed] == [out["client_id"]]
    assert mobile.get(f"{M}/testers").json() == {"items": []}


def test_pair_code_expires_after_5_minutes(clients: Clients, shift_clock: Callable[[timedelta], None]) -> None:
    mobile, admin = clients
    code = admin.post(f"{A}/pair-codes").json()["code"]
    shift_clock(timedelta(minutes=5, seconds=1))
    res = mobile.post(f"{M}/clients/pair", json={"pair_code": code, "device_name": "현장폰-1"})
    assert res.status_code == 410
    assert res.json()["error"]["code"] == "PAIR_CODE_EXPIRED"


def test_pair_validation(clients: Clients) -> None:
    mobile, _ = clients
    res = mobile.post(f"{M}/clients/pair", json={"pair_code": "12ab", "device_name": ""})
    assert res.status_code == 400
    assert set(res.json()["error"]["detail"]["fields"]) == {"pair_code", "device_name"}


def test_unpaired_client_gets_401(clients: Clients) -> None:
    mobile, _ = clients
    res = mobile.get(f"{M}/testers")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "CLIENT_UNKNOWN"
    mobile.cookies.set("ktl_client", "forged-token")
    assert mobile.get(f"{M}/testers").json()["error"]["code"] == "CLIENT_UNKNOWN"


def test_revoked_client_gets_403(clients: Clients, pair: Callable[..., dict]) -> None:
    mobile, admin = clients
    client = pair()
    assert mobile.get(f"{M}/testers").status_code == 200

    assert admin.delete(f"{A}/clients/{client['client_id']}").status_code == 204
    res = mobile.get(f"{M}/testers")
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "CLIENT_REVOKED"
    assert admin.get(f"{A}/clients").json()["items"] == []
    assert admin.delete(f"{A}/clients/{client['client_id']}").status_code == 404
    huge = "9" * 20  # SQLite INTEGER 범위 밖도 500이 아니라 404
    assert admin.delete(f"{A}/clients/cl_{huge}").status_code == 404


def test_parallel_pair_with_same_code_only_one_wins(clients: Clients, parallel: Callable) -> None:
    mobile, admin = clients
    code = admin.post(f"{A}/pair-codes").json()["code"]
    body = {"pair_code": code, "device_name": "현장폰"}
    codes = parallel(lambda: mobile.post(f"{M}/clients/pair", json=body).status_code, 8)
    assert codes.count(200) == 1 and codes.count(400) == 7
    assert len(admin.get(f"{A}/clients").json()["items"]) == 1


def test_too_many_wrong_pair_codes_expire_live_codes(clients: Clients, pair: Callable[..., dict]) -> None:
    mobile, admin = clients
    code = admin.post(f"{A}/pair-codes").json()["code"]
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    for _ in range(auth_service.MAX_PAIR_FAILURES):
        assert mobile.post(f"{M}/clients/pair", json={"pair_code": wrong, "device_name": "x"}).status_code == 400
    res = mobile.post(f"{M}/clients/pair", json={"pair_code": code, "device_name": "현장폰-1"})
    assert res.status_code == 410
    pair()  # 새로 발급한 코드는 쓸 수 있다


def test_issuing_code_cleans_up_old_rows(
    clients: Clients, pair: Callable[..., dict], shift_clock: Callable[[timedelta], None]
) -> None:
    _, admin = clients
    pair()  # 다 쓴 코드 1개
    admin.post(f"{A}/pair-codes")  # 쓰지 않은 코드 1개 → 곧 만료
    shift_clock(timedelta(minutes=6))
    admin.post(f"{A}/pair-codes")
    db = admin.app.state.session_factory()
    try:
        assert len(db.scalars(select(PairCode)).all()) == 1
    finally:
        db.close()


def test_openapi_has_client_cookie_scheme(clients: Clients) -> None:
    mobile, _ = clients
    schemes = mobile.get("/openapi.json").json()["components"]["securitySchemes"]
    assert schemes["client"] == {"type": "apiKey", "in": "cookie", "name": "ktl_client"}


def test_token_hash_and_ids() -> None:
    assert auth_service.hash_token("abc") != "abc"
    assert auth_service.parse_id("cl_3", "cl") == 3
    assert auth_service.parse_id("ts_01", "ts") is None and auth_service.parse_id("cl_3", "ts") is None
