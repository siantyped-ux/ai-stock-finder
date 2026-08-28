"""트랙 정의 테스트.

주식과 ETF 는 점수 척도가 달라 유니버스부터 산출물까지 전부 따로 둔다.
정의가 여러 파일에 흩어지면 한쪽만 고쳐져 산출물이 서로를 덮어쓴다.
"""
import pytest

import perf_report
import stock_finder as sf
import tracks


def test_every_track_defines_the_same_keys():
    keys = {"label", "history", "dashboard", "suffix", "max_correlation"}
    for name, spec in tracks.TRACKS.items():
        assert set(spec) == keys, name


def test_the_etf_track_blocks_duplicate_bets():
    # 2026-08-22 스캔의 BUY 90건에 XLV·VHT·IYH·FHLC·IXJ 가 함께 들어 있었고
    # 상관이 0.97~0.999 였다. 끄고 두면 신호 하나를 다섯 번 센다.
    assert tracks.max_correlation("etf") == 0.90


def test_the_stock_track_leaves_the_guard_off():
    # 주식에 상관 상한이 이로운지 재 본 적이 없다. 근거 없이 켜지 않는다.
    assert tracks.max_correlation("stocks") == 1.0


def test_max_correlation_rejects_an_unknown_track():
    with pytest.raises(ValueError):
        tracks.max_correlation("nope")


def test_the_report_applies_the_track_correlation_limit():
    # 리포트가 트랙별 상한을 실제로 백테스트에 넘기는지. 이게 끊기면 ETF
    # 리포트가 복제본을 다섯 개 산 포트폴리오를 성과라고 보고한다.
    assert perf_report.track_limits("etf").max_correlation == 0.90
    assert perf_report.track_limits("stocks").max_correlation == 1.0


def test_the_report_does_not_silently_cap_positions():
    # 동시 보유 상한은 재 본 적이 없다. 켜면 예전 결과의 의미가 바뀐다.
    for key in tracks.TRACKS:
        assert perf_report.track_limits(key).max_positions == 0


@pytest.mark.parametrize("key", ["history", "dashboard", "suffix"])
def test_the_two_tracks_never_share_an_output(key):
    """한 값이라도 겹치면 한 트랙이 다른 트랙의 산출물을 덮어쓴다."""
    assert tracks.TRACKS["stocks"][key] != tracks.TRACKS["etf"][key]


def test_paths_rejects_an_unknown_track():
    with pytest.raises(ValueError):
        tracks.paths("nope")


def test_history_glob_points_at_the_track_archive():
    assert tracks.history_glob("stocks") == "history/*.csv"
    assert tracks.history_glob("etf") == "history_etf/*.csv"


def test_the_scanner_and_the_report_share_one_definition():
    # 재정의하면 한쪽만 고쳐진다.
    assert sf.TRACKS is tracks.TRACKS
    assert perf_report.tracks.TRACKS is tracks.TRACKS


def test_the_scanner_still_exposes_track_paths():
    assert sf.track_paths("etf")["history"] == "history_etf"
