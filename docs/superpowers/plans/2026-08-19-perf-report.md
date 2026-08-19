# 가상매매 성과 리포트 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 매영업일 KST 10:00에 가상매매 성과를 원화 XLSX로 만들어 리포에 누적한다.

**Architecture:** `backtest.run()` 이 낸 `Trade[]` 를 종목당 정액 1,000만원 투자로 환산해 3시트 XLSX로 쓴다. 계산은 `perf_report.py` 의 순수 함수에 모으고, 네트워크는 환율 조회 한 곳뿐이다. 실행은 `report.yml` 워크플로가 맡는다.

**Tech Stack:** Python 3.11 · openpyxl · yfinance · pytest · GitHub Actions

**설계서:** `docs/superpowers/specs/2026-08-19-perf-report-design.md`

---

## 파일 구조

| 파일 | 책임 | 상태 |
|---|---|---|
| `trade_sim.py` | 요율 분기를 `cost_amount()` 로 분리, `Trade.mark_price` 추가 | 수정 |
| `tests/test_trade_sim.py` | `_trade` 헬퍼에 `mark_price` 추가 | 수정 |
| `perf_report.py` | 원화 환산·행 조립·XLSX 기록·CLI | 신규 |
| `tests/test_perf_report.py` | 위 전부의 테스트 | 신규 |
| `requirements.txt` | `openpyxl>=3.1` | 수정 |
| `.github/workflows/report.yml` | 매영업일 KST 10:00 실행 | 신규 |

`trade_sim.py` 는 파일·네트워크를 건드리지 않는 계약이 있다(`test_module_has_no_io_dependencies`). 환율·엑셀은 전부 `perf_report.py` 쪽이다.

---

### Task 1: 요율 분기를 cost_amount 로 분리

리포트가 미국/한국/세금 요율 분기를 두 번째로 구현하면, 요율을 고칠 때 한쪽만 고치는 사고가 난다.

**Files:**
- Modify: `trade_sim.py:34-59`
- Test: `tests/test_trade_sim.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trade_sim.py` 끝에 추가:

```python
def test_cost_amount_returns_each_side_at_its_own_price():
    # 진입 100, 청산 130. 매수측 (0.10+0.05)% x 100, 매도측 (0.10+0.05)% x 130
    buy, sell = ts.cost_amount(entry_price=100.0, exit_price=130.0,
                               market="US", costs=C)

    assert buy == pytest.approx(0.15)
    assert sell == pytest.approx(0.195)


def test_cost_amount_charges_kr_transfer_tax_on_the_sell_side_only():
    buy, sell = ts.cost_amount(entry_price=100.0, exit_price=130.0,
                               market="KR", costs=C)

    assert buy == pytest.approx((0.02 + 0.05) / 100 * 100.0)
    assert sell == pytest.approx((0.02 + 0.15 + 0.05) / 100 * 130.0)


def test_cost_amount_rejects_unknown_market():
    with pytest.raises(ValueError):
        ts.cost_amount(100.0, 130.0, "JP", C)


def test_cost_r_is_cost_amount_divided_by_r_unit():
    # 요율 분기가 복제되면 이 등식이 깨진다.
    buy, sell = ts.cost_amount(100.0, 130.0, "US", C)

    assert ts.cost_r(100.0, 130.0, 6.0, "US", C) == pytest.approx((buy + sell) / 6.0)
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_trade_sim.py -k cost_amount -v`

Expected: FAIL — `AttributeError: module 'trade_sim' has no attribute 'cost_amount'`

- [ ] **Step 3: 구현**

`trade_sim.py` 의 `cost_r` 전체(34~59행)를 아래로 교체:

```python
def cost_amount(entry_price: float, exit_price: float,
                market: str, costs: Costs) -> tuple:
    """왕복 거래비용을 (매수측, 매도측) 가격 단위로 돌려준다.

    매수측 비용은 진입가에, 매도측 비용은 청산가에 각각 매긴다 - 실제로
    수수료가 부과되는 가격이 그것이기 때문이다.

    시장별 요율 분기는 여기 한 곳에만 둔다. R 배수가 필요하면 cost_r 을
    쓰고, 원화 금액이 필요하면 이 값에 수량과 환율을 곱한다.
    """
    if market == "US":
        buy_pct = costs.us_buy_pct
        sell_pct = costs.us_sell_pct
        tax_pct = 0.0
    elif market == "KR":
        buy_pct = costs.kr_buy_pct
        sell_pct = costs.kr_sell_pct
        tax_pct = costs.kr_tax_pct
    else:
        raise ValueError(f"알 수 없는 시장: {market!r} (US 또는 KR 이어야 함)")

    buy_side = (buy_pct + costs.slippage_pct) / 100.0 * entry_price
    sell_side = (sell_pct + tax_pct + costs.slippage_pct) / 100.0 * exit_price
    return buy_side, sell_side


def cost_r(entry_price: float, exit_price: float, r_unit: float,
           market: str, costs: Costs) -> float:
    """왕복 거래비용을 R 배수로 환산한다.

    r_unit 이 클수록(변동성이 큰 종목일수록) 비용 부담이 자동으로 작아진다.
    미결 포지션은 마지막 종가를 청산가로 대신 넣어 부른다.
    """
    buy_side, sell_side = cost_amount(entry_price, exit_price, market, costs)
    return (buy_side + sell_side) / r_unit
```

- [ ] **Step 4: 전체 테스트 통과 확인**

Run: `python -m pytest -q`

Expected: PASS — 106 passed (기존 102 + 신규 4). `cost_r` 동작이 바뀌지 않았으므로 기존 비용 테스트도 그대로 통과해야 한다.

- [ ] **Step 5: 커밋**

```bash
git add trade_sim.py tests/test_trade_sim.py
git commit -m "Extract the cost rate branch so callers can charge in any unit"
```

---

### Task 2: Trade 에 mark_price 추가

미결 포지션은 `exit_price=None` 이라 평가 가격이 밖으로 안 나온다. 리포트 시트2 전체가 `entry_price + gross_r * r_unit` 역산에 의존하는 건 취약하다.

**Files:**
- Modify: `trade_sim.py:63-79` (`Trade`), `trade_sim.py:119-140` (`_make_trade`)
- Test: `tests/test_trade_sim.py:197-206` (`_trade` 헬퍼), 신규 테스트

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trade_sim.py` 끝에 추가. 헬퍼는 파일에 이미 있는 것을 쓴다 —
`_bar(date, o, h, l, c, atr=2.0)` · `_row(date, signal, source="live")` ·
`P = er.Params()` · `C = ts.Costs()`. 봉 값은 기존
`test_one_trade_opens_and_stays_open` 과 동일하게 맞춰, 미결 판정은 그
테스트가 이미 보장하고 여기서는 `mark_price` 만 본다:

```python
def test_open_trade_exposes_its_mark_price():
    # 미결이어도 평가 가격이 밖으로 나와야 원화 평가손익을 낼 수 있다.
    rows = [_row("d1", "BUY"), _row("d2", "BUY")]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 100.5, 102.0, 100.0, 101.5)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert trades[0].is_open
    assert trades[0].exit_price is None
    assert trades[0].mark_price == 101.5    # 마지막 종가로 평가한다


def test_closed_trade_mark_price_equals_its_exit_price():
    t = _trade(1.0)
    assert t.mark_price == t.exit_price
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_trade_sim.py -k mark_price -v`

Expected: FAIL — `TypeError: Trade.__init__() got an unexpected keyword argument 'mark_price'` 또는 `AttributeError: 'Trade' object has no attribute 'mark_price'`

- [ ] **Step 3: 구현**

`trade_sim.py` 의 `Trade` 마지막 필드 `net_r: float` 아래에 추가:

```python
    net_r: float
    # 미결 포지션의 평가 가격. 청산된 트레이드에서는 exit_price 와 같다.
    # 기본값을 두지 않는다 - 값을 빠뜨린 생성이 조용히 통과하면 안 된다.
    mark_price: float
```

`_make_trade` 의 `net_r=gross - cost,` 아래에 추가:

```python
        net_r=gross - cost,
        mark_price=exit_price,
    )
```

`tests/test_trade_sim.py` 의 `_trade` 헬퍼(197행)에 한 줄 추가:

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
        mark_price=106.0,
    )
```

- [ ] **Step 4: 전체 테스트 통과 확인**

Run: `python -m pytest -q`

Expected: PASS — 108 passed

- [ ] **Step 5: 실제 데이터로 회귀 확인**

Run: `python backtest.py`

Expected: 이전과 동일한 출력 — 닫힌 트레이드 1건(YPF, -0.73R), 미결 6건, 평가 -0.18R. 숫자가 하나라도 달라지면 `mark_price` 추가가 계산에 새어든 것이다.

- [ ] **Step 6: 커밋**

```bash
git add trade_sim.py tests/test_trade_sim.py
git commit -m "Expose the mark price so open positions can be valued"
```

---

### Task 3: openpyxl 의존성과 환율 조회 함수

**Files:**
- Modify: `requirements.txt`
- Create: `perf_report.py`
- Create: `tests/test_perf_report.py`

- [ ] **Step 1: 의존성 추가 및 설치**

`requirements.txt` 마지막에 한 줄 추가:

```
openpyxl>=3.1
```

Run: `python -m pip install "openpyxl>=3.1"`

Expected: 설치 성공 (또는 이미 최신)

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_perf_report.py` 신규 생성:

```python
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
```

- [ ] **Step 3: 실패 확인**

Run: `python -m pytest tests/test_perf_report.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'perf_report'`

- [ ] **Step 4: 구현**

`perf_report.py` 신규 생성:

```python
"""가상매매 성과를 원화 XLSX 리포트로 낸다.

backtest.run() 이 낸 트레이드를 종목당 정액 1,000만원 투자로 환산한다.
R 배수는 리스크 정규화 단위여서 "얼마 벌었나" 에 답하지 못한다.

설계: docs/superpowers/specs/2026-08-19-perf-report-design.md
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

import backtest
import console
import history
import trade_sim as ts

CAPITAL_KRW = 10_000_000

# 2구획 커스텀 서식. 음수 구획에 - 를 명시하므로 회계 서식의 괄호 표기
# (636,000) 는 나오지 않는다.
MONEY_FMT = '#,##0;-#,##0'
PRICE_FMT = '#,##0.00;-#,##0.00'
QTY_FMT = '#,##0'
RATE_FMT = '#,##0.00'
# 값은 12.34 로 저장하고 서식으로 % 를 붙인다. Excel 기본 0.00% 서식은
# 값이 0.1234 여야 해서, 셀을 직접 읽는 쪽이 100배 틀린다.
PCT_FMT = '0.00"%";-0.00"%"'


def fx_on(fx: dict, date: str, market: str) -> float:
    """해당 날짜의 원/달러 환율. 휴일이면 직전 영업일로 소급한다.

    한국 종목은 이미 원화라 1.0 이다. 호출부마다 분기를 두지 않으려고
    여기서 흡수한다.
    """
    if market == "KR":
        return 1.0
    if date in fx:
        return fx[date]
    earlier = [d for d in fx if d < date]
    if not earlier:
        raise ValueError(f"{date} 이전의 환율이 없다 (조회 범위를 늘려야 한다)")
    return fx[max(earlier)]
```

- [ ] **Step 5: 통과 확인**

Run: `python -m pytest tests/test_perf_report.py -v`

Expected: PASS — 4 passed

- [ ] **Step 6: 커밋**

```bash
git add requirements.txt perf_report.py tests/test_perf_report.py
git commit -m "Look up the exchange rate for a date, falling back to the prior session"
```

---

### Task 4: 트레이드 1건을 원화 행으로 환산

**Files:**
- Modify: `perf_report.py`
- Test: `tests/test_perf_report.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_perf_report.py` 끝에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_perf_report.py -v`

Expected: FAIL — 새 7건이 `AttributeError: module 'perf_report' has no attribute 'to_row'` 로 실패. 기존 4건은 통과.

- [ ] **Step 3: 구현**

`perf_report.py` 의 `fx_on` 아래에 추가:

```python
def to_row(trade, fx_entry: float, fx_exit: float,
           capital: int = CAPITAL_KRW, costs: ts.Costs = None) -> dict:
    """트레이드 1건을 원화 손익 행으로 환산한다.

    미결 포지션은 청산가 자리에 평가가격(mark_price)이 들어오고 매도비용도
    똑같이 뺀다 - 지금 팔면 손에 남는 돈이 평가액이다.

    두 퍼센트의 분모는 모두 투자원금이다. 순수익%의 분모에 매수비용을
    더하면 두 컬럼을 나란히 비교할 수 없다.
    """
    costs = costs or ts.Costs()

    entry_krw = trade.entry_price * fx_entry
    # 0주면 손익이 0이라 트레이드가 조용히 사라진다. 1주로 올린다.
    qty = max(1, int(capital // entry_krw))
    principal = entry_krw * qty

    exit_price = (trade.exit_price if trade.exit_price is not None
                  else trade.mark_price)
    gross = exit_price * fx_exit * qty - principal

    buy_side, sell_side = ts.cost_amount(trade.entry_price, exit_price,
                                         trade.market, costs)
    cost = buy_side * fx_entry * qty + sell_side * fx_exit * qty
    net = gross - cost

    return {
        "ticker": trade.ticker,
        "entry_date": trade.entry_date,
        "entry_price": trade.entry_price,
        "exit_date": trade.exit_date,
        "exit_price": exit_price,
        "gross_krw": gross,
        "gross_pct": gross / principal * 100.0,
        "net_krw": net,
        "net_pct": net / principal * 100.0,
        "qty": qty,
        "fx_entry": fx_entry,
        "fx_exit": fx_exit,
        "reason": trade.exit_reason,
        "bars_held": trade.bars_held,
    }
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_perf_report.py -v`

Expected: PASS — 11 passed

- [ ] **Step 5: 커밋**

```bash
git add perf_report.py tests/test_perf_report.py
git commit -m "Convert one trade into a won-denominated row"
```

---

### Task 5: 청산완료·미결·요약으로 나누기

**Files:**
- Modify: `perf_report.py`
- Test: `tests/test_perf_report.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_perf_report.py` 끝에 추가:

```python
def _result(trades, **kw):
    base = dict(
        trades=trades, dates=["2026-08-03", "2026-08-05"],
        live_rows=10, backfill_rows=90, failed=[],
        newest_bar="2026-08-05",
    )
    base.update(kw)
    return base


def test_open_positions_never_land_in_the_closed_sheet():
    built = pr.build_rows(_result([
        _trade(),
        _trade(ticker="BBB", is_open=True, exit_date=None,
               exit_price=None, mark_price=105.0),
    ]), FX)

    assert [r["ticker"] for r in built["closed"]] == ["AAA"]
    assert [r["ticker"] for r in built["open"]] == ["BBB"]


def test_open_position_is_marked_to_the_newest_bar_date():
    built = pr.build_rows(_result([
        _trade(is_open=True, exit_date=None, exit_price=None, mark_price=105.0),
    ]), FX)

    assert built["open"][0]["exit_date"] == "2026-08-05"


def test_win_rate_ignores_open_positions():
    # 닫힌 2건 중 1승. 미결은 큰 이익이지만 승률에 들어가면 안 된다 -
    # "아직 손절되지 않았을 뿐" 인 포지션이다.
    built = pr.build_rows(_result([
        _trade(ticker="WIN"),
        _trade(ticker="LOSS", exit_price=90.0, mark_price=90.0),
        _trade(ticker="OPEN", is_open=True, exit_date=None,
               exit_price=None, mark_price=200.0),
    ]), FX)
    s = built["summary"]

    assert s["closed_n"] == 2
    assert s["win_rate"] == pytest.approx(50.0)
    assert s["open_n"] == 1


def test_closed_rows_sort_by_exit_date_then_ticker():
    built = pr.build_rows(_result([
        _trade(ticker="ZZZ", exit_date="2026-08-05"),
        _trade(ticker="AAA", exit_date="2026-08-05"),
        _trade(ticker="MMM", exit_date="2026-08-03"),
    ]), FX)

    assert [r["ticker"] for r in built["closed"]] == ["MMM", "AAA", "ZZZ"]


def test_summary_survives_zero_closed_trades():
    s = pr.build_rows(_result([]), FX)["summary"]

    assert s["closed_n"] == 0
    assert s["win_rate"] is None
    assert s["avg_net_pct"] is None
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_perf_report.py -v`

Expected: FAIL — `AttributeError: module 'perf_report' has no attribute 'build_rows'`

- [ ] **Step 3: 구현**

`perf_report.py` 의 `to_row` 아래에 추가:

```python
def build_rows(result: dict, fx: dict, capital: int = CAPITAL_KRW,
               costs: ts.Costs = None) -> dict:
    """청산완료·미결·요약 세 덩어리로 나눈다.

    미결을 승률에 섞지 않는다. trade_sim.summarize 와 같은 원칙이다.
    """
    costs = costs or ts.Costs()
    mark_date = result["newest_bar"]

    closed, opened = [], []
    for t in result["trades"]:
        fx_entry = fx_on(fx, t.entry_date, t.market)
        if t.is_open:
            row = to_row(t, fx_entry, fx_on(fx, mark_date, t.market),
                         capital, costs)
            # 미결은 청산일이 없다. 평가 시점을 대신 넣는다.
            row["exit_date"] = mark_date
            opened.append(row)
        else:
            fx_exit = fx_on(fx, t.exit_date, t.market)
            closed.append(to_row(t, fx_entry, fx_exit, capital, costs))

    closed.sort(key=lambda r: (r["exit_date"], r["ticker"]))
    opened.sort(key=lambda r: (r["entry_date"], r["ticker"]))

    wins = sum(1 for r in closed if r["net_krw"] > 0)
    total_rows = result["live_rows"] + result["backfill_rows"]
    return {
        "closed": closed,
        "open": opened,
        "summary": {
            "generated": history.kst_now().strftime("%Y-%m-%d %H:%M KST"),
            "archive_from": result["dates"][0],
            "archive_to": result["dates"][-1],
            "live_rows": result["live_rows"],
            "backfill_rows": result["backfill_rows"],
            "backfill_pct": (result["backfill_rows"] / total_rows * 100.0)
                            if total_rows else 0.0,
            "mark_date": mark_date,
            "failed": result["failed"],
            "closed_n": len(closed),
            "win_rate": (wins / len(closed) * 100.0) if closed else None,
            "gross_krw": sum(r["gross_krw"] for r in closed),
            "net_krw": sum(r["net_krw"] for r in closed),
            # 트레이드별 순수익률의 단순평균이다. 금액 가중이 아니다.
            "avg_net_pct": (sum(r["net_pct"] for r in closed) / len(closed))
                           if closed else None,
            "open_n": len(opened),
            "open_net_krw": sum(r["net_krw"] for r in opened),
            "capital": capital,
        },
    }
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_perf_report.py -v`

Expected: PASS — 16 passed

- [ ] **Step 5: 커밋**

```bash
git add perf_report.py tests/test_perf_report.py
git commit -m "Split rows into closed, open, and summary"
```

---

### Task 6: XLSX 3시트 기록

**Files:**
- Modify: `perf_report.py`
- Test: `tests/test_perf_report.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_perf_report.py` 끝에 추가:

```python
def test_xlsx_has_three_sheets_with_the_requested_columns_first(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([_trade()]), FX))

    wb = load_workbook(path)
    assert wb.sheetnames == ["청산완료", "미결포지션", "요약"]

    header = [c.value for c in wb["청산완료"][1]]
    assert header[:9] == ["상품티커", "진입일자", "진입가격",
                          "청산일자", "청산가격", "총수익(원)",
                          "총수익(%)", "순수익(원)", "순수익(%)"]
    assert header[9:] == ["수량", "진입환율", "청산환율", "청산사유"]


def test_negative_money_renders_with_a_minus_sign(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(
        _result([_trade(exit_price=90.0, mark_price=90.0)]), FX))

    cell = load_workbook(path)["청산완료"]["F2"]

    assert cell.value < 0
    assert cell.number_format == "#,##0;-#,##0"
    # 회계 서식의 괄호 표기여서는 안 된다
    assert "(" not in cell.number_format


def test_percent_cells_store_the_readable_number_not_a_fraction(tmp_path):
    # 값이 0.1423 이면 셀을 직접 읽는 쪽이 100배 틀린다.
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([_trade()]), FX))

    cell = load_workbook(path)["청산완료"]["G2"]

    assert cell.value > 1.0
    assert cell.number_format == '0.00"%";-0.00"%"'


def test_closed_sheet_keeps_its_header_when_there_are_no_trades(tmp_path):
    # 시트가 없으면 파일이 깨진 것인지 트레이드가 없는 것인지 구분되지 않는다.
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([]), FX))

    ws = load_workbook(path)["청산완료"]

    assert ws.max_row == 1
    assert ws["A1"].value == "상품티커"


def test_open_sheet_labels_the_valuation_columns(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([
        _trade(is_open=True, exit_date=None, exit_price=None, mark_price=105.0),
    ]), FX))

    header = [c.value for c in load_workbook(path)["미결포지션"][1]]

    assert header[3] == "평가기준일"
    assert header[4] == "현재가"
    assert header[12] == "보유봉수"


def test_summary_leads_with_the_contamination_warning(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([_trade()]), FX))

    ws = load_workbook(path)["요약"]

    assert ws["A1"].value == "!! 경고"
    assert "파이프라인 검증용" in ws["B1"].value
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_perf_report.py -v`

Expected: FAIL — `AttributeError: module 'perf_report' has no attribute 'write_xlsx'`

- [ ] **Step 3: 구현**

`perf_report.py` 의 `build_rows` 아래에 추가:

```python
# (헤더, 행 키, 숫자서식). 앞 9개가 요청받은 컬럼이고, 뒤 4개는 검증용이다 -
# 원화 손익은 가격 x 수량 x 환율의 곱이라 셋이 다 보여야 검산이 된다.
CLOSED_COLS = [
    ("상품티커", "ticker", None),
    ("진입일자", "entry_date", None),
    ("진입가격", "entry_price", PRICE_FMT),
    ("청산일자", "exit_date", None),
    ("청산가격", "exit_price", PRICE_FMT),
    ("총수익(원)", "gross_krw", MONEY_FMT),
    ("총수익(%)", "gross_pct", PCT_FMT),
    ("순수익(원)", "net_krw", MONEY_FMT),
    ("순수익(%)", "net_pct", PCT_FMT),
    ("수량", "qty", QTY_FMT),
    ("진입환율", "fx_entry", RATE_FMT),
    ("청산환율", "fx_exit", RATE_FMT),
    ("청산사유", "reason", None),
]

OPEN_COLS = [
    ("상품티커", "ticker", None),
    ("진입일자", "entry_date", None),
    ("진입가격", "entry_price", PRICE_FMT),
    ("평가기준일", "exit_date", None),
    ("현재가", "exit_price", PRICE_FMT),
    ("평가 총수익(원)", "gross_krw", MONEY_FMT),
    ("평가 총수익(%)", "gross_pct", PCT_FMT),
    ("평가 순수익(원)", "net_krw", MONEY_FMT),
    ("평가 순수익(%)", "net_pct", PCT_FMT),
    ("수량", "qty", QTY_FMT),
    ("진입환율", "fx_entry", RATE_FMT),
    ("평가환율", "fx_exit", RATE_FMT),
    ("보유봉수", "bars_held", QTY_FMT),
]


def _write_sheet(ws, cols, rows) -> None:
    """헤더 + 데이터 행 + 열별 숫자서식. 행이 없어도 헤더는 쓴다."""
    ws.append([title for title, _key, _fmt in cols])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"

    for row in rows:
        ws.append([row[key] for _title, key, _fmt in cols])

    for i, (title, _key, fmt) in enumerate(cols, start=1):
        letter = get_column_letter(i)
        if fmt:
            for cell in ws[letter][1:]:
                cell.number_format = fmt
        ws.column_dimensions[letter].width = max(len(title) + 4, 12)


def _write_summary(ws, s: dict) -> None:
    """라벨-값 2열. 경고를 맨 위에 고정한다."""
    lines = [
        ("!! 경고", "이 리포트는 파이프라인 검증용이다. 시그널 성능의 근거가 아니다."),
        ("", f"아카이브의 {s['backfill_pct']:.0f}% 가 backfill 이라 "
             "스코어가 미확정 봉 결함에 오염돼 있다."),
        ("", "보유 상한 60거래일을 채운 표본이 나오기 전까지 승률·평균은 무의미하다."),
        ("", ""),
        ("리포트 생성", s["generated"]),
        ("아카이브 기간", f"{s['archive_from']} ~ {s['archive_to']}"),
        ("live 행수", s["live_rows"]),
        ("backfill 행수", s["backfill_rows"]),
        ("평가기준일", s["mark_date"]),
        ("시세 조회 실패", ", ".join(s["failed"]) if s["failed"] else "없음"),
        ("", ""),
        ("[청산완료]", ""),
        ("건수", s["closed_n"]),
        ("승률(%)", s["win_rate"]),
        ("누적 총수익(원)", s["gross_krw"]),
        ("누적 순수익(원)", s["net_krw"]),
        ("평균 순수익률(%)", s["avg_net_pct"]),
        ("", ""),
        ("[미결포지션]", ""),
        ("보유 건수", s["open_n"]),
        ("평가 순손익(원)", s["open_net_krw"]),
        ("", ""),
        ("[가정]", ""),
        ("종목당 투자금(원)", s["capital"]),
        ("매수 수량", "정액 ÷ 원화진입가, 소수점 내림 (최소 1주)"),
        ("비용", "미국 편도 0.10% · 한국 편도 0.02% + 거래세 0.15% · "
                 "슬리피지 편도 0.05%"),
        ("환율", "yfinance USDKRW=X 일봉 종가. 휴일은 직전 영업일로 소급"),
        ("승률·평균", "청산완료만으로 계산한다. 미결은 제외"),
    ]

    for label, value in lines:
        ws.append([label, value])

    for row in ws.iter_rows(min_col=1, max_col=2):
        label = row[0].value or ""
        if label.endswith("(원)"):
            row[1].number_format = MONEY_FMT
        elif label.endswith("(%)"):
            row[1].number_format = PCT_FMT

    ws["A1"].font = Font(bold=True)
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 80


def write_xlsx(path, built: dict) -> None:
    """청산완료 / 미결포지션 / 요약 3시트를 쓴다."""
    wb = Workbook()
    closed_ws = wb.active
    closed_ws.title = "청산완료"
    _write_sheet(closed_ws, CLOSED_COLS, built["closed"])
    _write_sheet(wb.create_sheet("미결포지션"), OPEN_COLS, built["open"])
    _write_summary(wb.create_sheet("요약"), built["summary"])

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_perf_report.py -v`

Expected: PASS — 22 passed

- [ ] **Step 5: 전체 테스트 확인**

Run: `python -m pytest -q`

Expected: PASS — 130 passed

- [ ] **Step 6: 커밋**

```bash
git add perf_report.py tests/test_perf_report.py
git commit -m "Write the three report sheets with minus-signed number formats"
```

---

### Task 7: 환율 조회와 CLI

**Files:**
- Modify: `perf_report.py`
- Test: 수동 실행 (`fetch_fx` 는 네트워크라 단위테스트하지 않는다)

- [ ] **Step 1: 구현**

`perf_report.py` 의 `write_xlsx` 아래에 추가:

```python
def fetch_fx(start: str, end: str) -> dict:
    """USDKRW=X 일봉 종가를 {YYYY-MM-DD: 환율} 로 받는다.

    실패하면 예외를 올린다. 고정환율로 대체하면 조용히 틀린 금액이
    리포에 커밋된다 - 리포트가 없는 편이 낫다.
    """
    df = yf.Ticker("USDKRW=X").history(start=start, end=end, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError(f"USDKRW=X 환율 조회 실패 ({start} ~ {end})")

    # NaN 종가는 버린다. fx_on 이 직전 영업일로 소급한다.
    return {d.strftime("%Y-%m-%d"): float(c)
            for d, c in zip(df.index, df["Close"]) if c == c}


def main():
    console.force_utf8()
    p = argparse.ArgumentParser(description="가상매매 성과 누적 리포트")
    p.add_argument("--history", default="history/*.csv")
    p.add_argument("--out-dir", default="reports")
    p.add_argument("--capital", type=int, default=CAPITAL_KRW)
    args = p.parse_args()

    result = backtest.run(args.history)
    if not result["dates"]:
        raise SystemExit("아카이브가 비어 있다")

    # 첫 진입일이 환율 휴일이어도 소급할 값이 있도록 10일 앞에서 시작한다.
    fx_start = (datetime.strptime(result["dates"][0], "%Y-%m-%d")
                - timedelta(days=10)).strftime("%Y-%m-%d")
    fx_end = (history.kst_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    fx = fetch_fx(fx_start, fx_end)

    built = build_rows(result, fx, args.capital)
    stamp = history.kst_now().strftime("%Y-%m-%d")
    path = Path(args.out_dir) / f"perf_{stamp}.xlsx"
    write_xlsx(path, built)

    s = built["summary"]
    print(f"{path} 작성 완료")
    print(f"  청산완료 {s['closed_n']}건 · 누적 순수익 {s['net_krw']:+,.0f}원")
    print(f"  미결 {s['open_n']}건 · 평가 순손익 {s['open_net_krw']:+,.0f}원")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실제 데이터로 실행**

Run: `python perf_report.py`

Expected: `reports\perf_2026-08-19.xlsx 작성 완료` 와 요약 2줄. 한글이 깨지지 않아야 한다(`console.force_utf8()`).

- [ ] **Step 3: 산출물 육안 확인**

`reports/perf_2026-08-19.xlsx` 를 열어 확인:

- 시트 3개가 있는지
- `청산완료` 에 YPF 1행, 총수익·순수익이 **음수이고 `-` 부호**인지
- `미결포지션` 에 6행(BWA·C·CNC·DVN·EXE·FISV)이 있는지
- `요약` 최상단에 경고 3줄이 있는지
- 진입환율·청산환율이 1,300~1,500 범위의 그럴듯한 값인지 — 환율 조인이 틀리면 여기서 드러난다

- [ ] **Step 4: 커밋**

```bash
git add perf_report.py
git commit -m "Fetch the exchange rate and write the dated report file"
```

`reports/` 는 이 커밋에 넣지 않는다. 첫 산출물은 워크플로가 만든다.

---

### Task 8: 매영업일 KST 10:00 워크플로

**Files:**
- Create: `.github/workflows/report.yml`

- [ ] **Step 1: 워크플로 작성**

`.github/workflows/report.yml` 신규 생성:

```yaml
name: Daily Performance Report

on:
  schedule:
    # UTC 01:00 = KST 10:00. 스캔(22:00 UTC)이 끝난 3시간 뒤라
    # 그날 KST 07:00 스캔 결과가 이미 아카이브에 있다.
    # 영업일은 단순 월~금이다. 한국·미국 공휴일은 구분하지 않는다 -
    # 휴일에는 전일과 같은 내용의 리포트가 하나 더 쌓일 뿐이고,
    # 거래소 캘린더 의존성을 들이는 것보다 낫다.
    # GitHub Actions 스케줄은 부하에 따라 5~30분 지연될 수 있음
    - cron: '0 1 * * 1-5'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  report:
    runs-on: ubuntu-latest
    timeout-minutes: 30

    steps:
      - name: Checkout repository
        uses: actions/checkout@v7
        with:
          # rebase가 필요하므로 shallow clone 대신 전체 히스토리를 가져옴
          fetch-depth: 0

      - name: Setup Python 3.11
        uses: actions/setup-python@v6
        with:
          python-version: '3.11'
          cache: pip

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Build the performance report
        run: python perf_report.py

      - name: Commit and push the report
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add reports/
          if git diff --staged --quiet; then
            echo "No report change to commit"
          else
            # KST 는 서머타임이 없어 UTC+9 고정. TZ=Asia/Seoul 은 tzdata 가 없는
            # 환경에서 조용히 UTC 를 KST 라고 찍으므로 오프셋으로 계산한다.
            git commit -m "Perf report: $(date -u -d '+9 hours' +'%Y-%m-%d KST')"
            for i in 1 2 3; do
              git pull --rebase origin main && git push && exit 0
              echo "push retry $i"
              sleep 5
            done
            echo "::error::Failed to push the report after 3 attempts"
            exit 1
          fi

  # 리포트가 실패하면 그날 성과 스냅샷이 비는데, 스캔과 달리 원천 데이터는
  # 남아 있으므로 재실행으로 복구된다. 그래도 조용히 넘어가면 며칠씩 비는
  # 것을 눈치채지 못하므로 이슈를 연다.
  notify-failure:
    needs: report
    if: always() && needs.report.result == 'failure'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: write

    steps:
      - name: Open an issue for the missing report
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          REPO: ${{ github.repository }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          # KST 는 서머타임이 없어 UTC+9 고정
          KST_DATE=$(date -u -d '+9 hours' +'%Y-%m-%d')
          TITLE="성과 리포트 실패 ${KST_DATE} (KST)"

          # 같은 날 재실행으로 이슈가 중복되지 않게 한다
          EXISTING=$(gh issue list --repo "$REPO" --state open \
            --search "\"$TITLE\" in:title" --json number --jq 'length')
          if [ "$EXISTING" != "0" ]; then
            echo "이미 열린 이슈가 있다. 건너뛴다."
            exit 0
          fi

          gh issue create --repo "$REPO" --title "$TITLE" --body "$(cat <<EOB
          ${KST_DATE} (KST) 성과 리포트 생성이 실패했습니다.

          스캔과 달리 원천 데이터(\`history/*.csv\`)는 남아 있으므로
          **재실행으로 복구됩니다.** 워크플로를 수동 실행하세요.

          실행 로그: ${RUN_URL}

          ## 확인할 것

          - **환율 조회 실패** — \`USDKRW=X 환율 조회 실패\` 가 보이면 yfinance
            rate limit 이거나 티커 응답이 빈 경우입니다. 고정환율로 대체하지
            않고 일부러 실패시킵니다 - 조용히 틀린 금액을 커밋하지 않으려는 것입니다.
          - **환율 소급 실패** — \`... 이전의 환율이 없다\` 는 조회 시작일이
            첫 진입일보다 충분히 앞서지 않은 경우입니다.
          - **시세 조회 실패** — 요약 시트의 \`시세 조회 실패\` 항목을 보세요.
            일부 종목 실패는 리포트를 막지 않습니다.
          - **push 실패** — rebase 재시도 3회를 모두 소진했는지 확인하세요.
          EOB
          )"
```

- [ ] **Step 2: YAML 문법 검증**

Run: `python -c "import yaml,io; yaml.safe_load(io.open('.github/workflows/report.yml',encoding='utf-8')); print('yaml ok')"`

Expected: `yaml ok`

(`pyyaml` 이 없으면 `python -m pip install pyyaml` 후 실행)

- [ ] **Step 3: 커밋과 푸시**

```bash
git add .github/workflows/report.yml
git commit -m "Build the performance report every weekday at 10:00 KST"
git push
```

- [ ] **Step 4: 수동 실행으로 검증**

Run: `gh workflow run "Daily Performance Report"`

30초쯤 뒤 확인: `gh run list --workflow "Daily Performance Report" --limit 1`

Expected: `completed  success`

- [ ] **Step 5: 산출물 확인**

Run: `git pull && ls -la reports/`

Expected: `perf_2026-08-19.xlsx` 가 있고, 커밋 메시지가 `Perf report: 2026-08-19 KST`

---

## 완료 확인

- [ ] `python -m pytest -q` → 130 passed
- [ ] `python perf_report.py` → XLSX 생성, 한글 정상
- [ ] `reports/perf_*.xlsx` 의 손실 셀이 `-` 부호로 표시됨
- [ ] `gh workflow run` 수동 실행 성공
- [ ] 요약 시트 최상단에 오염 경고가 있음
