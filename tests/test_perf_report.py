import pytest
from openpyxl import load_workbook

import perf_report as pr
import trade_sim as ts


FX = {"2026-08-03": 1300.0, "2026-08-05": 1350.0}


def _trade(**kw):
    """기본은 AAA 를 08-03 @100 에 사서 08-05 @110 에 판 트레이드."""
    base = dict(
        ticker="AAA", market="US", source="live",
        entry_date="2026-08-03", entry_price=100.0, r_unit=6.0,
        exit_date="2026-08-05", exit_price=110.0, mark_price=110.0,
        exit_reason="TRAIL", bars_held=2, is_open=False,
        gross_r=1.67, cost_r=0.05, net_r=1.62,
    )
    base.update(kw)
    return ts.Trade(**base)


def test_fx_on_exact_date():
    assert pr.fx_on(FX, "2026-08-03", "US") == 1300.0


def test_fx_on_holiday_falls_back_to_the_previous_session():
    # 08-04 는 환율 데이터가 없다. 08-03 으로 소급한다.
    assert pr.fx_on(FX, "2026-08-04", "US") == 1300.0


def test_fx_on_raises_when_nothing_earlier_exists():
    # 조용히 아무 환율이나 쓰면 틀린 금액이 리포에 커밋된다.
    with pytest.raises(ValueError):
        pr.fx_on(FX, "2026-08-01", "US")


def test_fx_on_is_one_for_kr_tickers():
    assert pr.fx_on(FX, "2026-08-01", "KR") == 1.0


def test_quantity_floors_to_whole_shares():
    # 1,000만원 / (100 x 1300) = 76.9 -> 76주. 잔액은 미투자.
    assert pr.to_row(_trade(), 1300.0, 1300.0)["qty"] == 76


def test_quantity_is_at_least_one_share():
    # 원화진입가가 정액보다 크면 0주가 되고 트레이드가 조용히 사라진다.
    assert pr.to_row(_trade(entry_price=10000.0), 1300.0, 1300.0)["qty"] == 1


def test_us_trade_converts_with_both_fx_rates():
    # 원금 100x1300x76 = 9,880,000 / 회수 110x1350x76 = 11,286,000
    # 매수비용 0.15x1300x76 = 14,820 / 매도비용 0.165x1350x76 = 16,929
    row = pr.to_row(_trade(), 1300.0, 1350.0)

    assert row["qty"] == 76
    assert row["gross_krw"] == pytest.approx(1_406_000.0)
    assert row["gross_pct"] == pytest.approx(14.2308, abs=1e-4)
    assert row["net_krw"] == pytest.approx(1_374_251.0)
    assert row["net_pct"] == pytest.approx(13.9094, abs=1e-4)


def test_loss_stays_negative_and_costs_make_it_worse():
    row = pr.to_row(_trade(exit_price=90.0, mark_price=90.0), 1300.0, 1300.0)

    assert row["gross_krw"] < 0
    assert row["net_krw"] < row["gross_krw"]


def test_kr_trade_needs_no_fx():
    # 1,000만원 / 50,000 = 200주. 원금 정확히 1,000만원.
    row = pr.to_row(_trade(market="KR", entry_price=50000.0,
                           exit_price=55000.0, mark_price=55000.0),
                    1.0, 1.0)

    assert row["qty"] == 200
    assert row["gross_krw"] == pytest.approx(1_000_000.0)


def test_krw_cost_agrees_with_cost_r():
    # 환율 1.0, 1주면 원화 비용은 cost_r x r_unit 과 같아야 한다.
    # 요율 분기가 두 곳에 복제되면 이 등식이 깨진다.
    t = _trade(market="KR", entry_price=50000.0, exit_price=55000.0,
               mark_price=55000.0, r_unit=3000.0)
    row = pr.to_row(t, 1.0, 1.0, capital=50000)

    assert row["qty"] == 1
    expected = ts.cost_r(50000.0, 55000.0, 3000.0, "KR", ts.Costs()) * 3000.0
    assert row["gross_krw"] - row["net_krw"] == pytest.approx(expected)


def test_open_position_uses_the_mark_price_and_still_pays_the_sell_side():
    t = _trade(is_open=True, exit_date=None, exit_price=None, mark_price=105.0)
    row = pr.to_row(t, 1300.0, 1300.0)

    assert row["exit_price"] == 105.0
    # 매도비용을 빼지 않으면 net == gross 가 된다
    assert row["net_krw"] < row["gross_krw"]
