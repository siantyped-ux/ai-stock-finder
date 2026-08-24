"""포트폴리오 계층 시뮬레이션 테스트.

동시 진입 상한과 상관 중복 차단이 이 모듈의 전부이므로, 그 두 규칙이
어떤 경우에 걸리고 어떤 경우에 안 걸리는지를 촘촘히 고정한다.

무엇보다 상한이 없을 때 trade_sim.simulate_ticker 와 같은 결과가 나와야
한다 - 갈라지면 규칙이 두 벌이 된 것이다.

설계: docs/superpowers/specs/2026-08-24-flow-axis-design.md
"""
import exit_rules as er
import portfolio as pf
import pytest
import trade_sim as ts


def bar(date, o, h, l, c, atr=1.0):
    return er.Bar(date, o, h, l, c, atr)


def bars(dates, prices, atr=1.0):
    """가격 목록으로 봉 사전. 고저는 종가 ±0 으로 단순화한다."""
    return {d: bar(d, p, p, p, p, atr) for d, p in zip(dates, prices)}


DATES = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]


def rows(signals, totals=None, target=None):
    totals = totals or [75] * len(signals)
    return [{"date": d, "signal": s, "total": t, "target": target,
             "source": "live"}
            for d, s, t in zip(DATES, signals, totals)]


# ─── 상한 없음: trade_sim 과 같아야 한다 ──────────────────────
def test_matches_simulate_ticker_when_unconstrained():
    """규칙이 두 벌이 되면 백테스트와 실거래가 갈라진다."""
    r = rows(["HOLD", "BUY", "BUY", "BUY", "BUY"])
    b = bars(DATES, [100, 100, 105, 110, 115])

    solo = ts.simulate_ticker("AAA", "US", r, b, er.Params(), ts.Costs())
    port = pf.simulate({"AAA": r}, {"AAA": b}, {"AAA": "US"})["trades"]

    assert len(solo) == len(port) == 1
    assert solo[0].entry_date == port[0].entry_date
    assert solo[0].entry_price == port[0].entry_price
    assert solo[0].net_r == pytest.approx(port[0].net_r)


def test_two_tickers_both_enter_without_a_cap():
    r = rows(["HOLD", "BUY", "BUY", "BUY", "BUY"])
    b = bars(DATES, [100, 100, 105, 110, 115])
    got = pf.simulate({"A": r, "B": r}, {"A": b, "B": b},
                      {"A": "US", "B": "US"})
    assert len(got["trades"]) == 2
    assert got["rejected"] == {"capacity": 0, "correlation": 0}


# ─── 동시 진입 상한 ─────────────────────────────────────────
def test_cap_limits_simultaneous_positions():
    r = rows(["HOLD", "BUY", "BUY", "BUY", "BUY"])
    b = bars(DATES, [100, 100, 105, 110, 115])
    got = pf.simulate({"A": r, "B": r, "C": r},
                      {"A": b, "B": b, "C": b},
                      {"A": "US", "B": "US", "C": "US"},
                      limits=pf.Limits(max_positions=2))
    assert len(got["trades"]) == 2
    assert got["rejected"]["capacity"] == 1


def test_cap_admits_the_highest_total_first():
    """자리가 하나면 그날 총점이 가장 높은 종목이 가져간다."""
    b = bars(DATES, [100, 100, 105, 110, 115])
    got = pf.simulate(
        {"LOW": rows(["HOLD", "BUY", "BUY", "BUY", "BUY"], [70] * 5),
         "HIGH": rows(["HOLD", "BUY", "BUY", "BUY", "BUY"], [90] * 5)},
        {"LOW": b, "HIGH": b}, {"LOW": "US", "HIGH": "US"},
        limits=pf.Limits(max_positions=1))
    assert [t.ticker for t in got["trades"]] == ["HIGH"]


def test_ties_break_by_ticker_for_determinism():
    """동점을 dict 순서에 맡기면 백테스트 결과가 실행마다 흔들린다."""
    b = bars(DATES, [100, 100, 105, 110, 115])
    r = rows(["HOLD", "BUY", "BUY", "BUY", "BUY"], [80] * 5)
    got = pf.simulate({"ZZZ": r, "AAA": r}, {"ZZZ": b, "AAA": b},
                      {"ZZZ": "US", "AAA": "US"},
                      limits=pf.Limits(max_positions=1))
    assert [t.ticker for t in got["trades"]] == ["AAA"]


def test_a_freed_slot_is_reusable_later():
    """청산으로 자리가 나면 이후 전환은 들어갈 수 있어야 한다."""
    # A 는 2일차 진입 후 3일차에 급락해 손절, B 는 4일차에 전환
    a_rows = rows(["HOLD", "BUY", "BUY", "AVOID", "AVOID"])
    b_rows = rows(["HOLD", "HOLD", "HOLD", "BUY", "BUY"])
    a_bars = bars(DATES, [100, 100, 50, 50, 50])
    b_bars = bars(DATES, [100, 100, 100, 100, 105])
    got = pf.simulate({"A": a_rows, "B": b_rows},
                      {"A": a_bars, "B": b_bars},
                      {"A": "US", "B": "US"},
                      limits=pf.Limits(max_positions=1))
    assert {t.ticker for t in got["trades"]} == {"A", "B"}


def test_blocked_transition_is_consumed_not_deferred():
    """상한에 막힌 전환이 살아남으면 훗날 엉뚱한 봉에서 뒤늦게 진입한다.

    A 가 자리를 계속 차지하는 동안 B 의 전환은 2일차 한 번뿐이다. 그 전환이
    소진되지 않으면 A 청산 뒤에 B 가 뒤늦게 열린다.
    """
    a_rows = rows(["HOLD", "BUY", "BUY", "BUY", "BUY"])
    b_rows = rows(["HOLD", "BUY", "HOLD", "HOLD", "HOLD"])
    b = bars(DATES, [100, 100, 105, 110, 115])
    got = pf.simulate({"A": a_rows, "B": b_rows}, {"A": b, "B": b},
                      {"A": "US", "B": "US"},
                      limits=pf.Limits(max_positions=1))
    assert [t.ticker for t in got["trades"]] == ["A"]
    assert got["rejected"]["capacity"] == 1


# ─── 상관 중복 ──────────────────────────────────────────────
def clone_correlator(pairs: dict):
    """지정한 쌍만 높은 상관을 내는 가짜 상관 함수."""
    def get(a, b):
        return pairs.get((a, b)) or pairs.get((b, a))
    return get


def test_correlated_second_entry_is_blocked():
    r = rows(["HOLD", "BUY", "BUY", "BUY", "BUY"])
    b = bars(DATES, [100, 100, 105, 110, 115])
    got = pf.simulate({"XLV": r, "VHT": r}, {"XLV": b, "VHT": b},
                      {"XLV": "US", "VHT": "US"},
                      limits=pf.Limits(max_correlation=0.90),
                      correlator=clone_correlator({("XLV", "VHT"): 0.999}))
    # 총점이 같으므로 티커 순으로 갈린다 - VHT 가 먼저 자리를 잡고 XLV 가 막힌다
    assert [t.ticker for t in got["trades"]] == ["VHT"]
    assert got["rejected"]["correlation"] == 1
    assert got["rejected_pairs"][0][1:] == ("XLV", "VHT", 0.999)


def test_uncorrelated_pair_both_enter():
    r = rows(["HOLD", "BUY", "BUY", "BUY", "BUY"])
    b = bars(DATES, [100, 100, 105, 110, 115])
    got = pf.simulate({"XLV": r, "XLE": r}, {"XLV": b, "XLE": b},
                      {"XLV": "US", "XLE": "US"},
                      limits=pf.Limits(max_correlation=0.90),
                      correlator=clone_correlator({("XLV", "XLE"): -0.10}))
    assert len(got["trades"]) == 2
    assert got["rejected"]["correlation"] == 0


def test_correlation_exactly_at_threshold_passes():
    """상한은 초과일 때만 막는다."""
    r = rows(["HOLD", "BUY", "BUY", "BUY", "BUY"])
    b = bars(DATES, [100, 100, 105, 110, 115])
    got = pf.simulate({"A": r, "B": r}, {"A": b, "B": b},
                      {"A": "US", "B": "US"},
                      limits=pf.Limits(max_correlation=0.90),
                      correlator=clone_correlator({("A", "B"): 0.90}))
    assert len(got["trades"]) == 2


def test_unmeasurable_correlation_does_not_block():
    """이력이 짧다는 이유로 막으면 신규 종목이 영구 배제된다."""
    r = rows(["HOLD", "BUY", "BUY", "BUY", "BUY"])
    b = bars(DATES, [100, 100, 105, 110, 115])
    got = pf.simulate({"A": r, "B": r}, {"A": b, "B": b},
                      {"A": "US", "B": "US"},
                      limits=pf.Limits(max_correlation=0.90),
                      correlator=clone_correlator({}))
    assert len(got["trades"]) == 2


def test_correlation_check_is_off_at_one():
    r = rows(["HOLD", "BUY", "BUY", "BUY", "BUY"])
    b = bars(DATES, [100, 100, 105, 110, 115])
    got = pf.simulate({"A": r, "B": r}, {"A": b, "B": b},
                      {"A": "US", "B": "US"},
                      limits=pf.Limits(max_correlation=1.0),
                      correlator=clone_correlator({("A", "B"): 0.999}))
    assert len(got["trades"]) == 2


# ─── 상관 계산 ──────────────────────────────────────────────
def test_daily_returns():
    assert pf.daily_returns([100.0, 110.0, 121.0]) == pytest.approx([0.1, 0.1])


def test_daily_returns_survives_a_zero_price():
    assert pf.daily_returns([0.0, 10.0]) == [0.0]


def test_correlation_of_identical_series_is_one():
    a = [0.01 * ((i % 7) - 3) for i in range(40)]
    assert pf.correlation(a, a) == pytest.approx(1.0)


def test_correlation_of_mirrored_series_is_minus_one():
    a = [0.01 * ((i % 7) - 3) for i in range(40)]
    assert pf.correlation(a, [-v for v in a]) == pytest.approx(-1.0)


def test_correlation_needs_enough_samples():
    a = [0.01, -0.01] * 10          # 20개
    assert pf.correlation(a, a) is None


def test_correlation_of_a_flat_series_is_none():
    """무변동이면 상관을 못 잰다. 0 으로 답하면 무관하다는 뜻이 되어 틀린다."""
    a = [0.01 * ((i % 7) - 3) for i in range(40)]
    assert pf.correlation(a, [0.0] * 40) is None


def test_correlator_caches_and_is_symmetric():
    closes = {"A": [100 + i for i in range(60)],
              "B": [100 + i * 2 for i in range(60)]}
    get = pf.build_correlator(closes)
    assert get("A", "B") == get("B", "A")


def test_correlator_handles_an_unknown_ticker():
    get = pf.build_correlator({"A": [100.0, 101.0]})
    assert get("A", "ZZZ") is None


# ─── 결과 형태 ──────────────────────────────────────────────
def test_open_position_is_marked_at_the_last_close():
    r = rows(["HOLD", "BUY", "BUY", "BUY", "BUY"])
    b = bars(DATES, [100, 100, 105, 110, 115])
    got = pf.simulate({"A": r}, {"A": b}, {"A": "US"})
    trade = got["trades"][0]
    assert trade.is_open and trade.mark_price == 115


def test_summary_is_included():
    r = rows(["HOLD", "BUY", "BUY", "BUY", "BUY"])
    b = bars(DATES, [100, 100, 105, 110, 115])
    got = pf.simulate({"A": r}, {"A": b}, {"A": "US"})
    assert got["summary"]["open"] == 1


def test_empty_input_is_safe():
    got = pf.simulate({}, {}, {})
    assert got["trades"] == [] and got["summary"]["closed"] == 0
