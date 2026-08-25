"""트랙 정의 테스트.

주식과 ETF 는 점수 척도가 달라 유니버스부터 산출물까지 전부 따로 둔다.
정의가 여러 파일에 흩어지면 한쪽만 고쳐져 산출물이 서로를 덮어쓴다.
"""
import pytest

import perf_report
import stock_finder as sf
import tracks


def test_every_track_defines_the_same_keys():
    keys = {"label", "history", "dashboard", "suffix", "report_prefix"}
    for name, spec in tracks.TRACKS.items():
        assert set(spec) == keys, name


@pytest.mark.parametrize("key", ["history", "dashboard", "suffix",
                                 "report_prefix"])
def test_the_two_tracks_never_share_an_output(key):
    """한 값이라도 겹치면 한 트랙이 다른 트랙의 산출물을 덮어쓴다."""
    assert tracks.TRACKS["stocks"][key] != tracks.TRACKS["etf"][key]


def test_paths_rejects_an_unknown_track():
    with pytest.raises(ValueError):
        tracks.paths("nope")


def test_history_glob_points_at_the_track_archive():
    assert tracks.history_glob("stocks") == "history/*.csv"
    assert tracks.history_glob("etf") == "history_etf/*.csv"


def test_report_paths_do_not_collide_on_the_same_day():
    a = tracks.report_path("stocks", "reports", "2026-08-25")
    b = tracks.report_path("etf", "reports", "2026-08-25")
    assert a != b
    assert a.name == "perf_2026-08-25.xlsx"
    assert b.name == "perf_etf_2026-08-25.xlsx"


def test_the_scanner_and_the_report_share_one_definition():
    # 재정의하면 한쪽만 고쳐진다.
    assert sf.TRACKS is tracks.TRACKS
    assert perf_report.tracks.TRACKS is tracks.TRACKS


def test_the_scanner_still_exposes_track_paths():
    assert sf.track_paths("etf")["history"] == "history_etf"
