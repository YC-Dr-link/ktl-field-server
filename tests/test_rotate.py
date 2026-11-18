"""REQ-B1-12 사진 회전 보정 테스트 — EXIF 방향대로 돌린 사본을 Reader에 넘기고 원본은 그대로 둔다.

사진은 테스트 안에서 Pillow로 만든 가짜 이미지(단색)만 쓴다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image
import pytest

from app.worker.child import read_one
from app.worker.rotate import make_rotated_copy, rotated_path

ORIENTATION = 0x0112


def make_jpeg(path: Path, size: tuple[int, int], orientation: int | None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    if orientation is not None:
        exif[ORIENTATION] = orientation
    Image.new("RGB", size, (200, 30, 30)).save(path, format="JPEG", exif=exif)
    return path


def sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def originals(tmp_path: Path) -> Path:
    return tmp_path / "storage" / "originals" / "case_1"


def test_rotated_path_maps_originals_to_derived(tmp_path: Path) -> None:
    src = tmp_path / "storage" / "originals" / "case_1" / "20261029_091102_prec_1.jpg"
    assert rotated_path(src) == tmp_path / "storage" / "derived" / "case_1" / "20261029_091102_prec_1_rotated.jpg"
    assert rotated_path(tmp_path / "loose.jpg") is None


@pytest.mark.parametrize(("orientation", "expected"), [(6, (20, 40)), (8, (20, 40)), (3, (40, 20)), (5, (20, 40))])
def test_exif_orientation_rotated_in_90_degree_steps(originals: Path, orientation: int, expected: tuple[int, int]) -> None:
    src = make_jpeg(originals / "a.jpg", (40, 20), orientation)
    before = sha(src)

    out = make_rotated_copy(str(src))

    assert Path(out) == rotated_path(src)
    with Image.open(out) as img:
        assert img.size == expected
        assert img.getexif().get(ORIENTATION, 1) == 1  # 돌린 사본에 방향 태그가 남으면 Reader가 한 번 더 돌린다
    assert sha(src) == before  # 원본은 그대로


@pytest.mark.parametrize("orientation", [None, 1])
def test_upright_photo_copied_byte_for_byte(originals: Path, orientation: int | None) -> None:
    src = make_jpeg(originals / "b.jpg", (40, 20), orientation)
    out = make_rotated_copy(str(src))
    assert out != str(src) and sha(out) == sha(src)


def test_retry_overwrites_existing_copy(originals: Path) -> None:
    src = make_jpeg(originals / "c.jpg", (40, 20), 6)
    first = make_rotated_copy(str(src))
    Path(first).write_bytes(b"stale")
    assert make_rotated_copy(str(src)) == first
    with Image.open(first) as img:
        assert img.size == (20, 40)


def test_unreadable_or_outside_storage_passes_original(tmp_path: Path, originals: Path) -> None:
    broken = originals / "d.jpg"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"\xff\xd8\xff not really a jpeg")
    assert make_rotated_copy(str(broken)) == str(broken)
    assert not list((tmp_path / "storage").glob("derived/**/*"))  # 반쯤 쓴 사본을 남기지 않는다

    loose = make_jpeg(tmp_path / "loose.jpg", (40, 20), 6)
    assert make_rotated_copy(str(loose)) == str(loose)


class SizeReader:
    """받은 경로와 이미지 크기를 기록하는 Reader 대역."""

    name = "size"

    def __init__(self) -> None:
        self.seen: list[tuple[str, tuple[int, int]]] = []

    def read(self, image_path: str, item: str) -> Any:
        from ktl_contract import ReadResult

        with Image.open(image_path) as img:
            self.seen.append((image_path, img.size))
        return ReadResult(values=[])


def test_reader_receives_rotated_copy(originals: Path) -> None:
    src = make_jpeg(originals / "e.jpg", (40, 20), 6)
    reader = SizeReader()

    reply = read_one({"size": reader}, {"reader": "size", "image_path": str(src), "item_key": "prec_1"})

    assert reply["ok"], reply
    assert reader.seen == [(str(rotated_path(src)), (20, 40))]
