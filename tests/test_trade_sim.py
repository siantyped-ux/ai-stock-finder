import pytest

import trade_sim as ts
import exit_rules as er


C = ts.Costs()


def test_costs_defaults():
    assert C.us_buy_pct == 0.10
    assert C.us_sell_pct == 0.10
    assert C.kr_buy_pct == 0.02
    assert C.kr_sell_pct == 0.02
    assert C.kr_tax_pct == 0.15
    assert C.slippage_pct == 0.05


def test_us_cost_charges_each_side_at_its_own_price():
    # 진입 100, 청산 130, 1R = 6.
    # 매수측 (0.10+0.05)% x 100 = 0.15
    # 매도측 (0.10+0.05)% x 130 = 0.195
    got = ts.cost_r(entry_price=100.0, exit_price=130.0, r_unit=6.0,
                    market="US", costs=C)
    assert got == pytest.approx((0.15 + 0.195) / 6.0)


def test_kr_cost_includes_the_sell_tax():
    # 매수측 (0.02+0.05)% x 100 = 0.07
    # 매도측 (0.02+0.15+0.05)% x 100 = 0.22
    got = ts.cost_r(entry_price=100.0, exit_price=100.0, r_unit=6.0,
                    market="KR", costs=C)
    assert got == pytest.approx((0.07 + 0.22) / 6.0)


def test_bigger_r_absorbs_cost():
    # r_unit 이 두 배면 비용 부담(R 기준)은 절반이다.
    small = ts.cost_r(100.0, 100.0, 6.0, "US", C)
    big = ts.cost_r(100.0, 100.0, 12.0, "US", C)
    assert big == pytest.approx(small / 2)


def test_unknown_market_is_rejected():
    with pytest.raises(ValueError):
        ts.cost_r(100.0, 100.0, 6.0, "JP", C)


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


P = er.Params()


def _bar(date, o, h, l, c, atr=2.0):
    return er.Bar(date, open=o, high=h, low=l, close=c, atr14=atr)


def _row(date, signal, source="live"):
    return {"date": date, "signal": signal, "total": 75, "source": source}


def test_one_trade_opens_and_stays_open():
    rows = [_row("d1", "BUY"), _row("d2", "BUY")]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 100.5, 102.0, 100.0, 101.5)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert len(trades) == 1
    t = trades[0]
    assert t.is_open is True
    assert t.entry_date == "d1"
    assert t.entry_price == 100.0
    assert t.r_unit == 6.0                 # 3.0 * 2.0
    assert t.exit_reason is None
    assert t.gross_r == pytest.approx((101.5 - 100.0) / 6.0)


def test_stop_closes_the_trade():
    rows = [_row("d1", "BUY"), _row("d2", "BUY")]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 99.0, 99.5, 90.0, 91.0)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert len(trades) == 1
    t = trades[0]
    assert t.is_open is False
    assert t.exit_reason == "STOP"
    assert t.exit_price == 94.0             # 100 - 3.0 * 2.0
    assert t.gross_r == pytest.approx(-1.0)
    assert t.net_r < t.gross_r              # 비용만큼 더 나쁘다


def test_no_bar_means_no_bars_held():
    rows = [_row("d1", "BUY"), _row("sat", "BUY"), _row("d2", "BUY")]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 100.5, 102.0, 100.0, 101.5)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert trades[0].bars_held == 2         # d1, d2 만. sat 은 세지 않는다


def test_entry_waits_for_the_next_session():
    # sat 에 전환됐고 sat 에는 봉이 없다. d2 에 진입해야 한다.
    rows = [_row("sat", "BUY"), _row("d2", "BUY")]
    bars = {"d2": _bar("d2", 50.0, 51.0, 49.0, 50.5)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert len(trades) == 1
    assert trades[0].entry_date == "d2"
    assert trades[0].entry_price == 50.0


def test_no_reentry_while_holding():
    rows = [_row(d, "BUY") for d in ("d1", "d2", "d3")]
    bars = {d: _bar(d, 100.0, 101.0, 99.5, 100.5) for d in ("d1", "d2", "d3")}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert len(trades) == 1


def test_missing_atr_at_entry_skips_the_trade():
    rows = [_row("d1", "BUY")]
    bars = {"d1": er.Bar("d1", open=100.0, high=101.0, low=99.0, close=100.5,
                         atr14=None)}

    assert ts.simulate_ticker("X", "US", rows, bars, P, C) == []


def test_signal_exit_uses_the_row_total():
    rows = [_row("d1", "BUY"), {"date": "d2", "signal": "HOLD", "total": 50,
                                "source": "live"}]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 100.0, 101.0, 99.5, 100.5)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert trades[0].exit_reason == "SIGNAL"
    assert trades[0].exit_price == 100.0    # 시가 체결


def _trade(net_r, is_open=False, reason="STOP"):
    return ts.Trade(
        ticker="X", market="US", source="live", entry_date="d1",
        entry_price=100.0, r_unit=6.0,
        exit_date=None if is_open else "d2",
        exit_price=None if is_open else 106.0,
        exit_reason=None if is_open else reason,
        bars_held=1, is_open=is_open,
        gross_r=net_r + 0.05, cost_r=0.05, net_r=net_r,
    )


def test_summary_counts_only_closed_trades():
    trades = [_trade(1.0), _trade(-1.0), _trade(5.0, is_open=True)]

    got = ts.summarize(trades)

    assert got["closed"] == 2
    assert got["open"] == 1
    assert got["win_rate"] == pytest.approx(0.5)
    assert got["avg_net_r"] == pytest.approx(0.0)


def test_summary_of_no_closed_trades_is_not_a_crash():
    got = ts.summarize([_trade(2.0, is_open=True)])

    assert got["closed"] == 0
    assert got["open"] == 1
    assert got["win_rate"] is None
    assert got["avg_net_r"] is None


def test_summary_breaks_down_by_exit_reason():
    trades = [_trade(1.0, reason="TRAIL"), _trade(-1.0, reason="STOP"),
              _trade(-1.0, reason="STOP")]

    got = ts.summarize(trades)

    assert got["by_reason"] == {"TRAIL": 1, "STOP": 2}


def test_summary_reports_open_r_separately():
    trades = [_trade(1.0), _trade(3.0, is_open=True)]

    got = ts.summarize(trades)

    assert got["avg_net_r"] == pytest.approx(1.0)     # 미결 3.0 은 안 섞인다
    assert got["open_net_r"] == pytest.approx(3.0)


def test_cost_grows_with_the_exit_price():
    # 진입가 기준 근사는 이 두 값을 같게 만들었다. 큰 승리일수록
    # 매도 비용이 실제로 커지는데 그것을 과소계상했다.
    flat = ts.cost_r(100.0, 100.0, 6.0, "US", C)
    winner = ts.cost_r(100.0, 160.0, 6.0, "US", C)
    assert winner > flat


def test_cost_understatement_at_ten_r_would_have_exceeded_a_hundredth_of_r():
    # US 매도측 0.15%. 10R 청산이면 근사 오차가 0.015R 이었다.
    entry, r_unit = 100.0, 6.0
    exit_price = entry + 10 * r_unit          # +10R
    exact = ts.cost_r(entry, exit_price, r_unit, "US", C)
    approx = ts.cost_r(entry, entry, r_unit, "US", C)
    assert exact - approx == pytest.approx(0.015, abs=1e-6)


def test_pending_persists_when_the_caller_forgets_to_consume():
    # consume() 는 호출자 책임이다. 잊으면 보유 중에도 진입 신호가 계속 난다.
    # 계약을 테스트로 고정해 두어, 동작이 바뀌면 드러나게 한다.
    st = ts.EntryState()
    r = ts.step_entry(st, "BUY")
    assert r.should_enter is True
    r2 = ts.step_entry(r.state, "BUY")        # consume 없이 그대로
    assert r2.should_enter is True


def test_weekend_gap_then_a_one_day_dropout():
    # 토 전환 -> 일(봉 없음) -> 월 이탈 -> 화 재전환
    st = ts.EntryState()
    sat = ts.step_entry(st, "BUY")
    assert sat.should_enter is True
    sun = ts.step_entry(sat.state, "BUY")
    assert sun.should_enter is True
    mon = ts.step_entry(sun.state, "HOLD")
    assert mon.should_enter is False
    tue = ts.step_entry(mon.state, "BUY")
    assert tue.should_enter is True
