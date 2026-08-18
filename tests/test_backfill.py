import pandas as pd

import backfill_history as bf


def _snap(date, count, sha):
    return {"date": date, "count": count, "sha": sha, "ts": date + "T03:00:00"}


def test_drops_snapshots_below_half_the_median():
    # 실제 git 이력의 종목 수. 3 / 133 / 773 이 손상본이다.
    snaps = [
        _snap("2026-07-31", 3, "a"),
        _snap("2026-07-31", 1064, "b"),
        _snap("2026-08-01", 1061, "c"),
        _snap("2026-08-02", 1061, "d"),
        _snap("2026-08-03", 133, "e"),
        _snap("2026-08-04", 1071, "f"),
        _snap("2026-08-17", 1093, "g"),
        _snap("2026-08-18", 1091, "h"),
        _snap("2026-08-18", 773, "i"),
        _snap("2026-08-18", 1087, "j"),
    ]

    kept = bf.drop_corrupt(snaps)
    kept_shas = {s["sha"] for s in kept}

    assert "a" not in kept_shas   # 3종목
    assert "e" not in kept_shas   # 133종목
    assert "i" not in kept_shas   # 773종목
    assert "b" in kept_shas
    assert "j" in kept_shas


def test_dedup_keeps_latest_per_date():
    snaps = [
        _snap("2026-08-18", 1091, "h"),
        _snap("2026-08-18", 1087, "j"),
    ]
    snaps[1]["ts"] = "2026-08-18T10:54:00"
    snaps[0]["ts"] = "2026-08-18T09:20:00"

    picked = bf.dedup_by_date(snaps)

    assert len(picked) == 1
    assert picked[0]["sha"] == "j"


def test_corrupt_run_does_not_shadow_good_snapshot_same_day():
    # 2026-08-18 실제 순서: 1091 -> 773(손상) -> 1087
    # 손상본 제거를 먼저 해야 1087이 살아남는다.
    snaps = [
        _snap("2026-08-17", 1093, "g"),
        _snap("2026-08-18", 1091, "h"),
        _snap("2026-08-18", 1087, "j"),
        _snap("2026-08-18", 773, "corrupt"),
    ]
    snaps[1]["ts"] = "2026-08-18T09:20:00"
    snaps[2]["ts"] = "2026-08-18T10:54:00"
    snaps[3]["ts"] = "2026-08-18T11:30:00"   # 손상본이 가장 늦음

    picked = bf.dedup_by_date(bf.drop_corrupt(snaps))
    by_date = {s["date"]: s["sha"] for s in picked}

    assert by_date["2026-08-18"] == "j"


def test_bar_limit_is_the_day_before_the_scan():
    # KST 8/19 06:00 스캔이 볼 수 있었던 마지막 봉은 8/18 세션이다.
    assert bf.bar_limit_for("2026-08-19") == "2026-08-18"


def test_slice_to_date_excludes_future_bars():
    idx = pd.date_range("2026-08-14", periods=5, freq="D")   # 14,15,16,17,18
    df = pd.DataFrame(
        {"Open": range(5), "High": range(5), "Low": range(5),
         "Close": range(5), "Volume": range(5)},
        index=idx,
    )

    sliced = bf.slice_to_date(df, bf.bar_limit_for("2026-08-17"))

    # 8/17 스캔 -> 8/16 봉까지만. 8/17·8/18 봉은 그 시점에 없었다.
    assert len(sliced) == 3
    assert f"{sliced.index[-1]:%Y-%m-%d}" == "2026-08-16"


def test_slice_to_date_returns_none_when_all_bars_are_future():
    idx = pd.date_range("2026-08-20", periods=3, freq="D")
    df = pd.DataFrame(
        {"Open": range(3), "High": range(3), "Low": range(3),
         "Close": range(3), "Volume": range(3)},
        index=idx,
    )

    assert bf.slice_to_date(df, bf.bar_limit_for("2026-08-17")) is None


def test_slice_to_date_handles_empty_input():
    assert bf.slice_to_date(None, "2026-08-17") is None
