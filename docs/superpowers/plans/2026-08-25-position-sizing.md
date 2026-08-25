# 포지션 사이징 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 자본 $10,000 을 시뮬레이터의 실제 제약으로 만들어, 거래당 리스크 $100 로 수량이 정해지고 현금이 바닥나면 신호를 놓치게 한다.

**Architecture:** 사이징 산수를 `sizing.py` 순수 모듈로 떼고, `portfolio.simulate` 가 현금 잔고만 들고 다니며 그 함수를 부른다. 수량은 `Trade.qty` 로 시뮬레이션 결과가 되고 리포트는 그 값을 쓴다.

**Tech Stack:** Python 3.11, pytest, dataclasses. 새 의존성 없음.

설계: `docs/superpowers/specs/2026-08-25-position-sizing-design.md`

---

## 사전 확인

이 계획이 손대는 곳의 현재 상태:

- `trade_sim.Trade` 는 `ticker, market, source, entry_date, entry_price, r_unit, exit_date, exit_price, exit_reason, bars_held, is_open, gross_r, cost_r, net_r, mark_price, initial_stop, high_since_entry, stop, target_price` 필드를 갖는다. `qty` 는 없다.
- `portfolio.simulate(rows_by_ticker, bars_by_ticker, markets, params, costs, limits, correlator, universe_exits)` 는 날짜 루프를 돌며 `rejected = {"capacity": 0, "correlation": 0}` 을 센다.
- `backtest.run(pattern, params, costs, us_only, entry_total, limits, start_date)` 는 `limits` 가 비활성이면 `trade_sim.simulate_ticker` 를, 아니면 `portfolio.simulate` 를 쓴다.
- `perf_report.to_row(trade, capital, costs)` 가 `qty = max(1, int(capital // trade.entry_price))` 로 수량을 만든다.

**현금 회전에 거래비용을 반영하지 않는다.** 비용은 `make_trade` 가 `cost_r` 로 이미 계산한다. 현금에서 또 빼면 같은 사실을 두 곳에서 관리하게 되고, 편도 0.15% 가 매수 가능 주 수를 바꾸는 경우는 사실상 없다. 현금은 "몇 주 살 수 있나" 를 정하는 용도로만 쓴다.

---

### Task 1: `sizing.py` — Account 와 수량 계산

**Files:**
- Create: `sizing.py`
- Test: `tests/test_sizing.py`

- [ ] **Step 1: Write the failing test**

`tests/test_sizing.py` 를 새로 만든다.

```python
"""포지션 사이징 테스트.

산수만 담는 순수 모듈이라 표 하나로 고정할 수 있다. 날짜도 네트워크도
들어오지 않는다.

설계: docs/superpowers/specs/2026-08-25-position-sizing-design.md
"""
import sizing


ACC = sizing.Account(capital=10_000)


def test_default_account_risks_one_percent():
    # 자본이 작으면 리스크 비율이 사실상 종목 수를 정한다. 1% 는 약 10종목.
    assert ACC.risk_budget == 100.0


def test_default_account_caps_a_position_at_twenty_percent():
    # 손절폭이 좁은 종목이 자본을 독식하는 것을 막는다(실측 PHG 42%).
    assert ACC.max_position == 2_000.0


def test_quantity_comes_from_the_risk_budget():
    # 1R 이 $6 이면 $100 을 잃도록 16주. 16 x 6 = 96 <= 100.
    assert sizing.shares(10_000, ACC, entry_price=100.0, r_unit=6.0) == 16


def test_the_position_cap_can_bind_before_the_risk_budget(): 
    # 1R 이 $1 이면 리스크로는 100주지만 100 x $50 = $5,000 로 상한 초과.
    # 상한 $2,000 에서 40주로 잘린다.
    assert sizing.shares(10_000, ACC, entry_price=50.0, r_unit=1.0) == 40


def test_cash_can_bind_before_both():
    # 현금이 $500 뿐이면 $100 짜리를 5주까지만 산다.
    assert sizing.shares(500, ACC, entry_price=100.0, r_unit=6.0) == 5


def test_a_share_too_expensive_for_the_budget_is_zero():
    # 1주 값이 상한을 넘으면 0주다. 목표 리스크를 못 맞추는 포지션은 열지 않는다.
    assert sizing.shares(10_000, ACC, entry_price=2_500.0, r_unit=6.0) == 0


def test_a_risk_unit_wider_than_the_budget_is_zero():
    # 1R 이 $150 이면 1주만 사도 $150 을 걸게 된다. 예산이 $100 이므로 사지 않는다.
    assert sizing.shares(10_000, ACC, entry_price=100.0, r_unit=150.0) == 0


def test_no_cash_means_no_shares():
    assert sizing.shares(0, ACC, entry_price=100.0, r_unit=6.0) == 0


def test_a_non_positive_risk_unit_is_refused():
    # r_unit 이 0 이면 나눗셈이 터지고, 음수면 수량이 음수가 된다.
    assert sizing.shares(10_000, ACC, entry_price=100.0, r_unit=0.0) == 0
    assert sizing.shares(10_000, ACC, entry_price=100.0, r_unit=-6.0) == 0


def test_a_non_positive_price_is_refused():
    assert sizing.shares(10_000, ACC, entry_price=0.0, r_unit=6.0) == 0


def test_a_custom_account_scales_both_budgets():
    acc = sizing.Account(capital=50_000, risk_pct=2.0, max_weight_pct=10.0)
    assert acc.risk_budget == 1_000.0
    assert acc.max_position == 5_000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sizing.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sizing'`

- [ ] **Step 3: Write minimal implementation**

`sizing.py` 를 새로 만든다.

```python
"""포지션 사이징.

거래당 잃을 금액을 고정하고 손절폭으로 수량을 역산한다. 정액으로 사면 손절폭이
종목마다 달라 실제 리스크가 제각각이 된다 - 2026-08-25 실측에서 손절폭이
4.7%~21.7% 로 4.6배 차이났고, 같은 $1,000 을 넣어도 잃는 돈은 $47 과 $217 였다.

리스크를 고정하면 1R 의 달러 가치가 종목마다 같아져 `R 합계 x 1R = 실현 손익`이
성립한다. 이 저장소가 성과를 R 배수로 재 온 것과 처음으로 맞물린다.

산수만 담는다. 날짜도 네트워크도 모른다 - exit_rules(청산 판정)와 flow(수급
점수)를 순수 모듈로 떼어 둔 것과 같은 결이다.

설계: docs/superpowers/specs/2026-08-25-position-sizing-design.md
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Account:
    """계좌. 얼마가 있고 한 번에 얼마를 걸지.

    portfolio.Limits 와 따로 두는 것은 성격이 다르기 때문이다. Limits 는
    "무엇을 막을까"(동시 보유 수, 상관)이고 이쪽은 "얼마가 있나" 다.

    risk_pct 는 초기 자본 대비다. 현재 평가자산이 아니다 - 표본이 0인 단계에서
    복리를 켜면 성과가 시그널 품질 때문인지 사이징 때문인지 갈라볼 수 없다.

    max_weight_pct 는 한 종목에 넣을 수 있는 상한이다. 리스크만으로 정하면
    손절폭이 좁은 종목이 자본을 독식한다(실측 PHG 42%).
    """
    capital: float
    risk_pct: float = 1.0
    max_weight_pct: float = 20.0

    @property
    def risk_budget(self) -> float:
        """거래당 잃을 금액."""
        return self.capital * self.risk_pct / 100.0

    @property
    def max_position(self) -> float:
        """한 종목에 넣을 수 있는 최대 금액."""
        return self.capital * self.max_weight_pct / 100.0


def shares(cash: float, account: Account,
           entry_price: float, r_unit: float) -> int:
    """살 주 수. 셋 중 가장 빡빡한 제약이 이긴다. 못 사면 0.

    0 을 돌려주면 호출자는 그 포지션을 열지 않는다. 목표 리스크를 못 맞추는
    포지션은 규칙을 흐리므로 여는 것보다 건너뛰는 편이 낫다.

    r_unit 이 예산보다 크면 1주만 사도 예산을 넘기므로 0 이 된다. 값이 0 이나
    음수면 나눗셈이 터지거나 수량이 음수가 되므로 같이 막는다.
    """
    if entry_price <= 0 or r_unit <= 0:
        return 0

    by_risk = int(account.risk_budget // r_unit)
    by_cap = int(account.max_position // entry_price)
    by_cash = int(cash // entry_price)
    return max(min(by_risk, by_cap, by_cash), 0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sizing.py -q`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add sizing.py tests/test_sizing.py
git commit -m "Add position sizing by fixed risk"
```

---

### Task 2: `Trade.qty` — 수량을 시뮬레이션 결과로

**Files:**
- Modify: `trade_sim.py` (`Trade` dataclass, `make_trade`)
- Test: `tests/test_trade_sim.py`

- [ ] **Step 1: Write the failing test**

`tests/test_trade_sim.py` 끝에 추가한다.

```python
# ─── 수량 ────────────────────────────────────────────────────
# 자본 제약이 생기면 수량은 시뮬레이션 결과여야 한다. 리포트가 사후에 지어내면
# 진입 여부에 영향을 주지 못하고, 같은 사실이 두 곳에서 따로 계산된다.

def test_a_trade_carries_no_quantity_without_capital():
    # 자본 없는 경로에는 수량 개념이 없다. 0 이나 1 로 채우지 않는다.
    rows = [_row("d1", "BUY"), _row("d2", "BUY")]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 100.5, 102.0, 100.0, 101.5)}

    t = ts.simulate_ticker("X", "US", rows, bars, P, C)[0]

    assert t.qty is None


def test_make_trade_records_the_quantity_it_was_given():
    pos = er.open_position("X", "d1", 100.0, 2.0, P)
    trade = ts.make_trade(pos, "US", "live", 110.0, "d2", "TRAIL", C, qty=16)

    assert trade.qty == 16
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trade_sim.py -q -k quantity`
Expected: FAIL — `AttributeError: 'Trade' object has no attribute 'qty'`

- [ ] **Step 3: Write minimal implementation**

`trade_sim.py` 의 `Trade` dataclass 맨 끝에 필드를 추가한다. 기존 필드 뒤에 붙여야 위치 인자로 만드는 코드가 깨지지 않는다.

```python
    target_price: Optional[float]
    # 자본 제약이 있을 때만 채워진다. 없으면 None - 0 이나 1 로 채우면
    # "안 샀다" 와 "한 주 샀다" 가 구분되지 않는다.
    qty: Optional[int] = None
```

`make_trade` 시그니처와 반환에 `qty` 를 넣는다.

```python
def make_trade(pos: er.Position, market: str, source: str,
                exit_price: float, exit_date: Optional[str],
                exit_reason: Optional[str], costs: Costs,
                qty: Optional[int] = None) -> Trade:
```

반환 dict 의 `target_price=pos.target_price,` 다음 줄에 추가한다.

```python
        target_price=pos.target_price,
        qty=qty,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trade_sim.py -q`
Expected: PASS — 기존 테스트도 전부 통과해야 한다. `qty` 는 기본값이 있어 기존 호출부가 깨지지 않는다.

- [ ] **Step 5: Commit**

```bash
git add trade_sim.py tests/test_trade_sim.py
git commit -m "Carry share count on Trade"
```

---

### Task 3: `portfolio.simulate` — 현금 추적

**Files:**
- Modify: `portfolio.py` (`simulate` 시그니처, 진입/청산 처리, 반환)
- Test: `tests/test_portfolio.py`

- [ ] **Step 1: Write the failing test**

`tests/test_portfolio.py` 끝에 추가한다.

```python
# ─── 자본 제약 ────────────────────────────────────────────────
# 자본이 없으면 "현금이 바닥나 못 샀다" 를 표현할 수 없다. 종목을 독립적으로
# 보는 simulate_ticker 로는 애초에 불가능한 계층이다.

import sizing


def test_quantity_is_recorded_on_the_trade():
    r = rows(["HOLD", "BUY", "BUY"])
    b = bars(DATES, [100, 100, 105, 110, 115])

    got = pf.simulate({"A": r}, {"A": b}, {"A": "US"},
                      account=sizing.Account(capital=10_000))

    # 1R = ATR 1.0 x 3 = 3.0. 예산 $100 / 3 = 33주. 상한 $2,000 / $100 = 20주.
    assert got["trades"][0].qty == 20


def test_cash_runs_out_and_the_rest_is_skipped():
    # 상한 20% 짜리 포지션 다섯이면 자본을 다 쓴다. 여섯째는 현금이 없다.
    r = rows(["HOLD", "BUY", "BUY"])
    b = bars(DATES, [100, 100, 105, 110, 115])
    names = ["A", "B", "C", "D", "E", "F"]

    got = pf.simulate({n: r for n in names}, {n: b for n in names},
                      {n: "US" for n in names},
                      account=sizing.Account(capital=10_000))

    assert len(got["trades"]) == 5
    assert got["rejected"]["cash"] == 1


def test_the_highest_score_gets_the_cash():
    b = bars(DATES, [100, 100, 105, 110, 115])
    got = pf.simulate(
        {"LOW": rows(["HOLD", "BUY", "BUY"], [70] * 3),
         "HIGH": rows(["HOLD", "BUY", "BUY"], [90] * 3)},
        {"LOW": b, "HIGH": b}, {"LOW": "US", "HIGH": "US"},
        account=sizing.Account(capital=2_000))

    assert [t.ticker for t in got["trades"]] == ["HIGH"]
    assert got["rejected"]["cash"] == 1


def test_closing_returns_the_cash():
    # A 는 3일차에 급락해 손절되고, 그 돈으로 4일차에 B 가 들어간다.
    a_rows = rows(["HOLD", "BUY", "BUY", "AVOID", "AVOID"])
    b_rows = rows(["HOLD", "HOLD", "HOLD", "BUY", "BUY"])
    a_bars = bars(DATES, [100, 100, 50, 50, 50])
    b_bars = bars(DATES, [100, 100, 100, 100, 105])

    got = pf.simulate({"A": a_rows, "B": b_rows},
                      {"A": a_bars, "B": b_bars},
                      {"A": "US", "B": "US"},
                      account=sizing.Account(capital=2_000))

    assert {t.ticker for t in got["trades"]} == {"A", "B"}


def test_the_report_needs_the_cash_left_over():
    r = rows(["HOLD", "BUY", "BUY"])
    b = bars(DATES, [100, 100, 105, 110, 115])

    got = pf.simulate({"A": r}, {"A": b}, {"A": "US"},
                      account=sizing.Account(capital=10_000))

    # 20주 x $100 = $2,000 투입. 남은 현금 $8,000.
    assert got["cash"] == 8_000
    assert got["capital"] == 10_000


def test_without_an_account_nothing_changes():
    # 자본을 주지 않으면 지금까지의 동작이다 - 신호가 나면 무조건 진입한다.
    r = rows(["HOLD", "BUY", "BUY", "BUY", "BUY"])
    b = bars(DATES, [100, 100, 105, 110, 115])

    got = pf.simulate({"A": r}, {"A": b}, {"A": "US"})

    assert got["trades"][0].qty is None
    assert got["cash"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_portfolio.py -q -k "cash or quantity or account"`
Expected: FAIL — `TypeError: simulate() got an unexpected keyword argument 'account'`

- [ ] **Step 3: Write minimal implementation**

`portfolio.py` 상단 import 에 추가한다.

```python
import exit_rules as er
import sizing
import trade_sim as ts
```

`simulate` 시그니처에 `account` 를 더한다.

```python
def simulate(rows_by_ticker: dict, bars_by_ticker: dict, markets: dict,
             params: er.Params = None, costs: ts.Costs = None,
             limits: Limits = None,
             correlator: Optional[Callable] = None,
             universe_exits: dict = None,
             account: sizing.Account = None) -> dict:
```

docstring 에 한 줄 더한다.

```python
    account         계좌. 주지 않으면 자본 제약이 없다(신호가 나면 무조건 진입)
```

상태 변수 옆에 현금과 수량 장부를 둔다. `rejected` 에도 항목을 더한다.

```python
    trades = []
    cash = account.capital if account else None
    qty_by_ticker: dict = {}       # ticker -> 보유 주 수
    rejected = {"capacity": 0, "correlation": 0, "cash": 0}
```

청산 처리에서 수량을 실어 보내고 현금을 회수한다. 기존 `trades.append(ts.make_trade(...))` 블록을 이렇게 바꾼다.

```python
            decision = er.evaluate(positions[ticker], bar, params)
            if decision is not None:
                qty = qty_by_ticker.pop(ticker, None)
                trades.append(ts.make_trade(positions[ticker],
                                            markets.get(ticker, "US"),
                                            sources.get(ticker, ""), decision.price,
                                            decision.date, decision.reason, costs,
                                            qty))
                # 회수금은 다음 신호에 다시 쓰인다. 비용은 빼지 않는다 -
                # make_trade 가 cost_r 로 이미 계산하고, 편도 0.15% 가 매수
                # 가능 주 수를 바꾸는 경우는 사실상 없다.
                if cash is not None and qty:
                    cash += qty * decision.price
                del positions[ticker]
                closed_today.add(ticker)
```

자격 심사 루프에서 상관 검사 다음에 사이징을 넣는다.

```python
            clash = _too_correlated(ticker, list(positions), limits, correlator)
            if clash is not None:
                rejected["correlation"] += 1
                rejected_pairs.append((date, ticker, clash[0], round(clash[1], 3)))
                continue

            pos = er.open_position(ticker, date, bar.open, bar.atr14, params,
                                   row.get("target"))
            qty = None
            if account is not None:
                # r_unit 은 open_position 이 정한 뒤에야 알 수 있다.
                qty = sizing.shares(cash, account, bar.open, pos.r_unit)
                if qty < 1:
                    rejected["cash"] += 1
                    continue
                cash -= qty * bar.open
                qty_by_ticker[ticker] = qty

            positions[ticker] = er.advance(pos, bar, params)
            sources[ticker] = row["source"]
            last_close[ticker] = bar.close
```

미결 평가 루프와 유니버스 이탈 청산에도 수량을 넘긴다.

```python
    for ticker in list(positions):
        decision = ts.universe_exit(
            positions[ticker], bars_by_ticker.get(ticker, {}),
            (universe_exits or {}).get(markets.get(ticker, "US")), params)
        if decision is not None:
            qty = qty_by_ticker.pop(ticker, None)
            trades.append(ts.make_trade(positions[ticker],
                                        markets.get(ticker, "US"),
                                        sources.get(ticker, ""), decision.price,
                                        decision.date, decision.reason, costs,
                                        qty))
            if cash is not None and qty:
                cash += qty * decision.price
            del positions[ticker]

    for ticker, pos in positions.items():
        close = last_close.get(ticker)
        if close is not None:
            trades.append(ts.make_trade(pos, markets.get(ticker, "US"),
                                        sources.get(ticker, ""), close,
                                        None, None, costs,
                                        qty_by_ticker.get(ticker)))
```

반환에 현금을 싣는다. 기존 반환 dict 에 두 줄을 더한다.

```python
        "rejected_pairs": rejected_pairs,
        # 리포트가 자본 사용률을 낸다. account 가 없으면 None 이다.
        "cash": cash,
        "capital": account.capital if account else None,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_portfolio.py -q`
Expected: PASS — 기존 테스트 전부 포함해 통과

- [ ] **Step 5: Commit**

```bash
git add portfolio.py tests/test_portfolio.py
git commit -m "Track cash in the portfolio simulation"
```

---

### Task 4: `backtest.run` — 계좌 연결

**Files:**
- Modify: `backtest.py` (`run` 시그니처, 경로 선택, 반환, CLI)
- Test: `tests/test_backtest.py`

- [ ] **Step 1: Write the failing test**

`tests/test_backtest.py` 끝에 추가한다.

```python
# ─── 계좌 ────────────────────────────────────────────────────
# 자본 제약이 있으면 종목을 독립적으로 볼 수 없다. "현금이 없어 못 샀다" 는
# 다른 종목이 이미 현금을 썼다는 뜻이라 포트폴리오 계층에서만 나온다.

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
        "2026-08-01": er.Bar("2026-08-01", 100.0, 101.0, 99.0, 100.0, 2.0)})
    monkeypatch.setattr(bt.pf, "simulate", spy)

    bt.run("x/*.csv", account=sizing.Account(capital=10_000))

    assert called.get("portfolio")


def test_the_result_carries_the_cash_position():
    import sizing
    # 실제 시뮬레이션 없이 반환 형태만 본다.
    result = bt.run("history_none/*.csv", account=sizing.Account(capital=500))

    assert result["cash"] == 500
    assert result["capital"] == 500
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_backtest.py -q -k account`
Expected: FAIL — `TypeError: run() got an unexpected keyword argument 'account'`

- [ ] **Step 3: Write minimal implementation**

`backtest.py` 상단 import 에 추가한다.

```python
import portfolio as pf
import sizing
import trade_sim as ts
```

`run` 시그니처에 `account` 를 더한다.

```python
def run(pattern: str = "history/*.csv", params: er.Params = None,
        costs: ts.Costs = None, us_only: bool = False,
        entry_total: int = None, limits: pf.Limits = None,
        start_date: str = None, account: sizing.Account = None) -> dict:
```

경로 선택 조건을 바꾼다. 기존 `if limits is None or (...)` 줄을 이렇게 만든다.

```python
    # 계좌가 있으면 반드시 포트폴리오 경로다. 종목을 따로 보면 "다른 종목이
    # 현금을 이미 썼다" 를 표현할 수 없다.
    unconstrained = (account is None and
                     (limits is None or (not limits.max_positions
                                         and limits.max_correlation >= 1.0)))
    rejected = {"capacity": 0, "correlation": 0, "cash": 0}
    rejected_pairs = []
    cash = capital = None
    if unconstrained:
        for ticker, prepared in prepared_by_ticker.items():
            trades.extend(ts.simulate_ticker(ticker, markets[ticker], prepared,
                                             bars_by_ticker[ticker], params, costs,
                                             exits.get(markets[ticker])))
    else:
        correlator = pf.build_correlator(
            {t: [b.close for b in sorted(bs.values(), key=lambda x: x.date)]
             for t, bs in bars_by_ticker.items()})
        out = pf.simulate(prepared_by_ticker, bars_by_ticker, markets,
                          params, costs, limits, correlator, exits, account)
        trades = out["trades"]
        rejected = out["rejected"]
        rejected_pairs = out["rejected_pairs"]
        cash = out["cash"]
        capital = out["capital"]
```

반환 dict 에 두 줄을 더한다.

```python
        "rejected_pairs": rejected_pairs,
        "cash": cash,
        "capital": capital,
```

CLI 에 옵션을 더한다. `--max-correlation` 다음에 넣는다.

```python
    p.add_argument("--capital", type=float, default=None,
                   help="초기 자본 USD. 주면 자본 제약이 켜진다 (예: 10000)")
    p.add_argument("--risk-pct", type=float, default=1.0,
                   help="거래당 리스크 (초기 자본 대비 %%, 기본 1.0)")
    p.add_argument("--max-weight-pct", type=float, default=20.0,
                   help="한 종목 투입 상한 (초기 자본 대비 %%, 기본 20.0)")
```

`main` 의 `run(...)` 호출 앞에 계좌를 만든다.

```python
    account = None
    if args.capital:
        account = sizing.Account(capital=args.capital,
                                 risk_pct=args.risk_pct,
                                 max_weight_pct=args.max_weight_pct)

    report(run(args.history, params, us_only=args.us_only,
               entry_total=args.entry_total, limits=limits,
               start_date=args.start_date, account=account))
```

`report` 함수의 거절 출력에 현금 항목을 더한다. `backtest.py:288` 근처의 블록을
이렇게 바꾼다 - 지금은 `capacity` 와 `correlation` 만 보고 있어 현금 부족이
조용히 묻힌다.

```python
    # 상한이 조용히 기회를 죽이면 결과만 보고는 알 수 없다
    rej = result.get("rejected") or {}
    if rej.get("capacity") or rej.get("correlation") or rej.get("cash"):
        print(f"  진입 거절: 자리부족 {rej.get('capacity', 0)}건 · "
              f"상관중복 {rej.get('correlation', 0)}건 · "
              f"현금부족 {rej.get('cash', 0)}건")
        for date, blocked, held, rho in (result.get("rejected_pairs") or [])[:8]:
            print(f"    {date} {blocked} 차단 (보유 {held} 와 rho={rho})")

    if result.get("capital"):
        used = result["capital"] - result["cash"]
        print(f"  자본: ${result['capital']:,.0f} 중 ${used:,.0f} 사용 "
              f"({used / result['capital'] * 100:.1f}%) · "
              f"잔여 ${result['cash']:,.0f}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_backtest.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backtest.py tests/test_backtest.py
git commit -m "Wire the account through the backtest"
```

---

### Task 5: `perf_report` — 시뮬레이터 수량 사용

**Files:**
- Modify: `perf_report.py` (`CAPITAL_USD` → `ACCOUNT`, `to_row`, `build_rows`, `main`)
- Test: `tests/test_perf_report.py`

- [ ] **Step 1: Write the failing test**

`tests/test_perf_report.py` 의 `_trade` 헬퍼에 `qty=10` 을 기본으로 넣는다. 기존 `base = dict(...)` 안에 한 줄 추가한다.

```python
        target_price=None, qty=10,
```

그리고 파일 끝에 추가한다.

```python
# ─── 수량은 시뮬레이터가 정한다 ───────────────────────────────
# 리포트가 사후에 지어내면 진입 여부에 영향을 주지 못하고, 같은 사실이 두 곳에서
# 따로 계산되어 어긋난다.

def test_the_row_uses_the_simulated_quantity():
    row = pr.to_row(_trade(qty=7))

    assert row["qty"] == 7
    # 원금도 그 수량으로 계산된다. 100 x 7 = 700, 회수 110 x 7 = 770.
    assert row["gross_usd"] == pytest.approx(70.0)


def test_a_trade_without_a_quantity_is_refused():
    # 자본 제약 없이 돌린 결과를 금액 리포트에 넣으면 수량을 지어내게 된다.
    with pytest.raises(ValueError, match="수량"):
        pr.to_row(_trade(qty=None))


def test_the_summary_carries_the_capital_position():
    built = pr.build_rows(_result([_trade()], cash=8_000, capital=10_000))

    assert built["summary"]["capital"] == 10_000
    assert built["summary"]["cash"] == 8_000
    assert built["summary"]["used_pct"] == pytest.approx(20.0)
```

`_result` 헬퍼에 두 키를 더한다.

```python
def _result(trades, **kw):
    base = dict(
        trades=trades, dates=["2026-08-03", "2026-08-05"],
        live_rows=10, backfill_rows=90, failed=[],
        newest_bar="2026-08-05", cash=None, capital=None,
    )
    base.update(kw)
    return base
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_perf_report.py -q -k "quantity or capital_position"`
Expected: FAIL — `to_row` 가 아직 `capital` 인자를 받고 `qty` 를 무시한다

- [ ] **Step 3: Write minimal implementation**

`perf_report.py` 상단 import 에 추가한다.

```python
import sizing
import stops
```

`CAPITAL_USD` 를 계좌로 바꾼다.

```python
# 초기 자본. 종목당 정액이 아니라 계좌 전체다 - 거래당 리스크와 투입 상한이
# 여기서 나온다. 자세한 근거는
# docs/superpowers/specs/2026-08-25-position-sizing-design.md
ACCOUNT = sizing.Account(capital=10_000)
```

`to_row` 에서 수량 계산을 걷어내고 `trade.qty` 를 쓴다.

```python
def to_row(trade, costs: ts.Costs = None) -> dict:
    """트레이드 1건을 달러 손익 행으로 환산한다.

    수량은 시뮬레이터가 정한 것을 그대로 쓴다. 여기서 다시 계산하면 진입
    여부를 좌우한 수량과 리포트의 수량이 어긋난다.
    """
    if trade.market != "US":
        raise ValueError(
            f"{trade.ticker}: 리포트는 USD 전용인데 market={trade.market!r} "
            "이다. 백테스트를 us_only 로 돌려야 한다.")
    if trade.qty is None:
        raise ValueError(
            f"{trade.ticker}: 수량이 없다. 금액 리포트를 내려면 백테스트를 "
            "account 와 함께 돌려야 한다.")

    costs = costs or ts.Costs()
    qty = trade.qty
    principal = trade.entry_price * qty
    ...
```

그 아래에서 다음 두 줄을 **지운다**.

```python
    # 0주면 손익이 0이라 트레이드가 조용히 사라진다. 1주로 올린다.
    qty = max(1, int(capital // trade.entry_price))
```

`principal = trade.entry_price * qty` 이하는 그대로 둔다 - 위에서 `qty` 를
`trade.qty` 로 이미 묶어 두었다.

`build_rows` 시그니처에서 `capital` 을 뺀다.

```python
def build_rows(result: dict, costs: ts.Costs = None,
               params: er.Params = None,
               start_date: str = REPORT_START) -> dict:
```

`to_row(t, capital, costs)` 호출 두 곳을 `to_row(t, costs)` 로 바꾼다.

summary dict 에서 `"capital": capital,` 을 지우고 세 줄을 넣는다.

```python
            "capital": result.get("capital"),
            "cash": result.get("cash"),
            "used_pct": (
                (result["capital"] - result["cash"]) / result["capital"] * 100.0
                if result.get("capital") else None),
```

`main` 에서 계좌를 만들어 넘긴다. `--capital` 의 뜻이 바뀐다.

```python
    p.add_argument("--capital", type=float, default=ACCOUNT.capital,
                   help=f"초기 자본 USD (기본: {ACCOUNT.capital:,.0f})")
```

```python
    account = sizing.Account(capital=args.capital)
    ...
        result = backtest.run(pattern, params, us_only=True,
                              start_date=args.start_date, account=account)
        ...
        by_track[key] = build_rows(result, params=params,
                                   start_date=args.start_date)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_perf_report.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add perf_report.py tests/test_perf_report.py
git commit -m "Report the quantity the simulation chose"
```

---

### Task 6: 요약 시트의 `[가정]` 블록 갱신

**Files:**
- Modify: `perf_report.py` (`_write_summary`)
- Test: `tests/test_perf_report.py`

- [ ] **Step 1: Write the failing test**

`tests/test_perf_report.py` 끝에 추가한다.

```python
def test_the_summary_states_the_sizing_rule(tmp_path):
    path = tmp_path / "r.xlsx"
    _write(path, pr.build_rows(_result([_trade()], cash=8_000, capital=10_000)))

    labels = {r[0].value: r[1].value
              for r in load_workbook(path)["요약"].iter_rows()}

    assert labels["초기 자본($)"] == 10_000
    assert labels["잔여 현금($)"] == 8_000
    assert labels["자본 사용률(%)"] == pytest.approx(20.0)
    assert "1%" in labels["거래당 리스크"]
    assert "20%" in labels["투입 상한"]
    assert "종목당 최대 진입금액($)" not in labels
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_perf_report.py -q -k sizing_rule`
Expected: FAIL — `KeyError: '초기 자본($)'`

- [ ] **Step 3: Write minimal implementation**

`_write_summary` 의 트랙 블록에 세 줄을 더한다. `("평가 순손익($)", t["open_net_usd"]),` 다음이다.

```python
            ("평가 순손익($)", t["open_net_usd"]),
            ("초기 자본($)", t["capital"]),
            ("잔여 현금($)", t["cash"]),
            ("자본 사용률(%)", t["used_pct"]),
```

`[가정]` 블록에서 종목당 정액 두 줄을 빼고 사이징 규칙을 넣는다.

```python
        ("[가정]", ""),
        ("거래당 리스크", "초기 자본의 1%. 손절폭으로 수량을 역산한다"),
        ("투입 상한", "한 종목에 초기 자본의 20% 까지"),
        ("기준 자본", "초기값 고정. 평가자산이 늘어도 리스크 금액은 그대로다"),
        ("못 살 때", "현금 부족이나 0주면 건너뛴다. 목표 리스크를 못 맞추는 "
                     "포지션은 열지 않는다"),
        ("비용", "미국 편도 0.10% · 슬리피지 편도 0.05%"),
        ("통화", "전부 USD. 미국 종목만 보므로 원화 환산을 하지 않는다"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q`
Expected: PASS — 전체 통과

- [ ] **Step 5: Commit**

```bash
git add perf_report.py tests/test_perf_report.py
git commit -m "State the sizing rule in the report summary"
```

---

### Task 7: 실데이터 검증

**Files:**
- 없음 (확인만)

- [ ] **Step 1: 백테스트를 자본과 함께 돌린다**

Run:
```bash
python backtest.py --history "history/*.csv" --start-date 2026-08-25 \
  --us-only --capital 10000
```

Expected: 진입 종목이 10개 안팎, 거절 사유에 `cash` 가 잡힌다.

- [ ] **Step 2: 설계 문서의 예상치와 대조한다**

설계는 리스크 1% 에서 **10종목·자본 사용률 96.6%** 를 예상했다. 실제 결과가
크게 다르면(±2종목을 넘거나 사용률이 90% 미만이면) 멈추고 원인을 찾는다.
2026-08-25 은 봉이 없어 진입이 0건일 수 있다 - 그때는 `--start-date 2026-08-24`
로 한 번 더 돌려 사이징이 실제로 작동하는지 본다.

- [ ] **Step 3: 리포트를 만들어 본다**

Run:
```bash
python perf_report.py --out-dir /tmp/sizing_check
```

Expected: 요약 시트에 초기 자본 $10,000 · 잔여 현금 · 자본 사용률이 찍힌다.
미결포지션 시트의 수량이 종목마다 다르고, `수량 x 진입가` 가 $2,000 을 넘지
않는다.

- [ ] **Step 4: 커밋할 것이 있으면 커밋한다**

검증에서 고친 것이 있으면 커밋한다. 없으면 넘어간다.

---

## 완료 조건

- [ ] `python -m pytest -q` 전부 통과
- [ ] `backtest.py --capital 10000` 이 현금 부족으로 거절한 건수를 출력한다
- [ ] 리포트 요약에 초기 자본·잔여 현금·자본 사용률이 있다
- [ ] 미결포지션의 `수량 x 진입가` 가 어느 종목도 $2,000 을 넘지 않는다
- [ ] `perf_report` 에 `CAPITAL_USD` 와 수량 계산이 남아 있지 않다
