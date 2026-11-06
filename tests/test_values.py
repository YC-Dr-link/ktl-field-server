"""REQ-B1-06 값 수정 · 확정 · 수정 이력 — PATCH /photos/{id}/values.

판독 결과 저장(#4 ↔ #5 연결)은 아직 없어서, 업로드한 사진에 가짜 판독 값을 DB로 직접 넣고 시험한다.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import time

import pytest

from app.api.schemas import ValueChange, ValuesPatch
from app.services import values as value_service
from tests.test_photos import ISO_WITH_OFFSET, _new_case, _upload, env  # noqa: F401  (env는 fixture)

# 가짜 판독 값 (02 §2.3 예시 모양, 실제 데이터 아님)
READ_VALUES = [
    {"key": "flow", "label": "유량", "value": "1.002", "unit": "L/min", "confidence": 0.93,
     "bbox": [412, 220, 690, 310], "status": "auto"},
    {"key": "reading_no", "label": "측정 번호", "value": "01 of 003", "unit": None, "confidence": 0.71,
     "bbox": [400, 120, 560, 160], "status": "needs_check"},
]


def _db(tmp_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(tmp_path / "test.db")


def _read_photo(client, tmp_path: Path, values=READ_VALUES, status: str = "done") -> tuple[str, str]:
    """사진을 올리고 판독이 끝난 것처럼 값을 넣는다 → (case_id, photo_id)."""
    case_id = _new_case(client)
    photo_id = _upload(client, case_id).json()["id"]
    with _db(tmp_path) as conn:
        conn.execute(
            'UPDATE photos SET status = ?, "values" = ? WHERE id = ?',
            (status, json.dumps(values, ensure_ascii=False), int(photo_id.removeprefix("ph_"))),
        )
    return case_id, photo_id


def _patch(client, photo_id: str, values: list[dict], confirm: bool = False, prefix: str = "/api/v1"):
    return client.patch(f"{prefix}/photos/{photo_id}/values", json={"values": values, "confirm": confirm})


def _history(tmp_path: Path) -> list[tuple]:
    with _db(tmp_path) as conn:
        return conn.execute(
            "SELECT photo_id, case_id, value_key, old_value, new_value, reason, edited_by, edited_at"
            " FROM value_edits ORDER BY id"
        ).fetchall()


def _stored_values(tmp_path: Path, photo_id: str) -> list[dict]:
    with _db(tmp_path) as conn:
        raw = conn.execute('SELECT "values" FROM photos WHERE id = ?', (int(photo_id[3:]),)).fetchone()[0]
    return json.loads(raw)


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_edit_without_reason_400(env, tmp_path: Path, reason) -> None:
    mobile, _, _ = env
    _, photo_id = _read_photo(mobile, tmp_path)
    change = {"key": "flow", "value": "1.020"}
    if reason is not None:
        change["reason"] = reason
    res = _patch(mobile, photo_id, [change])
    assert res.status_code == 400
    err = res.json()["error"]
    assert err["code"] == "VALIDATION"
    assert "values.0.reason" in err["detail"]["fields"]
    assert _stored_values(tmp_path, photo_id) == READ_VALUES
    assert _history(tmp_path) == []


def test_confirm_without_needs_check_value_400(env, tmp_path: Path) -> None:
    mobile, _, _ = env
    _, photo_id = _read_photo(mobile, tmp_path)
    res = _patch(mobile, photo_id, [{"key": "flow", "value": "1.002"}], confirm=True)
    assert res.status_code == 400
    assert "values[reading_no]" in res.json()["error"]["detail"]["fields"]
    assert _stored_values(tmp_path, photo_id) == READ_VALUES  # 하나도 확정되지 않는다


def test_edit_and_confirm_saves_history(env, tmp_path: Path) -> None:
    mobile, _, _ = env
    case_id, photo_id = _read_photo(mobile, tmp_path)
    res = _patch(
        mobile,
        photo_id,
        [{"key": "reading_no", "value": "01 of 003"},
         {"key": "flow", "value": "1.020", "reason": "OCR이 소수점 위치를 잘못 읽음"}],
        confirm=True,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == photo_id
    values = {v["key"]: v for v in body["values"]}
    assert [v["key"] for v in body["values"]] == ["flow", "reading_no"]  # 판독 순서 유지
    assert (values["flow"]["value"], values["flow"]["status"]) == ("1.020", "edited")
    assert (values["reading_no"]["value"], values["reading_no"]["status"]) == ("01 of 003", "confirmed")
    for v in values.values():
        assert v["confirmed_by"] == "ts_1"
        assert ISO_WITH_OFFSET.match(v["confirmed_at"])
    assert values["flow"]["bbox"] == [412, 220, 690, 310]  # 판독 정보는 그대로

    [row] = _history(tmp_path)
    assert row[:7] == (int(photo_id[3:]), int(case_id[5:]), "flow", "1.002", "1.020",
                       "OCR이 소수점 위치를 잘못 읽음", "ts_1")
    assert row[7]  # 시각

    # 칸 상태도 확정으로 보인다
    slot = mobile.get(f"/api/v1/cases/{case_id}").json()["units"][0]["items"][2]
    assert (slot["photo_id"], slot["status"]) == (photo_id, "confirmed")


def test_edit_first_then_confirm_marks_edited(env, tmp_path: Path) -> None:
    mobile, _, _ = env
    _, photo_id = _read_photo(mobile, tmp_path)
    res = _patch(mobile, photo_id, [{"key": "reading_no", "value": "02 of 003", "reason": "번호 오독"}])
    assert res.status_code == 200
    reading_no = next(v for v in res.json()["values"] if v["key"] == "reading_no")
    assert (reading_no["value"], reading_no["status"], reading_no["confirmed_by"]) == ("02 of 003", "needs_check", None)

    # 확정만 할 때는 사유 없이 — 앞서 고친 값이므로 edited
    res = _patch(mobile, photo_id, [{"key": "reading_no", "value": "02 of 003"}], confirm=True)
    assert res.status_code == 200
    statuses = {v["key"]: v["status"] for v in res.json()["values"]}
    assert statuses == {"flow": "auto", "reading_no": "edited"}  # 보내지 않은 auto 값은 그대로
    assert [r[3:5] for r in _history(tmp_path)] == [("01 of 003", "02 of 003")]


def test_confirmed_value_cannot_be_reconfirmed_or_edited_409(env, tmp_path: Path) -> None:
    mobile, _, _ = env
    _, photo_id = _read_photo(mobile, tmp_path)
    all_values = [{"key": "flow", "value": "1.002"}, {"key": "reading_no", "value": "01 of 003"}]
    assert _patch(mobile, photo_id, all_values, confirm=True).status_code == 200

    res = _patch(mobile, photo_id, all_values, confirm=True)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "ALREADY_CONFIRMED"
    res = _patch(mobile, photo_id, [{"key": "flow", "value": "9.999", "reason": "확정 후 수정"}])
    assert res.status_code == 409
    assert _history(tmp_path) == []
    # 바꾸지 않고 다시 보내기만 하면(확정 아님) 그대로 200
    assert _patch(mobile, photo_id, all_values).status_code == 200


@pytest.mark.parametrize(
    ("values", "confirm", "field"),
    [
        ([{"key": "nope", "value": "1"}], False, "values.0.key"),
        ([{"key": "flow", "value": "1.002"}, {"key": "flow", "value": "1.002"}], False, "values.1.key"),
        ([{"key": "flow", "value": "1" * 501, "reason": "길이"}], False, "values.0.value"),
        ([{"key": "flow", "value": "", "reason": "지움"}, {"key": "reading_no", "value": "01 of 003"}], True,
         "values.0.value"),
        ([{"key": "flow"}], False, "values.0.value"),
    ],
)
def test_bad_values_400(env, tmp_path: Path, values, confirm, field) -> None:
    mobile, _, _ = env
    _, photo_id = _read_photo(mobile, tmp_path)
    res = _patch(mobile, photo_id, values, confirm)
    assert res.status_code == 400
    assert field in res.json()["error"]["detail"]["fields"]


def test_completed_case_409(env, tmp_path: Path) -> None:
    mobile, _, _ = env
    _, photo_id = _read_photo(mobile, tmp_path)
    with _db(tmp_path) as conn:  # 건 완료 API는 다른 이슈 — DB로 직접 완료 처리
        conn.execute("UPDATE cases SET status = 'completed'")
    res = _patch(mobile, photo_id, [{"key": "flow", "value": "1.020", "reason": "수정"}])
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CASE_COMPLETED"


@pytest.mark.parametrize("status", ["queued", "reading", "failed"])
def test_photo_not_read_yet_400(env, tmp_path: Path, status: str) -> None:
    mobile, _, _ = env
    _, photo_id = _read_photo(mobile, tmp_path, status=status)
    res = _patch(mobile, photo_id, [{"key": "flow", "value": "1.002"}], confirm=True)
    assert res.status_code == 400
    assert "photo" in res.json()["error"]["detail"]["fields"]


@pytest.mark.parametrize("photo_id", ["ph_999", "ph_0", "301", "ph_abc"])
def test_unknown_photo_404(env, photo_id: str) -> None:
    mobile, _, _ = env
    res = _patch(mobile, photo_id, [])
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_retaken_photo_404(env, tmp_path: Path) -> None:
    mobile, _, _ = env
    case_id, photo_id = _read_photo(mobile, tmp_path)
    _upload(mobile, case_id)  # 같은 칸 다시 찍기 → 이전 사진 deleted
    assert _patch(mobile, photo_id, [{"key": "flow", "value": "1.002"}]).status_code == 404


def test_requires_login_and_admin_can_patch(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    _, photo_id = _read_photo(mobile, tmp_path)
    mobile.delete("/api/v1/sessions")
    res = _patch(mobile, photo_id, [{"key": "flow", "value": "1.020", "reason": "수정"}])
    assert res.status_code == 401

    res = _patch(admin, photo_id, [{"key": "flow", "value": "1.020", "reason": "관리 화면 수정"}],
                 prefix="/api/admin/v1")
    assert res.status_code == 200
    assert _history(tmp_path)[0][6] == "admin"


def test_concurrent_edits_do_not_lose_history(env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """같은 값을 동시에 고쳐도 이력의 이전 값이 이어진다 (읽고-고치고-쓰기 사이 끼어들기 없음)."""
    mobile, _, _ = env
    _, photo_id = _read_photo(mobile, tmp_path)
    real_now = value_service.local_now

    def slow_now():  # 읽은 뒤 쓰기 전에 다른 요청이 끼어들 틈을 일부러 만든다
        time.sleep(0.05)
        return real_now()

    monkeypatch.setattr(value_service, "local_now", slow_now)
    factory = mobile.app.state.session_factory

    def edit(n: int) -> None:
        with factory() as db:
            body = ValuesPatch(values=[ValueChange(key="flow", value=f"2.{n:03d}", reason=f"수정 {n}")])
            value_service.patch_values(db, photo_id, body, actor="ts_1")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(edit, range(8)))
    history = _history(tmp_path)
    assert len(history) == 8
    assert history[0][3] == "1.002"
    for prev, cur in zip(history, history[1:]):
        assert cur[3] == prev[4]  # 다음 이력의 이전 값 = 앞 이력의 새 값
    flow = next(v for v in _stored_values(tmp_path, photo_id) if v["key"] == "flow")
    assert flow["value"] == history[-1][4]


def test_openapi_documents_patch(env) -> None:
    mobile, _, _ = env
    spec = mobile.get("/openapi.json").json()
    op = spec["paths"]["/api/v1/photos/{photo_id}/values"]["patch"]
    assert {"200", "400", "409"} <= set(op["responses"])
    ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    schema = spec["components"]["schemas"][ref]
    assert schema["required"] == ["values"]
    assert set(schema["properties"]) == {"values", "confirm"}
    item = spec["components"]["schemas"]["ValueChange"]
    assert set(item["required"]) == {"key", "value"}
    assert set(item["properties"]) == {"key", "value", "reason"}


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"key": "flow", "value": "1.0\u000020", "reason": "제어 문자"}, "values.0.value"),
        ({"key": "flow", "value": "1.020", "reason": "사유\u0007"}, "values.0.reason"),
    ],
)
def test_control_characters_400(env, tmp_path: Path, change: dict, field: str) -> None:
    mobile, _, _ = env
    _, photo_id = _read_photo(mobile, tmp_path)
    res = _patch(mobile, photo_id, [change])
    assert res.status_code == 400
    assert field in res.json()["error"]["detail"]["fields"]
    assert _history(tmp_path) == []


def test_too_many_values_400(env, tmp_path: Path) -> None:
    mobile, _, _ = env
    _, photo_id = _read_photo(mobile, tmp_path)
    res = _patch(mobile, photo_id, [{"key": f"k{i}", "value": "1"} for i in range(101)])
    assert res.status_code == 400
    assert "values" in res.json()["error"]["detail"]["fields"]


def test_duplicate_stored_key_is_not_merged(env, tmp_path: Path) -> None:
    """모듈이 같은 key를 두 번 돌려줘도 다른 값을 고칠 때 섞이지 않고, 그 key는 고칠 수 없다."""
    mobile, _, _ = env
    dup = [READ_VALUES[0], {**READ_VALUES[0], "value": "9.999", "status": "auto"}, READ_VALUES[1]]
    _, photo_id = _read_photo(mobile, tmp_path, values=dup)
    res = _patch(mobile, photo_id, [{"key": "reading_no", "value": "02 of 003", "reason": "번호 오독"}])
    assert res.status_code == 200
    assert [v["value"] for v in res.json()["values"]] == ["1.002", "9.999", "02 of 003"]
    res = _patch(mobile, photo_id, [{"key": "flow", "value": "1.020", "reason": "수정"}])
    assert res.status_code == 400
    assert "values.0.key" in res.json()["error"]["detail"]["fields"]


def test_no_change_does_not_write(env, tmp_path: Path) -> None:
    mobile, _, _ = env
    case_id, photo_id = _read_photo(mobile, tmp_path)
    with _db(tmp_path) as conn:
        before = conn.execute("SELECT updated_at FROM cases").fetchone()[0]
    res = _patch(mobile, photo_id, [{"key": "flow", "value": "1.002"}])
    assert res.status_code == 200
    assert [v["status"] for v in res.json()["values"]] == ["auto", "needs_check"]
    with _db(tmp_path) as conn:
        assert conn.execute("SELECT updated_at FROM cases").fetchone()[0] == before
    assert _stored_values(tmp_path, photo_id) == READ_VALUES
