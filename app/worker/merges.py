"""선별/병합 작업 실행기 (REQ-B1-09): `POST /merges`는 202로 바로 돌려주고 여기서 차례로 실행한다.

한 번에 하나씩 돌린다 — 노트북 하나에서 판독 작업과 같이 돌아가므로 병합까지 동시에 여럿 띄우지 않는다.
모듈 자체는 app.worker.selector가 별도 프로세스에서 시간 제한을 두고 부른다.
"""
from __future__ import annotations

import logging
from pathlib import Path
import queue
import threading

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.services import merges as merge_service

logger = logging.getLogger(__name__)


class MergeRunner:
    def __init__(self, session_factory: sessionmaker[Session], storage_dir: Path) -> None:
        self._session_factory = session_factory
        self._storage_dir = storage_dir
        self._queue: queue.Queue[tuple[int, str] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def submit(self, merge_pk: int, *, loader: str) -> None:
        with self._lock:
            if self._thread is None:
                # daemon 스레드: ThreadPoolExecutor는 프로세스 종료 때 작업 스레드를 join하므로
                # 돌고 있는 병합(최대 60초)이 끝날 때까지 서버 종료가 멈춘다. daemon이면 기다리지 않는다.
                self._thread = threading.Thread(target=self._loop, name="merge", daemon=True)
                self._thread.start()
        self._queue.put((merge_pk, loader))

    def _loop(self) -> None:
        while (job := self._queue.get()) is not None:
            merge_pk, loader = job
            try:
                merge_service.run_merge(self._session_factory, self._storage_dir, merge_pk, loader=loader)
            except Exception:  # noqa: BLE001 - 실행기 스레드가 죽지 않게
                logger.exception("병합 실행기 오류 (mg_%s)", merge_pk)

    def recover(self) -> None:
        try:
            count = merge_service.fail_unfinished_merges(self._session_factory)
        except OperationalError:  # `alembic upgrade head` 전 — 서버는 띄우고 병합 정리만 건너뛴다
            logger.warning("merges 테이블이 없어 미완료 병합 정리를 건너뜁니다 (alembic upgrade head 필요).")
            return
        if count:
            logger.warning("끝나지 못한 병합 %d건을 failed로 바꿨습니다 (서버 재시작).", count)

    def shutdown(self) -> None:
        # 기다리지 않는다: 남은 병합은 queued · running으로 남아 다음 시작 때 failed로 정리되고,
        # 돌고 있는 모듈 프로세스는 daemon이라 서버와 같이 끝난다
        self._queue.put(None)
