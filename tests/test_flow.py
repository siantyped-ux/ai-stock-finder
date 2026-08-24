"""수급(flow) 축 순수 함수 테스트.

지표를 손으로 계산할 수 있는 인공 시계열로 검증한다. 실제 종목 데이터를
쓰지 않는 것은 의도다 - 시세는 변하고, 변하면 테스트가 이유 없이 깨진다.

설계: docs/superpowers/specs/2026-08-24-flow-axis-design.md
"""
import numpy as np
import pytest

import flow


def series(closes, vols=None, spread=1.0):
    """종가 목록으로 OHLCV 프레임 흉내를 낸다.

    pandas 를 쓰지 않는 것은 의도다. flow 는 .values 만 읽으므로 numpy 를
    감싼 최소 객체로 충분하고, 테스트가 pandas 버전에 묶이지 않는다.
    """
    c = np.asarray(closes, dtype=float)
    v = np.asarray(vols if vols is not None else [1e6] * len(c), dtype=float)

    class _Col:
        def __init__(self, a):
            self.values = a

    return {"Close": _Col(c), "High": _Col(c + spread), "Low": _Col(c - spread),
            "Volume": _Col(v)}


# ─── CMF ────────────────────────────────────────────────────
def test_cmf_close_at_high_is_positive():
    """종가가 고가에 붙으면 매수 우위(+1)."""
    n = 20
    c = np.full(n, 100.0)
    assert flow.cmf(c, c - 10, c, np.full(n, 1e6)) == pytest.approx(1.0)


def test_cmf_close_at_low_is_negative():
    n = 20
    c = np.full(n, 100.0)
    assert flow.cmf(c + 10, c, c, np.full(n, 1e6)) == pytest.approx(-1.0)


def test_cmf_zero_range_bars_do_not_produce_nan():
    """고가=저가 인 봉(거래정지)이 섞여도 0 으로 나누지 않는다."""
    n = 20
    c = np.full(n, 100.0)
    out = flow.cmf(c, c, c, np.full(n, 1e6))
    assert out == 0.0 and np.isfinite(out)


def test_cmf_zero_volume_is_neutral():
    n = 20
    c = np.full(n, 100.0)
    assert flow.cmf(c + 1, c - 1, c, np.zeros(n)) == 0.0


# ─── OBV ────────────────────────────────────────────────────
def test_obv_accumulates_on_up_days():
    c = np.array([10.0, 11.0, 12.0])
    v = np.array([100.0, 200.0, 300.0])
    assert list(flow.obv(c, v)) == [0.0, 200.0, 500.0]


def test_obv_subtracts_on_down_days():
    c = np.array([10.0, 9.0, 8.0])
    v = np.array([100.0, 200.0, 300.0])
    assert list(flow.obv(c, v)) == [0.0, -200.0, -500.0]


def test_obv_ignores_flat_days():
    c = np.array([10.0, 10.0, 11.0])
    v = np.array([100.0, 200.0, 300.0])
    assert list(flow.obv(c, v)) == [0.0, 0.0, 300.0]


# ─── 표준화 기울기 ───────────────────────────────────────────
def test_std_slope_sign_follows_direction():
    assert flow._std_slope(np.arange(60, dtype=float)) > 0
    assert flow._std_slope(np.arange(60, 0, -1, dtype=float)) < 0


def test_std_slope_is_scale_invariant():
    """단위가 달라도 같은 기울기가 나와야 OBV 와 가격을 비교할 수 있다."""
    a = np.arange(60, dtype=float)
    assert flow._std_slope(a) == pytest.approx(flow._std_slope(a * 1e9))


def test_std_slope_flat_series_is_zero():
    assert flow._std_slope(np.full(60, 7.0)) == 0.0


# ─── U/D Volume Ratio ───────────────────────────────────────
def test_ud_ratio_all_up_days_is_capped():
    c = np.arange(1.0, 52.0)
    assert flow.ud_volume_ratio(c, np.full(len(c), 1e6)) == 10.0


def test_ud_ratio_balanced_is_one():
    """상승 2일·하락 2일에 거래량이 같으면 1 이다."""
    c = np.array([10.0, 11.0, 10.0, 11.0, 10.0])
    assert flow.ud_volume_ratio(c, np.full(len(c), 1e6)) == pytest.approx(1.0)


def test_ud_ratio_weights_by_volume():
    """같은 상승·하락 일수라도 상승일 거래량이 2배면 비율은 2 다."""
    c = np.array([10.0, 11.0, 10.0, 11.0, 10.0])
    v = np.array([1e6, 2e6, 1e6, 2e6, 1e6])
    assert flow.ud_volume_ratio(c, v) == pytest.approx(2.0)


# ─── 종합 점수 ───────────────────────────────────────────────
def test_short_history_returns_placeholder():
    out, reasons = flow.calc_flow_score(series(list(range(50))))
    assert out == 40 and "데이터 부족" in reasons[0]


def test_score_is_clipped_to_0_100():
    """모든 항목이 한 방향으로 몰려도 범위를 벗어나지 않는다."""
    n = 120
    up = np.linspace(100, 300, n)
    score, _ = flow.calc_flow_score(series(up, np.linspace(1e6, 5e6, n)))
    assert 0 <= score <= 100

    down = np.linspace(300, 100, n)
    score2, _ = flow.calc_flow_score(series(down, np.linspace(5e6, 1e5, n)))
    assert 0 <= score2 <= 100


def test_accumulation_scores_above_distribution():
    """같은 가격 경로라도 종가 위치와 거래량 분포가 반대면 점수가 갈린다."""
    n = 120
    price = np.linspace(100, 130, n)
    vol = np.full(n, 5e6)

    strong = flow.calc_flow_score(series(price, vol))[0]
    # 하락일에만 거래량이 몰린 같은 가격 경로
    weak_price = np.linspace(130, 100, n)
    weak = flow.calc_flow_score(series(weak_price, vol))[0]
    assert strong > weak


def test_illiquid_name_is_disqualified():
    """거래대금이 하한 미만이면 감점이 아니라 판정 불가다.

    네 컴포넌트가 모두 거래량 기반이라, 거래량이 없으면 감점할 지표가
    아니라 믿을 수 없는 지표가 된다. TMH(거래대금 $0.0M)가 flow 71 로
    BUY 를 통과한 2026-08-24 실측이 근거다.
    """
    n = 120
    price = np.full(n, 10.0)
    score, reasons = flow.calc_flow_score(series(price, np.full(n, 1e4)))
    assert score == 40
    assert any("판정 불가" in r for r in reasons)


def test_illiquid_uptrend_cannot_reach_buy_threshold():
    """유동성 미달이면 추세가 아무리 좋아도 70 을 못 넘는다."""
    n = 120
    # 거래대금 $0.1M 수준이지만 가격은 완벽한 상승 추세
    price = np.linspace(100, 300, n)
    score, _ = flow.calc_flow_score(series(price, np.full(n, 500.0)))
    assert score < 70


def test_divergence_penalises_rising_price_with_falling_obv():
    """가격은 오르는데 물량이 나가면 감점된다."""
    n = 120
    # 계단식 상승이되 하락일 거래량이 압도적으로 큰 경로
    price = np.array([100 + i * 0.2 + (2.0 if i % 2 else 0.0) for i in range(n)])
    vol = np.array([1e5 if i % 2 else 9e6 for i in range(n)])
    score, reasons = flow.calc_flow_score(series(price, vol))
    assert score <= 50


def test_reasons_are_capped_at_five():
    n = 120
    score, reasons = flow.calc_flow_score(
        series(np.linspace(100, 300, n), np.linspace(1e6, 9e6, n)))
    assert len(reasons) <= 5
    assert all(isinstance(r, str) for r in reasons)


def test_score_is_int():
    n = 120
    score, _ = flow.calc_flow_score(series(np.linspace(100, 130, n)))
    assert isinstance(score, int)
