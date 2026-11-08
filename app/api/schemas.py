"""요청·응답 스키마 — field-server.openapi.yaml `components/schemas`와 같은 모양."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CaseStatus = Literal["open", "completed"]
ItemStatus = Literal["empty", "queued", "reading", "done", "needs_check", "confirmed", "failed"]


class CategoryOption(BaseModel):
    key: str
    label: str
    choices: list[str]


class CategoryItem(BaseModel):
    key: str = Field(examples=["prec_1"])
    label: str = Field(examples=["정밀오차 1"])
    reader: str = Field(examples=["flowmeter"], description="none이면 판독 없이 사진만")
    required: bool


class Category(BaseModel):
    category: str = Field(examples=["sampling"])
    label: str = Field(examples=["시료채취"])
    options: list[CategoryOption]
    items: list[CategoryItem]
    export: list[str]


class CategoryList(BaseModel):
    items: list[Category]


class CaseCreate(BaseModel):
    receipt_no: str = Field(pattern=r"^\d{2}-\d{6}-\d{2}$", examples=["26-000123-01"])
    category: str = Field(examples=["sampling"])
    unit_count: int = Field(ge=1, le=50, examples=[2])
    options: dict[str, str] = Field(default_factory=dict, examples=[{"flowmeter_type": "defender"}])


class Progress(BaseModel):
    total: int
    confirmed: int
    needs_check: int


class CaseSummary(BaseModel):
    id: str
    receipt_no: str
    category: str
    unit_count: int
    status: CaseStatus
    progress: Progress
    updated_at: str = Field(json_schema_extra={"format": "date-time"})


class UnitItem(BaseModel):
    key: str
    label: str
    required: bool
    photo_id: str | None
    status: ItemStatus


class Unit(BaseModel):
    index: int
    items: list[UnitItem]


class Case(CaseSummary):
    options: dict[str, str]
    created_by: str
    created_at: str = Field(json_schema_extra={"format": "date-time"})
    units: list[Unit]


class CaseList(BaseModel):
    items: list[CaseSummary]
    next_cursor: str | None


PhotoStatus = Literal["queued", "reading", "done", "failed", "deleted"]
ValueStatus = Literal["auto", "needs_check", "confirmed", "edited"]


class Value(BaseModel):
    key: str = Field(examples=["flow"])
    label: str = Field(examples=["유량"])
    value: str = Field(examples=["1.002"])
    unit: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    bbox: list[int] | None = Field(default=None, min_length=4, max_length=4)
    status: ValueStatus
    confirmed_by: str | None = None
    confirmed_at: str | None = Field(default=None, json_schema_extra={"format": "date-time"})


class PhotoWarning(BaseModel):
    model_config = ConfigDict(title="Warning")  # 계약 스키마 이름 그대로, 파이썬 내장 Warning은 가리지 않게

    code: str = Field(examples=["PRE_MEASURE_SCREEN"])
    message: str


class Photo(BaseModel):
    id: str = Field(examples=["ph_301"])
    case_id: str
    unit_index: int
    item_key: str
    reader: str
    status: PhotoStatus
    sha256: str
    stored_name: str = Field(examples=["20261029_091102_prec_1.jpg"])
    uploaded_by: str
    uploaded_at: str = Field(json_schema_extra={"format": "date-time"})
    values: list[Value]
    warnings: list[PhotoWarning]
    error: str | None
    read_ms: int | None


class ValueChange(BaseModel):
    key: str
    value: str
    reason: str | None = Field(default=None, description="값을 바꾸면 필수. 바꾸지 않고 확정만 하면 생략")


class ValuesPatch(BaseModel):
    values: list[ValueChange]
    confirm: bool = False


ExportKind = Literal["photo_values", "merge_raw"]
ExportFormat = Literal["csv", "txt", "xlsx"]
ExportEncoding = Literal["cp949", "utf-8"]
ExportStatus = Literal["queued", "running", "done", "failed"]


class ExportCreate(BaseModel):
    kind: ExportKind
    format: ExportFormat
    encoding: ExportEncoding = "cp949"
    include_photos: bool = False
    merge_id: str | None = Field(default=None, description="kind=merge_raw일 때")


class ExportPreview(BaseModel):
    columns: list[str]
    rows: list[list[str]]


class Export(BaseModel):
    id: str = Field(examples=["ex_5"])
    case_id: str
    kind: str
    format: str
    status: ExportStatus
    file_name: str | None = Field(examples=["26-000123-01_sampling_20261029-150301.csv"])
    preview: ExportPreview | None
    skipped_unconfirmed: int = Field(description="미확정이라 빠진 값 수")


# ── 관리 API 전용 (§2.5) ──────────────────────────────────────────────


class ReviewQueueItem(BaseModel):
    """확인 필요(needs_check) 값 하나와 그 값이 있는 건·사진·칸."""

    case_id: str = Field(examples=["case_12"])
    receipt_no: str = Field(examples=["26-000123-01"])
    photo_id: str = Field(examples=["ph_301"])
    unit_index: int
    item_key: str = Field(examples=["prec_1"])
    value: Value


class ReviewQueue(BaseModel):
    items: list[ReviewQueueItem]


class ReopenRequest(BaseModel):
    reason: str = Field(examples=["확정 값 정정 요청"])


class Settings(BaseModel):
    """관리 설정 (`GET · PUT /settings`). PUT은 전체 교체 — 빠진 칸은 기본값."""

    # 숫자 칸은 strict — JSON true가 1로 바뀌어 저장되지 않게
    # TODO(확인 필요): 범위(보관 1일 이상 · 기준 0~1 · 제한 1~60초)는 계약에 없어 안전용으로 정함
    export_encoding: ExportEncoding = "cp949"
    photo_retention_days: int | None = Field(default=None, ge=1, strict=True, description="null이면 자동 삭제 안 함")
    confidence_threshold: float = Field(default=0.8, ge=0, le=1, strict=True)
    read_timeout_s: int = Field(default=10, ge=1, le=60, strict=True)
