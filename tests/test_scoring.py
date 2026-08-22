"""종합점수·합의·신호 판정 순수 함수 테스트.

ETF 편입으로 판정 로직에 손을 대므로, 주식 판정이 한 건도 바뀌지 않는지를
회귀 테스트로 고정한다. 설계: docs/superpowers/specs/2026-08-22-us-etf-universe-design.md
"""
import pytest

import stock_finder as sf


# ─── 주식 신호 판정 회귀 (변경 전후로 동일해야 한다) ───────────
@pytest.mark.parametrize("total,cons,expected", [
    (80, 3, "STRONG_BUY"),
    (85, 4, "STRONG_BUY"),
    (80, 2, "WATCH"),      # cons 부족 → 강등
    (70, 3, "BUY"),
    (79, 3, "BUY"),
    (70, 2, "WATCH"),      # cons 부족 → 강등
    (60, 2, "WATCH"),
    (60, 1, "HOLD"),
    (45, 0, "HOLD"),
    (44, 4, "AVOID"),
    (35, 0, "AVOID"),
])
def test_stock_signal_unchanged(total, cons, expected):
    assert sf.calc_signal(total, cons) == expected


# ─── ETF 신호 판정 (축이 2개) ──────────────────────────────
@pytest.mark.parametrize("total,cons,expected", [
    (80, 2, "STRONG_BUY"),   # 2/2 = 1.00 >= 0.75
    (80, 1, "WATCH"),        # 1/2 = 0.50 < 0.75 → 강등
    (70, 2, "BUY"),
    (70, 1, "WATCH"),        # 1/2 = 0.50 → cons>=2 상당도 못 채움
    (60, 1, "WATCH"),        # 0.50 >= 0.50 → WATCH 는 통과
    (60, 0, "HOLD"),
    (44, 2, "AVOID"),
])
def test_etf_signal_uses_ratio(total, cons, expected):
    assert sf.calc_signal(total, cons, n_axes=2) == expected


def test_signal_ratio_thresholds_match_stock_counts():
    """비율 임계가 기존 개수 임계와 정확히 대응하는지."""
    # 주식 3/4 = 0.75 (BUY 기준), 2/4 = 0.50 (WATCH 기준)
    assert sf.calc_signal(70, 3, n_axes=4) == "BUY"
    assert sf.calc_signal(70, 2, n_axes=4) == "WATCH"
    assert sf.calc_signal(60, 2, n_axes=4) == "WATCH"
    assert sf.calc_signal(60, 1, n_axes=4) == "HOLD"


# ─── ETF 종합점수 (tech/macro 재정규화) ─────────────────────
def test_etf_total_renormalizes_two_axes():
    # 0.35/0.55 = 0.63636..., 0.20/0.55 = 0.36363...
    assert sf.calc_total_etf(100, 100) == 100
    assert sf.calc_total_etf(0, 0) == 0


def test_etf_total_weights_tech_more_than_macro():
    # tech 가 높을 때가 macro 가 높을 때보다 점수가 높아야 한다.
    assert sf.calc_total_etf(80, 40) > sf.calc_total_etf(40, 80)


def test_etf_total_matches_hand_calculation():
    # 80*0.63636 + 60*0.36363 = 50.909 + 21.818 = 72.727 → 73
    assert sf.calc_total_etf(80, 60) == 73


def test_etf_total_can_reach_the_70_filter():
    """중립값 50 방식이었다면 못 넘었을 구간을 넘는지."""
    assert sf.calc_total_etf(75, 62) >= 70


def test_etf_total_returns_int():
    assert isinstance(sf.calc_total_etf(71, 63), int)
