"""스코어 아카이브를 트레이드로 재현하고 R 통계를 출력한다.

history/*.csv 의 시그널과 yfinance 로 재조회한 OHLC 를 결합해 trade_sim 에
넘긴다. 규칙은 전부 trade_sim/exit_rules 에 있고 여기서는 데이터만 모은다.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
from collections import defaultdict

import numpy as np
import yfinance as yf

import exit_rules as er
import trade_sim as ts


def load_archive(pattern: str = "history/*.csv") -> list:
    """아카이브 전체를 날짜 오름차순 행 목록으로 읽는다."""
    rows = []
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8", newline="") as f:
            rows.extend(csv.DictReader(f))
    rows.sort(key=lambda r: (r["date"], r["ticker"]))
    return rows


def atr_series(hist_df, period: int = 14) -> dict:
    """날짜 -> 그 날짜 '전일까지'의 ATR.

    당일 고저를 포함해 계산하면 개장 전에 정해져 있어야 할 손절선이 미래
    정보를 쓰게 된다. exit_rules.Bar 가 명시한 타이밍 계약이다.
    """
    high = hist_df["High"].values.astype(float)
    low = hist_df["Low"].values.astype(float)
    close = hist_df["Close"].values.astype(float)
    dates = [f"{d:%Y-%m-%d}" for d in hist_df.index]

    tr = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - close[:-1]),
        np.abs(low[1:] - close[:-1]),
    ])

    out = {}
    for i in range(len(dates)):
        # tr[k] 는 봉 k+1 의 TR 이다(봉 k 와 k+1 로 계산). 따라서 봉 i 가
        # 열리기 전에 알 수 있는 TR 은 봉 1..i-1 의 것, 즉 tr[:i-1] 이다.
        # tr[:i] 로 자르면 tr[i-1] = 봉 i 자신의 TR 이 섞여 그 봉의 고저가
        # 자기 손절선 계산에 들어간다.
        available = tr[:max(i - 1, 0)]
        if len(available) >= period:
            out[dates[i]] = round(float(np.mean(available[-period:])), 4)
    return out


def fetch_bars(ticker: str) -> dict:
    """티커의 일봉을 날짜 -> exit_rules.Bar 로 반환한다. 실패하면 빈 dict."""
    try:
        df = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
    except Exception:
        return {}
    if df is None or df.empty:
        return {}

    df = df[df["Close"].notna()]
    if df.empty:
        return {}

    atrs = atr_series(df)
    bars = {}
    for idx, row in df.iterrows():
        date = f"{idx:%Y-%m-%d}"
        bars[date] = er.Bar(
            date=date,
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            atr14=atrs.get(date),
        )
    return bars
