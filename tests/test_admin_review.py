"""REQ-B2-06 관리 API — review-queue · 완료 건 다시 열기 · 설정 (02 §2.5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_photos import ISO_WITH_OFFSET, _new_case, _upload, env  # noqa: F401  (env는 fixture)
from tests.test_values import READ_VALUES, _db, _patch, _read_photo

ADMIN = "/api/admin/v1"
DEFAULT_SETTINGS = {
    "export_encoding": "cp949",
    "photo_retention_days": None,
    "confidence_threshold": 0.8,
    "read_timeout_s": 10,
}


def _set_case_status(tmp_path: Path, case_id: str, status: str) -> None:
    with _db(tmp_path) as conn:
        conn.execute("UPDATE cases SET status = ? WHERE id = ?", (status, int(case_id.removeprefix("case_"))))


def _reopens(tmp_path: Path) -> list[tuple]:
    with _db(tmp_path) as conn:
        return conn.execute("SELECT case_id, reason, reopened_by, reopened_at FROM case_reopens ORDER BY id").fetchall()


# ── review-queue ──────────────────────────────────────────────


def test_review_queue_lists_needs_check_values(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    assert admin.get(f"{ADMIN}/review-queue").json() == {"items": []}

    case_id, photo_id = _read_photo(mobile, tmp_path)
    res = admin.get(f"{ADMIN}/review-queue")
    assert res.status_code == 200
    assert res.json() == {
        "items": [
            {
                "case_id": case_id,
                "receipt_no": "26-000123-01",
                "photo_id": photo_id,
                "unit_index": 1,
                "item_key": "prec_1",
                "value": {**READ_VALUES[1], "confirmed_by": None, "confirmed_at": None},
            }
        ]
    }


def test_review_queue_drops_confirmed_completed_and_retaken(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    # 확인하고 확정한 값은 빠진다
    _, confirmed_photo = _read_photo(mobile, tmp_path)
    assert _patch(mobile, confirmed_photo, [{"key": "reading_no", "value": "01 of 003"}], confirm=True).status_code == 200
    # 완료된 건은 빠진다
    completed_case, _ = _read_photo(mobile, tmp_path)
    _set_case_status(tmp_path, completed_case, "completed")
    # 다시 찍은 칸은 새 사진(아직 판독 전)만 남아 빠진다
    retaken_case, _ = _read_photo(mobile, tmp_path)
    assert _upload(mobile, retaken_case).status_code == 202
    # 판독 중인 사진은 빠진다
    _read_photo(mobile, tmp_path, status="reading")

    assert admin.get(f"{ADMIN}/review-queue").json() == {"items": []}


def test_review_queue_orders_by_case_then_unit(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    first, _ = _read_photo(mobile, tmp_path)
    second, _ = _read_photo(mobile, tmp_path)
    case_ids = [item["case_id"] for item in admin.get(f"{ADMIN}/review-queue").json()["items"]]
    assert case_ids == [first, second]


# ── 완료 건 다시 열기 ──────────────────────────────────────────


def test_reopen_completed_case(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    case_id, photo_id = _read_photo(mobile, tmp_path)
    _set_case_status(tmp_path, case_id, "completed")
    assert _patch(mobile, photo_id, [{"key": "reading_no", "value": "01 of 003"}], confirm=True).status_code == 409

    res = admin.post(f"{ADMIN}/cases/{case_id}/reopen", json={"reason": "  값 정정 요청  "})
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == case_id and body["status"] == "open"
    assert ISO_WITH_OFFSET.match(body["updated_at"])
    assert body["units"][0]["items"]

    [(case_pk, reason, by, at)] = _reopens(tmp_path)
    assert (case_pk, reason, by) == (int(case_id[5:]), "값 정정 요청", "admin")
    assert at
    # 다시 열린 건은 수정할 수 있다
    assert _patch(mobile, photo_id, [{"key": "reading_no", "value": "01 of 003"}], confirm=True).status_code == 200


@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 501}, {"reason": "a\x00b"}])
def test_reopen_needs_reason(env, tmp_path: Path, body: dict) -> None:
    mobile, admin, _ = env
    case_id = _new_case(mobile)
    _set_case_status(tmp_path, case_id, "completed")
    res = admin.post(f"{ADMIN}/cases/{case_id}/reopen", json=body)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION"
    assert "reason" in res.json()["error"]["detail"]["fields"]
    assert _reopens(tmp_path) == []


def test_reopen_open_case_400_and_unknown_404(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    case_id = _new_case(mobile)
    res = admin.post(f"{ADMIN}/cases/{case_id}/reopen", json={"reason": "사유"})
    assert res.status_code == 400
    assert "status" in res.json()["error"]["detail"]["fields"]
    for missing in ("case_999", "abc"):
        res = admin.post(f"{ADMIN}/cases/{missing}/reopen", json={"reason": "사유"})
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "NOT_FOUND"
    assert _reopens(tmp_path) == []


# ── 설정 ────────────────────────────────────────────────────


def test_settings_defaults_and_partial_update(env) -> None:
    _, admin, _ = env
    assert admin.get(f"{ADMIN}/settings").json() == DEFAULT_SETTINGS

    res = admin.put(f"{ADMIN}/settings", json={"export_encoding": "utf-8", "photo_retention_days": 30})
    assert res.status_code == 200
    expected = {**DEFAULT_SETTINGS, "export_encoding": "utf-8", "photo_retention_days": 30}
    assert res.json() == expected
    assert admin.get(f"{ADMIN}/settings").json() == expected

    # 보낸 칸만 바뀌고, null은 '자동 삭제 안 함'으로 되돌린다
    res = admin.put(f"{ADMIN}/settings", json={"photo_retention_days": None, "confidence_threshold": 0.75})
    assert res.json() == {**expected, "photo_retention_days": None, "confidence_threshold": 0.75}


@pytest.mark.parametrize(
    "body",
    [
        {"export_encoding": "euc-kr"},
        {"confidence_threshold": 1.5},
        {"confidence_threshold": -0.1},
        {"photo_retention_days": 0},
        {"read_timeout_s": 0},
        {"read_timeout_s": "열"},
    ],
)
def test_settings_rejects_bad_values(env, body: dict) -> None:
    _, admin, _ = env
    res = admin.put(f"{ADMIN}/settings", json=body)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION"
    assert set(res.json()["error"]["detail"]["fields"]) == set(body)
    assert admin.get(f"{ADMIN}/settings").json() == DEFAULT_SETTINGS


def test_settings_survive_broken_stored_value(env, tmp_path: Path) -> None:
    _, admin, _ = env
    admin.put(f"{ADMIN}/settings", json={"export_encoding": "utf-8"})
    with _db(tmp_path) as conn:
        conn.execute(
            "INSERT INTO system_settings (key, value, updated_at) VALUES ('read_timeout_s', '\"x\"', '2026-11-08')"
        )
    assert admin.get(f"{ADMIN}/settings").json() == {**DEFAULT_SETTINGS, "export_encoding": "utf-8"}


# ── 앱 분리 · 계약 모양 ──────────────────────────────────────


def test_admin_only_paths_not_on_phone_app(env) -> None:
    mobile, _, _ = env
    phone_paths = mobile.get("/openapi.json").json()["paths"]
    for method, path in (("get", "/review-queue"), ("post", "/cases/{case_id}/reopen"), ("get", "/settings")):
        assert f"/api/v1{path}" not in phone_paths
        # 라우터에 없으면 404, 정적 파일 마운트가 받으면 POST는 405
        url = f"/api/v1{path}".replace("{case_id}", "case_1")
        assert getattr(mobile, method)(url).status_code in (404, 405)


def test_openapi_matches_contract(env) -> None:
    _, admin, _ = env
    spec = admin.get("/openapi.json").json()
    paths, schemas = spec["paths"], spec["components"]["schemas"]

    review = paths[f"{ADMIN}/review-queue"]["get"]
    assert review["tags"] == ["admin"]
    queue_ref = review["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    item_ref = schemas[queue_ref.rsplit("/", 1)[1]]["properties"]["items"]["items"]["$ref"]
    item = schemas[item_ref.rsplit("/", 1)[1]]["properties"]
    assert set(item) == {"case_id", "receipt_no", "photo_id", "unit_index", "item_key", "value"}
    assert item["value"]["$ref"] == "#/components/schemas/Value"

    reopen = paths[f"{ADMIN}/cases/{{case_id}}/reopen"]["post"]
    body_ref = reopen["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert schemas[body_ref.rsplit("/", 1)[1]]["required"] == ["reason"]
    assert reopen["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/Case"

    settings_ref = "#/components/schemas/Settings"
    assert paths[f"{ADMIN}/settings"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == settings_ref
    put = paths[f"{ADMIN}/settings"]["put"]
    assert put["requestBody"]["required"] is True
    assert put["requestBody"]["content"]["application/json"]["schema"]["$ref"] == settings_ref
    settings = schemas["Settings"]["properties"]
    assert set(settings) == set(DEFAULT_SETTINGS)
    assert settings["export_encoding"]["enum"] == ["cp949", "utf-8"]
    assert {k: settings[k].get("default") for k in ("export_encoding", "confidence_threshold", "read_timeout_s")} == {
        "export_encoding": "cp949",
        "confidence_threshold": 0.8,
        "read_timeout_s": 10,
    }
