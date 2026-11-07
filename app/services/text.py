"""사람이 적는 글(값 · 사유) 공통 검사 — 값 수정(REQ-B1-06)과 건 다시 열기에서 같이 쓴다."""
from __future__ import annotations

import re

MAX_TEXT = 500  # 값 · 사유 길이 상한 (DB · 화면 보호용)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def text_problem(text: str) -> str | None:
    """문제가 있으면 화면에 보일 이유, 없으면 None."""
    if len(text) > MAX_TEXT:
        return f"{MAX_TEXT}자 이하여야 합니다."
    if _CONTROL_CHARS.search(text):  # 내보내기(xlsx 등)에서 깨지는 제어 문자
        return "쓸 수 없는 문자가 들어 있습니다."
    return None
