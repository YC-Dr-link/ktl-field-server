"""선별/병합 작업 실행기 (REQ-B1-09): `POST /merges`는 202로 바로 돌려주고 여기서 차례로 실행한다.

한 번에 하나씩 돌린다 — 노트북 하나에서 판독 작업과 같이 돌아가므로 병합까지 동시에 여럿 띄우지 않는다.
모듈 자체는 app.worker.selector가 별도 프로세스에서 시간 제한을 두고 부른다.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import logging
from pathlib import Path

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.services import merges as merge_service

logger = logging.getLogger(__name__)


class MergeRunner:
    def __init__(self, session_factory: sessionmaker[Session], storage_dir: Path) -> None:
        self._session_factory = session_factory
        self._storage_dir = storage_dir
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="merge")

    def submit(self, merge_pk: int, *, loader: str) -> Future[None]:
        return self._executor.submit(
            merge_service.run_merge, self._session_factory, self._storage_dir, merge_pk, loader=loader
        )

    def recover(self) -> None:
        try:
            count = merge_service.fail_unfinished_merges(self._session_factory)
        except OperationalError:  # `alembic upgrade head` 전 — 서버는 띄우고 병합 정리만 건너뛴다
            logger.warning("merges 테이블이 없어 미완료 병합 정리를 건너뜁니다 (alembic upgrade head 필요).")
            return
        if count:
            logger.warning("끝나지 못한 병합 %d건을 failed로 바꿨습니다 (서버 재시작).", count)

    def shutdown(self) -> None:
        # 기다리지 않는다: 돌고 있는 모듈 프로세스는 daemon이라 서버와 같이 끝나고, 다음 시작 때 failed로 정리된다
        self._executor.shutdown(wait=False, cancel_futures=True)
