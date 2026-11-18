"""REQ-B1-11 작업 로그 해시 체인 — GET /audit (02 §2.5)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.test_photos import ISO_WITH_OFFSET, NEW_CASE, _new_case, env  # noqa: F401  (env는 fixture)
from tests.test_values import _db, _patch, _read_photo

ADMIN = "/api/admin/v1"
ENTRY_KEYS = {"seq", "at", "actor", "action", "target", "detail", "prev_hash", "hash"}


def _audit(admin, **params) -> dict:
    res = admin.get(f"{ADMIN}/audit", params=params)
    assert res.status_code == 200
    return res.json()


def test_changes_are_logged_in_a_valid_chain(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    case_id, photo_id = _read_photo(mobile, tmp_path)
    changed = [{"key": "reading_no", "value": "01 of 003"}, {"key": "flow", "value": "1.020", "reason": "소수점"}]
    assert _patch(mobile, photo_id, changed, confirm=True).status_code == 200

    body = _audit(admin)
    assert body["chain_ok"] is True
    items = body["items"]
    # env fixture: 단말 등록 · 시험자 추가 → 그 뒤 건 생성 · 업로드 · 값 수정 · 확정
    assert [i["action"] for i in items] == [
        "client.pair", "tester.create", "case.create", "photo.upload", "value.edit", "value.confirm",
    ]
    for i in items:
        assert set(i) == ENTRY_KEYS
        assert ISO_WITH_OFFSET.match(i["at"])
    assert [i["seq"] for i in items] == sorted(i["seq"] for i in items)
    assert all(cur["prev_hash"] == prev["hash"] for prev, cur in zip(items, items[1:]))
    edit = items[4]
    assert edit["target"] == photo_id and edit["actor"].startswith("ts_")
    assert edit["detail"] == {"key": "flow", "old": "1.002", "new": "1.020", "reason": "소수점"}
    assert items[2]["target"] == case_id


def test_case_filter_keeps_whole_chain_check(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    first, second = _new_case(mobile), _new_case(mobile)
    body = _audit(admin, case_id=second)
    assert body["chain_ok"] is True
    assert [(i["action"], i["target"]) for i in body["items"]] == [("case.create", second)]
    assert first != second


def test_tampered_middle_row_breaks_chain(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    for _ in range(3):
        _new_case(mobile)
    with _db(tmp_path) as conn:
        seqs = [r[0] for r in conn.execute("SELECT seq FROM audit_log ORDER BY seq")]
        conn.execute("UPDATE audit_log SET actor = 'someone' WHERE seq = ?", (seqs[len(seqs) // 2],))
    assert _audit(admin)["chain_ok"] is False


def test_tampered_middle_row_with_recomputed_hash_breaks_chain(env, tmp_path: Path) -> None:
    """행을 고치고 그 행의 hash까지 다시 계산해도 다음 행의 prev_hash와 안 맞는다."""
    from app.models.audit import AuditLog
    from app.services.audit import entry_hash

    mobile, admin, _ = env
    for _ in range(3):
        _new_case(mobile)
    with _db(tmp_path) as conn:
        seq, at, actor, action, target, case_pk, prev = conn.execute(
            "SELECT seq, at, actor, action, target, case_id, prev_hash FROM audit_log ORDER BY seq LIMIT 1 OFFSET 2"
        ).fetchone()
        forged = AuditLog(seq=seq, at=at, actor=actor, action=action, target=target, case_id=case_pk,
                          detail={"receipt_no": "26-000999-99"}, prev_hash=prev)
        conn.execute(
            "UPDATE audit_log SET detail = ?, hash = ? WHERE seq = ?",
            ('{"receipt_no": "26-000999-99"}', entry_hash(forged), seq),
        )
    assert _audit(admin)["chain_ok"] is False


def test_deleted_middle_row_breaks_chain(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    for _ in range(3):
        _new_case(mobile)
    with _db(tmp_path) as conn:
        conn.execute("DELETE FROM audit_log WHERE seq = (SELECT seq FROM audit_log ORDER BY seq LIMIT 1 OFFSET 2)")
    assert _audit(admin)["chain_ok"] is False


def test_concurrent_changes_do_not_fork_chain(env) -> None:
    mobile, admin, _ = env
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda _: mobile.post("/api/v1/cases", json=NEW_CASE).status_code, range(16)))
    assert codes == [201] * 16
    body = _audit(admin)
    assert body["chain_ok"] is True
    assert sum(i["action"] == "case.create" for i in body["items"]) == 16


def test_failed_change_leaves_no_log_and_pin_is_not_logged(env) -> None:
    mobile, admin, _ = env
    before = len(_audit(admin)["items"])
    assert mobile.post("/api/v1/cases", json={**NEW_CASE, "receipt_no": "bad"}).status_code == 400
    tester_id = admin.get(f"{ADMIN}/testers").json()["items"][0]["id"]
    assert admin.patch(f"{ADMIN}/testers/{tester_id}", json={"pin": "987654"}).status_code == 200

    items = _audit(admin)["items"]
    assert len(items) == before + 1
    assert items[-1]["action"] == "tester.update" and items[-1]["detail"] == {"pin": "reset"}
    assert "987654" not in str(items)
