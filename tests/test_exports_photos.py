"""REQ-B1-13 제출용 워터마크 사진 묶음 — include_photos면 zip(값 파일 + 워터마크 사본), 원본은 그대로.

사진은 Pillow로 메모리에서 만든 가짜 이미지 (실제 사진 아님).
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import stat
import zipfile

from PIL import Image

from tests.test_exports import _download, _export, _photo
from tests.test_exports_merge_raw import _done_merge
from tests.test_photos import _new_case, _upload, env  # noqa: F401  (env는 fixture)

ADMIN = "/api/admin/v1"


def _jpeg(width: int = 400, height: int = 200, orientation: int | None = None) -> bytes:
    img = Image.new("RGB", (width, height), (200, 220, 240))
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation  # EXIF Orientation
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif.tobytes())
    return out.getvalue()


def test_zip_has_values_file_and_watermarked_copies_only(env, tmp_path: Path) -> None:
    mobile, admin, storage_dir = env
    case_id = _new_case(mobile)
    sideways = _jpeg(orientation=6)  # 90° 돌려 찍힌 사진 → 사본은 세워서 200×400
    cert = _upload(mobile, case_id, unit_index=1, item_key="cert", data=sideways).json()
    retaken = _upload(mobile, case_id, unit_index=2, item_key="prec_1", data=_jpeg()).json()
    flow = _upload(mobile, case_id, unit_index=2, item_key="prec_1", data=_jpeg()).json()  # 다시 찍기
    broken = _upload(mobile, case_id, unit_index=1, item_key="prec_1").json()  # 매직 바이트만 맞는 가짜 → 못 염

    plain = _export(admin, case_id).json()
    body = _export(admin, case_id, include_photos=True).json()
    assert body["status"] == "done" and body["format"] == "csv"
    assert body["file_name"].endswith(".zip")
    assert body["preview"] == plain["preview"]

    with zipfile.ZipFile(io.BytesIO(_download(admin, body["id"]))) as zf:
        names = set(zf.namelist())
        stem = lambda p: Path(p["stored_name"]).stem  # noqa: E731
        assert names == {
            body["file_name"].removesuffix(".zip") + ".csv",
            f"photos/{stem(cert)}.jpg",
            f"photos/{stem(flow)}.jpg",
            "photos_skipped.txt",
        }
        values_name = next(n for n in names if n.endswith(".csv"))
        assert zf.read(values_name) == _download(admin, plain["id"])  # 값 파일은 따로 내보낸 것과 같다
        assert broken["stored_name"] in zf.read("photos_skipped.txt").decode("utf-8")
        assert retaken["stored_name"] not in zf.read("photos_skipped.txt").decode("utf-8")
        copy = Image.open(io.BytesIO(zf.read(f"photos/{stem(cert)}.jpg")))

    assert copy.size == (200, 400)  # EXIF 방향대로 세움
    assert not copy.getexif()  # 사본에 EXIF(GPS 등)를 남기지 않는다
    top, bottom = copy.getpixel((5, 5)), copy.getpixel((5, 395))
    assert sum(bottom) < sum(top) - 150  # 아래쪽 반투명 띠 = 워터마크

    # 원본은 바이트 · SHA-256 · 읽기 전용 그대로
    original = storage_dir / "originals" / case_id / cert["stored_name"]
    assert original.read_bytes() == sideways
    assert hashlib.sha256(original.read_bytes()).hexdigest() == cert["sha256"]
    assert not os.stat(original).st_mode & stat.S_IWUSR


def test_zip_without_unreadable_photos_has_no_skipped_list(env, tmp_path: Path) -> None:
    mobile, admin, _ = env
    case_id = _new_case(mobile)
    _upload(mobile, case_id, unit_index=1, item_key="cert", data=_jpeg())
    body = _export(admin, case_id, include_photos=True, format="txt", encoding="utf-8").json()
    with zipfile.ZipFile(io.BytesIO(_download(admin, body["id"]))) as zf:
        assert "photos_skipped.txt" not in zf.namelist()
        assert sum(n.startswith("photos/") for n in zf.namelist()) == 1


def test_include_photos_only_for_photo_values(env) -> None:
    _, admin, _ = env
    gas = admin.post(f"{ADMIN}/cases", json={"receipt_no": "26-000999-01", "category": "gas", "unit_count": 1}).json()
    merge = _done_merge(admin, gas["id"])
    res = admin.post(
        f"{ADMIN}/cases/{gas['id']}/exports",
        json={"kind": "merge_raw", "format": "csv", "merge_id": merge["id"], "include_photos": True},
    )
    assert res.status_code == 400
    assert "include_photos" in res.json()["error"]["detail"]["fields"]
