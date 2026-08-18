import csv
from datetime import datetime, timedelta, timezone

import pandas as pd

import history


KST = timezone(timedelta(hours=9))


def _row(**over):
    row = {
        "ticker": "NVDA", "name": "NVIDIA", "market": "US", "sector": "반도체",
        "bar_date": "2026-08-17", "close": 183.22, "volume": 41203000,
        "avg_vol20": 38500000.0, "atr14": 4.81, "market_cap": 4500000000000,
        "tech": 72, "macro": 65, "filing": 71, "value": 58, "total": 68,
        "consensus": 2, "signal": "WATCH", "ev": 0.66, "target": 8,
        "hitl": False, "source": "live",
    }
    row.update(over)
    return row


def test_write_snapshot_roundtrip(tmp_path):
    scan_ts = datetime(2026, 8, 19, 6, 0, 30, tzinfo=KST)
    rows = [_row(), _row(ticker="005930.KS", market="KR", signal="BUY")]

    path = history.write_snapshot(rows, scan_ts, out_dir=tmp_path)

    assert path.name == "2026-08-19.csv"

    with open(path, encoding="utf-8", newline="") as f:
        got = list(csv.DictReader(f))

    assert list(got[0].keys()) == list(history.FIELDS)
    assert len(got) == 2
    assert got[0]["ticker"] == "NVDA"
    assert got[0]["date"] == "2026-08-19"
    assert got[0]["scan_ts_kst"] == "2026-08-19T06:00:30+09:00"
    assert got[0]["close"] == "183.22"
    assert got[0]["hitl"] == "False"
    assert got[1]["ticker"] == "005930.KS"


def test_write_snapshot_missing_price_fields_are_blank(tmp_path):
    scan_ts = datetime(2026, 8, 19, 6, 0, 30, tzinfo=KST)
    rows = [_row(close=None, atr14=None, market_cap=None)]

    path = history.write_snapshot(rows, scan_ts, out_dir=tmp_path)

    with open(path, encoding="utf-8", newline="") as f:
        got = list(csv.DictReader(f))

    assert got[0]["close"] == ""
    assert got[0]["atr14"] == ""
    assert got[0]["market_cap"] == ""
    assert got[0]["ticker"] == "NVDA"


def test_write_snapshot_rejects_unknown_field(tmp_path):
    scan_ts = datetime(2026, 8, 19, 6, 0, 30, tzinfo=KST)
    rows = [_row(bogus=1)]

    try:
        history.write_snapshot(rows, scan_ts, out_dir=tmp_path)
    except ValueError as e:
        assert "bogus" in str(e)
    else:
        raise AssertionError("ValueError가 발생해야 한다")


def test_kst_now_has_offset():
    now = history.kst_now()
    assert now.utcoffset() == timedelta(hours=9)


def _hist_df(n=60):
    """등차로 오르는 합성 일봉. high-low = 2.0 고정이라 ATR이 정확히 2.0이 된다."""
    idx = pd.date_range("2026-06-01", periods=n, freq="D")
    close = [100.0 + i for i in range(n)]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [c + 1.0 for c in close],
            "Low": [c - 1.0 for c in close],
            "Close": close,
            "Volume": [1000 + i for i in range(n)],
        },
        index=idx,
    )


def test_price_fields_basic():
    df = _hist_df()
    got = history.price_fields(df, {"marketCap": 123456})

    assert got["bar_date"] == "2026-07-30"
    assert got["close"] == 159.0
    assert got["volume"] == 1059
    assert got["market_cap"] == 123456
    assert got["avg_vol20"] == round(sum(range(1040, 1060)) / 20, 2)


def test_price_fields_atr_is_true_range_average():
    df = _hist_df()
    got = history.price_fields(df, {})
    # high-low = 2.0, 전일종가 대비 갭 1.0 -> TR = max(2.0, 2.0, 0.0) = 2.0
    assert got["atr14"] == 2.0


def test_price_fields_short_history_returns_none_for_indicators():
    df = _hist_df(n=5)
    got = history.price_fields(df, {})

    assert got["close"] == 104.0        # 종가는 있음
    assert got["atr14"] is None         # 14봉 미만
    assert got["avg_vol20"] is None     # 20봉 미만


def test_price_fields_no_info():
    df = _hist_df()
    got = history.price_fields(df, None)
    assert got["market_cap"] is None
