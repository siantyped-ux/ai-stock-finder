import csv
from datetime import datetime, timedelta, timezone

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
