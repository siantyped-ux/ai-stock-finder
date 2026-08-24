"""아카이브 재계산 순수 함수 테스트.

네트워크를 타는 부분(load_frames · load_regimes)은 테스트하지 않는다. 검증할
값어치가 있는 것은 "행 하나를 어떻게 다시 채우는가" 이고 그것은 순수하다.

설계: docs/superpowers/specs/2026-08-24-flow-axis-design.md
"""
import numpy as np
import pandas as pd
import pytest

import flow
import recompute_history as rh
import stock_finder as sf


def frame(n=200, start=100.0, step=0.3, vol=5e6):
    """일봉 프레임 하나. 날짜는 거래일 근사로 영업일만 쓴다."""
    close = np.array([start + i * step for i in range(n)], dtype=float)
    idx = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame(
        {"High": close + 1.0, "Low": close - 1.0, "Close": close,
         "Volume": np.full(n, vol)},
        index=idx,
    )


def stock_row(**over):
    row = {
        "ticker": "AAA", "asset_type": "STOCK", "bar_date": "2026-08-21",
        "tech": "60", "macro": "62", "filing": "71", "value": "55",
        "total": "63", "consensus": "1", "signal": "HOLD",
        "ev": "0.1", "target": "1", "hitl": "False", "flow": "", "regime": "",
    }
    row.update(over)
    return row


def etf_row(**over):
    row = stock_row(ticker="EEE", asset_type="ETF", filing="", value="")
    row.update(over)
    return row


# ─── 캐시 직렬화 ────────────────────────────────────────────
def test_frame_survives_a_json_round_trip():
    """캐시는 JSON 이다. 왕복해도 tech/flow 가 같은 값을 내야 한다."""
    df = frame()
    back = rh._from_jsonable(rh._to_jsonable(df))

    assert list(back.columns) == ["High", "Low", "Close", "Volume"]
    assert len(back) == len(df)
    assert sf.calc_tech_score(back)[0] == sf.calc_tech_score(df)[0]
    assert flow.calc_flow_score(back)[0] == flow.calc_flow_score(df)[0]


def test_round_trip_keeps_dates_sliceable():
    """slice_to_date 가 인덱스를 날짜로 포맷하므로 DatetimeIndex 여야 한다."""
    back = rh._from_jsonable(rh._to_jsonable(frame()))
    assert isinstance(back.index, pd.DatetimeIndex)


def test_empty_frame_serialises_to_none():
    assert rh._to_jsonable(None) is None
    assert rh._to_jsonable(pd.DataFrame()) is None
    assert rh._from_jsonable(None) is None


# ─── 국면 판정 ──────────────────────────────────────────────
def test_regime_delegates_to_calc_macro_score():
    """임계를 복사하지 않는다. 원본이 바뀌면 이 테스트가 같이 따라간다."""
    for vix, y in ((12.0, 4.0), (20.0, 4.6), (30.0, 4.0), (16.0, 5.5)):
        assert rh.classify_regime(vix, y) == \
            sf.calc_macro_score(vix, 100.0, y, "미분류", None)[2]


@pytest.mark.parametrize("vix,us10y,expected", [
    (12.0, 4.0, "RISK_ON"),
    (30.0, 4.0, "RISK_OFF"),
    (16.0, 5.5, "RISK_OFF"),
    (20.0, 4.6, "NEUTRAL"),
])
def test_regime_branches(vix, us10y, expected):
    assert rh.classify_regime(vix, us10y) == expected


# ─── 행 재계산 ──────────────────────────────────────────────
def test_stock_row_is_recomputed_with_the_new_formula():
    frames = {"AAA": frame()}
    out, status = rh.recompute_row(stock_row(), frames, {"2026-08-21": "NEUTRAL"})

    assert status == "ok"
    sliced = rh.slice_to_date(frames["AAA"], "2026-08-21")
    tech = sf.calc_tech_score(sliced)[0]
    fl = flow.calc_flow_score(sliced)[0]

    assert out["tech"] == tech
    assert out["flow"] == fl
    assert out["total"] == sf.calc_total(tech, fl, 71, 55)
    assert out["consensus"] == sf.calc_consensus(tech, fl, 71, 55)
    assert out["regime"] == "NEUTRAL"


def test_etf_row_uses_the_two_axis_formula():
    frames = {"EEE": frame()}
    out, status = rh.recompute_row(etf_row(), frames, {})

    assert status == "ok"
    sliced = rh.slice_to_date(frames["EEE"], "2026-08-21")
    tech = sf.calc_tech_score(sliced)[0]
    fl = flow.calc_flow_score(sliced)[0]

    assert out["total"] == sf.calc_total_etf(tech, fl)
    assert out["consensus"] == sf.calc_consensus_etf(tech, fl)


def test_etf_row_never_reads_filing_or_value():
    """ETF 의 filing/value 는 빈 문자열이다. int() 를 태우면 터진다."""
    out, status = rh.recompute_row(etf_row(), {"EEE": frame()}, {})
    assert status == "ok"
    assert out["filing"] == "" and out["value"] == ""


def test_archived_filing_and_value_are_preserved():
    """FMP 시점 데이터는 다시 받을 수 없다. 아카이브 값을 그대로 쓴다."""
    out, _ = rh.recompute_row(stock_row(filing="71", value="55"),
                              {"AAA": frame()}, {})
    assert (out["filing"], out["value"]) == ("71", "55")


def test_macro_column_is_left_alone():
    """macro 는 총점에서 빠졌을 뿐 그날의 기록으로 남긴다."""
    out, _ = rh.recompute_row(stock_row(macro="62"), {"AAA": frame()}, {})
    assert out["macro"] == "62"


def test_price_columns_are_left_alone():
    row = stock_row()
    row["close"] = "123.45"
    out, _ = rh.recompute_row(row, {"AAA": frame()}, {})
    assert out["close"] == "123.45"
    assert out["bar_date"] == "2026-08-21"


# ─── 룩어헤드 ───────────────────────────────────────────────
def test_slicing_stops_at_bar_date():
    """그 스캔이 실제로 본 마지막 봉까지만 쓴다. 미래 봉이 섞이면 안 된다."""
    df = frame(n=200)
    frames = {"AAA": df}
    early, _ = rh.recompute_row(stock_row(bar_date="2026-05-01"), frames, {})
    late, _ = rh.recompute_row(stock_row(bar_date="2026-08-21"), frames, {})
    # 같은 프레임인데 자른 지점이 다르면 점수도 달라야 한다
    assert (early["tech"], early["flow"]) != (late["tech"], late["flow"])


def test_row_is_untouched_when_no_frame():
    out, status = rh.recompute_row(stock_row(), {}, {})
    assert status == "일봉 없음"
    assert out == stock_row()


def test_row_is_untouched_when_too_few_bars():
    out, status = rh.recompute_row(stock_row(), {"AAA": frame(n=40)}, {})
    assert status == "봉 부족"
    assert out == stock_row()


def test_row_is_untouched_when_bar_date_precedes_all_bars():
    out, status = rh.recompute_row(stock_row(bar_date="2020-01-01"),
                                   {"AAA": frame()}, {})
    assert status == "봉 부족"
    assert out == stock_row(bar_date="2020-01-01")


def test_recompute_does_not_mutate_the_input_row():
    row = stock_row()
    rh.recompute_row(row, {"AAA": frame()}, {"2026-08-21": "RISK_ON"})
    assert row["flow"] == "" and row["regime"] == ""


def test_missing_regime_becomes_blank_not_none():
    """history 는 빈 문자열을 쓴다. None 이면 CSV 에 'None' 이 박힌다."""
    out, _ = rh.recompute_row(stock_row(), {"AAA": frame()}, {})
    assert out["regime"] == ""


# ─── 출력 ───────────────────────────────────────────────────
def test_describe_handles_an_even_row_count():
    """행 수가 짝수면 중앙값이 float 이 된다. 정수 포맷으로 찍으면 터진다.

    파일 두 개만 재계산할 때 처음 드러났다 - 그전에는 981·539 로 홀수라
    우연히 통과하고 있었다.
    """
    rows = [{"total": t} for t in (50, 60, 70, 80)]
    rh.describe("이후", rows)          # 예외가 나면 실패다


def test_describe_survives_an_empty_list():
    rh.describe("이후", [])


def test_describe_skips_blank_totals():
    rh.describe("이후", [{"total": ""}, {"total": 70}])


# ─── 출력 스키마 ────────────────────────────────────────────
def test_recompute_preserves_every_input_column():
    """재기록은 history.FIELDS 로 쓴다. 입력에 있던 열이 사라지면 그 값이
    CSV 에서 빈칸이 된다."""
    row = stock_row()
    row.update({"name": "가", "market": "US", "sector": "IT", "source": "live",
                "close": "1.0", "volume": "2", "avg_vol20": "3",
                "atr14": "4", "market_cap": "5"})
    out, _ = rh.recompute_row(row, {"AAA": frame()}, {})
    assert set(out) == set(row)


def test_recompute_adds_no_unknown_columns():
    """history.FIELDS 밖의 열이 생기면 _prepare_row 가 거부한다."""
    import history
    out, _ = rh.recompute_row(stock_row(), {"AAA": frame()}, {})
    assert set(out) - set(history.FIELDS) == set()
