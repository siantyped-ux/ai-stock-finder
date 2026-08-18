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
