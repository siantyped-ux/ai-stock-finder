# 백테스트 하네스 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 스코어 아카이브와 청산 규칙을 결합해 "시그널대로 매매했다면 어떤 트레이드가 나왔는가"를 R 배수로 재현한다.

**Architecture:** `trade_sim.py` 가 순수 시뮬레이션(진입 판정·청산 루프·비용·집계)을 소유하고, `backtest.py` 가 아카이브 로드·OHLC 조회·리포트를 맡는다. 가격은 호출자가 넘기므로 시뮬레이터는 네트워크도 파일도 건드리지 않는다.

**Tech Stack:** Python 3.11, `exit_rules`(기존), yfinance·pandas(backtest.py 만), pytest

**Spec:** `docs/superpowers/specs/2026-08-18-backtest-harness-design.md`

---

## File Structure

| 파일 | 책임 |
|---|---|
| `trade_sim.py` (신규) | `Costs`·`Trade` 모델, 비용 R 환산, 종목별 시뮬레이션, R 집계 |
| `backtest.py` (신규) | `history/*.csv` 로드, OHLC 조회·ATR 산출, CLI, 리포트 출력 |
| `tests/test_trade_sim.py` (신규) | 합성 데이터 기반 순수 테스트 |

`trade_sim.py` 는 `exit_rules` 만 임포트한다. `history.py`·`stock_finder.py` 는 임포트하지 않는다 — 순환 의존을 막고, 하네스가 어떤 출처의 가격이든 넘길 수 있게 하기 위해서다.

**이 계획에 없는 것**: 포지션 사이징, 자산곡선·Sharpe·MDD, 워크포워드, 파라미터 최적화. 전부 3단계 범위다.

### 기존 API (변경하지 않음)

```python
exit_rules.Params(stop_atr_mult=3.0, trail_atr_mult=3.0, max_hold_days=60, exit_total=60)
exit_rules.Bar(date, open, high, low, close, atr14=None, total=None)
exit_rules.Position(ticker, entry_date, entry_price, initial_stop, r_unit,
                    high_since_entry, stop, bars_held)
exit_rules.ExitDecision(reason, price, date)

exit_rules.open_position(ticker, date, entry_price, atr_at_entry, params) -> Position
exit_rules.evaluate(position, bar, params) -> Optional[ExitDecision]
exit_rules.advance(position, bar, params) -> Position
```

아카이브 CSV 열(23개, 순서 고정):
`scan_ts_kst, date, ticker, name, market, sector, bar_date, close, volume, avg_vol20, atr14, market_cap, tech, macro, filing, value, total, consensus, signal, ev, target, hitl, source`

---

## Task 1: 비용 모델

**Files:**
- Create: `trade_sim.py`
- Test: `tests/test_trade_sim.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trade_sim.py` 신규 생성:

```python
import pytest

import trade_sim as ts


C = ts.Costs()


def test_costs_defaults():
    assert C.us_buy_pct == 0.10
    assert C.us_sell_pct == 0.10
    assert C.kr_buy_pct == 0.02
    assert C.kr_sell_pct == 0.02
    assert C.kr_tax_pct == 0.15
    assert C.slippage_pct == 0.05


def test_us_cost_in_r():
    # 진입가 100, 1R = 6.
    # 왕복 = 0.10 + 0.10 + 슬리피지 0.05*2 = 0.30% -> 0.30 원 -> 0.05 R
    got = ts.cost_r(entry_price=100.0, r_unit=6.0, market="US", costs=C)
    assert got == pytest.approx(0.30 / 6.0)


def test_kr_cost_includes_the_sell_tax():
    # 왕복 = 0.02 + 0.02 + 거래세 0.15 + 슬리피지 0.10 = 0.29%
    got = ts.cost_r(entry_price=100.0, r_unit=6.0, market="KR", costs=C)
    assert got == pytest.approx(0.29 / 6.0)


def test_bigger_r_absorbs_cost():
    # r_unit 이 두 배면 비용 부담(R 기준)은 절반이다.
    small = ts.cost_r(100.0, 6.0, "US", C)
    big = ts.cost_r(100.0, 12.0, "US", C)
    assert big == pytest.approx(small / 2)


def test_unknown_market_is_rejected():
    with pytest.raises(ValueError):
        ts.cost_r(100.0, 6.0, "JP", C)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_trade_sim.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trade_sim'`

- [ ] **Step 3: trade_sim.py 구현**

`trade_sim.py` 신규 생성:

```python
"""시그널 아카이브를 트레이드로 재현하는 순수 시뮬레이터.

성과는 R 배수로 집계한다. R = 진입가 - 초기 손절가 이므로 자본도 환율도
필요 없고, 한국·미국 종목을 같은 잣대로 비교할 수 있다. 포지션 사이징은
3단계 범위이며 여기서는 다루지 않는다.

파일도 네트워크도 건드리지 않는다. 가격은 호출자가 넘긴다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

import exit_rules as er


@dataclass(frozen=True)
class Costs:
    """편도 비용률(%). 전부 진입가 대비 백분율이다.

    kr_tax_pct 는 확정된 값이 아니다. 국내 증권거래세는 단계적으로 인하돼
    왔고 2026년 시행 세율을 확인하지 못했다. 현재 한국 종목 진입이 0건이라
    결과에 영향이 없으나, 한국 트레이드가 생기기 전에 실제 세율로 교체할 것.
    """
    us_buy_pct: float = 0.10
    us_sell_pct: float = 0.10
    kr_buy_pct: float = 0.02
    kr_sell_pct: float = 0.02
    kr_tax_pct: float = 0.15
    slippage_pct: float = 0.05


def cost_r(entry_price: float, r_unit: float, market: str,
           costs: Costs) -> float:
    """왕복 거래비용을 R 배수로 환산한다.

    매도 비용도 진입가 기준으로 잡는 근사다. 청산가 기준이 정확하지만
    그러면 미결 포지션의 비용이 확정되지 않아 닫힌 트레이드와 비교가
    어려워진다. 손절폭이 진입가의 10% 안팎이라 오차는 0.01R 미만이다.

    r_unit 이 클수록(변동성이 큰 종목일수록) 비용 부담이 자동으로 작아진다.
    """
    if market == "US":
        pct = costs.us_buy_pct + costs.us_sell_pct
    elif market == "KR":
        pct = costs.kr_buy_pct + costs.kr_sell_pct + costs.kr_tax_pct
    else:
        raise ValueError(f"알 수 없는 시장: {market!r} (US 또는 KR 이어야 함)")

    pct += costs.slippage_pct * 2
    return (entry_price * pct / 100.0) / r_unit
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_trade_sim.py -v`
Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add trade_sim.py tests/test_trade_sim.py
git commit -m "Convert round-trip trading costs into R multiples"
```

---

## Task 2: Trade 모델과 진입 판정

**Files:**
- Modify: `trade_sim.py`
- Modify: `tests/test_trade_sim.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_trade_sim.py` 끝에 추가:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_trade_sim.py -v`
Expected: FAIL — `AttributeError: module 'trade_sim' has no attribute 'EntryState'`

- [ ] **Step 3: 구현**

`trade_sim.py` 의 `cost_r` 아래에 추가:

```python
BUY_SIGNALS = ("BUY", "STRONG_BUY")


@dataclass(frozen=True)
class Trade:
    ticker: str
    market: str
    source: str
    entry_date: str
    entry_price: float
    r_unit: float
    exit_date: Optional[str]
    exit_price: Optional[float]
    exit_reason: Optional[str]
    bars_held: int
    is_open: bool
    gross_r: float
    cost_r: float
    net_r: float


@dataclass(frozen=True)
class EntryState:
    """진입 판정에 필요한 상태. 봉 유무와 무관하게 날짜마다 갱신한다."""
    was_buy: bool = False
    pending: bool = False


@dataclass(frozen=True)
class EntryStep:
    state: EntryState
    should_enter: bool


def step_entry(state: EntryState, signal: str) -> EntryStep:
    """아카이브 하루치를 소화해 진입 대기 여부를 갱신한다.

    BUY 로 전환되는 순간에만 pending 이 선다. 봉이 없는 날(주말·휴장)에도
    호출해야 한다 - 그러지 않으면 토요일에 전환된 종목이 월요일에는 이미
    전환이 아니어서 영영 진입하지 못한다.
    """
    is_buy = signal in BUY_SIGNALS

    if not is_buy:
        pending = False
    elif not state.was_buy:
        pending = True           # 전환
    else:
        pending = state.pending  # 계속 BUY - 기존 pending 유지

    return EntryStep(EntryState(was_buy=is_buy, pending=pending),
                     should_enter=pending)


def consume(state: EntryState) -> EntryState:
    """진입이 일어났으니 대기를 해제한다."""
    return replace(state, pending=False)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_trade_sim.py -v`
Expected: 11 passed

- [ ] **Step 5: 커밋**

```bash
git add trade_sim.py tests/test_trade_sim.py
git commit -m "Fire entries on transition into BUY and carry pending across sessionless days"
```

---

## Task 3: 종목별 시뮬레이션

**Files:**
- Modify: `trade_sim.py`
- Modify: `tests/test_trade_sim.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_trade_sim.py` 끝에 추가. 파일 상단 import 에 `import exit_rules as er` 를 추가한다:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_trade_sim.py -v`
Expected: FAIL — `AttributeError: module 'trade_sim' has no attribute 'simulate_ticker'`

- [ ] **Step 3: 구현**

`trade_sim.py` 끝에 추가:

```python
def _make_trade(pos: er.Position, market: str, source: str,
                exit_price: float, exit_date: Optional[str],
                exit_reason: Optional[str], costs: Costs) -> Trade:
    gross = (exit_price - pos.entry_price) / pos.r_unit
    cost = cost_r(pos.entry_price, pos.r_unit, market, costs)
    return Trade(
        ticker=pos.ticker,
        market=market,
        source=source,
        entry_date=pos.entry_date,
        entry_price=pos.entry_price,
        r_unit=pos.r_unit,
        exit_date=exit_date,
        exit_price=exit_price if exit_date else None,
        exit_reason=exit_reason,
        bars_held=pos.bars_held,
        is_open=exit_date is None,
        gross_r=gross,
        cost_r=cost,
        net_r=gross - cost,
    )


def simulate_ticker(ticker: str, market: str, rows: list, bars: dict,
                    params: er.Params, costs: Costs) -> list:
    """티커 하나의 트레이드를 재현한다.

    rows 는 아카이브 행(date·signal·total·source)을 날짜 오름차순으로,
    bars 는 날짜 -> exit_rules.Bar 매핑이다. 봉이 없는 날은 세션이 없었다는
    뜻이므로 보유 일수를 세지 않는다.

    exit_rules 의 계약대로 evaluate 를 먼저 하고 advance 를 나중에 한다.
    진입한 봉도 advance 로 접어 넣는다 - 그래야 그날 고가가 다음 봉의
    트레일 계산에 반영되고 bars_held 가 실제 보유 봉 수와 맞는다. 다만
    진입 봉에서는 evaluate 를 돌리지 않는다. 그 봉의 시가에 막 들어갔고,
    같은 봉에서 청산까지 판정하려면 봉 안의 시간 순서를 알아야 한다.
    """
    trades = []
    state = EntryState()
    pos = None
    last_close = None
    open_source = ""

    for row in rows:
        bar = bars.get(row["date"])
        if bar is not None and row.get("total") is not None:
            # 그날 스코어를 봉에 실어 SIGNAL 판정이 가능하게 한다.
            bar = er.Bar(bar.date, bar.open, bar.high, bar.low, bar.close,
                         bar.atr14, row["total"])

        if pos is not None and bar is not None:
            decision = er.evaluate(pos, bar, params)
            if decision is not None:
                trades.append(_make_trade(pos, market, open_source,
                                          decision.price, decision.date,
                                          decision.reason, costs))
                pos = None
            else:
                pos = er.advance(pos, bar, params)
                last_close = bar.close

        step = step_entry(state, row["signal"])
        entered = False
        if step.should_enter and pos is None and bar is not None:
            if bar.atr14:
                pos = er.open_position(ticker, row["date"], bar.open,
                                       bar.atr14, params)
                pos = er.advance(pos, bar, params)
                open_source = row["source"]
                last_close = bar.close
            # ATR 이 없어 못 들어갔어도 이 전환은 소진한다. 그러지 않으면
            # 같은 전환으로 다음 봉에 뒤늦게 진입해 버린다.
            entered = True

        state = consume(step.state) if entered else step.state

    if pos is not None and last_close is not None:
        trades.append(_make_trade(pos, market, open_source, last_close,
                                  None, None, costs))

    return trades
```


- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_trade_sim.py -v`
Expected: 18 passed

- [ ] **Step 5: 전체 테스트 확인**

Run: `python -m pytest tests/ -v`
Expected: 87 passed (기존 69 + 신규 18)

- [ ] **Step 6: 커밋**

```bash
git add trade_sim.py tests/test_trade_sim.py
git commit -m "Simulate one ticker's trades from archived signals"
```

---

## Task 4: R 집계

**Files:**
- Modify: `trade_sim.py`
- Modify: `tests/test_trade_sim.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_trade_sim.py` 끝에 추가:

```python
def _trade(net_r, is_open=False, reason="STOP"):
    return ts.Trade(
        ticker="X", market="US", source="live", entry_date="d1",
        entry_price=100.0, r_unit=6.0,
        exit_date=None if is_open else "d2",
        exit_price=None if is_open else 106.0,
        exit_reason=None if is_open else reason,
        bars_held=1, is_open=is_open,
        gross_r=net_r + 0.05, cost_r=0.05, net_r=net_r,
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_trade_sim.py -v`
Expected: FAIL — `AttributeError: module 'trade_sim' has no attribute 'summarize'`

- [ ] **Step 3: 구현**

`trade_sim.py` 끝에 추가하고, 파일 상단 import 에 `from collections import Counter` 를 추가한다:

```python
# 파일 상단 import 블록에 추가:
#     from collections import Counter


def summarize(trades: list[Trade]) -> dict:
    """R 통계를 낸다. 닫힌 트레이드와 미결 포지션을 절대 섞지 않는다.

    미결을 승률에 넣으면 "아직 손절되지 않았을 뿐"인 포지션이 승리로 잡혀
    성과가 부풀려진다.
    """
    closed = [t for t in trades if not t.is_open]
    opened = [t for t in trades if t.is_open]

    wins = sum(1 for t in closed if t.net_r > 0)
    return {
        "closed": len(closed),
        "open": len(opened),
        "win_rate": (wins / len(closed)) if closed else None,
        "avg_net_r": (sum(t.net_r for t in closed) / len(closed)) if closed else None,
        "total_net_r": sum(t.net_r for t in closed),
        "open_net_r": sum(t.net_r for t in opened),
        "by_reason": dict(Counter(t.exit_reason for t in closed)),
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/ -v`
Expected: 91 passed (기존 87 + 신규 4)

- [ ] **Step 5: 커밋**

```bash
git add trade_sim.py tests/test_trade_sim.py
git commit -m "Aggregate R statistics without mixing open positions in"
```

---

## Task 5: 순수성 계약

**Files:**
- Modify: `tests/test_trade_sim.py`

- [ ] **Step 1: 테스트 추가**

`tests/test_trade_sim.py` 끝에 추가. 파일 상단에 `import inspect` 를 추가한다:

```python
def test_module_has_no_io_dependencies():
    # 하네스가 어떤 출처의 가격이든 넘길 수 있어야 하고, history/stock_finder 를
    # 임포트하면 순환 의존이 생긴다.
    src = inspect.getsource(ts)
    for banned in ("import history", "import stock_finder", "import yfinance",
                   "import requests", "open(", "subprocess", "csv"):
        assert banned not in src, f"trade_sim 이 {banned} 를 쓰면 안 된다"
```

- [ ] **Step 2: 테스트 실행**

Run: `python -m pytest tests/test_trade_sim.py -v`
Expected: 23 passed — 구현이 이미 조건을 만족하므로 바로 통과한다.
실패하면 `trade_sim.py` 가 스펙을 위반한 것이므로 모듈을 고친다. 테스트를 고치지 않는다.

- [ ] **Step 3: 커밋**

```bash
git add tests/test_trade_sim.py
git commit -m "Pin the simulator's purity contract"
```

---

## Task 6: 아카이브 로드와 OHLC 조회

**Files:**
- Create: `backtest.py`

- [ ] **Step 1: 구현**

`backtest.py` 신규 생성:

```python
"""스코어 아카이브를 트레이드로 재현하고 R 통계를 출력한다.

history/*.csv 의 시그널과 yfinance 로 재조회한 OHLC 를 결합해 trade_sim 에
넘긴다. 규칙은 전부 trade_sim/exit_rules 에 있고 여기서는 데이터만 모은다.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
from collections import defaultdict

import numpy as np
import yfinance as yf

import exit_rules as er
import trade_sim as ts


def load_archive(pattern: str = "history/*.csv") -> list[dict]:
    """아카이브 전체를 날짜 오름차순 행 목록으로 읽는다."""
    rows = []
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8", newline="") as f:
            rows.extend(csv.DictReader(f))
    rows.sort(key=lambda r: (r["date"], r["ticker"]))
    return rows


def atr_series(hist_df, period: int = 14) -> dict:
    """날짜 -> 그 날짜 '전일까지'의 ATR.

    당일 고저를 포함해 계산하면 개장 전에 정해져 있어야 할 손절선이 미래
    정보를 쓰게 된다. exit_rules.Bar 가 명시한 타이밍 계약이다.
    """
    high = hist_df["High"].values.astype(float)
    low = hist_df["Low"].values.astype(float)
    close = hist_df["Close"].values.astype(float)
    dates = [f"{d:%Y-%m-%d}" for d in hist_df.index]

    tr = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - close[:-1]),
        np.abs(low[1:] - close[:-1]),
    ])

    out = {}
    for i in range(len(dates)):
        # i 번째 봉 시점에 알 수 있는 TR 은 인덱스 i-1 까지다.
        available = tr[:i]
        if len(available) >= period:
            out[dates[i]] = round(float(np.mean(available[-period:])), 4)
    return out


def fetch_bars(ticker: str) -> dict:
    """티커의 일봉을 날짜 -> exit_rules.Bar 로 반환한다. 실패하면 빈 dict."""
    try:
        df = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
    except Exception:
        return {}
    if df is None or df.empty:
        return {}

    df = df[df["Close"].notna()]
    if df.empty:
        return {}

    atrs = atr_series(df)
    bars = {}
    for idx, row in df.iterrows():
        date = f"{idx:%Y-%m-%d}"
        bars[date] = er.Bar(
            date=date,
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            atr14=atrs.get(date),
        )
    return bars
```

- [ ] **Step 2: 아카이브 로드 확인**

```bash
python -c "
import backtest as bt
rows = bt.load_archive()
print('행 수:', len(rows))
print('날짜 수:', len({r['date'] for r in rows}))
print('source:', {r['source'] for r in rows})
print('첫 행 열 수:', len(rows[0]))
"
```
Expected: 19,000행대, 날짜 18개, `source={'backfill'}`, 열 23개

- [ ] **Step 3: ATR 타이밍 확인**

```bash
python -c "
import backtest as bt
bars = bt.fetch_bars('NVDA')
ds = sorted(bars)[-3:]
for d in ds:
    b = bars[d]
    print(d, 'close', round(b.close,2), 'atr14', b.atr14)
print('봉 수:', len(bars))
"
```
Expected: 마지막 3봉이 출력되고 `atr14` 가 채워져 있음. 값이 매일 조금씩 달라야 한다(고정이면 전일까지 슬라이싱이 틀린 것).

- [ ] **Step 4: 커밋**

```bash
git add backtest.py
git commit -m "Load the score archive and rebuild bars with lagged ATR"
```

---

## Task 7: 리포트와 CLI

**Files:**
- Modify: `backtest.py`

- [ ] **Step 1: 구현**

`backtest.py` 끝에 추가:

```python
def run(pattern: str = "history/*.csv", params: er.Params = None,
        costs: ts.Costs = None) -> dict:
    """아카이브 전체를 시뮬레이션하고 트레이드·통계·커버리지를 돌려준다."""
    params = params or er.Params()
    costs = costs or ts.Costs()

    rows = load_archive(pattern)
    by_ticker = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(r)

    # 한 번이라도 BUY 였던 티커만 시세를 받는다.
    candidates = {t for t, rs in by_ticker.items()
                  if any(r["signal"] in ts.BUY_SIGNALS for r in rs)}

    trades, failed = [], []
    for ticker in sorted(candidates):
        bars = fetch_bars(ticker)
        if not bars:
            failed.append(ticker)
            continue
        rs = by_ticker[ticker]
        market = rs[0]["market"]
        prepared = [{"date": r["date"], "signal": r["signal"],
                     "total": int(r["total"]) if r["total"] else None,
                     "source": r["source"]} for r in rs]
        trades.extend(ts.simulate_ticker(ticker, market, prepared, bars,
                                         params, costs))

    dates = sorted({r["date"] for r in rows})
    sources = [r["source"] for r in rows]
    return {
        "trades": trades,
        "summary": ts.summarize(trades),
        "dates": dates,
        "live_rows": sum(1 for s in sources if s == "live"),
        "backfill_rows": sum(1 for s in sources if s == "backfill"),
        "candidates": sorted(candidates),
        "failed": failed,
    }


def report(result: dict) -> None:
    """데이터 출처를 가장 먼저 출력한다.

    이 경고가 없으면 몇 달 뒤 결과만 보고 시그널이 검증됐다고 오독한다.
    backfill 행의 스코어는 그때 대시보드가 보여준 값이지 올바른 값이 아니다.
    """
    dates, s = result["dates"], result["summary"]
    live, back = result["live_rows"], result["backfill_rows"]

    print("=" * 60)
    print("[데이터 커버리지]")
    print(f"  아카이브 {len(dates)}일 ({dates[0]} ~ {dates[-1]})")
    print(f"  source: backfill {back}행 / live {live}행")
    if live == 0:
        print("  !! 전부 backfill 이다. 이 스코어는 미확정 봉 결함에 오염된")
        print("     값이므로, 아래 결과는 파이프라인 검증용이며 시그널 성능의")
        print("     근거가 아니다.")
    elif back:
        print(f"  !! backfill 이 섞여 있다 (전체의 {back/(back+live)*100:.0f}%).")
    if result["failed"]:
        print(f"  시세 조회 실패: {', '.join(result['failed'])}")

    print()
    print(f"[닫힌 트레이드] {s['closed']}건")
    if s["closed"]:
        print(f"  승률 {s['win_rate']*100:.1f}% · 평균 {s['avg_net_r']:+.2f}R"
              f" · 합계 {s['total_net_r']:+.2f}R")
        print(f"  청산사유: {s['by_reason']}")

    print(f"[미결 포지션] {s['open']}건 · 평가 {s['open_net_r']:+.2f}R")
    for t in result["trades"]:
        if t.is_open:
            print(f"    {t.ticker:8s} {t.entry_date} @{t.entry_price:.2f}"
                  f" · {t.bars_held}봉 · {t.net_r:+.2f}R")
    print("=" * 60)


def main():
    p = argparse.ArgumentParser(description="스코어 아카이브 백테스트")
    p.add_argument("--history", default="history/*.csv")
    p.add_argument("--stop-atr-mult", type=float, default=3.0)
    p.add_argument("--trail-atr-mult", type=float, default=3.0)
    p.add_argument("--max-hold-days", type=int, default=60)
    p.add_argument("--exit-total", type=int, default=60)
    args = p.parse_args()

    params = er.Params(
        stop_atr_mult=args.stop_atr_mult,
        trail_atr_mult=args.trail_atr_mult,
        max_hold_days=args.max_hold_days,
        exit_total=args.exit_total,
    )
    report(run(args.history, params))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실제 아카이브로 실행**

Run: `python backtest.py`
Expected: 커버리지 경고(전부 backfill), 닫힌 트레이드와 미결 포지션이 각각 출력된다.
시세 조회에 티커 7개 분량이 필요해 약 10~30초 걸린다. 실제 건수를 verbatim 으로 보고할 것.

- [ ] **Step 3: 파라미터 전달 확인**

Run: `python backtest.py --exit-total 70`
Expected: `exit_total` 이 높아져 SIGNAL 청산이 늘어난다 - 기본 실행보다 닫힌 트레이드가 같거나 많아야 한다. 두 실행의 건수를 함께 보고할 것.

- [ ] **Step 4: 전체 테스트**

Run: `python -m pytest tests/ -v`
Expected: 92 passed

- [ ] **Step 5: 커밋**

```bash
git add backtest.py
git commit -m "Report R statistics with the data provenance warning first"
```

---

## 완료 기준 점검

- [ ] `trade_sim.py` 가 순수 함수로 구현되고 테스트가 통과한다 (Task 4 Step 4)
- [ ] `trade_sim.py` 가 `history.py`·`stock_finder.py` 를 임포트하지 않는다 (Task 5)
- [ ] `python backtest.py` 가 오류 없이 실행되어 닫힌 트레이드와 미결 포지션을 분리 출력한다 (Task 7 Step 2)
- [ ] 리포트가 `source` 구성을 통계보다 먼저 출력한다 (Task 7 Step 1·2)
- [ ] 스펙의 테스트 11개 항목이 모두 커버된다:
  진입 전환(Task 2) · 주말 이월(Task 2·3) · pending 해제(Task 2) ·
  보유 중 재진입 없음(Task 3) · 청산 후 재진입(Task 2) ·
  비용 US(Task 1) · 비용 KR(Task 1) · 미결 분리(Task 4) ·
  미결 평가(Task 3) · evaluate→advance 순서(Task 3) · 봉 없는 날(Task 3)
