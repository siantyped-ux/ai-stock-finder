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


def test_entry_fires_only_on_transition_into_buy():
    # 연속 BUY 에서는 첫날만 진입 신호다. 상태를 이어받아야 한다 -
    # 매번 초기 상태로 호출하면 매일 전환으로 보인다.
    st = ts.EntryState()
    got = []
    for sig in ("HOLD", "BUY", "BUY", "BUY"):
        step = ts.step_entry(st, sig)
        got.append(step.should_enter)
        st = ts.consume(step.state) if step.should_enter else step.state
    assert got == [False, True, False, False]


def test_pending_survives_a_day_without_a_bar():
    # 토요일에 전환됐지만 세션이 없으면, 다음 세션까지 pending 이 유지된다.
    st = ts.EntryState()
    r1 = ts.step_entry(st, "BUY")            # 토요일 전환
    assert r1.should_enter is True
    r2 = ts.step_entry(r1.state, "BUY")      # 봉 없어 진입 못 함
    assert r2.should_enter is True


def test_pending_clears_when_the_signal_leaves_buy():
    st = ts.EntryState()
    r1 = ts.step_entry(st, "BUY")
    r2 = ts.step_entry(r1.state, "WATCH")
    assert r2.should_enter is False


def test_pending_clears_once_consumed():
    st = ts.EntryState()
    r1 = ts.step_entry(st, "BUY")
    r2 = ts.step_entry(ts.consume(r1.state), "BUY")
    assert r2.should_enter is False


def test_strong_buy_counts_as_buy():
    st = ts.EntryState()
    got = ts.step_entry(st, "STRONG_BUY")
    assert got.should_enter is True


def test_reentry_needs_a_fresh_transition():
    # BUY 를 벗어났다 돌아와야 다시 진입 신호가 난다.
    st = ts.EntryState()
    r = ts.step_entry(st, "BUY")
    r = ts.step_entry(ts.consume(r.state), "BUY")
    assert r.should_enter is False
    r = ts.step_entry(r.state, "HOLD")
    assert r.should_enter is False
    r = ts.step_entry(r.state, "BUY")
    assert r.should_enter is True
