"""B-4 Selector를 별도 프로세스에서 시간 제한을 두고 부른다 (REQ-B1-09).

모듈이 예외를 내거나, 멈추거나, 프로세스째 죽어도 서버는 계속 돈다 — 그 호출만 SelectorFailed가 된다.
결과는 자식 프로세스 안에서 JSON으로 바로 쓸 수 있는 값(dict · list · str · 숫자 · None)으로 바꿔서 넘긴다.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
import importlib
import logging
import math
import multiprocessing
from multiprocessing.connection import Connection
from typing import Any

logger = logging.getLogger(__name__)

# TODO(확인 필요): Selector 시간 제한은 계약(02 §3.3)에 없다 — Reader의 1장 10초를 detect·series에 쓰고,
#   run은 여러 파일·구간 계산이라 60초로 둠. 실제 B-4 모듈 속도를 보고 정한다.
DETECT_TIMEOUT_S = 10.0
SERIES_TIMEOUT_S = 10.0
RUN_TIMEOUT_S = 60.0
# 파이썬 시작 · import 몫 (모듈 시간 제한과 따로 잰다)
STARTUP_TIMEOUT_S = 30.0
DEFAULT_LOADER = "app.modules:get_selector"
_JOIN_GRACE_S = 5.0


class SelectorFailed(Exception):
    """모듈 예외 · 시간 초과 · 프로세스 비정상 종료. 메시지는 화면에 그대로 보여 줄 한국어."""


def call_selector(
    selector: str,
    method: str,
    args: tuple[Any, ...],
    *,
    timeout: float,
    loader: str = DEFAULT_LOADER,
) -> Any:
    """`get_selector(selector).<method>(*args)`를 새 프로세스에서 실행하고 JSON 모양 결과를 돌려준다.

    `timeout`은 모듈이 일하는 시간만 잰다. 파이썬 시작 · pandas · 모듈 import는 STARTUP_TIMEOUT_S로 따로 잰다
    (현장 노트북 첫 실행은 백신 검사 등으로 시작만 수 초 걸릴 수 있다).
    """
    ctx = multiprocessing.get_context("spawn")  # fork는 서버 스레드·잠금을 복사하므로 쓰지 않는다
    receiver, sender = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child_main, args=(sender, loader, selector, method, args), daemon=True)
    proc.start()
    sender.close()  # 자식이 결과 없이 죽으면 recv()가 EOFError를 내도록 부모 쪽 끝은 닫는다
    timed_out = False
    try:
        kind, payload = _receive(receiver, STARTUP_TIMEOUT_S, selector, "모듈을 불러오지 못했습니다")
        if kind == "ready":
            kind, payload = _receive(receiver, timeout, selector, f"모듈이 {timeout:.0f}초 안에 끝나지 않았습니다")
    except SelectorFailed:
        timed_out = proc.is_alive()
        raise
    finally:
        receiver.close()
        _stop(proc, grace=0.0 if timed_out else _JOIN_GRACE_S)  # 멈춘 모듈은 더 기다리지 않고 바로 끈다
    if kind != "ok":
        raise SelectorFailed(payload)
    return payload


def _receive(receiver: Connection, timeout: float, selector: str, timeout_message: str) -> tuple[str, Any]:
    if not receiver.poll(timeout):
        raise SelectorFailed(f"{selector} {timeout_message}.")
    try:
        return receiver.recv()
    except (EOFError, OSError):
        raise SelectorFailed(f"{selector} 모듈 프로세스가 비정상 종료했습니다.") from None


def _stop(proc: multiprocessing.process.BaseProcess, grace: float = _JOIN_GRACE_S) -> None:
    proc.join(grace)
    if proc.is_alive():
        proc.terminate()
        proc.join(_JOIN_GRACE_S)
    if proc.is_alive():  # pragma: no cover - terminate도 안 듣는 경우
        proc.kill()
        proc.join()


def _child_main(conn: Connection, loader: str, selector: str, method: str, args: tuple[Any, ...]) -> None:
    """자식 프로세스: 모듈을 불러오면 ("ready"), 끝나면 ("ok", 결과) 또는 ("error", 메시지)를 보낸다."""
    try:
        try:
            import pandas  # noqa: F401  - 모듈이 함수 안에서 import해도 시작 시간에 들어가게 미리
        except ModuleNotFoundError:  # pragma: no cover
            pass
        module_name, func_name = loader.split(":", 1)
        instance = getattr(importlib.import_module(module_name), func_name)(selector)
        conn.send(("ready", None))
        message: tuple[str, Any] = ("ok", _CONVERTERS[method](getattr(instance, method)(*args)))
    except Exception as exc:  # noqa: BLE001 - 모듈 예외는 모두 그 호출의 실패로 돌려준다
        message = ("error", f"{selector} 모듈 오류: {str(exc).strip() or type(exc).__name__}")
    try:
        conn.send(message)
    except Exception as exc:  # noqa: BLE001 - 결과를 못 보내는 것도 실패로
        conn.send(("error", f"{selector} 모듈 결과를 전달하지 못했습니다: {exc}"))
    finally:
        conn.close()


# ── 결과 → JSON 모양 (자식 프로세스 안에서) ──────────────────────────


def to_cell(value: Any) -> Any:
    """표 한 칸을 JSON 값으로. 시각은 ISO 8601 + 로컬 오프셋, 빈 값(NaN · NaT)은 None."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if _is_missing(value):
        return None
    if type(value).__name__ == "datetime64":  # ns 정밀도는 .item()이 정수가 되므로 pandas로 바꾼다
        import pandas as pd

        value = pd.Timestamp(value)
    elif hasattr(value, "item") and not isinstance(value, (datetime, date, timedelta)):  # numpy 스칼라
        try:
            return to_cell(value.item())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "to_pydatetime"):  # pandas.Timestamp
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        # tz 없는 시각은 노트북 로컬 시각으로 본다 (02 §1 시각 규칙)
        return value.astimezone().isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def to_json(value: Any) -> Any:
    """meta · detect() 결과처럼 중첩된 값을 JSON 모양으로."""
    if isinstance(value, Mapping):
        return {str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json(v) for v in value]
    if hasattr(value, "to_dict") and hasattr(value, "columns"):  # DataFrame이 meta에 들어온 경우
        return table_payload(value)
    return to_cell(value)


def table_payload(df: Any) -> dict[str, Any]:
    """DataFrame → `{ "columns": [...], "rows": [[...], ...] }` (칼럼 순서 = 출력 순서 그대로)."""
    return {
        "columns": [str(c) for c in df.columns],
        "rows": [[to_cell(v) for v in row] for row in df.itertuples(index=False, name=None)],
    }


def _is_missing(value: Any) -> bool:
    try:
        import pandas as pd
    except ModuleNotFoundError:  # pragma: no cover
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _detect_payload(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    if not isinstance(result, Mapping):
        raise TypeError("detect()는 dict 또는 None을 돌려줘야 합니다")
    return to_json(result) or None


def _series_payload(result: Any) -> dict[str, Any]:
    if not hasattr(result, "columns") or "t" not in result.columns:
        raise TypeError("series()는 t 칼럼이 있는 DataFrame을 돌려줘야 합니다")
    return table_payload(result)


def _run_payload(result: Any) -> dict[str, Any]:
    table = getattr(result, "table", None)
    if table is None or not hasattr(table, "columns") or len(table.columns) == 0:
        raise TypeError("run()은 표(table)가 있는 SelectResult를 돌려줘야 합니다")
    warnings = [{"code": str(w.code), "message": str(w.message)} for w in getattr(result, "warnings", None) or []]
    meta = to_json(getattr(result, "meta", None) or {})
    return {"table": table_payload(table), "warnings": warnings, "meta": meta if isinstance(meta, dict) else {}}


_CONVERTERS = {"detect": _detect_payload, "series": _series_payload, "run": _run_payload}
