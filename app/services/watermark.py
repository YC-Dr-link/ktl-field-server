"""제출용 워터마크 사본 (REQ-B1-13). 원본은 읽기만 하고, 워터마크는 메모리의 사본에만 그린다."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

# 열 수 없는 사진(손상 · HEIC 등 Pillow 미지원 형식 · 너무 큰 해상도)
UNREADABLE = (OSError, ValueError, Image.DecompressionBombError)


def watermarked_jpeg(src: Path, text: str) -> bytes:
    """`src`를 바로 세우고(EXIF 방향) 아래쪽 반투명 띠에 `text`를 쓴 JPEG. EXIF(GPS 등)는 사본에 남기지 않는다.

    `text`는 ASCII만 — 기본 내장 글꼴을 쓰므로(오프라인, 시스템 글꼴에 기대지 않음) 한글은 못 그린다.
    """
    with Image.open(src) as original:
        img = ImageOps.exif_transpose(original).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    size = max(12, img.width // 40)
    font = ImageFont.load_default(size)
    pad = size // 2
    band = draw.textbbox((0, 0), text, font=font)[3] + 2 * pad
    draw.rectangle((0, img.height - band, img.width, img.height), fill=(0, 0, 0, 128))
    draw.text((pad, img.height - band + pad), text, font=font, fill=(255, 255, 255, 255))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()
