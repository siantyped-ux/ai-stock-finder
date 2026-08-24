"""종합점수·합의·신호 판정 순수 함수 테스트.

신호 판정(calc_signal)의 총점·비율 임계는 변하지 않았다. 바뀐 것은 축 구성과
가중치이며, 그 회귀는 calc_total / calc_total_etf 쪽에 고정한다.

설계: docs/superpowers/specs/2026-08-24-flow-axis-design.md
      docs/superpowers/specs/2026-08-22-us-etf-universe-design.md (선행)
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


# ─── ETF BUY 문턱 (완화 철회) ──────────────────────────────
def test_etf_buy_requires_both_axes():
    """완화(0.50)를 되돌렸다. ETF 는 가진 축 100% 가 70 이상이어야 BUY 다.

    완화가 있던 동안 ETF 판정은 tech 단일 축 도장으로 붕괴했다 - 2026-08-22
    실측 BUY 88건 전부 tech 하나로만 통과했고 macro >= 70 은 0건이었다.
    macro 를 빼고 flow 를 넣어 두 축 다 살렸으므로 완화가 필요 없다.
    """
    assert sf.calc_signal(82, 1, n_axes=2) == "WATCH"
    assert sf.calc_signal(82, 2, n_axes=2) == "STRONG_BUY"


def test_etf_bar_is_stricter_than_stock():
    """ETF 2/2(100%) 는 주식 3/4(75%) 보다 엄격하다."""
    assert sf.calc_signal(72, 1, n_axes=2) == "WATCH"   # 1/2 = 0.50
    assert sf.calc_signal(72, 3, n_axes=4) == "BUY"     # 3/4 = 0.75


def test_etf_low_total_still_fails_regardless_of_ratio():
    """합의를 다 채워도 점수 문턱은 따로 넘어야 한다."""
    assert sf.calc_signal(69, 2, n_axes=2) == "WATCH"
    assert sf.calc_signal(44, 2, n_axes=2) == "AVOID"


def test_no_asset_class_specific_buy_ratio_remains():
    """ETF_BUY_RATIO 상수는 제거되었다. 자산군별 완화가 되살아나면 실패한다."""
    assert not hasattr(sf, "ETF_BUY_RATIO")


def test_stock_judgement_uses_the_default_ratio():
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


# ─── ETF 종합점수 (tech/flow 재정규화) ──────────────────────
def test_etf_total_renormalizes_two_axes():
    # 0.30/0.50 = 0.60, 0.20/0.50 = 0.40
    assert sf.calc_total_etf(100, 100) == 100
    assert sf.calc_total_etf(0, 0) == 0


def test_etf_total_weights_tech_more_than_flow():
    assert sf.calc_total_etf(80, 40) > sf.calc_total_etf(40, 80)


def test_etf_total_matches_hand_calculation():
    # 80*0.60 + 60*0.40 = 48 + 24 = 72
    assert sf.calc_total_etf(80, 60) == 72


def test_etf_total_matches_stock_scale():
    """두 축이 모두 70 이면 주식이 네 축 모두 70 일 때와 같은 70 이 나온다.

    척도 통일이 이 축 설계의 목적이다. 중립 50 대입 방식이었다면 같은
    입력에서 45점이 나와 ETF 가 70 문턱에 영영 닿지 못했다.
    """
    assert sf.calc_total_etf(70, 70) == sf.calc_total(70, 70, 70, 70) == 70


def test_etf_total_returns_int():
    assert isinstance(sf.calc_total_etf(71, 63), int)


# ─── 주식 종합점수 (macro 제거 · flow 신설) ──────────────────
def test_stock_total_matches_hand_calculation():
    # 80*0.30 + 60*0.20 + 70*0.30 + 50*0.20 = 24 + 12 + 21 + 10 = 67
    assert sf.calc_total(80, 60, 70, 50) == 67


def test_stock_total_weights_sum_to_one():
    assert sf.calc_total(100, 100, 100, 100) == 100
    assert sf.calc_total(0, 0, 0, 0) == 0


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
    kept = sf.filter_for_output(ROWS, min_total=70)
    assert [r["t"] for r in kept] == ["AAA", "BBB"]


def test_filter_threshold_zero_keeps_everything():
    assert len(sf.filter_for_output(ROWS, min_total=0)) == 4


def test_filter_does_not_mutate_input():
    sf.filter_for_output(ROWS, min_total=70)
    assert len(ROWS) == 4


def test_filter_drops_rows_without_total():
    rows = [{"t": "EEE", "at": "STOCK", "signal": "HOLD"}]
    assert sf.filter_for_output(rows, min_total=70) == []


# ─── 단일 임계 (자산군별 임계 제거) ──────────────────────────
# min_total_etf=78 은 척도가 어긋난 것을 표시 단계에서 가리던 땜질이었고
# 순위는 고치지 못했다 - 주식 최고점이 77인데 77점 ETF 가 전 종목 위에 섰다.
# flow 축으로 척도를 맞췄으므로 임계는 하나다.
MIXED = [
    {"t": "STK", "at": "STOCK", "total": 72, "signal": "BUY"},
    {"t": "ETF77", "at": "ETF", "total": 77, "signal": "WATCH"},
    {"t": "ETF69", "at": "ETF", "total": 69, "signal": "WATCH"},
]


def test_same_threshold_applies_to_both_asset_classes():
    kept = sf.filter_for_output(MIXED, min_total=70)
    assert [r["t"] for r in kept] == ["STK", "ETF77"]


def test_filter_does_not_read_asset_type():
    """자산군별 분기가 되살아나면 실패한다."""
    rows = [{"t": "NOAT", "total": 72, "signal": "BUY"}]
    assert [r["t"] for r in sf.filter_for_output(rows, min_total=70)] == ["NOAT"]


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
    shown = sf.filter_for_output(SCANNED, min_total=70)
    got = sf.scan_summary(SCANNED, shown)

    assert got["scanned"] == 5
    assert got["shown"] == 2          # A(75) + E(80)
    assert got["hitl"] == 2           # C, D — 둘 다 필터에서 빠졌다
    assert got["avoid"] == 2


def test_summary_hitl_survives_a_filter_that_excludes_every_avoid():
    """AVOID 는 총점 45 미만이라 70점 필터를 절대 통과하지 못한다."""
    shown = sf.filter_for_output(SCANNED, min_total=70)
    assert all(r["signal"] != "AVOID" for r in shown)
    assert sf.scan_summary(SCANNED, shown)["hitl"] == 2


def test_summary_signal_breakdown():
    got = sf.scan_summary(SCANNED, [])
    assert (got["strong_buy"], got["buy"], got["watch"]) == (1, 1, 1)


def test_summary_handles_an_empty_scan():
    got = sf.scan_summary([], [])
    assert got["scanned"] == 0 and got["hitl"] == 0
