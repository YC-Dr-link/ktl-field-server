"""SQLAlchemy 2.0 ORM 베이스, 서버 설정, SQLite(WAL) 엔진 및 DB 세션 모듈."""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Engine, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_STORAGE_DIR = BASE_DIR / "storage"
DEFAULT_DB_PATH = DEFAULT_STORAGE_DIR / "ktl_field.db"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


@dataclass(frozen=True)
class ServerSettings:
    """현장 서버 바인딩 및 경로 설정 (SRP: 설정 검증)."""

    mobile_host: str = "0.0.0.0"
    mobile_port: int = 8765
    admin_host: str = "127.0.0.1"
    admin_port: int = 8766
    database_url: str = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"
    storage_dir: Path = DEFAULT_STORAGE_DIR
    static_dir: Path = BASE_DIR / "static"

    def __post_init__(self) -> None:
        if self.admin_host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("REQ-B1-01 보안 규칙 위반: 관리 API 호스트는 반드시 루프백(127.0.0.1)이어야 합니다.")


def load_settings() -> ServerSettings:
    """환경 변수에서 설정을 읽어 검증된 ServerSettings 인스턴스를 반환한다."""
    storage_dir = Path(os.getenv("KTL_STORAGE_DIR", str(DEFAULT_STORAGE_DIR)))
    default_db = f"sqlite:///{(storage_dir / 'ktl_field.db').as_posix()}"
    return ServerSettings(
        mobile_host=os.getenv("KTL_MOBILE_HOST", "0.0.0.0"),
        mobile_port=int(os.getenv("KTL_MOBILE_PORT", "8765")),
        admin_host=os.getenv("KTL_ADMIN_HOST", "127.0.0.1"),
        admin_port=int(os.getenv("KTL_ADMIN_PORT", "8766")),
        database_url=os.getenv("KTL_DATABASE_URL", default_db),
        storage_dir=storage_dir,
        static_dir=Path(os.getenv("KTL_STATIC_DIR", str(BASE_DIR / "static"))),
    )


def local_now() -> datetime:
    """노트북 로컬 타임존 오프셋이 포함된 현재 시각을 반환한다."""
    return datetime.now(timezone.utc).astimezone()


class Base(DeclarativeBase):
    """모든 SQLAlchemy 2.0 모델의 선언적 베이스 클래스."""


class SystemSetting(Base):
    """현장 서버 기본 설정 테이블 (`/api/admin/v1/settings`용 기초 엔티티)."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(512), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=local_now, onupdate=local_now)


def _enable_sqlite_wal(dbapi_connection: Any, _connection_record: Any) -> None:
    """SQLite 연결 시 WAL 모드와 외래키 제약을 활성화한다."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
    finally:
        cursor.close()


def create_db_engine(database_url: str | None = None) -> Engine:
    """데이터베이스 엔진을 생성하고 SQLite(WAL) 이벤트 리스너를 등록한다."""
    url = database_url or load_settings().database_url
    if url.startswith("sqlite:///") and ":memory:" not in url:
        db_file = Path(url.replace("sqlite:///", "", 1))
        db_file.parent.mkdir(parents=True, exist_ok=True)

    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    db_engine = create_engine(url, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):
        event.listen(db_engine, "connect", _enable_sqlite_wal)

    return db_engine


engine: Engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    """요청 단위 데이터베이스 세션을 제공하는 제네레이터."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


__all__ = [
    "Base",
    "LOOPBACK_HOSTS",
    "ServerSettings",
    "SessionLocal",
    "SystemSetting",
    "create_db_engine",
    "engine",
    "get_db",
    "load_settings",
    "local_now",
]
