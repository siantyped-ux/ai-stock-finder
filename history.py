"""스캔 스코어 이력 적재.

일별 CSV 한 파일에 전체 유니버스의 스코어와 가격 스냅샷을 기록한다.
날짜 관련 열은 모두 KST 기준이며, bar_date만 거래소 세션 날짜를 그대로 쓴다.
"""
from __future__ import annotations

import csv
import math
import numbers
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

KST = timezone(timedelta(hours=9))

# 열 순서 고정. 변경 시 기존 CSV와 호환이 깨지므로 끝에만 추가할 것.
#
# flow 는 2026-08-24 신설된 수급 축이다. macro 는 총점에서 빠졌지만 열은
# 남긴다 - 과거 행과의 연속성이 끊기고, 국면 분석에는 계속 쓸 수 있다.
# regime 은 매매 계층의 국면 게이트가 읽는다.
FIELDS = (
    "scan_ts_kst", "date", "ticker", "name", "market", "sector",
    "bar_date", "close", "volume", "avg_vol20", "atr14", "market_cap",
    "tech", "macro", "filing", "value", "total", "consensus", "signal",
    "ev", "target", "hitl", "source", "asset_type", "flow", "regime",
)

# write_snapshot이 채우므로 호출자가 넘기지 않는 열
_AUTO_FIELDS = ("scan_ts_kst", "date")
_ROW_FIELDS = tuple(f for f in FIELDS if f not in _AUTO_FIELDS)


def kst_now() -> datetime:
    """현재 시각을 KST aware datetime으로 반환.

    CI 러너는 UTC, 개발 머신은 KST라서 naive datetime.now()는 환경마다
    다른 값을 낸다. 항상 명시 변환한다.
    """
    return datetime.now(timezone.utc).astimezone(KST)


# 값이 비어도 되는 열. 시세 조회 실패나 소급 적재에서는 정상적으로 빈다.
# filing/value 는 ETF 때문에 비워질 수 있다 - ETF 에는 개별기업 재무·공시
# 데이터가 없다. 0 을 넣지 않는 것은 "0점을 받았다" 와 구분하기 위해서다.
#
# flow/regime 이 비어도 되는 이유는 소급 적재 때문이다. backfill_history 는
# git 에 남은 dashboard_data.js 스냅샷을 재생하는데, 그 시절 스냅샷에는 두
# 값이 아예 없다. 라이브 스캔은 항상 채운다.
_NULLABLE_FIELDS = frozenset({
    "bar_date", "close", "volume", "avg_vol20", "atr14", "market_cap",
    "filing", "value", "flow", "regime",
})
_REQUIRED_FIELDS = tuple(f for f in _ROW_FIELDS if f not in _NULLABLE_FIELDS)


def _is_nan(value) -> bool:
    """NaN 여부. NaN은 자기 자신과 같지 않은 유일한 값이다.

    numpy 스칼라도 numbers.Real 이라 함께 걸린다.
    """
    return isinstance(value, numbers.Real) and value != value


def _finite_or_none(value):
    """NaN·inf는 None으로 바꾸고, 유한하면 원래 값을 타입 그대로 돌려준다.

    타입을 보존하는 이유는 marketCap 같은 정수가 float로 바뀌면 CSV에
    불필요한 소수점이 붙기 때문이다.
    """
    if value is None:
        return None
    try:
        if not math.isfinite(float(value)):
            return None
    except (TypeError, ValueError):
        return None
    return value


def _csv_value(value):
    """CSV에 쓸 값. 결측은 None이든 NaN이든 모두 빈칸으로 통일한다.

    NaN을 그대로 두면 리터럴 "nan" 문자열이 기록돼 결측 표현이 두 갈래가 된다.
    """
    if value is None or _is_nan(value):
        return ""
    return value


def _atr(high, low, close, period: int = 14) -> Optional[float]:
    """단순평균 ATR. 봉 수가 period+1 미만이면 None."""
    if len(close) < period + 1:
        return None
    prev_close = close[:-1]
    tr = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - prev_close),
        np.abs(low[1:] - prev_close),
    ])
    mean_tr = _finite_or_none(np.mean(tr[-period:]))
    return None if mean_tr is None else round(float(mean_tr), 4)


def price_fields(hist_df, info: Optional[dict] = None) -> dict:
    """이력 행의 가격 관련 필드를 산출한다.

    스캔이 이미 받아온 데이터에서 뽑으므로 API 호출이 늘지 않는다.
    산출 불가한 값은 None으로 두고 호출자가 그대로 넘긴다.
    """
    info = info or {}
    close = hist_df["Close"].values.astype(float)
    high = hist_df["High"].values.astype(float)
    low = hist_df["Low"].values.astype(float)
    volume = hist_df["Volume"].values.astype(float)

    last_index = hist_df.index[-1]

    last_close = _finite_or_none(close[-1])
    last_volume = _finite_or_none(volume[-1])
    mean_vol = _finite_or_none(np.mean(volume[-20:])) if len(volume) >= 20 else None

    return {
        "bar_date": f"{last_index:%Y-%m-%d}",
        "close": None if last_close is None else round(float(last_close), 4),
        "volume": None if last_volume is None else int(last_volume),
        "avg_vol20": None if mean_vol is None else round(float(mean_vol), 2),
        "atr14": _atr(high, low, close),
        "market_cap": _finite_or_none(info.get("marketCap")),
    }


def _prepare_row(row: dict, auto: dict) -> dict:
    """행 하나를 검증하고 CSV 쓰기용 dict로 만든다."""
    unknown = set(row) - set(_ROW_FIELDS)
    if unknown:
        raise ValueError(f"알 수 없는 열: {sorted(unknown)}")

    missing = [f for f in _REQUIRED_FIELDS if row.get(f) is None]
    if missing:
        raise ValueError(f"필수 열 누락: {missing}")

    out = dict(auto)
    for field in _ROW_FIELDS:
        out[field] = _csv_value(row.get(field))
    return out


def write_snapshot(rows: list[dict], scan_ts: datetime, out_dir="history") -> Path:
    """이력 행들을 history/<KST날짜>.csv 로 기록하고 경로를 반환한다.

    같은 KST 날짜에 다시 호출되면 통째로 덮어쓴다. 쓰기는 임시 파일에 한 뒤
    성공했을 때만 교체하므로, 중간에 실패하면 최종 경로에는 아무것도 남지 않는다.

    bool 값은 CSV에 "True"/"False" 문자열로 기록된다. 읽을 때 bool()로
    캐스팅하면 "False"도 참이 되므로 주의할 것.
    """
    if scan_ts.tzinfo is None:
        raise ValueError(
            "scan_ts 에 시간대(tzinfo)가 없습니다. history.kst_now() 를 쓰세요. "
            "naive datetime 은 시스템 시간대로 해석돼 날짜가 어긋납니다."
        )

    scan_kst = scan_ts.astimezone(KST)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{scan_kst:%Y-%m-%d}.csv"

    auto = {
        "scan_ts_kst": scan_kst.isoformat(),
        "date": f"{scan_kst:%Y-%m-%d}",
    }

    fd, tmp_name = tempfile.mkstemp(dir=str(out_dir), suffix=".csv.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="raise")
            writer.writeheader()
            for row in rows:
                writer.writerow(_prepare_row(row, auto))
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    return path
