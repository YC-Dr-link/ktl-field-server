"""보관소(원본 읽기 전용 + SHA-256), 확정·이력, 내보내기, 해시 체인 로그 서비스 패키지."""
from __future__ import annotations

from pathlib import Path

STORAGE_SUBDIRS = ("originals", "derived", "submit", "exports")


def ensure_storage_dirs(storage_root: Path) -> None:
    """storage/ 하위 디렉터리(originals, derived, submit, exports)를 준비한다."""
    for sub in STORAGE_SUBDIRS:
        (storage_root / sub).mkdir(parents=True, exist_ok=True)
