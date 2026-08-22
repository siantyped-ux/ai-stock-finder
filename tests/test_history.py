import csv
import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import history


KST = timezone(timedelta(hours=9))


def _row(**over):
    row = {
        "ticker": "NVDA", "name": "NVIDIA", "market": "US", "sector": "반도체",
        "bar_date": "2026-08-17", "close": 183.22, "volume": 41203000,
        "avg_vol20": 38500000.0, "atr14": 4.81, "market_cap": 4500000000000,
        "tech": 72, "macro": 65, "filing": 71, "value": 58, "total": 68,
        "consensus": 2, "signal": "WATCH", "ev": 0.66, "target": 8,
        "hitl": False, "source": "live", "asset_type": "STOCK",
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


def test_write_snapshot_rejects_naive_scan_ts(tmp_path):
    naive = datetime(2026, 8, 19, 6, 0, 30)
    with pytest.raises(ValueError) as exc:
        history.write_snapshot([_row()], naive, out_dir=tmp_path)
    assert "tz" in str(exc.value).lower() or "시간대" in str(exc.value)


def test_write_snapshot_rejects_missing_required_field(tmp_path):
    scan_ts = datetime(2026, 8, 19, 6, 0, 30, tzinfo=KST)
    row = _row()
    del row["ticker"]
    with pytest.raises(ValueError) as exc:
        history.write_snapshot([row], scan_ts, out_dir=tmp_path)
    assert "ticker" in str(exc.value)


def test_write_snapshot_allows_missing_price_fields(tmp_path):
    # 시세 조회 실패·소급 적재에서는 가격 열이 비는 것이 정상이다.
    scan_ts = datetime(2026, 8, 19, 6, 0, 30, tzinfo=KST)
    row = _row()
    for f in ("bar_date", "close", "volume", "avg_vol20", "atr14", "market_cap"):
        del row[f]

    path = history.write_snapshot([row], scan_ts, out_dir=tmp_path)

    with open(path, encoding="utf-8", newline="") as f:
        got = list(csv.DictReader(f))
    assert got[0]["close"] == ""
    assert got[0]["market_cap"] == ""
    assert got[0]["ticker"] == "NVDA"


def test_write_snapshot_writes_nan_as_blank(tmp_path):
    scan_ts = datetime(2026, 8, 19, 6, 0, 30, tzinfo=KST)
    rows = [_row(close=float("nan"), atr14=float("nan"))]

    path = history.write_snapshot(rows, scan_ts, out_dir=tmp_path)

    with open(path, encoding="utf-8", newline="") as f:
        got = list(csv.DictReader(f))
    assert got[0]["close"] == ""
    assert got[0]["atr14"] == ""


def test_write_snapshot_leaves_no_file_when_a_later_row_is_bad(tmp_path):
    scan_ts = datetime(2026, 8, 19, 6, 0, 30, tzinfo=KST)
    rows = [_row(), _row(ticker="BAD", bogus=1)]

    with pytest.raises(ValueError):
        history.write_snapshot(rows, scan_ts, out_dir=tmp_path)

    # 잘린 CSV가 최종 경로에 남으면 안 된다. 임시 파일도 치워져야 한다.
    assert list(tmp_path.iterdir()) == []


def test_write_snapshot_overwrite_does_not_leave_stale_rows(tmp_path):
    scan_ts = datetime(2026, 8, 19, 6, 0, 30, tzinfo=KST)
    history.write_snapshot([_row(), _row(ticker="AAPL")], scan_ts, out_dir=tmp_path)

    path = history.write_snapshot([_row(ticker="TSLA")], scan_ts, out_dir=tmp_path)

    with open(path, encoding="utf-8", newline="") as f:
        got = list(csv.DictReader(f))
    assert [r["ticker"] for r in got] == ["TSLA"]


def test_price_fields_nan_volume_does_not_raise():
    df = _hist_df()
    df.loc[df.index[-1], "Volume"] = float("nan")

    got = history.price_fields(df, {})

    assert got["volume"] is None
    assert got["close"] == 159.0


def test_price_fields_nan_close_returns_none():
    df = _hist_df()
    df.loc[df.index[-1], "Close"] = float("nan")

    got = history.price_fields(df, {})

    assert got["close"] is None


def test_price_fields_nan_market_cap_returns_none():
    df = _hist_df()
    got = history.price_fields(df, {"marketCap": float("nan")})
    assert got["market_cap"] is None


def test_price_fields_market_cap_keeps_int_type():
    df = _hist_df()
    got = history.price_fields(df, {"marketCap": 123456})
    assert isinstance(got["market_cap"], int)


def test_atr_uses_the_gap_branch_not_just_the_daily_range():
    # 당일 레인지는 좁지만(0.5) 전일 종가 대비 갭이 크다(1.0).
    # high-low 만 쓰는 잘못된 구현이면 0.5가 나온다.
    idx = pd.date_range("2026-06-01", periods=20, freq="D")
    close = [100.0] * 19 + [101.0]
    high = [100.25] * 19 + [101.0]
    low = [99.75] * 19 + [100.5]
    df = pd.DataFrame(
        {"Open": close, "High": high, "Low": low, "Close": close,
         "Volume": [1000] * 20},
        index=idx,
    )

    got = history.price_fields(df, {})

    # 마지막 봉 TR = max(101.0-100.5, |101.0-100.0|, |100.5-100.0|) = 1.0
    # 나머지 봉 TR = 0.5 -> 14봉 평균 = (0.5*13 + 1.0) / 14
    assert got["atr14"] == round((0.5 * 13 + 1.0) / 14, 4)


# ─── asset_type 컬럼 (ETF 편입) ────────────────────────────
def test_fields_end_with_asset_type():
    """컬럼은 끝에만 추가한다. 중간에 넣으면 기존 CSV 와 호환이 깨진다."""
    assert history.FIELDS[-1] == "asset_type"


def test_filing_and_value_are_nullable_for_etfs():
    """ETF 는 filing/value 를 계산할 수 없어 빈 값으로 기록된다."""
    assert "filing" in history._NULLABLE_FIELDS
    assert "value" in history._NULLABLE_FIELDS


def test_etf_row_writes_with_blank_filing_and_value(tmp_path):
    row = {
        "ticker": "SPY", "name": "SPDR S&P 500 ETF Trust", "market": "US",
        "sector": "미분류", "asset_type": "ETF",
        "tech": 78, "macro": 64, "filing": None, "value": None,
        "total": 73, "consensus": 2, "signal": "BUY",
        "ev": 1.2, "target": 14, "hitl": False, "source": "live",
        "bar_date": "2026-08-22", "close": 640.0, "volume": 1000,
        "avg_vol20": 900.0, "atr14": 5.0, "market_cap": 6.2e11,
    }
    path = history.write_snapshot([row], history.kst_now(), out_dir=tmp_path)

    written = list(csv.DictReader(open(path, encoding="utf-8")))
    assert written[0]["asset_type"] == "ETF"
    assert written[0]["filing"] == ""
    assert written[0]["value"] == ""


def test_stock_row_still_requires_filing_and_value_values(tmp_path):
    """주식 행은 여전히 두 축을 채워야 한다 - 빈 값은 계산 실패를 뜻한다."""
    row = {
        "ticker": "AAPL", "name": "Apple Inc.", "market": "US",
        "sector": "IT", "asset_type": "STOCK",
        "tech": 70, "macro": 60, "filing": 65, "value": 55,
        "total": 64, "consensus": 1, "signal": "WATCH",
        "ev": 0.8, "target": 10, "hitl": False, "source": "live",
        "bar_date": "2026-08-22", "close": 230.0, "volume": 1000,
        "avg_vol20": 900.0, "atr14": 3.0, "market_cap": 3.4e12,
    }
    path = history.write_snapshot([row], history.kst_now(), out_dir=tmp_path)

    written = list(csv.DictReader(open(path, encoding="utf-8")))
    assert written[0]["asset_type"] == "STOCK"
    assert written[0]["filing"] == "65"
