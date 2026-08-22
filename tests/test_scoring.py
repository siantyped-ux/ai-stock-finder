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


# ─── ETF BUY 임계 완화 (1/2 로도 BUY) ──────────────────────
# macro 축이 NEUTRAL 국면에서 최대 65 라 70 문턱을 못 넘는다. ETF 는 축이
# 둘뿐이라 그 순간 1/2 로 고정되어 BUY 가 구조적으로 불가능했다.
def test_etf_reaches_buy_with_one_of_two_axes():
    got = sf.calc_signal(82, 1, n_axes=2, buy_ratio=sf.ETF_BUY_RATIO)
    assert got == "BUY"


def test_etf_strong_buy_still_needs_both_axes():
    """최상위 등급은 완화하지 않는다. 1/2 로 열면 ETF 대부분이 STRONG_BUY 가
    되고 HITL 까지 전부 켜진다."""
    assert sf.calc_signal(85, 1, n_axes=2, buy_ratio=sf.ETF_BUY_RATIO) == "BUY"
    assert sf.calc_signal(85, 2, n_axes=2, buy_ratio=sf.ETF_BUY_RATIO) == "STRONG_BUY"


def test_etf_low_total_still_fails_regardless_of_ratio():
    """완화한 것은 합의 비율이지 점수 문턱이 아니다."""
    assert sf.calc_signal(69, 2, n_axes=2, buy_ratio=sf.ETF_BUY_RATIO) == "WATCH"
    assert sf.calc_signal(44, 2, n_axes=2, buy_ratio=sf.ETF_BUY_RATIO) == "AVOID"


def test_relaxed_ratio_does_not_leak_into_stock_judgement():
    """주식은 기본값을 쓴다. 2/4 로 BUY 가 나오면 안 된다."""
    assert sf.calc_signal(75, 2) == "WATCH"
    assert sf.calc_signal(75, 2, n_axes=4, buy_ratio=sf.STOCK_BUY_RATIO) == "WATCH"


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


# ─── ETF 합의 개수 ─────────────────────────────────────────
def test_etf_consensus_counts_two_axes():
    assert sf.calc_consensus_etf(80, 75) == 2
    assert sf.calc_consensus_etf(80, 60) == 1
    assert sf.calc_consensus_etf(50, 60) == 0


def test_etf_consensus_boundary_is_inclusive():
    assert sf.calc_consensus_etf(70, 70) == 2
    assert sf.calc_consensus_etf(69, 69) == 0


# ─── 출력 필터 ─────────────────────────────────────────────
ROWS = [
    {"t": "AAA", "at": "STOCK", "total": 77, "signal": "BUY"},
    {"t": "BBB", "at": "STOCK", "total": 70, "signal": "WATCH"},
    {"t": "CCC", "at": "STOCK", "total": 69, "signal": "WATCH"},
    {"t": "DDD", "at": "STOCK", "total": 35, "signal": "AVOID"},
]


def test_filter_keeps_at_and_above_threshold():
    kept = sf.filter_for_output(ROWS, min_total=70, min_total_etf=78)
    assert [r["t"] for r in kept] == ["AAA", "BBB"]


def test_filter_threshold_zero_keeps_everything():
    assert len(sf.filter_for_output(ROWS, min_total=0, min_total_etf=0)) == 4


def test_filter_does_not_mutate_input():
    sf.filter_for_output(ROWS, min_total=70, min_total_etf=78)
    assert len(ROWS) == 4


def test_filter_drops_rows_without_total():
    rows = [{"t": "EEE", "at": "STOCK", "signal": "HOLD"}]
    assert sf.filter_for_output(rows, min_total=70, min_total_etf=78) == []


# ─── ETF 별도 임계 ─────────────────────────────────────────
# ETF 는 분산 효과로 변동성이 낮아 tech 점수가 높게 나온다. 같은 70점 선으로
# 자르면 대시보드가 ETF 로 뒤덮인다 (2026-08-22 실측: 주식 4.7% 통과 vs
# ETF 16.8%).
MIXED = [
    {"t": "STK", "at": "STOCK", "total": 72, "signal": "BUY"},
    {"t": "ETF77", "at": "ETF", "total": 77, "signal": "WATCH"},
    {"t": "ETF78", "at": "ETF", "total": 78, "signal": "BUY"},
]


def test_etf_uses_its_own_threshold():
    kept = sf.filter_for_output(MIXED, min_total=70, min_total_etf=78)
    assert [r["t"] for r in kept] == ["STK", "ETF78"]


def test_stock_threshold_does_not_apply_to_etfs():
    """ETF77 은 주식 기준(70)은 넘지만 ETF 기준(78)은 못 넘는다."""
    kept = sf.filter_for_output(MIXED, min_total=70, min_total_etf=78)
    assert "ETF77" not in [r["t"] for r in kept]


def test_etf_threshold_does_not_apply_to_stocks():
    rows = [{"t": "STK", "at": "STOCK", "total": 72, "signal": "BUY"}]
    kept = sf.filter_for_output(rows, min_total=70, min_total_etf=78)
    assert [r["t"] for r in kept] == ["STK"]


def test_missing_asset_type_is_treated_as_stock():
    """옛 행에는 at 키가 없다. 그 시절 유니버스는 전부 개별주식이었다."""
    rows = [{"t": "OLD", "total": 72, "signal": "BUY"}]
    kept = sf.filter_for_output(rows, min_total=70, min_total_etf=78)
    assert [r["t"] for r in kept] == ["OLD"]


# ─── 지표 카드 요약 (필터 이전 전체 기준) ────────────────────
SCANNED = [
    {"t": "A", "at": "STOCK", "total": 75, "signal": "BUY", "hitl": False},
    {"t": "B", "at": "STOCK", "total": 62, "signal": "WATCH", "hitl": False},
    {"t": "C", "at": "STOCK", "total": 40, "signal": "AVOID", "hitl": True},
    {"t": "D", "at": "STOCK", "total": 38, "signal": "AVOID", "hitl": True},
    {"t": "E", "at": "ETF", "total": 80, "signal": "STRONG_BUY", "hitl": False},
]


def test_summary_counts_the_full_scan_not_the_filtered_list():
    """이게 이 함수의 존재 이유다. 표시 목록만 세면 HITL 이 0이 된다."""
    shown = sf.filter_for_output(SCANNED, min_total=70, min_total_etf=78)
    got = sf.scan_summary(SCANNED, shown)

    assert got["scanned"] == 5
    assert got["shown"] == 2          # A(75) + E(80)
    assert got["hitl"] == 2           # C, D — 둘 다 필터에서 빠졌다
    assert got["avoid"] == 2


def test_summary_hitl_survives_a_filter_that_excludes_every_avoid():
    """AVOID 는 총점 45 미만이라 70점 필터를 절대 통과하지 못한다."""
    shown = sf.filter_for_output(SCANNED, min_total=70, min_total_etf=78)
    assert all(r["signal"] != "AVOID" for r in shown)
    assert sf.scan_summary(SCANNED, shown)["hitl"] == 2


def test_summary_signal_breakdown():
    got = sf.scan_summary(SCANNED, [])
    assert (got["strong_buy"], got["buy"], got["watch"]) == (1, 1, 1)


def test_summary_handles_an_empty_scan():
    got = sf.scan_summary([], [])
    assert got["scanned"] == 0 and got["hitl"] == 0
