import pytest

import exit_rules as er
import stops
import trade_sim as ts


P = er.Params()


def _open(**kw):
    """기본은 진입 100, 1R=6, 손절 94, 최고 102(트레일 미발동), 평가 101.5."""
    base = dict(
        ticker="AAA", market="US", source="live",
        entry_date="2026-08-03", entry_price=100.0, r_unit=6.0,
        exit_date=None, exit_price=None, mark_price=101.5,
        exit_reason=None, bars_held=2, is_open=True,
        gross_r=0.25, cost_r=0.05, net_r=0.20,
        initial_stop=94.0, high_since_entry=102.0, stop=94.0,
        target_price=None,
    )
    base.update(kw)
    return ts.Trade(**base)


def test_trail_is_off_before_the_high_reaches_one_r():
    v = stops.stop_view(_open(), P)

    assert v["trail_active"] is False
    assert v["trail_trigger"] == pytest.approx(106.0)     # 진입가 + 1R
    # 최고가 102 에서 106 까지 3.92% 더 올라야 켜진다
    assert v["to_trigger_pct"] == pytest.approx(3.9216, abs=1e-4)


def test_trail_is_on_once_the_high_reaches_one_r():
    v = stops.stop_view(_open(high_since_entry=106.0, stop=100.0,
                              mark_price=105.0), P)

    assert v["trail_active"] is True
    assert v["to_trigger_pct"] is None                     # 이미 켜졌다
    assert v["stop"] == pytest.approx(100.0)               # 본전으로 올라와 있다


def test_risk_pct_is_one_r_against_the_entry_price():
    # 손절폭은 ATR 비례라 종목마다 다르다. 그 폭을 % 로 보여준다.
    assert stops.stop_view(_open(), P)["risk_pct"] == pytest.approx(6.0)


def test_room_pct_measures_from_the_current_price_down_to_the_stop():
    # (101.5 - 94) / 101.5 = 7.389%
    v = stops.stop_view(_open(), P)

    assert v["room_pct"] == pytest.approx(7.3892, abs=1e-4)


def test_bars_left_counts_down_to_the_time_cap():
    v = stops.stop_view(_open(bars_held=58), P)

    assert v["bars_left"] == 2                             # 60 - 58


def test_closed_trades_are_rejected():
    # 청산된 트레이드에는 "지금 어디서 잘리는가" 가 없다.
    closed = _open(is_open=False, exit_date="2026-08-05", exit_price=110.0)

    with pytest.raises(ValueError):
        stops.stop_view(closed, P)


def test_open_views_sort_by_how_close_the_stop_is():
    # 가장 위험한(손절선에 가까운) 포지션이 맨 위에 와야 눈에 띈다.
    trades = [
        _open(ticker="FAR", mark_price=120.0),
        _open(ticker="NEAR", mark_price=95.0),
        _open(ticker="MID", mark_price=105.0),
        _open(ticker="CLOSED", is_open=False, exit_date="d", exit_price=110.0),
    ]

    got = [v["ticker"] for v in stops.build_views(trades, P)]

    assert got == ["NEAR", "MID", "FAR"]                   # CLOSED 는 빠진다
