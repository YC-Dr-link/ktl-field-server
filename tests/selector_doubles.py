"""병합 테스트용 Selector 대역. 자식 프로세스에서 불리므로 동작은 환경 변수 KTL_TEST_SELECTOR_MODE로 고른다.

- (없음)   ktl_contract.fakes.FakeSelector 그대로
- unknown  detect()가 None (모르는 형식)
- raise    detect · series · run 모두 예외
- hang     run이 끝나지 않음 (시간 제한 시험)
- crash    run 도중 프로세스가 죽음
- odd      numpy 값 · NaN · tz 있는 시각 · 받은 경로/옵션을 meta로 돌려줌
"""
from __future__ import annotations

import os
import time
from typing import Any

from ktl_contract import SelectResult, Warning
from ktl_contract.fakes import FakeSelector


class _Double(FakeSelector):
    def __init__(self, name: str, mode: str) -> None:
        super().__init__(name)
        self.mode = mode

    def detect(self, path: str) -> dict[str, Any] | None:
        if self.mode == "unknown":
            return None
        if self.mode == "raise":
            raise ValueError("형식을 알 수 없습니다 (대역)")
        if self.mode == "odd":
            import numpy as np

            return {"kind": "gas_gl240", "interval_s": np.int64(60), "unit": "PPB", "rows": np.int64(3)}
        return super().detect(path)

    def series(self, paths: list[str]):
        import numpy as np
        import pandas as pd

        if self.mode == "raise":
            raise ValueError("시계열을 만들 수 없습니다 (대역)")
        if self.mode == "odd":
            t = pd.date_range("2026-10-29 10:00", periods=3, freq="min", tz="Asia/Seoul")
            return pd.DataFrame({"t": t, "CH1": [np.float64(1.5), np.nan, np.float64(2.0)]})
        return super().series(paths)

    def run(self, paths: list[str], options: dict[str, Any]) -> SelectResult:
        import numpy as np
        import pandas as pd

        if self.mode == "raise":
            raise ValueError("ZDS 구간에 값이 없습니다 (대역)")
        if self.mode == "hang":
            time.sleep(60)
        if self.mode == "crash":
            os._exit(3)
        if self.mode == "odd":
            table = pd.DataFrame({"code": ["ZDS", "SDS"], "v1": [np.int64(1), np.nan]})
            meta = {"paths": paths, "options": options, "at": pd.Timestamp("2026-10-29 10:00")}
            return SelectResult(table=table, warnings=[Warning("GAP", "빈 구간이 있습니다.")], meta=meta)
        return super().run(paths, options)


def get_selector(name: str) -> FakeSelector:
    return _Double(name, os.environ.get("KTL_TEST_SELECTOR_MODE", ""))
