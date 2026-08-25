import pandas as pd

import backtest as bt


def _flat_frame(n=20):
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame(
        dict(Open=[100.0] * n, High=[101.0] * n, Low=[99.0] * n,
             Close=[100.0] * n),
        index=idx,
    )


def test_atr_excludes_the_bar_it_is_attached_to():
    # 이 봉의 고저가 자기 ATR 에 들어가면, 개장 전에 정해져야 할 손절선이
    # 그날 장중 정보를 쓰게 된다. 계획 초안이 tr[:i] 로 잘라 실제로 샜다.
    calm = _flat_frame()
    spike = calm.copy()
    spike.iloc[-1, spike.columns.get_loc("High")] = 500.0
    spike.iloc[-1, spike.columns.get_loc("Low")] = 1.0

    last = sorted(bt.atr_series(calm))[-1]

    assert bt.atr_series(calm)[last] == bt.atr_series(spike)[last]


def test_atr_reflects_the_previous_bar():
    # 직전 봉의 변동은 반영돼야 한다. 아예 한 칸 더 잘라내면 그것도 사라진다.
    calm = _flat_frame()
    spike = calm.copy()
    spike.iloc[-2, spike.columns.get_loc("High")] = 500.0
    spike.iloc[-2, spike.columns.get_loc("Low")] = 1.0

    last = sorted(bt.atr_series(calm))[-1]

    assert bt.atr_series(spike)[last] > bt.atr_series(calm)[last]


def test_atr_needs_enough_history():
    # 15봉이면 TR 이 14개지만 그중 마지막은 당일 것이라 쓸 수 없다.
    short = _flat_frame(n=15)
    assert sorted(short.index)[-1].strftime("%Y-%m-%d") not in bt.atr_series(short)


def test_run_deduplicates_repeated_ticker_dates(monkeypatch):
    # 실제 아카이브에 (ticker, date) 중복이 존재한다. 그대로 두면 같은 봉을
    # 두 번 처리해 진입 봉까지 평가하게 되고 bars_held 가 부풀려진다.
    import exit_rules as er

    rows = [
        {"ticker": "X", "date": "2026-01-02", "market": "US", "signal": "BUY",
         "total": "75", "source": "live"},
        {"ticker": "X", "date": "2026-01-02", "market": "US", "signal": "BUY",
         "total": "75", "source": "live"},
        {"ticker": "X", "date": "2026-01-03", "market": "US", "signal": "BUY",
         "total": "75", "source": "live"},
    ]
    bars = {
        "2026-01-02": er.Bar("2026-01-02", 100.0, 101.0, 99.0, 100.0,
                             atr14=2.0, total=None),
        "2026-01-03": er.Bar("2026-01-03", 100.0, 101.0, 99.0, 100.0,
                             atr14=2.0, total=None),
    }

    monkeypatch.setattr(bt, "load_archive", lambda pattern: rows)
    monkeypatch.setattr(bt, "fetch_bars", lambda ticker: bars)

    result = bt.run()

    assert len(result["trades"]) == 1
    assert result["trades"][0].bars_held == 2      # 중복이면 3 이 된다


def _archive_row(**over):
    row = {"ticker": "X", "date": "2026-01-02", "market": "US",
           "signal": "BUY", "total": "75", "target": "9", "source": "live"}
    row.update(over)
    return row


def _stub_bars():
    import exit_rules as er
    return {
        "2026-01-02": er.Bar("2026-01-02", 100.0, 101.0, 99.0, 100.0,
                             atr14=2.0, total=None),
        "2026-01-03": er.Bar("2026-01-03", 100.0, 101.0, 99.0, 100.0,
                             atr14=2.0, total=None),
    }


def _spy_rows(monkeypatch, rows):
    """run() 이 simulate_ticker 에 실제로 넘긴 행을 가로챈다."""
    import trade_sim as ts

    seen = {}
    real = ts.simulate_ticker

    def spy(ticker, market, prepared, bars, params, costs, universe_exit=None):
        seen["rows"] = prepared
        return real(ticker, market, prepared, bars, params, costs,
                    universe_exit)

    monkeypatch.setattr(bt, "load_archive", lambda pattern: rows)
    monkeypatch.setattr(bt, "fetch_bars", lambda ticker: _stub_bars())
    monkeypatch.setattr(ts, "simulate_ticker", spy)
    bt.run()
    return seen["rows"]


def test_prepared_rows_carry_the_target(monkeypatch):
    # 아카이브의 target 이 여기서 끊기면 목표가가 조용히 전부 None 이 된다.
    rows = _spy_rows(monkeypatch, [_archive_row()])

    assert rows[0]["target"] == 9


def test_prepared_rows_tolerate_a_missing_target_column(monkeypatch):
    # 예전 백필 파일에는 target 컬럼이 없다. 죽지 않고 None 이어야 한다.
    row = _archive_row()
    del row["target"]
    rows = _spy_rows(monkeypatch, [row])

    assert rows[0]["target"] is None


def test_prepared_rows_tolerate_an_empty_target(monkeypatch):
    rows = _spy_rows(monkeypatch, [_archive_row(target="")])

    assert rows[0]["target"] is None


# ─── 아카이브 행 필터 (US 단독 / 70점 진입) ─────────────────
ARCHIVE = [
    {"ticker": "AAPL", "market": "US", "date": "2026-08-01",
     "total": "72", "signal": "BUY", "source": "live"},
    {"ticker": "005930.KS", "market": "KR", "date": "2026-08-01",
     "total": "75", "signal": "BUY", "source": "live"},
    {"ticker": "MSFT", "market": "US", "date": "2026-08-01",
     "total": "71", "signal": "WATCH", "source": "live"},
]


def test_us_only_drops_korean_rows():
    kept = bt.filter_rows(ARCHIVE, us_only=True, entry_total=None)
    assert [r["ticker"] for r in kept] == ["AAPL", "MSFT"]


def test_no_filters_keeps_everything():
    kept = bt.filter_rows(ARCHIVE, us_only=False, entry_total=None)
    assert len(kept) == 3


def test_entry_total_promotes_high_scores_to_buy():
    """entry_total 을 주면 그 점수 이상인 행의 signal 을 BUY 로 바꾼다."""
    kept = bt.filter_rows(ARCHIVE, us_only=False, entry_total=70)
    assert all(r["signal"] == "BUY" for r in kept)


def test_entry_total_leaves_low_scores_alone():
    rows = [{"ticker": "X", "market": "US", "date": "2026-08-01",
             "total": "69", "signal": "HOLD", "source": "live"}]
    kept = bt.filter_rows(rows, us_only=False, entry_total=70)
    assert kept[0]["signal"] == "HOLD"


def test_entry_total_handles_blank_total():
    rows = [{"ticker": "X", "market": "US", "date": "2026-08-01",
             "total": "", "signal": "HOLD", "source": "live"}]
    kept = bt.filter_rows(rows, us_only=False, entry_total=70)
    assert kept[0]["signal"] == "HOLD"


def test_filter_does_not_mutate_the_input_rows():
    bt.filter_rows(ARCHIVE, us_only=False, entry_total=70)
    assert ARCHIVE[2]["signal"] == "WATCH"


# --- 유니버스 이탈 -------------------------------------------------------
#
# 개별 종목의 결측으로는 이탈을 판정하지 않는다. 실측상 중간 결측이 최장 16
# 스캔일까지 있고 그 뒤 정상 복귀한다(KRYS 2026-08-02 -> 08-20). 반면 한
# 시장의 행이 통째로 0 이 되는 것은 데이터 실패가 아니라 유니버스 결정이다.

def _r(date, market):
    return {"date": date, "market": market}


def test_a_market_that_disappears_leaves_on_the_next_scan():
    rows = [_r("d1", "US"), _r("d1", "KR"),
            _r("d2", "US"), _r("d2", "KR"),
            _r("d3", "US")]

    assert bt.universe_exit_dates(rows) == {"KR": "d3"}


def test_a_market_present_on_the_last_scan_has_not_left():
    rows = [_r("d1", "US"), _r("d1", "KR"),
            _r("d2", "US"), _r("d2", "KR")]

    assert bt.universe_exit_dates(rows) == {}


def test_a_market_missing_for_one_scan_and_back_has_not_left():
    # 하루 비었다가 돌아오는 것은 조회 실패다. 마지막 등장 이후로 계속
    # 없을 때만 이탈로 본다.
    rows = [_r("d1", "US"), _r("d1", "KR"),
            _r("d2", "US"),
            _r("d3", "US"), _r("d3", "KR")]

    assert bt.universe_exit_dates(rows) == {}


def test_the_only_market_never_leaves():
    rows = [_r("d1", "US"), _r("d2", "US")]

    assert bt.universe_exit_dates(rows) == {}


# ─── 리포트 시작일 ───────────────────────────────────────────
# 07-31~08-21 아카이브는 66%가 backfill 이라 스코어가 미확정 봉 결함에
# 오염돼 있다. 깨끗한 live 구간부터 다시 세려면 그 앞을 잘라야 한다.

def test_start_date_drops_earlier_rows():
    rows = [{"ticker": "A", "market": "US", "date": "2026-08-24",
             "total": "80", "signal": "BUY", "source": "live"},
            {"ticker": "A", "market": "US", "date": "2026-08-25",
             "total": "80", "signal": "BUY", "source": "live"}]

    kept = bt.filter_rows(rows, start_date="2026-08-25")

    assert [r["date"] for r in kept] == ["2026-08-25"]


def test_the_start_date_itself_is_kept():
    rows = [{"ticker": "A", "market": "US", "date": "2026-08-25",
             "total": "80", "signal": "BUY", "source": "live"}]

    assert len(bt.filter_rows(rows, start_date="2026-08-25")) == 1


def test_no_start_date_keeps_everything():
    rows = [{"ticker": "A", "market": "US", "date": "2026-07-31",
             "total": "80", "signal": "BUY", "source": "live"}]

    assert len(bt.filter_rows(rows)) == 1


# ─── 시장 표시 ───────────────────────────────────────────────
# 청산 리포트에 "어느 시장 상품인가"를 적는다. ETF 는 거래소보다 자산군이
# 중요하다 - AMEX 라고 적혀 있으면 NYSE Arca 상장 ETF 인지 알 수 없다.

def test_venue_of_an_etf_is_the_asset_class():
    assert bt.venue_of({"asset_type": "ETF", "exchange": "AMEX"}) == "ETF"


def test_venue_of_a_stock_is_its_exchange():
    assert bt.venue_of({"asset_type": "STOCK", "exchange": "NASDAQ"}) == "NASDAQ"


def test_venue_is_blank_when_the_archive_predates_the_column():
    # 2026-08-25 이전 행에는 거래소가 없다. 지어내지 않는다.
    assert bt.venue_of({"asset_type": "STOCK", "exchange": ""}) == ""
    assert bt.venue_of({"asset_type": "STOCK"}) == ""


# --- account --------------------------------------------------------------
# With capital, tickers cannot be simulated independently: "no cash left" is
# a statement about what the other names already bought.

def test_an_account_forces_the_portfolio_path(monkeypatch):
    import sizing
    called = {}

    def spy(*a, **kw):
        called["portfolio"] = True
        return {"trades": [], "rejected": {"capacity": 0, "correlation": 0,
                                           "cash": 0},
                "rejected_pairs": [], "cash": 10_000, "capital": 10_000}

    monkeypatch.setattr(bt, "load_archive", lambda pattern: [
        {"ticker": "A", "market": "US", "date": "2026-08-01", "total": "80",
         "signal": "BUY", "source": "live", "target": "10",
         "asset_type": "STOCK", "exchange": "NYSE"}])
    monkeypatch.setattr(bt, "fetch_bars", lambda ticker: {
        "2026-08-01": bt.er.Bar("2026-08-01", 100.0, 101.0, 99.0, 100.0, 2.0)})
    monkeypatch.setattr(bt.pf, "simulate", spy)

    bt.run("x/*.csv", account=sizing.Account(capital=10_000))

    assert called.get("portfolio")


def test_the_result_carries_the_cash_position(monkeypatch):
    import sizing
    monkeypatch.setattr(bt, "load_archive", lambda pattern: [])

    result = bt.run("x/*.csv", account=sizing.Account(capital=500))

    assert result["cash"] == 500
    assert result["capital"] == 500


def test_without_an_account_the_result_has_no_capital(monkeypatch):
    monkeypatch.setattr(bt, "load_archive", lambda pattern: [])

    result = bt.run("x/*.csv")

    assert result["cash"] is None
    assert result["capital"] is None
