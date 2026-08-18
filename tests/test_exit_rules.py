import inspect
from dataclasses import replace

import pytest

import exit_rules as er


P = er.Params()


def test_params_defaults_are_the_four_documented_values():
    assert P.stop_atr_mult == 3.0
    assert P.trail_atr_mult == 3.0
    assert P.max_hold_days == 60
    assert P.exit_total == 60


def test_open_position_sets_stop_and_r_unit_from_atr():
    pos = er.open_position("NVDA", "2026-08-19", entry_price=100.0,
                           atr_at_entry=2.0, params=P)

    assert pos.ticker == "NVDA"
    assert pos.entry_date == "2026-08-19"
    assert pos.entry_price == 100.0
    assert pos.initial_stop == 94.0        # 100 - 3.0 * 2.0
    assert pos.r_unit == 6.0               # 100 - 94
    assert pos.high_since_entry == 100.0   # 진입가에서 시작
    assert pos.bars_held == 0


def test_open_position_rejects_zero_atr():
    # 손절폭 0 이면 R 이 0 이 되어 이후 모든 계산이 무의미해진다.
    with pytest.raises(ValueError):
        er.open_position("NVDA", "2026-08-19", entry_price=100.0,
                         atr_at_entry=0.0, params=P)


def test_open_position_rejects_none_atr():
    with pytest.raises(ValueError):
        er.open_position("NVDA", "2026-08-19", entry_price=100.0,
                         atr_at_entry=None, params=P)


def test_open_position_rejects_negative_atr():
    with pytest.raises(ValueError):
        er.open_position("NVDA", "2026-08-19", entry_price=100.0,
                         atr_at_entry=-1.0, params=P)


def _pos(**over):
    """진입가 100, ATR 2.0, 손절 94, 1R = 6 인 기준 포지션."""
    pos = er.open_position("NVDA", "2026-08-19", entry_price=100.0,
                           atr_at_entry=2.0, params=P)
    if over:
        pos = replace(pos, **over)
    return pos


def test_current_stop_is_initial_before_trail_activates():
    # 고점이 진입가+1R(=106) 에 못 미치면 트레일링은 켜지지 않는다.
    pos = _pos(high_since_entry=105.0)
    assert er.current_stop(pos, P, atr=2.0) == 94.0


def test_trail_lands_exactly_on_breakeven_at_one_r():
    # 두 ATR 배수가 같으므로 고점이 정확히 1R 일 때 손절선 = 진입가.
    pos = _pos(high_since_entry=106.0)
    assert er.current_stop(pos, P, atr=2.0) == 100.0


def test_trail_follows_the_high():
    pos = _pos(high_since_entry=120.0)
    # 120 - 3.0 * 2.0 = 114
    assert er.current_stop(pos, P, atr=2.0) == 114.0


def test_trail_never_drops_below_the_initial_stop():
    # 변동성이 급등해 트레일 계산값이 초기 손절 아래로 내려가도 손절선은 올라간 채 유지.
    pos = _pos(high_since_entry=106.0)
    assert er.current_stop(pos, P, atr=20.0) == 94.0


def test_missing_atr_keeps_the_initial_stop():
    pos = _pos(high_since_entry=120.0)
    assert er.current_stop(pos, P, atr=None) == 94.0


def test_advance_updates_high_and_bar_count():
    pos = _pos()
    bar = er.Bar("2026-08-20", open=101.0, high=108.0, low=99.0, close=107.0)

    got = er.advance(pos, bar, P)

    assert got.high_since_entry == 108.0
    assert got.bars_held == 1
    assert got.entry_price == 100.0      # 나머지는 불변


def test_advance_keeps_the_higher_high():
    pos = _pos(high_since_entry=120.0)
    bar = er.Bar("2026-08-20", open=101.0, high=108.0, low=99.0, close=107.0)

    got = er.advance(pos, bar, P)

    assert got.high_since_entry == 120.0
    assert got.bars_held == 1


def test_stop_fills_at_the_stop_when_low_touches_it():
    pos = _pos()
    bar = er.Bar("2026-08-20", open=99.0, high=100.0, low=94.0, close=95.0,
                 atr14=2.0, total=75)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "STOP"
    assert got.price == 94.0
    assert got.date == "2026-08-20"


def test_stop_fills_at_the_open_on_a_gap_down():
    # 시가가 이미 손절선 아래면 그 가격에 체결된다 — 슬리피지.
    pos = _pos()
    bar = er.Bar("2026-08-20", open=90.0, high=92.0, low=88.0, close=91.0,
                 atr14=2.0, total=75)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "STOP"
    assert got.price == 90.0


def test_no_exit_when_nothing_triggers():
    pos = _pos()
    bar = er.Bar("2026-08-20", open=101.0, high=103.0, low=99.0, close=102.0,
                 atr14=2.0, total=75)

    assert er.evaluate(pos, bar, P) is None


def test_trail_exit_is_labelled_trail_not_stop():
    pos = _pos(high_since_entry=120.0)   # 손절선 = 114
    bar = er.Bar("2026-08-20", open=118.0, high=119.0, low=113.0, close=115.0,
                 atr14=2.0, total=75)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "TRAIL"
    assert got.price == 114.0


def test_time_exit_fills_at_the_open():
    pos = _pos(bars_held=60)
    bar = er.Bar("2026-08-20", open=105.0, high=106.0, low=104.0, close=105.5,
                 atr14=2.0, total=75)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "TIME"
    assert got.price == 105.0


def test_signal_exit_fills_at_the_open():
    pos = _pos()
    bar = er.Bar("2026-08-20", open=105.0, high=106.0, low=104.0, close=105.5,
                 atr14=2.0, total=59)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "SIGNAL"
    assert got.price == 105.0


def test_hysteresis_holds_between_entry_and_exit_thresholds():
    # 진입 70 / 청산 60 사이에서는 아무 일도 일어나지 않는다.
    pos = _pos()
    bar = er.Bar("2026-08-20", open=105.0, high=106.0, low=104.0, close=105.5,
                 atr14=2.0, total=65)

    assert er.evaluate(pos, bar, P) is None


def test_signal_exit_at_exactly_the_threshold_does_not_fire():
    pos = _pos()
    bar = er.Bar("2026-08-20", open=105.0, high=106.0, low=104.0, close=105.5,
                 atr14=2.0, total=60)

    assert er.evaluate(pos, bar, P) is None


def test_time_beats_stop_on_the_same_bar():
    # 둘 다 발동해도 TIME 은 개장 전에 결정돼 시가에 나간다.
    pos = _pos(bars_held=60)
    bar = er.Bar("2026-08-20", open=99.0, high=100.0, low=90.0, close=91.0,
                 atr14=2.0, total=75)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "TIME"
    assert got.price == 99.0


def test_signal_beats_stop_on_the_same_bar():
    pos = _pos()
    bar = er.Bar("2026-08-20", open=99.0, high=100.0, low=90.0, close=91.0,
                 atr14=2.0, total=50)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "SIGNAL"
    assert got.price == 99.0


def test_missing_total_skips_signal_but_keeps_stop():
    pos = _pos()
    bar = er.Bar("2026-08-20", open=99.0, high=100.0, low=90.0, close=91.0,
                 atr14=2.0, total=None)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "STOP"


def test_todays_high_does_not_set_todays_stop():
    # 룩어헤드 차단. 오늘 고가 120 이 트레일을 켜더라도 오늘 손절선은
    # 어제까지의 고점(=진입가)으로 계산된 94 여야 한다. 저가 95 는 94 를 안 건드린다.
    pos = _pos()
    bar = er.Bar("2026-08-20", open=101.0, high=120.0, low=95.0, close=119.0,
                 atr14=2.0, total=75)

    assert er.evaluate(pos, bar, P) is None

    after = er.advance(pos, bar, P)
    assert after.high_since_entry == 120.0
    assert er.current_stop(after, P, atr=2.0) == 114.0


def test_module_has_no_io_dependencies():
    # 순환 의존과 숨은 I/O 를 막는다. 2단계 하네스가 어떤 출처의 가격이든
    # 넘길 수 있어야 하고, 규칙 모듈이 스스로 데이터를 읽으면 그 계약이 깨진다.
    src = inspect.getsource(er)
    for banned in ("import history", "import stock_finder", "import yfinance",
                   "import requests", "open(", "subprocess"):
        assert banned not in src, f"exit_rules 가 {banned} 를 쓰면 안 된다"


def test_params_has_exactly_four_fields():
    # v5 설계서가 파라미터 5개 초과를 금지한다.
    import dataclasses
    assert len(dataclasses.fields(er.Params)) == 4


def test_time_does_not_fire_one_bar_before_the_cap():
    # SIGNAL 은 임계 양쪽을 모두 검증하는데 TIME 은 발동 케이스만 있었다.
    pos = _pos(bars_held=59)
    bar = er.Bar("2026-08-20", open=105.0, high=106.0, low=104.0, close=105.5,
                 atr14=2.0, total=75)

    assert er.evaluate(pos, bar, P) is None


def test_stop_does_not_retreat_when_atr_widens():
    # 확정된 리뷰 결함. 고점이 그대로인데 ATR 이 확대되면 손절선이 후퇴했었다.
    pos = _pos()
    up = er.Bar("2026-08-20", open=101.0, high=140.0, low=100.0, close=139.0,
                atr14=2.0, total=75)
    pos = er.advance(pos, up, P)
    assert pos.stop == 134.0            # 140 - 3.0 * 2.0

    calm = er.Bar("2026-08-21", open=139.0, high=140.0, low=138.0, close=139.0,
                  atr14=10.0, total=75)
    pos = er.advance(pos, calm, P)

    assert pos.stop == 134.0            # 110 으로 내려가지 않는다


def test_stop_holds_through_a_missing_atr_bar():
    pos = _pos()
    up = er.Bar("2026-08-20", open=101.0, high=140.0, low=100.0, close=139.0,
                atr14=2.0, total=75)
    pos = er.advance(pos, up, P)

    gap = er.Bar("2026-08-21", open=139.0, high=139.5, low=138.0, close=139.0,
                 atr14=None, total=75)
    pos = er.advance(pos, gap, P)

    assert pos.stop == 134.0            # 초기 손절 94 로 원복하지 않는다


def test_current_stop_returns_the_stored_stop_when_atr_is_missing():
    pos = _pos()
    up = er.Bar("2026-08-20", open=101.0, high=140.0, low=100.0, close=139.0,
                atr14=2.0, total=75)
    pos = er.advance(pos, up, P)

    assert er.current_stop(pos, P, atr=None) == 134.0


def test_open_position_seeds_the_stop_at_the_initial_stop():
    pos = _pos()
    assert pos.stop == pos.initial_stop == 94.0


def test_evaluate_uses_the_ratcheted_stop():
    pos = _pos()
    up = er.Bar("2026-08-20", open=101.0, high=140.0, low=100.0, close=139.0,
                atr14=2.0, total=75)
    pos = er.advance(pos, up, P)   # stop = 134

    drop = er.Bar("2026-08-21", open=139.0, high=139.0, low=133.0, close=134.0,
                  atr14=10.0, total=75)
    got = er.evaluate(pos, drop, P)

    assert got.reason == "TRAIL"
    assert got.price == 134.0


def test_advance_honours_a_non_default_trail_multiplier():
    # advance 가 Params() 기본값을 하드코딩하면 저장 손절선과 evaluate 판정이 어긋난다.
    p2 = er.Params(trail_atr_mult=2.0)
    pos = er.open_position("X", "d0", 100.0, 2.0, p2)
    bar = er.Bar("d1", open=101.0, high=140.0, low=100.0, close=139.0,
                 atr14=2.0, total=75)

    after = er.advance(pos, bar, p2)

    assert after.stop == 136.0          # 140 - 2.0 * 2.0, 기본값이면 134
    assert er.current_stop(after, p2, atr=2.0) == 136.0


def test_all_dataclasses_are_frozen():
    # 백테스트 루프가 Position 을 봉마다 넘겨 쓰므로, 가변이 되면 한 트레이드의
    # 상태 변경이 다른 트레이드로 샐 수 있다.
    for cls in (er.Params, er.Bar, er.Position, er.ExitDecision):
        assert cls.__dataclass_params__.frozen, cls.__name__
