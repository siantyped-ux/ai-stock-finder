import pytest

import trade_sim as ts


C = ts.Costs()


def test_costs_defaults():
    assert C.us_buy_pct == 0.10
    assert C.us_sell_pct == 0.10
    assert C.kr_buy_pct == 0.02
    assert C.kr_sell_pct == 0.02
    assert C.kr_tax_pct == 0.15
    assert C.slippage_pct == 0.05


def test_us_cost_in_r():
    # 진입가 100, 1R = 6.
    # 왕복 = 0.10 + 0.10 + 슬리피지 0.05*2 = 0.30% -> 0.30 원 -> 0.05 R
    got = ts.cost_r(entry_price=100.0, r_unit=6.0, market="US", costs=C)
    assert got == pytest.approx(0.30 / 6.0)


def test_kr_cost_includes_the_sell_tax():
    # 왕복 = 0.02 + 0.02 + 거래세 0.15 + 슬리피지 0.10 = 0.29%
    got = ts.cost_r(entry_price=100.0, r_unit=6.0, market="KR", costs=C)
    assert got == pytest.approx(0.29 / 6.0)


def test_bigger_r_absorbs_cost():
    # r_unit 이 두 배면 비용 부담(R 기준)은 절반이다.
    small = ts.cost_r(100.0, 6.0, "US", C)
    big = ts.cost_r(100.0, 12.0, "US", C)
    assert big == pytest.approx(small / 2)


def test_unknown_market_is_rejected():
    with pytest.raises(ValueError):
        ts.cost_r(100.0, 6.0, "JP", C)
