"""REQ-B1-09 가스·라돈 파일 업로드 · 시계열 · 선별/병합 — B-4 Selector 호출 → 결과 표 반환.

Selector는 별도 프로세스에서 불리므로 대역(tests/selector_doubles.py)은 환경 변수로 동작을 고른다.
"""
from __future__ import annotations

from collections.abc import Iterator
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest

from app.main import create_admin_app, create_mobile_app
from app.models import BASE_DIR, create_session_factory, load_settings
from app.services import merges as merge_service
from app.services import storage

CATEGORIES_YAML = Path(__file__).parent / "fixtures" / "synthetic" / "categories.yaml"
ADMIN = "/api/admin/v1"
ADMIN_HEADERS = {"Host": "127.0.0.1:8766"}
DOUBLES = "tests.selector_doubles:get_selector"
ISO_WITH_OFFSET = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
# 가짜 로거 파일 (실제 형식 아님)
FAKE_LOG = b"t,CH1\n2026-10-29 10:00,1.0\n2026-10-29 10:01,1.1\n"


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Path]]:
    storage_dir = tmp_path / "storage"
    monkeypatch.setenv("KTL_ADMIN_SIDECAR", "0")
    monkeypatch.setenv("KTL_STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("KTL_DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("KTL_CATEGORIES_PATH", str(CATEGORIES_YAML))
    monkeypatch.delenv("KTL_TEST_SELECTOR_MODE", raising=False)
    command.upgrade(Config(str(BASE_DIR / "alembic.ini")), "head")
    app = create_admin_app(load_settings())
    app.state.selector_loader = DOUBLES
    with TestClient(app, client=("127.0.0.1", 50000), headers=ADMIN_HEADERS) as admin:
        yield admin, storage_dir
    for path in storage_dir.rglob("*"):  # Windows에서 tmp_path 정리가 읽기 전용 원본에 막히지 않게
        if path.is_file():
            os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)


def _new_case(admin: TestClient, category: str = "gas") -> str:
    res = admin.post(f"{ADMIN}/cases", json={"receipt_no": "26-000200-01", "category": category, "unit_count": 1})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _upload(admin: TestClient, case_id: str, name: str = "logger_1min.csv", data: bytes = FAKE_LOG):
    return admin.post(f"{ADMIN}/cases/{case_id}/files", files={"file": (name, data, "text/csv")})


def _file(admin: TestClient, case_id: str, data: bytes = FAKE_LOG) -> str:
    res = _upload(admin, case_id, data=data)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _merge(admin: TestClient, case_id: str, file_ids: list[str], selector: str = "gas_select", options=None):
    return admin.post(
        f"{ADMIN}/cases/{case_id}/merges", json={"selector": selector, "file_ids": file_ids, "options": options or {}}
    )


def _wait(admin: TestClient, merge_id: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = admin.get(f"{ADMIN}/merges/{merge_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError(f"{merge_id}가 {timeout}초 안에 끝나지 않음")


def _field(res) -> set[str]:
    assert res.status_code == 400, res.text
    return set(res.json()["error"]["detail"]["fields"])


# ── 파일 업로드 ──────────────────────────────────────────────────


def test_upload_detects_and_keeps_read_only_original(env, tmp_path: Path) -> None:
    admin, storage_dir = env
    case_id = _new_case(admin)
    res = _upload(admin, case_id, name="..\\..\\logger_1min.csv")
    assert res.status_code == 201
    body = res.json()
    assert set(body) == {"id", "case_id", "name", "sha256", "detected"}
    assert re.match(r"^file_\d+$", body["id"]) and body["case_id"] == case_id
    assert body["name"] == "logger_1min.csv"  # 경로 부분은 뺀다
    assert body["sha256"] == hashlib.sha256(FAKE_LOG).hexdigest()
    assert body["detected"]["kind"] == "gas_gl240"

    with sqlite3.connect(tmp_path / "test.db") as conn:
        rel, size = conn.execute("SELECT original_path, size_bytes FROM data_files").fetchone()
    original = storage_dir / rel
    assert rel.startswith(f"originals/{case_id}/files/") and rel.endswith(".csv")
    assert original.read_bytes() == FAKE_LOG and size == len(FAKE_LOG)
    assert not os.stat(original).st_mode & stat.S_IWUSR


@pytest.mark.parametrize("mode", ["unknown", "raise"])
def test_unknown_or_failed_detect_is_null(env, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    admin, _ = env
    monkeypatch.setenv("KTL_TEST_SELECTOR_MODE", mode)
    res = _upload(admin, _new_case(admin))
    assert res.status_code == 201
    assert res.json()["detected"] is None


def test_upload_rejects_bad_requests(env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    admin, storage_dir = env
    assert "case_id" in _field(_upload(admin, _new_case(admin, "sampling")))  # 가스·라돈 건이 아님
    case_id = _new_case(admin)
    assert "file" in _field(_upload(admin, case_id, data=b""))
    assert _upload(admin, "case_999").status_code == 404

    monkeypatch.setattr(storage, "MAX_DATA_FILE_BYTES", 10)
    res = _upload(admin, case_id)
    assert res.status_code == 413 and res.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert not list((storage_dir / "originals").rglob("*.csv"))  # 거절한 파일은 남기지 않는다

    with sqlite3.connect(tmp_path / "test.db") as conn:  # 건 완료 API는 다른 이슈 — DB로 직접 완료
        conn.execute("UPDATE cases SET status = 'completed'")
    res = _upload(admin, case_id)
    assert res.status_code == 409 and res.json()["error"]["code"] == "CASE_COMPLETED"


def test_files_and_merges_are_admin_only(env) -> None:
    admin, _ = env
    mobile = TestClient(create_mobile_app(load_settings()))
    case_id = _new_case(admin)
    res = mobile.post(f"/api/v1/cases/{case_id}/files", files={"file": ("a.csv", FAKE_LOG)})
    assert res.status_code in (404, 405)  # 휴대폰 앱에는 이 경로가 없다
    assert mobile.get("/api/v1/merges/mg_1").status_code == 404


# ── 시계열 ───────────────────────────────────────────────────────


def test_series_returns_columns_unit_rows(env, monkeypatch: pytest.MonkeyPatch) -> None:
    admin, _ = env
    monkeypatch.setenv("KTL_TEST_SELECTOR_MODE", "odd")
    file_id = _file(admin, _new_case(admin))
    res = admin.get(f"{ADMIN}/files/{file_id}/series")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"columns", "unit", "rows"}
    assert body["columns"] == ["t", "CH1"] and body["unit"] == "PPB"
    assert body["rows"][0] == ["2026-10-29T10:00:00+09:00", 1.5]
    assert body["rows"][1][1] is None  # NaN → null
    assert all(ISO_WITH_OFFSET.match(row[0]) for row in body["rows"])


def test_series_with_fake_module_and_errors(env, monkeypatch: pytest.MonkeyPatch) -> None:
    admin, _ = env
    file_id = _file(admin, _new_case(admin))
    body = admin.get(f"{ADMIN}/files/{file_id}/series").json()
    assert body["columns"][0] == "t" and len(body["rows"]) == 30 and body["unit"] is None
    assert admin.get(f"{ADMIN}/files/file_999/series").status_code == 404
    assert admin.get(f"{ADMIN}/files/mg_1/series").status_code == 404

    monkeypatch.setenv("KTL_TEST_SELECTOR_MODE", "raise")
    res = admin.get(f"{ADMIN}/files/{file_id}/series")
    assert res.status_code == 400 and "file" in res.json()["error"]["detail"]["fields"]


# ── 선별/병합 ─────────────────────────────────────────────────────


def test_gas_merge_end_to_end(env) -> None:
    admin, _ = env
    case_id = _new_case(admin)
    files = [_file(admin, case_id), _file(admin, case_id, data=FAKE_LOG + b"x")]
    ranges = {"ZDS": ["2026-10-29T10:00:00+09:00", "2026-10-29T10:30:00+09:00"]}
    res = _merge(admin, case_id, files, options={"ranges": ranges})
    assert res.status_code == 202
    queued = res.json()
    assert set(queued) == {"id", "case_id", "selector", "status", "table", "meta", "warnings", "error"}
    assert re.match(r"^mg_\d+$", queued["id"]) and queued["status"] == "queued" and queued["table"] is None

    done = _wait(admin, queued["id"])
    assert done["status"] == "done" and done["error"] is None
    assert done["table"]["columns"] == ["code"] + [f"v{i}" for i in range(1, 11)]
    assert done["table"]["rows"][0][0] == "ZDS" and len(done["table"]["rows"]) == 13
    assert done["meta"]["options"] == {"ranges": ranges}


def test_radon_reference_is_passed_as_path(env, monkeypatch: pytest.MonkeyPatch) -> None:
    admin, storage_dir = env
    monkeypatch.setenv("KTL_TEST_SELECTOR_MODE", "odd")
    case_id = _new_case(admin, "radon")
    files = [_file(admin, case_id), _file(admin, case_id, data=FAKE_LOG + b"x")]
    res = _merge(admin, case_id, files, "radon_merge", {"reference": files[1], "align": "hour"})
    done = _wait(admin, res.json()["id"])
    assert done["status"] == "done", done
    paths = done["meta"]["paths"]
    assert len(paths) == 2 and all(Path(p).is_file() for p in paths)
    assert done["meta"]["options"] == {"reference": paths[1], "align": "hour"}
    assert done["meta"]["at"] == "2026-10-29T10:00:00+09:00"
    assert done["table"] == {"columns": ["code", "v1"], "rows": [["ZDS", 1.0], ["SDS", None]]}
    assert done["warnings"] == [{"code": "GAP", "message": "빈 구간이 있습니다."}]


def test_merge_validation(env, tmp_path: Path) -> None:
    admin, _ = env
    case_id, other = _new_case(admin), _new_case(admin)
    file_id, other_file = _file(admin, case_id), _file(admin, other)
    assert "selector" in _field(_merge(admin, case_id, [file_id], "radon_merge"))
    assert "file_ids" in _field(_merge(admin, case_id, [other_file]))
    assert "file_ids" in _field(_merge(admin, case_id, [file_id, file_id]))
    assert "file_ids" in _field(_merge(admin, case_id, ["mg_1"]))
    assert "file_ids" in _field(_merge(admin, case_id, []))
    assert "selector" in _field(_merge(admin, case_id, [file_id], "dust_select"))
    assert "options.ranges" in _field(_merge(admin, case_id, [file_id], options={"ranges": [1, 2]}))
    assert _merge(admin, "case_999", [file_id]).status_code == 404
    assert admin.get(f"{ADMIN}/merges/mg_999").status_code == 404
    assert admin.get(f"{ADMIN}/merges/file_1").status_code == 404

    radon = _new_case(admin, "radon")
    radon_file = _file(admin, radon)
    assert "options.reference" in _field(_merge(admin, radon, [radon_file], "radon_merge", {"reference": file_id}))
    assert "options.align" in _field(_merge(admin, radon, [radon_file], "radon_merge", {"align": "day"}))

    with sqlite3.connect(tmp_path / "test.db") as conn:
        conn.execute("UPDATE cases SET status = 'completed'")
    res = _merge(admin, case_id, [file_id])
    assert res.status_code == 409 and res.json()["error"]["code"] == "CASE_COMPLETED"


@pytest.mark.parametrize(
    ("mode", "message"),
    [("raise", "ZDS 구간에 값이 없습니다"), ("hang", "초 안에 끝나지 않았습니다"), ("crash", "비정상 종료")],
)
def test_module_failure_marks_only_that_merge_failed(env, monkeypatch: pytest.MonkeyPatch, mode: str, message: str) -> None:
    admin, _ = env
    case_id = _new_case(admin)
    file_id = _file(admin, case_id)
    monkeypatch.setattr(merge_service, "RUN_TIMEOUT_S", 2.0)
    monkeypatch.setenv("KTL_TEST_SELECTOR_MODE", mode)
    failed = _wait(admin, _merge(admin, case_id, [file_id]).json()["id"])
    assert failed["status"] == "failed" and failed["table"] is None
    assert message in failed["error"]

    monkeypatch.delenv("KTL_TEST_SELECTOR_MODE")  # 서버는 계속 돈다 — 다음 병합은 정상
    assert _wait(admin, _merge(admin, case_id, [file_id]).json()["id"])["status"] == "done"


def test_unfinished_merges_fail_on_restart(env, tmp_path: Path) -> None:
    admin, _ = env
    case_id = _new_case(admin)
    file_id = _file(admin, case_id)
    with sqlite3.connect(tmp_path / "test.db") as conn:  # 서버가 병합 도중 꺼진 상태를 만든다
        conn.execute(
            "INSERT INTO merges (case_id, selector, file_ids, options, status, meta, warnings, created_by, created_at)"
            " VALUES (?, 'gas_select', ?, '{}', 'running', '{}', '[]', 'admin', '2026-10-29 10:00:00')",
            (int(case_id.split("_")[1]), f"[{file_id.split('_')[1]}]"),
        )
        merge_pk = conn.execute("SELECT max(id) FROM merges").fetchone()[0]
    with TestClient(create_admin_app(load_settings()), client=("127.0.0.1", 50000), headers=ADMIN_HEADERS) as again:
        body = again.get(f"{ADMIN}/merges/mg_{merge_pk}").json()
    assert body["status"] == "failed" and body["error"] == merge_service.RESTART_ERROR


def test_merge_raw_table_for_export(env) -> None:
    admin, _ = env
    case_id = _new_case(admin)
    file_id = _file(admin, case_id)
    done = _wait(admin, _merge(admin, case_id, [file_id]).json()["id"])
    case_pk = int(case_id.split("_")[1])
    with create_session_factory(load_settings().database_url)() as db:
        assert merge_service.merge_raw_table(db, case_pk, done["id"]) == done["table"]
        with pytest.raises(Exception, match="VALIDATION|요청"):
            merge_service.merge_raw_table(db, case_pk + 1, done["id"])


def test_openapi_documents_merge_api(env) -> None:
    admin, _ = env
    spec = admin.get("/openapi.json").json()
    paths = spec["paths"]
    assert "201" in paths[f"{ADMIN}/cases/{{case_id}}/files"]["post"]["responses"]
    assert "200" in paths[f"{ADMIN}/files/{{file_id}}/series"]["get"]["responses"]
    assert "202" in paths[f"{ADMIN}/cases/{{case_id}}/merges"]["post"]["responses"]
    assert "200" in paths[f"{ADMIN}/merges/{{merge_id}}"]["get"]["responses"]
    schemas = spec["components"]["schemas"]
    assert set(schemas["DataFile"]["properties"]) == {"id", "case_id", "name", "sha256", "detected"}
    assert set(schemas["Merge"]["properties"]) == {"id", "case_id", "selector", "status", "table", "meta", "warnings", "error"}
    assert set(schemas["MergeCreate"]["required"]) == {"selector", "file_ids"}
    assert schemas["MergeCreate"]["properties"]["selector"]["enum"] == ["gas_select", "radon_merge"]
