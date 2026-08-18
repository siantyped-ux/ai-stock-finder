import numpy as np
import pandas as pd

import stock_finder


def _df(closes):
    idx = pd.date_range("2026-06-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1 if c == c else np.nan for c in closes],
            "Low": [c - 1 if c == c else np.nan for c in closes],
            "Close": closes,
            "Volume": [1000 + i for i in range(len(closes))],
        },
        index=idx,
    )


def test_drops_trailing_unsettled_bar():
    # yfinance가 미국 종목에 OHLC=NaN, Volume 있음인 미확정 봉을 붙여 보낸다.
    df = _df([100.0, 101.0, 102.0, float("nan")])

    got = stock_finder.drop_unsettled_bars(df)

    assert len(got) == 3
    assert got["Close"].iloc[-1] == 102.0
    assert f"{got.index[-1]:%Y-%m-%d}" == "2026-06-03"


def test_keeps_a_clean_frame_untouched():
    df = _df([100.0, 101.0, 102.0])
    got = stock_finder.drop_unsettled_bars(df)
    assert len(got) == 3


def test_drops_interior_nan_bar_too():
    df = _df([100.0, float("nan"), 102.0])
    got = stock_finder.drop_unsettled_bars(df)
    assert list(got["Close"]) == [100.0, 102.0]


def test_empty_frame_survives():
    df = _df([])
    got = stock_finder.drop_unsettled_bars(df)
    assert len(got) == 0
