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

    got = er.advance(pos, bar)

    assert got.high_since_entry == 108.0
    assert got.bars_held == 1
    assert got.entry_price == 100.0      # 나머지는 불변


def test_advance_keeps_the_higher_high():
    pos = _pos(high_since_entry=120.0)
    bar = er.Bar("2026-08-20", open=101.0, high=108.0, low=99.0, close=107.0)

    got = er.advance(pos, bar)

    assert got.high_since_entry == 120.0
    assert got.bars_held == 1
