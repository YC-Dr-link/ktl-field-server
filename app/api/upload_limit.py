"""업로드 본문 크기 제한 (02 §1.1: 413 FILE_TOO_LARGE — 사진 20 MB 초과, 로거 파일도 같은 방식).

multipart 본문은 라우터에 닿기 전에 임시 파일로 끝까지 받아지므로, 그 전에 끊지 않으면
아주 큰 요청이 노트북 디스크를 채울 수 있다. 업로드 경로만 본문을 세어 넘으면 바로 413으로 끊는다.
"""
from __future__ import annotations

import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import error_response
from app.services.storage import (
    DATA_FILE_TOO_LARGE_MESSAGE,
    MAX_DATA_FILE_BYTES,
    MAX_PHOTO_BYTES,
    TOO_LARGE_MESSAGE,
)

# multipart 경계·폼 칸(unit_index 등) 몫
_MULTIPART_OVERHEAD = 64 * 1024
# (경로, 본문 상한, 413 메시지) — 사진은 휴대폰·관리 앱 모두, 로거 파일은 관리 앱에만 경로가 있다
_UPLOAD_LIMITS = (
    (re.compile(r"/cases/[^/]+/photos/?$"), MAX_PHOTO_BYTES + _MULTIPART_OVERHEAD, TOO_LARGE_MESSAGE),
    (re.compile(r"/cases/[^/]+/files/?$"), MAX_DATA_FILE_BYTES + _MULTIPART_OVERHEAD, DATA_FILE_TOO_LARGE_MESSAGE),
)


def _limit_for(path: str) -> tuple[int, str] | None:
    for pattern, max_bytes, message in _UPLOAD_LIMITS:
        if pattern.search(path):
            return max_bytes, message
    return None


class UploadSizeLimit:
    def __init__(self, app: ASGIApp, max_bytes: int | None = None) -> None:
        self.app = app
        self.max_bytes = max_bytes  # 테스트용: 주면 모든 업로드 경로에 이 상한을 쓴다

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = _limit_for(scope["path"]) if scope["type"] == "http" and scope["method"] == "POST" else None
        if limit is None:
            await self.app(scope, receive, send)
            return
        max_bytes = self.max_bytes if self.max_bytes is not None else limit[0]
        too_large = limit[1]
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and length.isdigit() and int(length) > max_bytes:
            await _send_413(scope, receive, send, too_large)
            return

        received = 0
        rejected = False

        async def limited_receive() -> Message:
            # 넘는 순간 413을 직접 보내고, 안쪽 앱에는 연결이 끊긴 것으로 알려 남은 본문을 받지 않게 한다
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    rejected = True
                    await _send_413(scope, receive, send, too_large)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not rejected:  # 413을 보낸 뒤 안쪽 앱이 내는 응답(400 등)은 버린다
                await send(message)

        await self.app(scope, limited_receive, guarded_send)


async def _send_413(scope: Scope, receive: Receive, send: Send, message: str) -> None:
    await error_response(413, "FILE_TOO_LARGE", message)(scope, receive, send)
