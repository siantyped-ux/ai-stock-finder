# 목표가 익절(TARGET)과 목표구간 리포트 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 대시보드의 `목표가 상승률`을 청산 규칙과 성과 리포트에 연결한다. 목표가 도달 시 청산하는 `TARGET` 사유를 기본 꺼짐으로 추가하고, 미결포지션 시트에 목표%·목표가·달성률·위험보상 네 컬럼을 항상 표시한다.

**Architecture:** 목표가는 진입 봉에서 한 번 확정돼 `Position.target_price` → `Trade.target_price`로 흐른다. 규칙은 `exit_rules.py`에만 있고, `perf_report.py`는 `Trade`에서 읽기만 한다(재시뮬 없음). `Params.use_target`이 기본 `False`라 기존 동작과 테스트 130개는 전부 그대로다.

**Tech Stack:** Python 3, dataclasses(frozen), pytest, openpyxl

**설계 문서:** `docs/superpowers/specs/2026-08-20-target-exit-design.md`

---

## 배경 지식 (이 저장소를 처음 보는 사람용)

- `exit_rules.py` — 순수 함수만 있다. 파일·네트워크를 건드리지 않는다. `Params`는 파라미터 5개가 상한이다(v5 설계서 규칙). 현재 4개.
- `trade_sim.py` — `simulate_ticker`가 아카이브 행과 봉을 받아 트레이드를 재현한다. `evaluate`를 먼저, `advance`를 나중에 호출하는 순서가 룩어헤드 방지의 핵심이라 절대 바꾸지 않는다.
- `backtest.py` — `history/*.csv`를 읽어 `simulate_ticker`를 돌린다. `run()`이 dict를 돌려준다.
- `perf_report.py` — `backtest.run()` 결과를 원화 XLSX 3시트로 낸다.
- `stops.py` — 미결 포지션의 손절선 조회 전용. **이번 작업에서 건드리지 않는다.**
- 테스트 실행: 저장소 루트에서 `python -m pytest tests/ -q`
- 커밋 메시지는 영문, 코드 주석·문서는 한글이다.

## 파일 구조

| 파일 | 이번 작업에서의 책임 | 변경 |
|---|---|---|
| `exit_rules.py` | 목표가 확정과 `TARGET` 판정. 규칙의 유일한 출처 | 수정 |
| `trade_sim.py` | 목표가를 `Trade`까지 실어 나름 | 수정 |
| `backtest.py` | 아카이브의 `target` 컬럼을 시뮬레이터에 전달, CLI 플래그 | 수정 |
| `perf_report.py` | 목표 컬럼 4개 + 요약 한 줄 (읽기만) | 수정 |
| `stops.py` | — | **변경 없음** |
| `tests/test_exit_rules.py` | 목표가 확정·판정·우선순위 | 수정 |
| `tests/test_trade_sim.py` | 전달과 고정 | 수정 |
| `tests/test_perf_report.py` | 컬럼 값과 빈칸 | 수정 |
| `tests/test_stops.py` | `Trade` 필드 추가에 따른 헬퍼 보정만 | 수정 |

---

### Task 1: 목표가를 진입 시점에 확정한다

**Files:**
- Modify: `exit_rules.py:22-27` (`Params`), `exit_rules.py:56-64` (`Position`), `exit_rules.py:67-90` (`open_position`)
- Test: `tests/test_exit_rules.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_exit_rules.py`에서 `test_params_defaults_are_the_four_documented_values` 바로 아래에 넣는다.

```python
def test_use_target_defaults_to_off():
    # 익절이 기대값을 올리는지 모르는 상태다. 기본값을 바꾸지 않는다.
    assert P.use_target is False


def test_open_position_sets_target_price_from_target_pct():
    pos = er.open_position("NVDA", "2026-08-19", entry_price=100.0,
                           atr_at_entry=2.0, params=P, target_pct=9)
    assert pos.target_price == pytest.approx(109.0)


def test_open_position_without_target_pct_has_no_target():
    pos = er.open_position("NVDA", "2026-08-19", entry_price=100.0,
                           atr_at_entry=2.0, params=P)
    assert pos.target_price is None


@pytest.mark.parametrize("bad", [0, -2, -15])
def test_non_positive_target_pct_disables_the_target(bad):
    # 목표가가 진입가 이하면 "익절"이 즉시 손실 확정이 된다.
    pos = er.open_position("NVDA", "2026-08-19", entry_price=100.0,
                           atr_at_entry=2.0, params=P, target_pct=bad)
    assert pos.target_price is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_exit_rules.py -q -k "target"`
Expected: FAIL — `AttributeError: 'Params' object has no attribute 'use_target'` 및 `TypeError: open_position() got an unexpected keyword argument 'target_pct'`

- [ ] **Step 3: `Params`에 5번째 파라미터를 추가한다**

`exit_rules.py`의 `Params` 클래스를 통째로 아래로 바꾼다.

```python
@dataclass(frozen=True)
class Params:
    """청산 파라미터 5개.

    v5 설계서가 파라미터 5개 초과를 금지한다. 기본값은 전부 튜닝되지 않았다 —
    백테스트가 없어 맞출 수 없고, 과최적화 금지 원칙상 지금 맞춰서도 안 된다.

    두 ATR 배수가 같은 값인 것은 우연이 아니다. 고점이 진입가+1R 에 닿는 순간
    트레일 손절선이 정확히 진입가가 되어, "1R 도달 시 본전이동"이 파라미터를
    추가하지 않고 자동으로 나온다.

    use_target 은 기본이 꺼짐이다. 목표폭 대비 손절폭(위험보상비)이 1 근처라
    목표가 익절이 기대값을 올리는지 내리는지 아직 알 수 없다. 켠 결과와 끈
    결과를 나란히 비교할 수 있게만 해 두고, 기본값은 건드리지 않는다.
    """
    stop_atr_mult: float = 3.0
    trail_atr_mult: float = 3.0
    max_hold_days: int = 60
    exit_total: int = 60
    use_target: bool = False
```

- [ ] **Step 4: `Position`에 목표가를 담는다**

`exit_rules.py`의 `Position` 클래스를 아래로 바꾼다.

```python
@dataclass(frozen=True)
class Position:
    ticker: str
    entry_date: str
    entry_price: float
    initial_stop: float
    r_unit: float
    high_since_entry: float
    stop: float
    bars_held: int
    # 진입일 스코어의 목표 상승률로 확정한 익절가. 목표를 알 수 없거나
    # 목표가가 진입가 이하면 None 이고, 그 포지션에는 TARGET 규칙이 없다.
    # initial_stop 과 같이 진입 시점에 고정한다 - 도중에 움직이면 "목표
    # 달성" 의 정의가 흔들려 달성률을 비교할 수 없다.
    target_price: Optional[float]
```

- [ ] **Step 5: `open_position`이 목표가를 계산하게 한다**

`exit_rules.py`의 `open_position`을 아래로 바꾼다.

```python
def open_position(ticker: str, date: str, entry_price: float,
                  atr_at_entry: Optional[float], params: Params,
                  target_pct: Optional[float] = None) -> Position:
    """진입 시점 ATR 로 초기 손절선과 R 을, 진입일 스코어로 목표가를 확정한다.

    초기 손절은 진입 시점 ATR 로 고정한다 — R 정의가 도중에 흔들리면 손익을
    R 배수로 비교할 수 없게 된다. 목표가도 같은 이유로 진입 시점에 고정한다.

    target_pct 는 아카이브의 target 컬럼 값(3개월 기대 상승률 %)이다.
    없거나 0 이하면 목표가를 두지 않는다 - 목표가가 진입가 이하이면 익절이
    곧 손실 확정이 되어 규칙이 뒤집힌다.
    """
    if atr_at_entry is None or atr_at_entry <= 0:
        raise ValueError(
            f"{ticker}: atr_at_entry 가 {atr_at_entry} 입니다. "
            "손절폭이 0 이면 R 이 0 이 되어 이후 계산이 전부 무의미해집니다."
        )

    initial_stop = entry_price - params.stop_atr_mult * atr_at_entry
    target_price = (entry_price * (1 + target_pct / 100.0)
                    if target_pct is not None and target_pct > 0 else None)
    return Position(
        ticker=ticker,
        entry_date=date,
        entry_price=entry_price,
        initial_stop=initial_stop,
        r_unit=entry_price - initial_stop,
        high_since_entry=entry_price,
        stop=initial_stop,
        bars_held=0,
        target_price=target_price,
    )
```

- [ ] **Step 6: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_exit_rules.py -q`
Expected: PASS — 기존 테스트 포함 전부 통과

- [ ] **Step 7: 커밋한다**

```bash
git add exit_rules.py tests/test_exit_rules.py
git commit -m "Fix the target price at entry"
```

---

### Task 2: `evaluate`가 목표가 도달을 판정한다

**Files:**
- Modify: `exit_rules.py:60-64` (`ExitDecision` 주석), `exit_rules.py:140-160` (`evaluate`)
- Test: `tests/test_exit_rules.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_exit_rules.py` 맨 끝에 붙인다. `_pos` 헬퍼는 이미 파일에 있다.

```python
PT = er.Params(use_target=True)


def _tp(**over):
    """목표 9% (목표가 109) 를 단 기준 포지션. 진입 100 · 1R=6 · 손절 94."""
    pos = er.open_position("NVDA", "2026-08-19", entry_price=100.0,
                           atr_at_entry=2.0, params=P, target_pct=9)
    if over:
        pos = replace(pos, **over)
    return pos


def test_target_exits_when_the_high_reaches_it():
    bar = er.Bar("d", open=105.0, high=110.0, low=104.0, close=109.5,
                 atr14=2.0, total=75)
    decision = er.evaluate(_tp(), bar, PT)

    assert decision.reason == "TARGET"
    assert decision.price == 109.0
    assert decision.date == "d"


def test_target_fills_at_the_open_when_the_gap_clears_it():
    # 갭상승으로 시가가 이미 목표 위면 그 가격에 체결된다.
    # 갭하락 시 min(open, stop) 으로 체결하는 손절 처리의 대칭이다.
    bar = er.Bar("d", open=112.0, high=115.0, low=111.0, close=113.0,
                 atr14=2.0, total=75)
    assert er.evaluate(_tp(), bar, PT).price == 112.0


def test_stop_wins_when_the_same_bar_touches_both():
    # 고가가 목표를, 저가가 손절선을 같은 봉에서 건드린다. 일봉으로는 어느
    # 쪽이 먼저였는지 알 수 없으므로 비관적으로 손절을 잡는다.
    bar = er.Bar("d", open=105.0, high=110.0, low=90.0, close=95.0,
                 atr14=2.0, total=75)
    decision = er.evaluate(_tp(), bar, PT)

    assert decision.reason == "STOP"
    assert decision.price == 94.0


def test_target_is_ignored_when_the_rule_is_off():
    bar = er.Bar("d", open=105.0, high=110.0, low=104.0, close=109.5,
                 atr14=2.0, total=75)
    assert er.evaluate(_tp(), bar, P) is None


def test_target_is_ignored_without_a_target_price():
    bar = er.Bar("d", open=105.0, high=999.0, low=104.0, close=500.0,
                 atr14=2.0, total=75)
    assert er.evaluate(_pos(), bar, PT) is None


def test_time_beats_target():
    bar = er.Bar("d", open=105.0, high=110.0, low=104.0, close=109.5,
                 atr14=2.0, total=75)
    decision = er.evaluate(_tp(bars_held=60), bar, PT)

    assert decision.reason == "TIME"
    assert decision.price == 105.0


def test_signal_beats_target():
    bar = er.Bar("d", open=105.0, high=110.0, low=104.0, close=109.5,
                 atr14=2.0, total=50)
    assert er.evaluate(_tp(), bar, PT).reason == "SIGNAL"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_exit_rules.py -q -k "target or beats"`
Expected: FAIL — `test_target_exits_when_the_high_reaches_it`에서 `AttributeError: 'NoneType' object has no attribute 'reason'` (`evaluate`가 `None`을 돌려준다)

- [ ] **Step 3: `ExitDecision` 주석에 사유를 하나 추가한다**

`exit_rules.py`의 `ExitDecision`을 아래로 바꾼다.

```python
@dataclass(frozen=True)
class ExitDecision:
    reason: str      # "TIME" | "SIGNAL" | "STOP" | "TRAIL" | "TARGET"
    price: float
    date: str
```

- [ ] **Step 4: `evaluate`에 TARGET 판정을 맨 뒤로 넣는다**

`exit_rules.py`의 `evaluate`를 통째로 아래로 바꾼다.

```python
def evaluate(position: Position, bar: Bar,
             params: Params) -> Optional[ExitDecision]:
    """이 봉에서 청산이 발생하는지 판정한다. 없으면 None.

    순서는 하루 안의 시간 순서다. TIME 과 SIGNAL 은 개장 전에 결정된다 —
    bars_held 는 결정론적이고 total 은 KST 07:00 스캔에서 이미 나와 있다.
    따라서 둘 다 시가 시장가로 나가고, 장중에 걸린 손절보다 먼저 체결된다.

    TARGET 은 맨 뒤다. 고가가 목표를, 저가가 손절선을 같은 봉에서 건드리면
    일봉만으로는 어느 쪽이 먼저였는지 알 수 없다. 백테스트가 실제보다 좋게
    나오는 것보다 나쁘게 나오는 편이 안전하므로 손절을 먼저 잡는다.
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

    if (params.use_target and position.target_price is not None
            and bar.high >= position.target_price):
        # 갭상승으로 시가가 이미 목표 위면 그 가격에 체결된다.
        fill = max(bar.open, position.target_price)
        return ExitDecision("TARGET", fill, bar.date)

    return None
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_exit_rules.py -q`
Expected: PASS — 전부 통과

- [ ] **Step 6: 커밋한다**

```bash
git add exit_rules.py tests/test_exit_rules.py
git commit -m "Close the position when it reaches its target"
```

---

### Task 3: 목표가를 `Trade`까지 실어 나른다

**Files:**
- Modify: `trade_sim.py:74-97` (`Trade`), `trade_sim.py:139-163` (`_make_trade`), `trade_sim.py:216-219` (`simulate_ticker`의 진입 분기)
- Test: `tests/test_trade_sim.py`, `tests/test_perf_report.py:11-24`, `tests/test_stops.py:10-22`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_trade_sim.py`에서 `test_signal_closes_the_trade` 계열 테스트 뒤, `_trade(net_r, ...)` 헬퍼 정의 앞에 넣는다.

```python
def _target_row(date, target, signal="BUY"):
    return {"date": date, "signal": signal, "total": 75,
            "source": "live", "target": target}


def test_target_price_reaches_the_trade():
    rows = [_target_row("d1", 9)]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert trades[0].target_price == pytest.approx(109.0)


def test_target_price_stays_fixed_when_later_scans_change_it():
    # 진입 후 스코어가 올라 target 이 30% 가 돼도 목표가는 진입일 값이다.
    rows = [_target_row("d1", 9), _target_row("d2", 30)]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 100.5, 102.0, 100.0, 101.5)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert trades[0].target_price == pytest.approx(109.0)


def test_missing_target_key_leaves_no_target():
    # 예전 백필 파일에는 target 컬럼이 없을 수 있다. 죽지 않아야 한다.
    rows = [_row("d1", "BUY")]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert trades[0].target_price is None


def test_target_exit_closes_the_trade_when_enabled():
    rows = [_target_row("d1", 9), _target_row("d2", 9)]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 101.0, 110.0, 100.0, 109.0)}

    trades = ts.simulate_ticker("X", "US", rows, bars,
                                er.Params(use_target=True), C)

    assert trades[0].exit_reason == "TARGET"
    assert trades[0].exit_price == 109.0


def test_target_exit_does_nothing_by_default():
    rows = [_target_row("d1", 9), _target_row("d2", 9)]
    bars = {"d1": _bar("d1", 100.0, 101.0, 99.0, 100.5),
            "d2": _bar("d2", 101.0, 110.0, 100.0, 109.0)}

    trades = ts.simulate_ticker("X", "US", rows, bars, P, C)

    assert trades[0].is_open is True
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_trade_sim.py -q -k "target"`
Expected: FAIL — `AttributeError: 'Trade' object has no attribute 'target_price'`

- [ ] **Step 3: `Trade`에 필드를 추가한다**

`trade_sim.py`의 `Trade` 클래스에서 마지막 세 필드(`initial_stop`, `high_since_entry`, `stop`) 뒤에 아래를 붙인다.

```python
    # 진입일 스코어로 확정한 익절가. 목표가 없는 포지션은 None 이다.
    # 기본값을 두지 않는다 - mark_price 와 같은 규약이다.
    target_price: Optional[float]
```

- [ ] **Step 4: `_make_trade`가 목표가를 전달하게 한다**

`trade_sim.py`의 `_make_trade` 안 `Trade(...)` 생성에서 `stop=pos.stop,` 다음 줄에 아래를 넣는다.

```python
        target_price=pos.target_price,
```

- [ ] **Step 5: `simulate_ticker`가 진입일의 target 을 넘기게 한다**

`trade_sim.py`의 `simulate_ticker` 안 진입 분기를 아래로 바꾼다.

```python
                if bar.atr14:
                    # 진입일 행의 target 만 쓴다. 이후 스캔에서 값이 바뀌어도
                    # 목표가는 따라가지 않는다.
                    pos = er.open_position(ticker, row["date"], bar.open,
                                           bar.atr14, params,
                                           row.get("target"))
                    pos = er.advance(pos, bar, params)
```

- [ ] **Step 6: `Trade`를 직접 만드는 테스트 헬퍼 세 곳을 보정한다**

필드에 기본값이 없으므로 직접 생성하는 곳이 전부 깨진다. 셋 다 `None`으로 채운다.

`tests/test_perf_report.py`의 `_trade` 헬퍼 `base` dict 마지막 줄 뒤:

```python
        initial_stop=94.0, high_since_entry=110.0, stop=94.0,
        target_price=None,
```

`tests/test_stops.py`의 `_open` 헬퍼 `base` dict 마지막 줄 뒤:

```python
        initial_stop=94.0, high_since_entry=102.0, stop=94.0,
        target_price=None,
```

`tests/test_trade_sim.py`의 `_trade(net_r, ...)` 헬퍼 `ts.Trade(...)` 마지막 인자 뒤:

```python
        initial_stop=94.0, high_since_entry=106.0, stop=94.0,
        target_price=None,
    )
```

- [ ] **Step 7: 테스트 전체가 통과하는지 확인한다**

Run: `python -m pytest tests/ -q`
Expected: PASS — 기존 130개 + 신규 전부 통과

- [ ] **Step 8: 커밋한다**

```bash
git add trade_sim.py tests/test_trade_sim.py tests/test_perf_report.py tests/test_stops.py
git commit -m "Carry the target price on the trade"
```

---

### Task 4: 아카이브의 `target` 컬럼을 시뮬레이터에 연결한다

**Files:**
- Modify: `backtest.py:126-130` (`prepared`), `backtest.py:201-217` (`main`)
- Test: `tests/test_backtest.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_backtest.py` 맨 끝에 붙인다.

```python
def test_prepared_rows_carry_the_target(monkeypatch, tmp_path):
    # run() 이 아카이브의 target 컬럼을 시뮬레이터까지 넘기는지 확인한다.
    # 컬럼이 여기서 끊기면 목표가가 조용히 전부 None 이 된다.
    csv_path = tmp_path / "2026-08-03.csv"
    csv_path.write_text(
        "scan_ts_kst,date,ticker,name,market,sector,bar_date,close,volume,"
        "avg_vol20,atr14,market_cap,tech,macro,filing,value,total,consensus,"
        "signal,ev,target,hitl,source\n"
        "2026-08-03T07:00:00,2026-08-03,XYZ,X Corp,US,Tech,2026-08-02,"
        "100.0,1,1,2.0,1,70,70,70,70,75,2,BUY,0.75,9,False,live\n",
        encoding="utf-8")

    seen = {}

    def fake_fetch(ticker):
        return {"2026-08-03": er.Bar("2026-08-03", 100.0, 101.0, 99.0,
                                     100.5, atr14=2.0)}

    real_simulate = ts.simulate_ticker

    def spy(ticker, market, rows, bars, params, costs):
        seen["rows"] = rows
        return real_simulate(ticker, market, rows, bars, params, costs)

    monkeypatch.setattr(backtest, "fetch_bars", fake_fetch)
    monkeypatch.setattr(ts, "simulate_ticker", spy)

    backtest.run(str(tmp_path / "*.csv"))

    assert seen["rows"][0]["target"] == 9


def test_prepared_rows_tolerate_a_missing_target_column(monkeypatch, tmp_path):
    csv_path = tmp_path / "2026-08-03.csv"
    csv_path.write_text(
        "date,ticker,market,signal,total,source\n"
        "2026-08-03,XYZ,US,BUY,75,live\n", encoding="utf-8")

    seen = {}

    monkeypatch.setattr(backtest, "fetch_bars", lambda t: {
        "2026-08-03": er.Bar("2026-08-03", 100.0, 101.0, 99.0, 100.5,
                             atr14=2.0)})

    real_simulate = ts.simulate_ticker

    def spy(ticker, market, rows, bars, params, costs):
        seen["rows"] = rows
        return real_simulate(ticker, market, rows, bars, params, costs)

    monkeypatch.setattr(ts, "simulate_ticker", spy)

    backtest.run(str(tmp_path / "*.csv"))

    assert seen["rows"][0]["target"] is None
```

파일 상단 import 에 필요한 것이 없으면 아래를 추가한다.

```python
import backtest
import exit_rules as er
import trade_sim as ts
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_backtest.py -q -k "prepared"`
Expected: FAIL — `KeyError: 'target'`

- [ ] **Step 3: `prepared` 에 target 을 넣는다**

`backtest.py`의 `run()` 안 `prepared` 생성을 아래로 바꾼다.

```python
        prepared = [{"date": r["date"], "signal": r["signal"],
                     "total": int(r["total"]) if r["total"] else None,
                     # 목표 상승률(%). 예전 백필 파일에는 컬럼이 없을 수 있다.
                     "target": int(r["target"]) if r.get("target") else None,
                     "source": r["source"]} for r in rs]
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_backtest.py -q`
Expected: PASS

- [ ] **Step 5: `--use-target` 플래그를 추가한다**

`backtest.py`의 `main()`을 아래로 바꾼다.

```python
def main():
    console.force_utf8()
    p = argparse.ArgumentParser(description="스코어 아카이브 백테스트")
    p.add_argument("--history", default="history/*.csv")
    p.add_argument("--stop-atr-mult", type=float, default=3.0)
    p.add_argument("--trail-atr-mult", type=float, default=3.0)
    p.add_argument("--max-hold-days", type=int, default=60)
    p.add_argument("--exit-total", type=int, default=60)
    p.add_argument("--use-target", action="store_true",
                   help="목표가 도달 시 익절한다 (기본: 사용 안 함)")
    args = p.parse_args()

    params = er.Params(
        stop_atr_mult=args.stop_atr_mult,
        trail_atr_mult=args.trail_atr_mult,
        max_hold_days=args.max_hold_days,
        exit_total=args.exit_total,
        use_target=args.use_target,
    )
    report(run(args.history, params))
```

- [ ] **Step 6: 플래그가 실제로 붙는지 눈으로 확인한다**

Run: `python backtest.py --help`
Expected: 출력에 `--use-target  목표가 도달 시 익절한다 (기본: 사용 안 함)` 이 보인다

- [ ] **Step 7: 커밋한다**

```bash
git add backtest.py tests/test_backtest.py
git commit -m "Feed the archived target into the simulation"
```

---

### Task 5: 미결포지션 시트에 목표 컬럼 네 개를 붙인다

**Files:**
- Modify: `perf_report.py:197-217` (`OPEN_COLS`), `perf_report.py:115-176` (`build_rows`)
- Test: `tests/test_perf_report.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_perf_report.py`에서 `test_closed_sheet_is_untouched_by_the_stop_columns` 뒤에 넣는다.

```python
def test_open_sheet_appends_the_target_columns(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(
        _result([_held(target_price=109.0)]), FX))

    header = [c.value for c in load_workbook(path)["미결포지션"][1]]

    assert header[17:] == ["목표(%)", "목표가", "달성률(%)", "위험보상"]


def test_target_progress_measures_the_way_to_the_target():
    # 진입 100 · 목표 109 · 평가 101.5 → 목표폭 9 중 1.5 만큼 왔다.
    # 위험보상 = 목표폭 9 / 1R 6 = 1.5
    row = pr.build_rows(_result([_held(target_price=109.0)]), FX)["open"][0]

    assert row["target_pct"] == pytest.approx(9.0)
    assert row["target_price"] == pytest.approx(109.0)
    assert row["target_progress_pct"] == pytest.approx(16.6667, abs=1e-4)
    assert row["reward_risk"] == pytest.approx(1.5)


def test_target_progress_is_negative_below_the_entry():
    # 진입가 아래면 목표에서 멀어진 것이다. 부호가 없으면 진행처럼 읽힌다.
    row = pr.build_rows(
        _result([_held(mark_price=97.0, target_price=109.0)]), FX)["open"][0]

    assert row["target_progress_pct"] == pytest.approx(-33.3333, abs=1e-4)


def test_target_columns_are_blank_without_a_target():
    row = pr.build_rows(_result([_held()]), FX)["open"][0]

    assert row["target_pct"] is None
    assert row["target_price"] is None
    assert row["target_progress_pct"] is None
    assert row["reward_risk"] is None


def test_closed_sheet_is_untouched_by_the_target_columns(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(
        _result([_trade(target_price=109.0)]), FX))

    header = [c.value for c in load_workbook(path)["청산완료"][1]]

    assert "목표가" not in header
    assert header[-1] == "청산사유"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_perf_report.py -q -k "target"`
Expected: FAIL — `KeyError: 'target_pct'`

- [ ] **Step 3: 목표 컬럼을 계산하는 헬퍼를 추가한다**

`perf_report.py`의 `build_rows` 정의 바로 위에 넣는다.

```python
def target_cols(trade) -> dict:
    """미결 포지션의 목표가 관련 네 값. 목표가가 없으면 전부 빈칸이다.

    목표(%) 는 Trade.target_price 에서 되계산한다. 아카이브의 target 정수를
    따로 싣지 않는다 - 같은 사실을 두 곳에 두면 어긋날 수 있고, 목표가가
    유일한 진실이어야 한다.

    달성률은 목표폭 대비 어디까지 왔는지다. 진입가 아래면 음수가 나오고,
    그것이 맞다 - 목표에서 멀어졌다는 뜻이다.
    """
    tp = trade.target_price
    if tp is None:
        return {"target_pct": None, "target_price": None,
                "target_progress_pct": None, "reward_risk": None}

    entry = trade.entry_price
    return {
        "target_pct": (tp / entry - 1) * 100.0,
        "target_price": tp,
        "target_progress_pct": (trade.mark_price - entry) / (tp - entry) * 100.0,
        # 목표폭 ÷ 손절폭. 1 미만이면 목표를 다 채워도 손절 한 번보다 덜 번다.
        "reward_risk": (tp - entry) / trade.r_unit,
    }
```

- [ ] **Step 4: `build_rows` 의 미결 분기에서 헬퍼를 부른다**

`perf_report.py`의 `build_rows` 안 `row["trail"] = ...` 다음 줄, `opened.append(row)` 앞에 아래를 넣는다.

```python
            row.update(target_cols(t))
```

- [ ] **Step 5: `OPEN_COLS` 끝에 네 컬럼을 붙인다**

`perf_report.py`의 `OPEN_COLS` 리스트에서 `("트레일", "trail", None),` 다음 줄에 아래를 넣는다.

```python
    # 손절선이 "어디서 잘리나" 라면 이쪽은 "어디까지 가면 되나" 다.
    # use_target 이 꺼져 있어도 표시한다 - 규칙과 무관한 위치 정보다.
    ("목표(%)", "target_pct", PCT_FMT),
    ("목표가", "target_price", PRICE_FMT),
    ("달성률(%)", "target_progress_pct", PCT_FMT),
    ("위험보상", "reward_risk", RATE_FMT),
```

- [ ] **Step 6: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_perf_report.py -q`
Expected: PASS

- [ ] **Step 7: 커밋한다**

```bash
git add perf_report.py tests/test_perf_report.py
git commit -m "Show the target zone on the open positions sheet"
```

---

### Task 6: 요약 시트가 익절 규칙의 on/off 를 밝힌다

**Files:**
- Modify: `perf_report.py:115-176` (`build_rows` summary), `perf_report.py:272-303` (`_write_summary`), `perf_report.py:351-372` (`main`)
- Test: `tests/test_perf_report.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_perf_report.py` 상단 import 에 아래를 추가한다.

```python
import exit_rules as er
```

그리고 Task 5 에서 넣은 테스트들 뒤에 붙인다.

```python
def _summary_rows(path):
    return {r[0].value: r[1].value
            for r in load_workbook(path)["요약"].iter_rows(min_col=1,
                                                          max_col=2)}


def test_summary_says_the_target_exit_is_off_by_default(tmp_path):
    # 컬럼은 항상 보이는데 규칙은 꺼져 있다. 어느 쪽인지 적어 두지 않으면
    # 목표가가 익절 예고처럼 읽힌다.
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([_trade()]), FX))

    assert _summary_rows(path)["목표가 익절"] == "사용 안 함 (--use-target 으로 켬)"


def test_summary_says_the_target_exit_is_on_when_enabled(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([_trade()]), FX,
                                      params=er.Params(use_target=True)))

    assert _summary_rows(path)["목표가 익절"] == "사용함 (목표가 도달 시 청산)"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_perf_report.py -q -k "target_exit"`
Expected: FAIL — `KeyError: '목표가 익절'`

- [ ] **Step 3: summary dict 에 플래그를 싣는다**

`perf_report.py`의 `build_rows` 안 summary dict 에서 `"capital": capital,` 다음 줄에 아래를 넣는다.

```python
            "use_target": params.use_target,
```

- [ ] **Step 4: `_write_summary` 가 그 줄을 쓰게 한다**

`perf_report.py`의 `_write_summary` 안 `lines` 리스트 마지막 항목
`("승률·평균", "청산완료만으로 계산한다. 미결은 제외"),` 다음 줄에 아래를 넣는다.

```python
        ("목표가 익절", "사용함 (목표가 도달 시 청산)" if s["use_target"]
                      else "사용 안 함 (--use-target 으로 켬)"),
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_perf_report.py -q`
Expected: PASS

- [ ] **Step 6: `perf_report` 에도 `--use-target` 플래그를 단다**

이 줄이 `사용함`이 되려면 CLI 에서 켤 수 있어야 한다. `perf_report.py`의 `main()` 에서
`p.add_argument("--mail", ...)` 다음 줄에 아래를 넣는다.

```python
    p.add_argument("--use-target", action="store_true",
                   help="목표가 도달 시 익절한 결과로 리포트를 낸다")
```

그리고 같은 함수에서 `result = backtest.run(args.history)` 와
`built = build_rows(result, fx, args.capital)` 두 줄을 아래로 바꾼다.

```python
    params = er.Params(use_target=args.use_target)
    result = backtest.run(args.history, params)
```

```python
    built = build_rows(result, fx, args.capital, params=params)
```

- [ ] **Step 7: 플래그가 붙었는지 확인한다**

Run: `python perf_report.py --help`
Expected: 출력에 `--use-target  목표가 도달 시 익절한 결과로 리포트를 낸다` 이 보인다

- [ ] **Step 8: 커밋한다**

```bash
git add perf_report.py tests/test_perf_report.py
git commit -m "State whether the target exit is enabled"
```

---

### Task 7: 기본값이 새지 않았는지 실제 아카이브로 검증한다

**Files:**
- 코드 변경 없음. 검증만 한다.

- [ ] **Step 1: 테스트 전체를 돌린다**

Run: `python -m pytest tests/ -q`
Expected: PASS — 실패 0건. 기존 130개가 하나라도 깨졌다면 `use_target` 기본값이 샌 것이다.

- [ ] **Step 2: 기본 백테스트가 기존과 동일한지 대조한다**

`backtest.py` 는 실행할 때마다 yfinance 에서 봉을 새로 받는다. 시차를 두고 두 번
돌리면 새 세션의 봉이 섞여 코드와 무관한 차이가 난다. 그래서 변경 전 코드를
worktree 로 꺼내 **같은 시점에 두 번** 돌린다. `git stash` 는 쓰지 않는다 —
이 시점에는 모든 변경이 이미 커밋돼 있어 stash 할 것이 없다.

```bash
git log --oneline -8
```

출력에서 `Fix the target price at entry` **바로 앞** 커밋 해시를 찾아 `<BASE>` 에 넣는다.
`<TMP>` 는 이 세션의 스크래치패드 경로다.

```bash
git worktree add "<TMP>/base" <BASE>
python backtest.py > "<TMP>/after.txt"
(cd "<TMP>/base" && python backtest.py) > "<TMP>/before.txt"
diff "<TMP>/before.txt" "<TMP>/after.txt"
git worktree remove "<TMP>/base"
```

Expected: `diff` 출력이 비어 있다. 한 줄이라도 다르면 기본 동작이 바뀐 것이므로
되돌아가 원인을 찾는다. 두 실행 사이에 미국장 새 종가가 확정되면 봉 날짜가
달라질 수 있으니, 차이가 최신 봉 하나에만 국한되는지 먼저 확인한다.

- [ ] **Step 3: 익절을 켠 결과를 뽑아 비교한다**

Run: `python backtest.py --use-target`
Expected: 정상 종료. 청산 사유에 `TARGET` 이 섞여 나오거나, 아직 목표에 닿은 포지션이 없어 기존과 같을 수 있다. 둘 다 정상이다.

- [ ] **Step 4: 리포트를 실제로 생성한다**

Run: `python perf_report.py --out-dir reports`
Expected: `reports/perf_<KST날짜>.xlsx 작성 완료` 출력. 미결포지션 시트 18~21열에 목표(%)·목표가·달성률(%)·위험보상이 채워져 있고, 요약 시트에 `목표가 익절 | 사용 안 함 (--use-target 으로 켬)` 이 있다.

`reports/` 는 `.gitignore` 에 있으므로 **커밋하지 않는다.** 저장소가 public 이라 손익 금액을 영구 이력에 남기지 않는다.

- [ ] **Step 5: 커밋할 것이 남았는지 확인한다**

Run: `git status --short`
Expected: `reports/` 외에 추적되지 않은 변경이 없다. 있으면 앞 태스크에서 커밋을 빠뜨린 것이다.

---

## 완료 기준

- [ ] `python -m pytest tests/ -q` 전부 통과
- [ ] `python backtest.py` 출력이 변경 전과 완전히 동일
- [ ] `python backtest.py --use-target` 이 정상 종료
- [ ] 미결포지션 시트에 목표 컬럼 4개가 보이고, 목표가 없는 행은 빈칸
- [ ] 요약 시트에 익절 on/off 한 줄이 있음
- [ ] `stops.py` 는 변경되지 않음
