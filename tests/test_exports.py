"""REQ-B1-08 내보내기 photo_values — 기기 1대 = 1행, 칼럼 = categories.yaml export, 확정값만 · 미확정 수.

판독 결과 저장(#4 ↔ #5 연결)은 아직 없어서, 업로드한 사진에 가짜 판독 값을 DB로 직접 넣고 시험한다.
테스트용 export 칼럼 (tests/fixtures/synthetic/categories.yaml): [photo_first, cert.approval_no, prec_1.flow]
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3

from fastapi.testclient import TestClient

from tests.test_photos import _new_case, _upload, env  # noqa: F401  (env는 fixture)

ADMIN = "/api/admin/v1"
COLUMNS = ["photo_first", "cert.approval_no", "prec_1.flow"]
EXPORT_KEYS = {"id", "case_id", "kind", "format", "status", "file_name", "preview", "skipped_unconfirmed"}
FILE_NAME = re.compile(r"^26-000123-01_sampling_\d{8}-\d{6}\.(csv|txt)$")

# 가짜 판독 값 (실제 데이터 아님)
CERT_VALUES = [{"key": "approval_no", "label": "승인번호", "value": "가짜-0001", "confidence": 0.95, "status": "auto"}]
FLOW_VALUES = [
    {"key": "flow", "label": "유량", "value": "1.002", "unit": "L/min", "confidence": 0.93, "status": "auto"},
    {"key": "reading_no", "label": "측정 번호", "value": "01 of 003", "confidence": 0.71, "status": "needs_check"},
]


def _set_read(tmp_path: Path, photo_id: str, values: list[dict], status: str = "done") -> None:
    """판독이 끝난 것처럼 사진에 값을 넣는다."""
    with sqlite3.connect(tmp_path / "test.db") as conn:
        conn.execute(
            'UPDATE photos SET status = ?, "values" = ? WHERE id = ?',
            (status, json.dumps(values, ensure_ascii=False), int(photo_id.removeprefix("ph_"))),
        )


def _photo(client: TestClient, tmp_path: Path, case_id: str, unit: int, item: str, values: list[dict]) -> dict:
    photo = _upload(client, case_id, unit_index=unit, item_key=item).json()
    _set_read(tmp_path, photo["id"], values)
    return photo


def _confirm(client: TestClient, photo_id: str, values: list[dict]) -> None:
    res = client.patch(f"/api/v1/photos/{photo_id}/values", json={"values": values, "confirm": True})
    assert res.status_code == 200, res.text


def _export(admin: TestClient, case_id: str, **body):
    return admin.post(f"{ADMIN}/cases/{case_id}/exports", json={"kind": "photo_values", "format": "csv", **body})


def _download(admin: TestClient, export_id: str) -> bytes:
    res = admin.get(f"{ADMIN}/exports/{export_id}/download")
    assert res.status_code == 200
    return res.content


def _two_unit_case(mobile: TestClient, tmp_path: Path) -> tuple[str, dict, dict]:
    """기기 1: 필증 확정 · 유량 수정 후 확정 / 기기 2: 유량 자동 판독만 (미확정). → (case_id, 기기1 필증, 기기2 유량)"""
    case_id = _new_case(mobile)
    cert = _photo(mobile, tmp_path, case_id, 1, "cert", CERT_VALUES)
    _confirm(mobile, cert["id"], [{"key": "approval_no", "value": "가짜-0001"}])
    flow = _photo(mobile, tmp_path, case_id, 1, "prec_1", FLOW_VALUES)
    _confirm(
        mobile,
        flow["id"],
        [{"key": "flow", "value": "1.020", "reason": "소수점 위치 오독"}, {"key": "reading_no", "value": "01 of 003"}],
    )
    unit2 = _photo(mobile, tmp_path, case_id, 2, "prec_1", FLOW_VALUES)
    return case_id, cert, unit2


def test_csv_cp949_confirmed_values_only_one_row_per_unit(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    case_id, cert, unit2 = _two_unit_case(mobile, tmp_path)

    res = _export(admin, case_id)  # encoding 기본값 cp949
    assert res.status_code == 202
    body = res.json()
    assert set(body) == EXPORT_KEYS
    assert body["id"].startswith("ex_") and body["case_id"] == case_id
    assert (body["kind"], body["format"], body["status"]) == ("photo_values", "csv", "done")
    assert FILE_NAME.match(body["file_name"])
    rows = [
        [cert["stored_name"], "가짜-0001", "1.020"],  # 수정 후 확정(edited)한 값은 새 값
        [unit2["stored_name"], "", ""],  # 필증 사진 없음 · 유량은 자동 판독(미확정) → 빈 칸
    ]
    assert body["preview"] == {"columns": COLUMNS, "rows": rows}
    assert body["skipped_unconfirmed"] == 2

    raw = _download(admin, body["id"])
    expected = "photo_first,cert.approval_no,prec_1.flow\r\n" + "".join(",".join(r) + "\r\n" for r in rows)
    assert raw == expected.encode("cp949")
    assert admin.get(f"{ADMIN}/exports/{body['id']}").json() == body


def test_txt_utf8_is_tab_separated(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    case_id, cert, unit2 = _two_unit_case(mobile, tmp_path)
    body = _export(admin, case_id, format="txt", encoding="utf-8").json()
    assert body["file_name"].endswith(".txt")
    lines = _download(admin, body["id"]).decode("utf-8").split("\r\n")
    assert lines[0].split("\t") == COLUMNS
    assert lines[1].split("\t") == [cert["stored_name"], "가짜-0001", "1.020"]
    assert lines[2].split("\t") == [unit2["stored_name"], "", ""]


def test_needs_check_and_unread_values_are_skipped(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    case_id = _new_case(mobile)
    _photo(mobile, tmp_path, case_id, 1, "cert", [dict(CERT_VALUES[0], status="needs_check")])
    _upload(mobile, case_id, unit_index=1, item_key="prec_1")  # 아직 queued
    body = _export(admin, case_id).json()
    assert [r[1:] for r in body["preview"]["rows"]] == [["", ""], ["", ""]]
    assert body["preview"]["rows"][1][0] == ""  # 사진 없는 기기는 photo_first도 빈 칸
    assert body["skipped_unconfirmed"] == 4  # 2대 × 값 칼럼 2개


def test_retaken_photo_values_are_not_exported(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    case_id = _new_case(mobile)
    old = _photo(mobile, tmp_path, case_id, 1, "prec_1", FLOW_VALUES[:1])
    _confirm(mobile, old["id"], [{"key": "flow", "value": "1.002"}])
    new = _upload(mobile, case_id, unit_index=1, item_key="prec_1").json()  # 다시 찍기 → old는 deleted
    row = _export(admin, case_id).json()["preview"]["rows"][0]
    assert row == [new["stored_name"], "", ""]


def test_cp949_unencodable_value_400_and_nothing_written(env, tmp_path: Path) -> None:
    mobile, admin, storage_dir = env
    case_id = _new_case(mobile)
    _photo(mobile, tmp_path, case_id, 1, "cert", [dict(CERT_VALUES[0], value="é€\U0001f600", status="confirmed")])
    res = _export(admin, case_id, encoding="cp949")
    assert res.status_code == 400
    err = res.json()["error"]
    assert err["code"] == "VALIDATION"
    assert list(err["detail"]["fields"]) == ["rows.1.cert.approval_no"]
    assert not any((storage_dir / "exports").rglob("*.csv"))
    assert _export(admin, case_id, encoding="utf-8").status_code == 202  # utf-8이면 그대로 나간다


def test_same_second_exports_get_distinct_files(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    case_id, _, _ = _two_unit_case(mobile, tmp_path)
    first, second = _export(admin, case_id).json(), _export(admin, case_id).json()
    assert first["id"] != second["id"]
    assert _download(admin, first["id"]) == _download(admin, second["id"])


def test_unsupported_requests_400(env) -> None:
    mobile, admin, _ = env
    case_id = _new_case(mobile)
    for extra, field in [
        ({"format": "xlsx"}, "format"),
        ({"include_photos": True}, "include_photos"),
        ({"kind": "merge_raw"}, "kind"),
        ({"encoding": "euc-kr"}, "encoding"),
        ({"format": "pdf"}, "format"),
    ]:
        res = _export(admin, case_id, **extra)
        assert res.status_code == 400, extra
        assert field in res.json()["error"]["detail"]["fields"]

    gas = admin.post(f"{ADMIN}/cases", json={"receipt_no": "26-000999-01", "category": "gas", "unit_count": 1})
    res = _export(admin, gas.json()["id"])
    assert res.status_code == 400
    assert "kind" in res.json()["error"]["detail"]["fields"]


def test_not_found_and_admin_only(env) -> None:
    mobile, admin, _ = env
    case_id = _new_case(mobile)
    assert _export(admin, "case_999").json()["error"]["code"] == "NOT_FOUND"
    for export_id in ("ex_999", "ex_0", "5", "ex_abc"):
        assert admin.get(f"{ADMIN}/exports/{export_id}").status_code == 404
        assert admin.get(f"{ADMIN}/exports/{export_id}/download").status_code == 404
    # 내보내기는 관리 API 전용 — 휴대폰 API에는 경로가 없다 (정적 파일 마운트가 있으면 POST는 405)
    res = mobile.post(f"/api/v1/cases/{case_id}/exports", json={"kind": "photo_values", "format": "csv"})
    assert res.status_code in (404, 405)


def test_download_404_when_file_removed(env, tmp_path: Path) -> None:
    mobile, admin, storage_dir = env
    case_id = _new_case(mobile)
    body = _export(admin, case_id).json()
    for path in (storage_dir / "exports").rglob(body["file_name"]):
        path.unlink()
    assert admin.get(f"{ADMIN}/exports/{body['id']}/download").status_code == 404


def test_openapi_lists_export_paths_on_admin_only(env) -> None:
    mobile, admin, _ = env
    admin_paths = admin.get("/openapi.json").json()["paths"]
    for path in ("/api/admin/v1/cases/{case_id}/exports", "/api/admin/v1/exports/{export_id}",
                 "/api/admin/v1/exports/{export_id}/download"):
        assert path in admin_paths
    assert not any("exports" in p for p in mobile.get("/openapi.json").json()["paths"])
    schema = admin.get("/openapi.json").json()["components"]["schemas"]["Export"]
    assert set(schema["properties"]) == EXPORT_KEYS
