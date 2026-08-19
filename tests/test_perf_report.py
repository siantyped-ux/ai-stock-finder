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
