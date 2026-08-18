"""스캔 스코어 이력 적재.

일별 CSV 한 파일에 전체 유니버스의 스코어와 가격 스냅샷을 기록한다.
날짜 관련 열은 모두 KST 기준이며, bar_date만 거래소 세션 날짜를 그대로 쓴다.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

KST = timezone(timedelta(hours=9))

# 열 순서 고정. 변경 시 기존 CSV와 호환이 깨지므로 끝에만 추가할 것.
FIELDS = (
    "scan_ts_kst", "date", "ticker", "name", "market", "sector",
    "bar_date", "close", "volume", "avg_vol20", "atr14", "market_cap",
    "tech", "macro", "filing", "value", "total", "consensus", "signal",
    "ev", "target", "hitl", "source",
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
    return round(float(np.mean(tr[-period:])), 4)


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
    avg_vol20 = round(float(np.mean(volume[-20:])), 2) if len(volume) >= 20 else None

    return {
        "bar_date": f"{last_index:%Y-%m-%d}",
        "close": round(float(close[-1]), 4),
        "volume": int(volume[-1]),
        "avg_vol20": avg_vol20,
        "atr14": _atr(high, low, close),
        "market_cap": info.get("marketCap"),
    }


def write_snapshot(rows: list[dict], scan_ts: datetime, out_dir="history") -> Path:
    """이력 행들을 history/<KST날짜>.csv 로 기록하고 경로를 반환한다.

    같은 KST 날짜에 다시 호출되면 덮어쓴다.
    """
    scan_kst = scan_ts.astimezone(KST)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{scan_kst:%Y-%m-%d}.csv"

    auto = {
        "scan_ts_kst": scan_kst.isoformat(),
        "date": f"{scan_kst:%Y-%m-%d}",
    }

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            unknown = set(row) - set(_ROW_FIELDS)
            if unknown:
                raise ValueError(f"알 수 없는 열: {sorted(unknown)}")
            out = dict(auto)
            for field in _ROW_FIELDS:
                value = row.get(field)
                out[field] = "" if value is None else value
            writer.writerow(out)

    return path
