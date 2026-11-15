"""사진 회전 보정 (REQ-B1-12): EXIF 방향대로 90° 단위로 돌린 사본을 `derived/`에 만들어 Reader에 넘긴다.

원본(`originals/`)은 읽기만 한다. 판독 작업 프로세스 안에서 부르므로 깨진 사진·큰 사진도 1장 10초 제한 안에 갇힌다.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil

from PIL import Image, ImageOps, UnidentifiedImageError


def rotated_path(original: str | os.PathLike[str]) -> Path | None:
    """`…/originals/<건>/<이름>.jpg` → `…/derived/<건>/<이름>_rotated.jpg`. 보관소 밖 경로면 None."""
    path = Path(original)
    parts = list(path.parts)
    found = [k for k, p in enumerate(parts[:-1]) if p == "originals"]
    if not found:
        return None
    parts[found[-1]] = "derived"
    return Path(*parts).with_name(f"{path.stem}_rotated{path.suffix}")


def make_rotated_copy(original: str) -> str:
    """회전 보정 사본 경로를 돌려준다. 보관소 밖이거나 Pillow가 못 여는 사진(HEIC 등)이면 원본 경로 그대로.

    EXIF 방향이 없거나 1이면 다시 인코딩하지 않고 바이트 그대로 복사한다 (JPEG 화질 손실 없음).
    """
    dest = rotated_path(original)
    if dest is None:
        return original
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        with Image.open(original) as img:
            orientation = img.getexif().get(0x0112, 1)  # 0x0112 = Orientation
            dest.parent.mkdir(parents=True, exist_ok=True)
            if orientation == 1:
                shutil.copyfile(original, tmp)
            else:
                # exif_transpose가 회전·뒤집기를 하고 Orientation 태그를 지운다 (bbox는 돌린 이미지 기준)
                # quality는 JPEG·WebP만 쓰고 PNG는 무시한다
                ImageOps.exif_transpose(img).save(tmp, format=img.format, quality=95)
            os.replace(tmp, dest)  # 재시도 때 같은 사본을 덮어쓴다
    except (UnidentifiedImageError, OSError):
        tmp.unlink(missing_ok=True)
        # TODO(확인 필요): HEIC는 pillow-heif 없이는 못 연다 — 현장 휴대폰이 HEIC를 보내는지, Reader가 읽는지
        return original
    return str(dest)
