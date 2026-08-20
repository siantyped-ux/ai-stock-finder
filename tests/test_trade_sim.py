import pytest
import inspect

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


def _target_row(date, target, signal="BUY"):
    return {"date": date, "signal": signal, "total": 75,
            "source": "live", "target": target}


def test_target_price_reaches_the_trade():
    rows = [_target_row("d1", 9)]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert trades[0].target_price == pytest.approx(109.0)


def test_target_price_stays_fixed_when_later_scans_change_it():
    # 진입 후 스코어가 올라 target 이 30% 가 돼도 목표가는 진입일 값이다.
    rows = [_target_row("d1", 9), _target_row("d2", 30)]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 100.5, 102.0, 100.0, 101.5)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert trades[0].target_price == pytest.approx(109.0)


def test_missing_target_key_leaves_no_target():
    # 예전 백필 파일에는 target 컬럼이 없을 수 있다. 죽지 않아야 한다.
    rows = [_row("d1", "BUY")]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert trades[0].target_price is None


def test_target_exit_closes_the_trade_when_enabled():
    rows = [_target_row("d1", 9), _target_row("d2", 9)]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 101.0, 110.0, 100.0, 109.0)}

    trades = ts.simulate_ticker("X", "US", rows, bars,
                                er.Params(use_target=True), C)

    assert trades[0].exit_reason == "TARGET"
    assert trades[0].exit_price == pytest.approx(109.0)


def test_target_exit_does_nothing_by_default():
    rows = [_target_row("d1", 9), _target_row("d2", 9)]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 101.0, 110.0, 100.0, 109.0)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert trades[0].is_open is True


def _trade(net_r, is_open=False, reason="STOP"):
    return ts.Trade(
        ticker="X", market="US", source="live", entry_date="d1",
        entry_price=100.0, r_unit=6.0,
        exit_date=None if is_open else "d2",
        exit_price=None if is_open else 106.0,
        exit_reason=None if is_open else reason,
        bars_held=1, is_open=is_open,
        gross_r=net_r + 0.05, cost_r=0.05, net_r=net_r,
        mark_price=106.0,
        initial_stop=94.0, high_since_entry=106.0, stop=94.0,
        target_price=None,
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


def test_module_has_no_io_dependencies():
    # 하네스가 어떤 출처의 가격이든 넘길 수 있어야 하고, history/stock_finder 를
    # 임포트하면 순환 의존이 생긴다.
    src = inspect.getsource(ts)
    for banned in ("import history", "import stock_finder", "import yfinance",
                   "import requests", "open(", "subprocess", "csv"):
        assert banned not in src, f"trade_sim 이 {banned} 를 쓰면 안 된다"


def test_a_transition_while_holding_does_not_leak_into_a_later_bar():
    # d4 전환은 A 를 보유 중이라 쓸 수 없다. A 가 d5 에 청산돼도
    # 그 전환으로 새 포지션을 열어서는 안 된다 - d5 는 전환일이 아니다.
    rows = [_row("d1", "BUY"), _row("d2", "BUY"), _row("d3", "WATCH"),
            _row("d4", "BUY"), _row("d5", "BUY")]
    bars = {d: _bar(d, 100.0, 101.0, 99.0, 100.0)
            for d in ("d1", "d2", "d3", "d4")}
    bars["d5"] = _bar("d5", 99.0, 99.0, 90.0, 91.0)      # 손절

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert len(trades) == 1
    assert trades[0].entry_date == "d1"
    assert trades[0].exit_date == "d5"


def test_no_entry_on_the_bar_a_position_closed():
    # 청산은 장중 손절가에, 진입은 그 봉 시가에 체결된다. 같은 봉에서
    # 둘 다 일어나면 진입이 자기가 뒤따른다는 청산보다 앞서게 된다.
    rows = [_row("d1", "BUY"), _row("d2", "HOLD"), _row("d3", "BUY")]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.0),
            "d2": _bar("d2", 100.0, 101.0, 99.0, 100.0),
            "d3": _bar("d3", 99.0, 99.0, 90.0, 91.0)}     # d3 전환 + 손절

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    entries = [t.entry_date for t in trades]
    assert "d3" not in entries


def test_cost_amount_returns_each_side_at_its_own_price():
    # 진입 100, 청산 130. 매수측 (0.10+0.05)% x 100, 매도측 (0.10+0.05)% x 130
    buy, sell = ts.cost_amount(entry_price=100.0, exit_price=130.0,
                               market="US", costs=C)

    assert buy == pytest.approx(0.15)
    assert sell == pytest.approx(0.195)


def test_cost_amount_charges_kr_transfer_tax_on_the_sell_side_only():
    buy, sell = ts.cost_amount(entry_price=100.0, exit_price=130.0,
                               market="KR", costs=C)

    assert buy == pytest.approx((0.02 + 0.05) / 100 * 100.0)
    assert sell == pytest.approx((0.02 + 0.15 + 0.05) / 100 * 130.0)


def test_cost_amount_rejects_unknown_market():
    with pytest.raises(ValueError):
        ts.cost_amount(100.0, 130.0, "JP", C)


def test_cost_r_is_cost_amount_divided_by_r_unit():
    # 요율 분기가 복제되면 이 등식이 깨진다.
    buy, sell = ts.cost_amount(100.0, 130.0, "US", C)

    assert ts.cost_r(100.0, 130.0, 6.0, "US", C) == pytest.approx((buy + sell) / 6.0)


def test_open_trade_exposes_its_mark_price():
    # 미결이어도 평가 가격이 밖으로 나와야 원화 평가손익을 낼 수 있다.
    rows = [_row("d1", "BUY"), _row("d2", "BUY")]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 100.5, 102.0, 100.0, 101.5)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert trades[0].is_open
    assert trades[0].exit_price is None
    assert trades[0].mark_price == 101.5    # 마지막 종가로 평가한다


def test_closed_trade_mark_price_equals_its_exit_price():
    t = _trade(1.0)
    assert t.mark_price == t.exit_price


def test_trade_carries_the_stop_state():
    # stops.py 가 시뮬레이션 루프를 복사하지 않고도 "지금 어디서 잘리는가" 를
    # 답할 수 있어야 한다. 복사하면 규칙이 두 벌이 되어 갈라진다.
    rows = [_row("d1", "BUY"), _row("d2", "BUY")]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 100.5, 102.0, 100.0, 101.5)}

    t = ts.simulate_ticker("X", "US", rows, bars, P, C)[0]

    assert t.initial_stop == pytest.approx(94.0)     # 100 - 3.0 * 2.0
    assert t.high_since_entry == 102.0               # d1 101 -> d2 102
    assert t.stop == pytest.approx(94.0)             # +1R(106) 미달이라 트레일 off


def test_stop_ratchets_to_breakeven_when_the_high_hits_one_r():
    # 손절 배수와 트레일 배수가 같아서 +1R 도달 시 손절선이 정확히 진입가가 된다.
    rows = [_row("d1", "BUY"), _row("d2", "BUY")]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 100.5, 106.0, 100.0, 105.0)}

    t = ts.simulate_ticker("X", "US", rows, bars, P, C)[0]

    assert t.high_since_entry == 106.0
    assert t.stop == pytest.approx(100.0)            # 106 - 3.0*2.0 = 진입가
