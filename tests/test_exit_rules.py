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
