"""categories.yaml 기준 Reader/Selector 모듈 로더."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from ktl_contract import Reader, Selector
from ktl_contract.fakes import FakeReader, FakeSelector


def load_categories(yaml_path: Path | None = None) -> dict[str, Any]:
    """categories.yaml 파일을 읽어 파싱된 딕셔너리를 반환한다."""
    if yaml_path is None:
        # TODO(확인 필요): 배포 시 categories.yaml 패키지 리소스 경로 확정 필요
        candidate = Path(__file__).resolve().parent.parent / "src" / "ktl-contract" / "categories.yaml"
        if not candidate.is_file():
            return {"version": 1, "categories": []}
        yaml_path = candidate
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {"version": 1, "categories": []}


def get_reader(name: str) -> Reader:
    """이름에 해당하는 Reader 모듈(기본: FakeReader)을 반환한다."""
    return FakeReader(name=name)


def get_selector(name: str) -> Selector:
    """이름에 해당하는 Selector 모듈(기본: FakeSelector)을 반환한다."""
    return FakeSelector(name=name)
