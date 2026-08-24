"""선행 수익률 계산 테스트.

값이 틀리면 스코어링 변경을 반대로 판정하게 되므로, 경계 조건을 촘촘히 건다.

설계: docs/superpowers/specs/2026-08-24-flow-axis-design.md
"""
import pytest

import forward_returns as fr

# 종가가 100, 110, 121, ... 로 매일 10% 오르는 5거래일
PRICES = {
    "AAA": (["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"],
            [100.0, 110.0, 121.0, 133.1, 146.41]),
}


# ─── 날짜 탐색 ──────────────────────────────────────────────
@pytest.mark.parametrize("target,expected", [
    ("2026-08-03", 0),
    ("2026-08-05", 2),
    ("2026-08-07", 4),
    ("2026-08-09", 4),   # 주말 - 그 이하의 마지막 봉
    ("2026-08-02", -1),  # 첫 봉보다 이르다
])
def test_index_at_or_before(target, expected):
    assert fr._index_at_or_before(PRICES["AAA"][0], target) == expected


def test_index_handles_a_holiday_gap():
    """bar_date 가 휴장일이면 그 이전 마지막 거래일을 잡아야 한다."""
    dates = ["2026-08-03", "2026-08-07"]
    assert fr._index_at_or_before(dates, "2026-08-05") == 0


# ─── 선행 수익률 ────────────────────────────────────────────
def test_one_day_forward_return():
    assert fr.forward_return(PRICES, "AAA", "2026-08-03", 1) == pytest.approx(10.0)


def test_multi_day_forward_return():
    # 100 -> 121
    assert fr.forward_return(PRICES, "AAA", "2026-08-03", 2) == pytest.approx(21.0)


def test_negative_return():
    prices = {"BBB": (["2026-08-03", "2026-08-04"], [100.0, 90.0])}
    assert fr.forward_return(prices, "BBB", "2026-08-03", 1) == pytest.approx(-10.0)


def test_returns_none_when_horizon_exceeds_available_bars():
    """마지막 봉으로 대체하지 않는다. 구간이 짧아진 행이 섞이면 평균이 왜곡된다."""
    assert fr.forward_return(PRICES, "AAA", "2026-08-06", 5) is None
    assert fr.forward_return(PRICES, "AAA", "2026-08-07", 1) is None


def test_returns_none_for_unknown_ticker():
    assert fr.forward_return(PRICES, "ZZZ", "2026-08-03", 1) is None


def test_returns_none_before_first_bar():
    assert fr.forward_return(PRICES, "AAA", "2020-01-01", 1) is None


def test_returns_none_on_zero_base_price():
    prices = {"CCC": (["2026-08-03", "2026-08-04"], [0.0, 10.0])}
    assert fr.forward_return(prices, "CCC", "2026-08-03", 1) is None


def test_bar_date_on_a_holiday_measures_from_the_prior_close():
    """휴장일 bar_date 는 직전 종가를 기준으로 잡는다.

    8/05·8/06 봉이 없는 종목에서 bar_date 8/05 는 8/04 종가(110)를 기준으로
    삼아야 하고, 1거래일 뒤는 8/07 종가(150)다.
    """
    prices = {"AAA": (["2026-08-03", "2026-08-04", "2026-08-07"],
                      [100.0, 110.0, 150.0])}
    assert fr.forward_return(prices, "AAA", "2026-08-05", 1) == pytest.approx(
        (150.0 / 110.0 - 1) * 100)


# ─── 신호의 값어치 ──────────────────────────────────────────
def test_edge_is_buy_mean_minus_universe_mean():
    data = {
        "BUY계열·STOCK": {5: [3.0, 5.0]},     # 평균 4.0
        "전체·STOCK": {5: [1.0, 1.0, 1.0, 1.0]},  # 평균 1.0
    }
    got = fr.edge(data, 5, "STOCK")
    assert got[0] == pytest.approx(3.0)
    assert got[1] == 2


def test_edge_is_negative_when_buys_lag_the_universe():
    """이 부호가 판정의 전부다. 음수면 신호가 무작위보다 못하다는 뜻이다."""
    data = {
        "BUY계열·STOCK": {5: [-1.0, -1.0]},
        "전체·STOCK": {5: [2.0, 2.0]},
    }
    assert fr.edge(data, 5, "STOCK")[0] == pytest.approx(-3.0)


def test_edge_is_none_without_enough_samples():
    """아카이브 막바지에 등장한 자산군은 선행 구간이 없어 표본이 빈다."""
    assert fr.edge({"BUY계열·ETF": {5: [1.0]}, "전체·ETF": {5: []}}, 5, "ETF") is None
    assert fr.edge({}, 5, "ETF") is None


# ─── 출력 ───────────────────────────────────────────────────
def test_stat_line_reports_sample_size_and_win_rate():
    out = fr.stat_line("BUY", [1.0, -1.0, 2.0, 4.0])
    assert "n=4" in out and "승률 75.0%" in out


def test_stat_line_flags_a_thin_sample():
    assert "표본 부족" in fr.stat_line("BUY", [1.0])
    assert "표본 부족" in fr.stat_line("BUY", [])
