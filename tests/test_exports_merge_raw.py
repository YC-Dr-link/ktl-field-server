"""REQ-B1-09 merge_raw 내보내기 — 가스 · 라돈 병합 결과 표를 xlsm RAW 시트 배치 그대로 (02 §2.5).

병합은 tests/selector_doubles.py 대역으로 돌린다 (가짜 값, 실제 데이터 아님).
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from app.services.exports import build_merge_table, encode
from app.api.errors import ApiError
from tests.test_merges import ADMIN, _file, _merge, _new_case, _wait, env  # noqa: F401  (env는 fixture)


def _done_merge(admin, case_id: str) -> dict:
    done = _wait(admin, _merge(admin, case_id, [_file(admin, case_id)]).json()["id"])
    assert done["status"] == "done", done
    return done


def _export(admin, case_id: str, **body):
    payload = {"kind": "merge_raw", "format": "csv", **body}
    return admin.post(f"{ADMIN}/cases/{case_id}/exports", json=payload)


def _download(admin, export_id: str) -> bytes:
    res = admin.get(f"{ADMIN}/exports/{export_id}/download")
    assert res.status_code == 200
    return res.content


def _expected(table: dict, sep: str) -> str:
    lines = [sep.join(table["columns"])]
    lines += [sep.join("" if v is None else str(v) for v in row) for row in table["rows"]]
    return "\r\n".join(lines) + "\r\n"


def test_merge_raw_csv_cp949_keeps_table_as_is(env, tmp_path: Path) -> None:
    admin, _ = env
    case_id = _new_case(admin)
    done = _done_merge(admin, case_id)

    res = _export(admin, case_id, merge_id=done["id"], encoding="cp949")
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["kind"] == "merge_raw" and body["status"] == "done"
    assert body["skipped_unconfirmed"] == 0
    assert body["file_name"].startswith("26-000200-01_gas_") and body["file_name"].endswith(".csv")
    assert body["preview"]["columns"] == done["table"]["columns"]
    assert len(body["preview"]["rows"]) == min(20, len(done["table"]["rows"]))

    assert _download(admin, body["id"]).decode("cp949") == _expected(done["table"], ",")
    with sqlite3.connect(tmp_path / "test.db") as conn:  # 어떤 병합을 내보냈는지 남는다
        merge_pk = conn.execute("SELECT merge_id FROM exports WHERE id = ?", (int(body["id"][3:]),)).fetchone()[0]
    assert merge_pk == int(done["id"][3:])


def test_merge_raw_txt_utf8_is_tab_separated(env) -> None:
    admin, _ = env
    case_id = _new_case(admin)
    done = _done_merge(admin, case_id)
    body = _export(admin, case_id, merge_id=done["id"], format="txt", encoding="utf-8").json()
    assert body["file_name"].endswith(".txt")
    assert _download(admin, body["id"]).decode("utf-8") == _expected(done["table"], "\t")


def test_merge_raw_blank_and_number_cells(env, monkeypatch: pytest.MonkeyPatch) -> None:
    admin, _ = env
    monkeypatch.setenv("KTL_TEST_SELECTOR_MODE", "odd")  # 표: [["ZDS", 1.0], ["SDS", None]]
    case_id = _new_case(admin)
    done = _done_merge(admin, case_id)
    body = _export(admin, case_id, merge_id=done["id"]).json()
    assert _download(admin, body["id"]).decode("cp949") == "code,v1\r\nZDS,1.0\r\nSDS,\r\n"
    assert body["preview"]["rows"] == [["ZDS", "1.0"], ["SDS", ""]]


def test_merge_raw_validation(env, monkeypatch: pytest.MonkeyPatch) -> None:
    admin, _ = env
    case_id = _new_case(admin)
    other_case = _new_case(admin)
    done = _done_merge(admin, other_case)

    def fields(**body) -> dict:
        res = _export(admin, case_id, **body)
        assert res.status_code == 400, res.text
        assert res.json()["error"]["code"] == "VALIDATION"
        return res.json()["error"]["detail"]["fields"]

    assert "merge_id" in fields()  # merge_id 없음
    assert "merge_id" in fields(merge_id=done["id"])  # 다른 건의 병합
    for bad in ("mg_999", "mg_0", "abc", ""):
        assert "merge_id" in fields(merge_id=bad)

    monkeypatch.setenv("KTL_TEST_SELECTOR_MODE", "raise")  # 실패한 병합은 내보내지 않는다
    failed = _wait(admin, _merge(admin, case_id, [_file(admin, case_id)]).json()["id"])
    assert failed["status"] == "failed"
    assert "merge_id" in fields(merge_id=failed["id"])

    # 사진 업무 분류는 merge_raw가 없다 · 병합 분류는 photo_values가 없다
    sampling = _new_case(admin, category="sampling")
    res = _export(admin, sampling, merge_id=done["id"])
    assert res.status_code == 400 and "kind" in res.json()["error"]["detail"]["fields"]
    res = admin.post(f"{ADMIN}/cases/{case_id}/exports", json={"kind": "photo_values", "format": "csv"})
    assert res.status_code == 400 and "kind" in res.json()["error"]["detail"]["fields"]


def test_cp949_unencodable_column_names_reported() -> None:
    table = build_merge_table({"columns": ["code", "농도😀"], "rows": [["ZDS", 1], ["SDS", "값😀"]]})
    assert table.rows == [["ZDS", "1"], ["SDS", "값😀"]]
    with pytest.raises(ApiError) as err:
        encode(table, "csv", "cp949")
    assert set(err.value.detail["fields"]) == {"columns.농도😀", "rows.2.농도😀"}
    assert encode(table, "csv", "utf-8").decode("utf-8").startswith("code,농도😀\r\n")


def test_nan_cell_is_blank() -> None:
    assert build_merge_table({"columns": ["v"], "rows": [[float("nan")], [0.5]]}).rows == [[""], ["0.5"]]
