# 청산 규칙 모듈 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 진입한 포지션이 언제 어떤 가격에 청산되는지를 순수 함수로 정의해, 2단계 백테스트와 4단계 실거래가 같은 규칙을 쓰도록 한다.

**Architecture:** `exit_rules.py` 단일 모듈. I/O 없는 순수 함수 네 개(`open_position`, `current_stop`, `evaluate`, `advance`)와 dataclass 네 개. 가격은 호출자가 `Bar`로 넘긴다 — 모듈은 파일도 네트워크도 건드리지 않는다.

**Tech Stack:** Python 3.11 표준 라이브러리만 (dataclasses, typing), pytest

**Spec:** `docs/superpowers/specs/2026-08-18-exit-rules-design.md`

---

## File Structure

| 파일 | 책임 |
|---|---|
| `exit_rules.py` (신규) | 데이터 모델 4개 + 순수 함수 4개. 청산 판정의 유일한 소유자 |
| `tests/test_exit_rules.py` (신규) | 트리거별·우선순위·룩어헤드·결측·입력검증 테스트 |

`exit_rules.py`는 `history.py`도 `stock_finder.py`도 임포트하지 않는다. 순환 의존을 막고,
2단계 하네스가 어떤 출처의 가격이든 넘길 수 있게 하기 위해서다.

**호출자(2단계 하네스)의 책임이라 이 계획에 없는 것**: 스펙의 결측 처리 중 "특정일 OHLC
결측 시 평가·갱신 건너뛰기"와 "진입일 시가 없으면 진입 취소"는 루프를 도는 쪽의 일이다.
이 모듈에는 해당 함수가 없는 것이 정상이며, 빠뜨린 것이 아니다.

의존성 추가 없음 — numpy도 pandas도 쓰지 않는다. 스칼라 산술만 한다.

---

## Task 1: 데이터 모델과 진입

**Files:**
- Create: `exit_rules.py`
- Test: `tests/test_exit_rules.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_exit_rules.py` 신규 생성:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_exit_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'exit_rules'`

- [ ] **Step 3: exit_rules.py 구현**

`exit_rules.py` 신규 생성:

```python
"""포지션 청산 규칙.

진입한 포지션이 언제 어떤 가격에 청산되는지를 정의한다. 2단계 백테스트 하네스와
4단계 실행 엔진이 같은 모듈을 쓴다 — 규칙이 한 곳에만 있어야 백테스트와 실거래가
갈라지지 않는다.

전부 순수 함수다. 파일도 네트워크도 건드리지 않고, 가격은 호출자가 Bar 로 넘긴다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class Params:
    """청산 파라미터 4개.

    v5 설계서가 파라미터 5개 초과를 금지한다. 기본값은 전부 튜닝되지 않았다 —
    백테스트가 없어 맞출 수 없고, 과최적화 금지 원칙상 지금 맞춰서도 안 된다.

    두 ATR 배수가 같은 값인 것은 우연이 아니다. 고점이 진입가+1R 에 닿는 순간
    트레일 손절선이 정확히 진입가가 되어, "1R 도달 시 본전이동"이 파라미터를
    추가하지 않고 자동으로 나온다.
    """
    stop_atr_mult: float = 3.0
    trail_atr_mult: float = 3.0
    max_hold_days: int = 60
    exit_total: int = 60


@dataclass(frozen=True)
class Bar:
    """하루치 시세와 그날의 스코어.

    atr14 가 없으면 트레일링을 적용하지 않고, total 이 없으면 SIGNAL 판정을 건너뛴다.
    """
    date: str
    open: float
    high: float
    low: float
    close: float
    atr14: Optional[float] = None
    total: Optional[int] = None


@dataclass(frozen=True)
class Position:
    ticker: str
    entry_date: str
    entry_price: float
    initial_stop: float
    r_unit: float
    high_since_entry: float
    bars_held: int


@dataclass(frozen=True)
class ExitDecision:
    reason: str      # "TIME" | "SIGNAL" | "STOP" | "TRAIL"
    price: float
    date: str


def open_position(ticker: str, date: str, entry_price: float,
                  atr_at_entry: Optional[float], params: Params) -> Position:
    """진입 시점 ATR 로 초기 손절선과 R 을 확정한다.

    초기 손절은 진입 시점 ATR 로 고정한다 — R 정의가 도중에 흔들리면 손익을
    R 배수로 비교할 수 없게 된다.
    """
    if atr_at_entry is None or atr_at_entry <= 0:
        raise ValueError(
            f"{ticker}: atr_at_entry 가 {atr_at_entry} 입니다. "
            "손절폭이 0 이면 R 이 0 이 되어 이후 계산이 전부 무의미해집니다."
        )

    initial_stop = entry_price - params.stop_atr_mult * atr_at_entry
    return Position(
        ticker=ticker,
        entry_date=date,
        entry_price=entry_price,
        initial_stop=initial_stop,
        r_unit=entry_price - initial_stop,
        high_since_entry=entry_price,
        bars_held=0,
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_exit_rules.py -v`
Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add exit_rules.py tests/test_exit_rules.py
git commit -m "Add exit rule data model and position entry"
```

---

## Task 2: 손절선 계산과 상태 갱신

**Files:**
- Modify: `exit_rules.py`
- Modify: `tests/test_exit_rules.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_exit_rules.py` 끝에 추가:

```python
def _pos(**over):
    """진입가 100, ATR 2.0, 손절 94, 1R = 6 인 기준 포지션."""
    pos = er.open_position("NVDA", "2026-08-19", entry_price=100.0,
                           atr_at_entry=2.0, params=P)
    if over:
        pos = replace(pos, **over)
    return pos


def test_current_stop_is_initial_before_trail_activates():
    # 고점이 진입가+1R(=106) 에 못 미치면 트레일링은 켜지지 않는다.
    pos = _pos(high_since_entry=105.0)
    assert er.current_stop(pos, P, atr=2.0) == 94.0


def test_trail_lands_exactly_on_breakeven_at_one_r():
    # 두 ATR 배수가 같으므로 고점이 정확히 1R 일 때 손절선 = 진입가.
    pos = _pos(high_since_entry=106.0)
    assert er.current_stop(pos, P, atr=2.0) == 100.0


def test_trail_follows_the_high():
    pos = _pos(high_since_entry=120.0)
    # 120 - 3.0 * 2.0 = 114
    assert er.current_stop(pos, P, atr=2.0) == 114.0


def test_trail_never_drops_below_the_initial_stop():
    # 변동성이 급등해 트레일 계산값이 초기 손절 아래로 내려가도 손절선은 올라간 채 유지.
    pos = _pos(high_since_entry=106.0)
    assert er.current_stop(pos, P, atr=20.0) == 94.0


def test_missing_atr_keeps_the_initial_stop():
    pos = _pos(high_since_entry=120.0)
    assert er.current_stop(pos, P, atr=None) == 94.0


def test_advance_updates_high_and_bar_count():
    pos = _pos()
    bar = er.Bar("2026-08-20", open=101.0, high=108.0, low=99.0, close=107.0)

    got = er.advance(pos, bar)

    assert got.high_since_entry == 108.0
    assert got.bars_held == 1
    assert got.entry_price == 100.0      # 나머지는 불변


def test_advance_keeps_the_higher_high():
    pos = _pos(high_since_entry=120.0)
    bar = er.Bar("2026-08-20", open=101.0, high=108.0, low=99.0, close=107.0)

    got = er.advance(pos, bar)

    assert got.high_since_entry == 120.0
    assert got.bars_held == 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_exit_rules.py -v`
Expected: FAIL — `AttributeError: module 'exit_rules' has no attribute 'current_stop'`

- [ ] **Step 3: 구현**

`exit_rules.py`의 `open_position` 아래에 추가:

```python
def current_stop(position: Position, params: Params,
                 atr: Optional[float]) -> float:
    """현재 유효한 손절선.

    고점이 진입가+1R 에 닿으면 트레일링이 켜진다. 트레일링은 현재 ATR 을 쓴다
    (Chandelier 표준) — 3개월간 변동성이 크게 바뀌므로 진입 시점 값에 묶어두면
    뒤로 갈수록 부정확해진다. 손절선은 절대 내려가지 않는다.
    """
    if atr is None:
        return position.initial_stop

    trail_active = position.high_since_entry >= position.entry_price + position.r_unit
    if not trail_active:
        return position.initial_stop

    trailed = position.high_since_entry - params.trail_atr_mult * atr
    return max(position.initial_stop, trailed)


def advance(position: Position, bar: Bar) -> Position:
    """봉 하나를 소화하고 포지션 상태를 갱신한다.

    반드시 evaluate 다음에 호출할 것. 먼저 호출하면 오늘 고가로 계산한 손절선이
    오늘 장중에 체결되는 셈이 되어 룩어헤드가 된다.
    """
    return replace(
        position,
        high_since_entry=max(position.high_since_entry, bar.high),
        bars_held=position.bars_held + 1,
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_exit_rules.py -v`
Expected: 12 passed

- [ ] **Step 5: 커밋**

```bash
git add exit_rules.py tests/test_exit_rules.py
git commit -m "Compute the trailing stop and advance position state"
```

---

## Task 3: 청산 판정

**Files:**
- Modify: `exit_rules.py`
- Modify: `tests/test_exit_rules.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_exit_rules.py` 끝에 추가:

```python
def test_stop_fills_at_the_stop_when_low_touches_it():
    pos = _pos()
    bar = er.Bar("2026-08-20", open=99.0, high=100.0, low=94.0, close=95.0,
                 atr14=2.0, total=75)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "STOP"
    assert got.price == 94.0
    assert got.date == "2026-08-20"


def test_stop_fills_at_the_open_on_a_gap_down():
    # 시가가 이미 손절선 아래면 그 가격에 체결된다 — 슬리피지.
    pos = _pos()
    bar = er.Bar("2026-08-20", open=90.0, high=92.0, low=88.0, close=91.0,
                 atr14=2.0, total=75)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "STOP"
    assert got.price == 90.0


def test_no_exit_when_nothing_triggers():
    pos = _pos()
    bar = er.Bar("2026-08-20", open=101.0, high=103.0, low=99.0, close=102.0,
                 atr14=2.0, total=75)

    assert er.evaluate(pos, bar, P) is None


def test_trail_exit_is_labelled_trail_not_stop():
    pos = _pos(high_since_entry=120.0)   # 손절선 = 114
    bar = er.Bar("2026-08-20", open=118.0, high=119.0, low=113.0, close=115.0,
                 atr14=2.0, total=75)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "TRAIL"
    assert got.price == 114.0


def test_time_exit_fills_at_the_open():
    pos = _pos(bars_held=60)
    bar = er.Bar("2026-08-20", open=105.0, high=106.0, low=104.0, close=105.5,
                 atr14=2.0, total=75)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "TIME"
    assert got.price == 105.0


def test_signal_exit_fills_at_the_open():
    pos = _pos()
    bar = er.Bar("2026-08-20", open=105.0, high=106.0, low=104.0, close=105.5,
                 atr14=2.0, total=59)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "SIGNAL"
    assert got.price == 105.0


def test_hysteresis_holds_between_entry_and_exit_thresholds():
    # 진입 70 / 청산 60 사이에서는 아무 일도 일어나지 않는다.
    pos = _pos()
    bar = er.Bar("2026-08-20", open=105.0, high=106.0, low=104.0, close=105.5,
                 atr14=2.0, total=65)

    assert er.evaluate(pos, bar, P) is None


def test_signal_exit_at_exactly_the_threshold_does_not_fire():
    pos = _pos()
    bar = er.Bar("2026-08-20", open=105.0, high=106.0, low=104.0, close=105.5,
                 atr14=2.0, total=60)

    assert er.evaluate(pos, bar, P) is None


def test_time_beats_stop_on_the_same_bar():
    # 둘 다 발동해도 TIME 은 개장 전에 결정돼 시가에 나간다.
    pos = _pos(bars_held=60)
    bar = er.Bar("2026-08-20", open=99.0, high=100.0, low=90.0, close=91.0,
                 atr14=2.0, total=75)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "TIME"
    assert got.price == 99.0


def test_signal_beats_stop_on_the_same_bar():
    pos = _pos()
    bar = er.Bar("2026-08-20", open=99.0, high=100.0, low=90.0, close=91.0,
                 atr14=2.0, total=50)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "SIGNAL"
    assert got.price == 99.0


def test_missing_total_skips_signal_but_keeps_stop():
    pos = _pos()
    bar = er.Bar("2026-08-20", open=99.0, high=100.0, low=90.0, close=91.0,
                 atr14=2.0, total=None)

    got = er.evaluate(pos, bar, P)

    assert got.reason == "STOP"


def test_todays_high_does_not_set_todays_stop():
    # 룩어헤드 차단. 오늘 고가 120 이 트레일을 켜더라도 오늘 손절선은
    # 어제까지의 고점(=진입가)으로 계산된 94 여야 한다. 저가 95 는 94 를 안 건드린다.
    pos = _pos()
    bar = er.Bar("2026-08-20", open=101.0, high=120.0, low=95.0, close=119.0,
                 atr14=2.0, total=75)

    assert er.evaluate(pos, bar, P) is None

    after = er.advance(pos, bar)
    assert after.high_since_entry == 120.0
    assert er.current_stop(after, P, atr=2.0) == 114.0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_exit_rules.py -v`
Expected: FAIL — `AttributeError: module 'exit_rules' has no attribute 'evaluate'`

- [ ] **Step 3: 구현**

`exit_rules.py` 끝에 추가:

```python
def evaluate(position: Position, bar: Bar,
             params: Params) -> Optional[ExitDecision]:
    """이 봉에서 청산이 발생하는지 판정한다. 없으면 None.

    순서는 하루 안의 시간 순서다. TIME 과 SIGNAL 은 개장 전에 결정된다 —
    bars_held 는 결정론적이고 total 은 KST 07:00 스캔에서 이미 나와 있다.
    따라서 둘 다 시가 시장가로 나가고, 장중에 걸린 손절보다 먼저 체결된다.
    """
    if position.bars_held >= params.max_hold_days:
        return ExitDecision("TIME", bar.open, bar.date)

    if bar.total is not None and bar.total < params.exit_total:
        return ExitDecision("SIGNAL", bar.open, bar.date)

    stop = current_stop(position, params, bar.atr14)
    if bar.low <= stop:
        trailing = stop > position.initial_stop
        # 갭하락으로 시가가 이미 손절선 아래면 그 가격에 체결된다.
        fill = min(bar.open, stop)
        return ExitDecision("TRAIL" if trailing else "STOP", fill, bar.date)

    return None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_exit_rules.py -v`
Expected: 24 passed

- [ ] **Step 5: 전체 테스트 확인**

Run: `python -m pytest tests/ -v`
Expected: 59 passed (기존 35 + 신규 24)

- [ ] **Step 6: 커밋**

```bash
git add exit_rules.py tests/test_exit_rules.py
git commit -m "Decide position exits in open-then-intraday order"
```

---

## Task 4: 순수성과 완료 기준 검증

**Files:**
- Modify: `tests/test_exit_rules.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_exit_rules.py` 끝에 추가. 파일 상단에 `import inspect`를 추가한다:

```python
def test_module_has_no_io_dependencies():
    # 순환 의존과 숨은 I/O 를 막는다. 2단계 하네스가 어떤 출처의 가격이든
    # 넘길 수 있어야 하고, 규칙 모듈이 스스로 데이터를 읽으면 그 계약이 깨진다.
    src = inspect.getsource(er)
    for banned in ("import history", "import stock_finder", "import yfinance",
                   "import requests", "open(", "subprocess"):
        assert banned not in src, f"exit_rules 가 {banned} 를 쓰면 안 된다"


def test_params_has_exactly_four_fields():
    # v5 설계서가 파라미터 5개 초과를 금지한다.
    import dataclasses
    assert len(dataclasses.fields(er.Params)) == 4
```

- [ ] **Step 2: 테스트 실패 확인 또는 통과 확인**

Run: `python -m pytest tests/test_exit_rules.py -v`
Expected: 26 passed — 구현이 이미 조건을 만족하므로 두 테스트 모두 바로 통과한다.
통과하지 않으면 `exit_rules.py`가 스펙을 위반한 것이므로 모듈을 고친다. 테스트를 고치지 않는다.

- [ ] **Step 3: 전체 테스트 확인**

Run: `python -m pytest tests/ -v`
Expected: 61 passed (기존 35 + 신규 26)

- [ ] **Step 4: 계약 확인 — 스펙의 공개 함수 4개가 모두 존재하는지**

```bash
python -c "
import exit_rules as er
for name in ('open_position', 'current_stop', 'evaluate', 'advance'):
    assert callable(getattr(er, name)), name
for name in ('Params', 'Bar', 'Position', 'ExitDecision'):
    assert hasattr(er, name), name
p = er.Params()
print('파라미터:', p)
pos = er.open_position('X', '2026-08-19', 100.0, 2.0, p)
print('진입:', pos)
from dataclasses import replace
print('1R 도달 시 손절선:', er.current_stop(replace(pos, high_since_entry=106.0), p, 2.0))
"
```
Expected: 파라미터 4개가 3.0/3.0/60/60 으로 출력되고, 1R 도달 시 손절선이 정확히 `100.0`

- [ ] **Step 5: 커밋**

```bash
git add tests/test_exit_rules.py
git commit -m "Pin the purity and parameter-count contracts"
```

---

## 완료 기준 점검

- [ ] `exit_rules.py`의 네 함수가 순수 함수로 구현되고 테스트가 통과한다 (Task 3 Step 5, Task 4 Step 3)
- [ ] 파라미터는 정확히 4개이며 기본값이 코드에 명시돼 있다 (Task 4 Step 1, Step 4)
- [ ] 모듈이 `history.py`·`stock_finder.py`를 임포트하지 않는다 (Task 4 Step 1)
- [ ] 스펙의 테스트 11개 항목이 모두 커버된다:
  STOP 체결·갭하락(Task 3) · 트레일 본전이동(Task 2) · 트레일 하한(Task 2) ·
  TIME(Task 3) · SIGNAL(Task 3) · 히스테리시스(Task 3) · TIME>STOP(Task 3) ·
  SIGNAL>STOP(Task 3) · 룩어헤드(Task 3) · 결측(Task 2·3) · 입력검증(Task 1)
