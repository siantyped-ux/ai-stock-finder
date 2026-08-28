# ETF 진입·청산 밴드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ETF 트랙의 진입·청산 문턱 사이 밴드를 10점에서 30점으로 넓히고, 앞으로 이 값들을 데이터로 고를 수 있도록 트랙별 파라미터·진입 강화 스위치·노출 비교 도구를 만든다.

**Architecture:** 트랙별 매매 파라미터를 `tracks.py` 에 스칼라로 모으고(`trade_params`), `backtest.py` 는 그것을 기본값으로 읽되 CLI 인자가 언제나 이기는 3단 폴백을 쓴다. 진입 강화는 스캐너(`stock_finder.calc_signal`)가 아니라 매매 계층에서 한다 — 아카이브의 `signal` 열 정의를 고정해야 과거 행을 재현할 수 있다. `perf_report` 는 두 트랙에 같은 `Params` 를 쓰고 있어 함께 배선한다.

**Tech Stack:** Python 3, pytest, dataclasses (frozen), argparse, numpy, yfinance

**설계 문서:** `docs/superpowers/specs/2026-08-28-etf-entry-exit-band-design.md`

---

## 개정 (2026-08-28, Task 1 코드 리뷰 반영)

**`tracks` 의 진입 문턱 키 이름을 `entry_total` 이 아니라 `min_total` 로 한다.**

`backtest.filter_rows` 에 이미 `entry_total` 파라미터가 있는데 그쪽은 문턱을
**내리는** 도구다(그 점수 이상을 BUY 로 승격시키는 비교용). 우리 키는 문턱을
**올린다**. Task 5 의 `resolve_trade_params` 에서 두 이름이 한 함수 안에
들어오므로, 방향이 반대인 동명 식별자 둘이 15줄 안에 공존하게 된다 - 조용히
뒤집힌 필터가 나오는 전형적인 형태다.

새 원칙: **tracks 의 키는 그것이 먹여 주는 소비자 파라미터 이름과 1:1 이다.**

| tracks 키 | 소비자 |
|---|---|
| `min_total` | `backtest.filter_rows(min_total=)` |
| `exit_total` | `exit_rules.Params.exit_total` |
| `stop_atr_mult` | `exit_rules.Params.stop_atr_mult` |
| `trail_atr_mult` | `exit_rules.Params.trail_atr_mult` |

번역 단계가 아예 없어진다. 밴드 언어(진입/청산)는 docstring 에만 둔다.

`filter_rows` 의 기존 `entry_total`(승격)은 **그대로 둔다.** 사용자에게 노출된
`--entry-total` 플래그를 바꾸는 것은 이 계획의 범위 밖이다.

아래 Task 1 본문은 이 개정을 반영해 고쳐 두었다. Task 5·7 도 마찬가지다.

---

## File Structure

| 파일 | 역할 | 변경 |
|---|---|---|
| `tracks.py` | 트랙 정의. 아무것도 임포트하지 않는 정의 모듈 | 매매 파라미터 4종 + `trade_params()` 추가 |
| `backtest.py` | 아카이브 재현 + CLI | `min_total` 필터, 봉 캐시, 3단 폴백, `--compare` |
| `perf_report.py` | 일일 성과 리포트 | `track_params()` 추가, 트랙별 Params 배선 |
| `tests/test_tracks.py` | 트랙 정의 테스트 | 키 집합 갱신 + `trade_params` 테스트 |
| `tests/test_backtest.py` | 백테스트 테스트 | 필터·캐시·폴백·비교 테스트 |
| `tests/test_perf_report.py` | 리포트 테스트 | 트랙별 Params 테스트 |

`stock_finder.py` 는 건드리지 않는다.

---

### Task 1: 트랙별 매매 파라미터

**Files:**
- Modify: `tracks.py:31-49` (TRACKS 딕셔너리), 파일 끝에 함수 추가
- Test: `tests/test_tracks.py:13-16` (키 집합 테스트), 파일 끝에 테스트 추가

- [ ] **Step 1: 기존 키 집합 테스트를 새 키까지 포함하도록 고친다**

`tests/test_tracks.py` 의 `test_every_track_defines_the_same_keys` 를 통째로 교체한다. 이 테스트는 키 집합을 정확히(`==`) 검사하므로 키를 추가하면 반드시 깨진다.

```python
def test_every_track_defines_the_same_keys():
    keys = {"label", "history", "dashboard", "suffix", "max_correlation",
            "min_total", "exit_total", "stop_atr_mult", "trail_atr_mult"}
    for name, spec in tracks.TRACKS.items():
        assert set(spec) == keys, name
```

- [ ] **Step 2: `trade_params` 테스트를 `tests/test_tracks.py` 끝에 추가한다**

```python
def test_trade_params_gives_the_etf_band():
    # 진입 75 / 청산 45. 45 는 임의값이 아니라 calc_signal 의 AVOID 경계다 -
    # "스캔이 이 종목을 회피로 볼 때 나간다" 가 된다.
    p = tracks.trade_params("etf")
    assert p["min_total"] == 75
    assert p["exit_total"] == 45


def test_trade_params_leaves_the_stock_track_at_todays_values():
    # 주식 트랙의 동작은 이번 변경으로 바뀌지 않는다.
    assert tracks.trade_params("stocks") == {
        "min_total": 70, "exit_total": 60,
        "stop_atr_mult": 3.0, "trail_atr_mult": 3.0,
    }


def test_the_etf_band_is_wider_than_the_stock_band():
    etf = tracks.trade_params("etf")
    stocks = tracks.trade_params("stocks")
    assert (etf["min_total"] - etf["exit_total"]
            > stocks["min_total"] - stocks["exit_total"])


def test_the_two_atr_multiples_stay_coupled():
    # 고점이 진입가+1R 에 닿는 순간 트레일 손절선이 정확히 진입가가 되는
    # 본전이동 성질. 한쪽만 바꾸면 파라미터를 늘리지 않고 얻던 성질이 깨진다.
    for key in tracks.TRACKS:
        p = tracks.trade_params(key)
        assert p["stop_atr_mult"] == p["trail_atr_mult"], key


def test_the_atr_multiples_are_unchanged():
    # 7일 표본에서 STOP·TRAIL 이 한 번도 걸리지 않았다. 발동한 적 없는
    # 파라미터를 튜닝하면 효과가 포지션 축소로만 나타나 되돌릴 근거가 안 남는다.
    for key in tracks.TRACKS:
        assert tracks.trade_params(key)["stop_atr_mult"] == 3.0, key


def test_trade_params_hands_back_a_copy():
    # paths() 는 살아 있는 TRACKS 항목을 그대로 준다. 이쪽이 같은 규약을
    # 물려받으면 호출자가 값 하나를 고칠 때 프로세스 전역이 바뀐다.
    tracks.trade_params("etf")["exit_total"] = 999
    assert tracks.TRACKS["etf"]["exit_total"] == 45


def test_trade_params_rejects_an_unknown_track():
    with pytest.raises(ValueError):
        tracks.trade_params("nope")
```

- [ ] **Step 3: 테스트를 돌려 실패를 확인한다**

Run: `python -m pytest tests/test_tracks.py -v`
Expected: FAIL — `test_every_track_defines_the_same_keys` 가 AssertionError, `trade_params` 관련 6건이 `AttributeError: module 'tracks' has no attribute 'trade_params'`

- [ ] **Step 4: `tracks.py` 의 TRACKS 에 파라미터 4종을 넣는다**

`tracks.py` 의 `TRACKS = {...}` 블록을 통째로 교체한다.

```python
TRACKS = {
    "stocks": {
        "label": "미국 주식",
        "history": "history",
        "dashboard": "dashboard_data.js",
        # 대시보드 전역 변수 접미사. 한 페이지가 두 파일을 함께 읽으므로
        # 이름이 같으면 나중에 로드된 쪽이 앞의 것을 덮어쓴다.
        "suffix": "",
        # 1.0 은 검사를 끈다는 뜻이다. 머리말 참조.
        "max_correlation": 1.0,
        # calc_signal 의 BUY 문턱과 같은 값이라 오늘 아카이브에서는 아무것도
        # 거르지 않는다. 구조적으로 무해한 것이 아니라 데이터가 그럴 뿐이다 -
        # total 이 빈 BUY 행이 한 건이라도 생기면 그때부터 거른다.
        "min_total": 70,
        "exit_total": 60,
        "stop_atr_mult": 3.0,
        "trail_atr_mult": 3.0,
    },
    "etf": {
        "label": "ETF",
        "history": "history_etf",
        "dashboard": "dashboard_data_etf.js",
        "suffix": "_ETF",
        "max_correlation": 0.90,
        "min_total": 75,
        "exit_total": 45,
        "stop_atr_mult": 3.0,
        "trail_atr_mult": 3.0,
    },
}
```

- [ ] **Step 5: `tracks.py` 파일 끝에 `trade_params` 를 추가한다**

`max_correlation()` 함수 바로 아래에 붙인다.

```python
def trade_params(track: str) -> dict:
    """그 트랙의 매매 파라미터 4종.

    키 이름은 전부 소비자의 파라미터 이름과 1:1 이다 - min_total 은
    backtest.filter_rows(min_total=), 나머지 셋은 exit_rules.Params 의 같은
    이름 필드로 그대로 들어간다. 번역 단계를 두지 않는 것은 의도다.

    특히 이것을 entry_total 이라 부르지 않는다. backtest.filter_rows 에 이미
    entry_total 이 있는데 그쪽은 문턱을 **내리는**(BUY 로 승격) 비교용
    손잡이라 방향이 정반대다. 같은 이름 둘이 한 함수 안에서 만나면 조용히
    뒤집힌 필터가 나온다.

    exit_rules.Params 를 여기서 만들지 않는 것은 max_correlation 이
    portfolio.Limits 를 만들지 않는 것과 같은 이유다 - tracks 는 아무것도
    임포트하지 않는 정의 모듈이고, 스캐너가 이것을 읽는다. 여기서 매매
    계층을 끌어오면 스캔이 백테스트 전체를 함께 임포트하게 된다.

    min_total 과 exit_total 은 히스테리시스 밴드의 양끝이다. 들어갈 때는
    까다롭게, 한 번 들어가면 웬만해선 안 흔들리게. ETF 가 75/45 로 주식
    70/60 보다 넓다.

    ## 45 의 근거는 약하다

    2026-08-28 실측이 말해 주는 것은 "exit_total 이 실제로 발동하는 유일한
    청산이고 ATR 손절은 7일간 한 번도 안 걸렸다" 까지다. 45 라는 값 자체는
    측정된 것이 아니라 calc_signal 의 AVOID 경계에서 고른 것이다 - exit_rules
    가 total < exit_total 로 판정하므로 45 는 "스캔이 회피로 볼 때 나간다" 와
    정확히 같아진다. 표본은 닫힌 트레이드 1건(SDOG)뿐이다. 아카이브가 30일을
    넘기면 45 와 60 을 나란히 재 볼 것.

    ## 두 ATR 배수는 같이 움직여야 한다

    고점이 진입가+1R 에 닿는 순간 트레일 손절선이 정확히 진입가가 되어
    "1R 도달 시 본전이동" 이 파라미터를 늘리지 않고 나온다. 한쪽만 바꾸면
    이 성질이 깨진다. 3.0 을 유지하는 것은 발동한 적이 없어 튜닝할 근거가
    0이기 때문이다 - 지금 올리면 효과가 포지션 축소로만 나타난다.
    """
    p = paths(track)
    return {k: p[k] for k in ("min_total", "exit_total",
                              "stop_atr_mult", "trail_atr_mult")}
```

- [ ] **Step 6: 테스트를 돌려 통과를 확인한다**

Run: `python -m pytest tests/test_tracks.py -v`
Expected: PASS (전부)

- [ ] **Step 7: 트랙 정의를 읽는 다른 모듈이 안 깨졌는지 확인한다**

Run: `python -m pytest tests/ -q`
Expected: PASS. `stock_finder` 와 `perf_report` 가 `tracks.TRACKS` 를 공유하므로(`test_the_scanner_and_the_report_share_one_definition`) 키 추가가 그쪽을 깨뜨리지 않는지 본다.

- [ ] **Step 8: 커밋**

```bash
git add tracks.py tests/test_tracks.py
git commit -m "Give each track its own trade parameters"
```

---

### Task 2: `filter_rows` 에 진입 강화 필터를 넣는다

기존 `entry_total` 은 그 점수 이상을 BUY 로 **승격**시키는 완화 도구다. 문턱을 **올리는** 대칭 손잡이가 없어서 `--entry-total 80` 을 줘도 진입이 줄지 않았다 (2026-08-28 실측: 후보가 55 -> 62 로 늘었다).

**Files:**
- Modify: `backtest.py:91-134` (`filter_rows`)
- Test: `tests/test_backtest.py` 끝에 추가

- [ ] **Step 1: 실패하는 테스트를 `tests/test_backtest.py` 끝에 추가한다**

```python
def _filter_row(**over):
    row = {"ticker": "X", "date": "2026-01-02", "market": "US",
           "signal": "BUY", "total": "70", "source": "live"}
    row.update(over)
    return row


def test_min_total_demotes_a_buy_below_the_threshold():
    out = bt.filter_rows([_filter_row(total="70")], min_total=75)
    assert out[0]["signal"] == "HOLD"


def test_min_total_keeps_a_buy_at_the_threshold():
    out = bt.filter_rows([_filter_row(total="75")], min_total=75)
    assert out[0]["signal"] == "BUY"


def test_min_total_demotes_a_strong_buy_too():
    # 규칙은 일관돼야 한다. STRONG_BUY 는 정의상 total>=80 이라 실무에서
    # 걸릴 일이 드물지만, 예외를 두면 그 예외가 다음 버그가 된다.
    out = bt.filter_rows([_filter_row(signal="STRONG_BUY", total="70")],
                         min_total=75)
    assert out[0]["signal"] == "HOLD"


def test_min_total_demotes_a_buy_with_no_score():
    # 점수를 모르는 채로 문턱을 통과시키면 문턱이 있으나 마나가 된다.
    out = bt.filter_rows([_filter_row(total="")], min_total=75)
    assert out[0]["signal"] == "HOLD"


def test_min_total_leaves_non_buy_rows_alone():
    out = bt.filter_rows([_filter_row(signal="WATCH", total="60")],
                         min_total=75)
    assert out[0]["signal"] == "WATCH"


def test_demotion_wins_over_promotion():
    # entry_total 로 올린 뒤 min_total 로 내린다. 둘 다 주면 강등이 이긴다 -
    # "N 이상을 BUY 로 보되 M 미만은 버린다" 가 된다.
    out = bt.filter_rows([_filter_row(signal="WATCH", total="65")],
                         entry_total=60, min_total=75)
    assert out[0]["signal"] == "HOLD"


def test_min_total_does_not_mutate_the_input():
    # 같은 아카이브로 여러 케이스를 돌린다. 입력을 바꾸면 두 번째 케이스가
    # 첫 번째의 결과 위에서 돈다.
    rows = [_filter_row(total="70")]
    bt.filter_rows(rows, min_total=75)
    assert rows[0]["signal"] == "BUY"


def test_no_min_total_leaves_everything_alone():
    out = bt.filter_rows([_filter_row(total="70")])
    assert out[0]["signal"] == "BUY"
```

- [ ] **Step 2: 테스트를 돌려 실패를 확인한다**

Run: `python -m pytest tests/test_backtest.py -k min_total -v`
Expected: FAIL — `TypeError: filter_rows() got an unexpected keyword argument 'min_total'`

- [ ] **Step 3: `filter_rows` 를 고친다**

`backtest.py` 의 `filter_rows` 함수를 통째로 교체한다.

```python
def filter_rows(rows: list, us_only: bool = False,
                entry_total: int = None, start_date: str = None,
                min_total: int = None) -> list:
    """아카이브 행을 진입 조건에 맞게 걸러 낸다.

    start_date 는 그 날짜부터만 본다. 07-31~08-21 구간은 66% 가 backfill 이라
    스코어가 미확정 봉 결함에 오염돼 있어, 깨끗한 live 구간부터 다시 세려면
    앞을 잘라야 한다. 자르면 그 이전 BUY 전환도 함께 사라져 진입이 생기지
    않는다 - 의도한 동작이다.

    us_only 는 과거 아카이브에 남아 있는 한국 행을 뺀다. 7/31~8/22 데이터에는
    KR 이 들어 있어서, 미국 단독 성과를 보려면 여기서 빼야 한다.

    entry_total 과 min_total 은 대칭이지만 별개의 손잡이다.

    entry_total 은 그 점수 이상인 행의 signal 을 BUY 로 **올린다**(완화).
    원래 진입 조건은 signal in (BUY, STRONG_BUY) 이고 BUY 정의가
    total>=70 and cons>=3 이라, consensus 를 무시했을 때 성과가 어떻게
    달라지는지 보려는 것이다.

    min_total 은 그 점수 미만인 BUY 를 HOLD 로 **내린다**(강화). 진입 문턱을
    실제로 올리는 유일한 경로다 - stock_finder.calc_signal 의 70/80 을
    건드리면 과거 아카이브(70 기준)와 미래 아카이브(75 기준)의 signal 열
    정의가 갈라져 과거 행을 재현할 수 없게 된다.

    HOLD 로 내리는 이유는 trade_sim.step_entry 가 BUY 로의 **전환**을 보기
    때문이다. 강등하면 그날의 전환이 사라지고, 나중에 총점이 진짜로 문턱을
    넘는 날 HOLD -> BUY 전환이 새로 생겨 그 시점에 진입한다.

    총점이 비어 있는 BUY 도 강등한다. 점수를 모르는 채로 통과시키면 문턱이
    있으나 마나가 된다.

    강등을 승격 뒤에 둔다. 둘 다 주면 강등이 이긴다.

    입력 행을 바꾸지 않는다. 같은 아카이브로 여러 케이스를 돌리기 때문이다.
    """
    out = []
    for r in rows:
        if start_date and r["date"] < start_date:
            continue
        if us_only and r.get("market") != "US":
            continue
        if entry_total is not None:
            total = r.get("total")
            if total not in (None, "") and int(total) >= entry_total:
                r = {**r, "signal": "BUY"}
        if min_total is not None and r["signal"] in ts.BUY_SIGNALS:
            total = r.get("total")
            if total in (None, "") or int(total) < min_total:
                r = {**r, "signal": "HOLD"}
        out.append(r)
    return out
```

- [ ] **Step 4: 테스트를 돌려 통과를 확인한다**

Run: `python -m pytest tests/test_backtest.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add backtest.py tests/test_backtest.py
git commit -m "Add a filter that actually raises the entry bar"
```

---

### Task 3: `run()` 이 `min_total` 을 넘긴다

**Files:**
- Modify: `backtest.py:164-170` (`run` 시그니처), `backtest.py:176-177` (`filter_rows` 호출)
- Test: `tests/test_backtest.py` 끝에 추가

- [ ] **Step 1: 실패하는 테스트를 추가한다**

```python
def _band_bars(dates):
    import exit_rules as er
    return {d: er.Bar(d, 100.0, 101.0, 99.0, 100.0, atr14=2.0, total=None)
            for d in dates}


def test_run_keeps_a_weak_signal_out_of_the_book(monkeypatch):
    rows = [
        {"ticker": "X", "date": "2026-01-02", "market": "US", "signal": "BUY",
         "total": "70", "source": "live"},
    ]
    bars = _band_bars(["2026-01-02", "2026-01-03"])
    monkeypatch.setattr(bt, "load_archive", lambda pattern: rows)
    monkeypatch.setattr(bt, "fetch_bars", lambda ticker: bars)

    assert bt.run("x", min_total=75)["trades"] == []
    assert bt.run("x")["trades"] != []


def test_a_demoted_ticker_enters_when_it_later_clears_the_bar(monkeypatch):
    # 강등은 영구 배제가 아니다. 총점이 진짜로 문턱을 넘는 날 HOLD -> BUY
    # 전환이 새로 생겨 그날 진입한다.
    rows = [
        {"ticker": "X", "date": "2026-01-02", "market": "US", "signal": "BUY",
         "total": "70", "source": "live"},
        {"ticker": "X", "date": "2026-01-05", "market": "US", "signal": "BUY",
         "total": "80", "source": "live"},
    ]
    bars = _band_bars(["2026-01-02", "2026-01-05", "2026-01-06"])
    monkeypatch.setattr(bt, "load_archive", lambda pattern: rows)
    monkeypatch.setattr(bt, "fetch_bars", lambda ticker: bars)

    trades = bt.run("x", min_total=75)["trades"]

    assert [t.entry_date for t in trades] == ["2026-01-05"]
```

- [ ] **Step 2: 테스트를 돌려 실패를 확인한다**

Run: `python -m pytest tests/test_backtest.py -k "weak_signal or clears_the_bar" -v`
Expected: FAIL — `TypeError: run() got an unexpected keyword argument 'min_total'`

- [ ] **Step 3: `run()` 시그니처와 `filter_rows` 호출을 고친다**

`backtest.py` 에서 `def run(...)` 의 시그니처를 이렇게 바꾼다.

```python
def run(pattern: str = "history/*.csv", params: er.Params = None,
        costs: ts.Costs = None, us_only: bool = False,
        entry_total: int = None, limits: pf.Limits = None,
        start_date: str = None, account: sizing.Account = None,
        min_total: int = None) -> dict:
```

그리고 그 안의 `filter_rows` 호출을 이렇게 바꾼다.

```python
    rows = filter_rows(load_archive(pattern), us_only=us_only,
                       entry_total=entry_total, start_date=start_date,
                       min_total=min_total)
```

- [ ] **Step 4: 테스트를 돌려 통과를 확인한다**

Run: `python -m pytest tests/test_backtest.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add backtest.py tests/test_backtest.py
git commit -m "Thread the entry bar through run()"
```

---

### Task 4: 봉 캐시

`--compare` 는 같은 프로세스에서 `run()` 을 두 번 돈다. 캐시가 없으면 yfinance 를 두 번 때린다.

**Files:**
- Modify: `backtest.py:63-89` (`fetch_bars`)
- Test: `tests/test_backtest.py` 끝에 추가

- [ ] **Step 1: 실패하는 테스트를 추가한다**

```python
def test_fetch_bars_caches_a_ticker(monkeypatch):
    import pandas as pd
    calls = []

    class _Stub:
        def __init__(self, ticker):
            calls.append(ticker)

        def history(self, period, auto_adjust):
            return _flat_frame(n=40)

    bt.clear_bars_cache()
    monkeypatch.setattr(bt.yf, "Ticker", _Stub)

    first = bt.fetch_bars("X")
    second = bt.fetch_bars("X")

    assert calls == ["X"]
    assert first is second


def test_fetch_bars_caches_a_failure(monkeypatch):
    # 실패도 캐시하지 않으면 조회가 안 되는 티커를 설정마다 다시 때린다.
    calls = []

    class _Dead:
        def __init__(self, ticker):
            calls.append(ticker)

        def history(self, period, auto_adjust):
            raise RuntimeError("no network")

    bt.clear_bars_cache()
    monkeypatch.setattr(bt.yf, "Ticker", _Dead)

    assert bt.fetch_bars("X") == {}
    assert bt.fetch_bars("X") == {}
    assert calls == ["X"]


def test_clear_bars_cache_empties_it(monkeypatch):
    # 테스트가 서로에게 캐시를 넘기면 안 된다.
    calls = []

    class _Stub:
        def __init__(self, ticker):
            calls.append(ticker)

        def history(self, period, auto_adjust):
            return _flat_frame(n=40)

    bt.clear_bars_cache()
    monkeypatch.setattr(bt.yf, "Ticker", _Stub)

    bt.fetch_bars("X")
    bt.clear_bars_cache()
    bt.fetch_bars("X")

    assert calls == ["X", "X"]
```

- [ ] **Step 2: 테스트를 돌려 실패를 확인한다**

Run: `python -m pytest tests/test_backtest.py -k "bars_cache or caches" -v`
Expected: FAIL — `AttributeError: module 'backtest' has no attribute 'clear_bars_cache'`

- [ ] **Step 3: `fetch_bars` 에 캐시를 붙인다**

`backtest.py` 의 `def fetch_bars(ticker: str) -> dict:` 함수를 통째로 교체하고, 그 **위에** 캐시 딕셔너리와 비우기 함수를 둔다.

```python
# 티커 -> {날짜: Bar}. 프로세스 수명이다.
#
# --compare 가 같은 아카이브를 두 설정으로 돌리기 때문에 필요하다. 캐시가
# 없으면 두 번째 설정이 yfinance 를 통째로 다시 때린다 - 55종목이면 왕복이
# 두 배가 되고, 그 사이 시세가 바뀌면 두 열이 서로 다른 데이터 위에서
# 비교된다. 후자가 진짜 문제다.
_BARS_CACHE: dict = {}


def clear_bars_cache() -> None:
    """캐시를 비운다. 테스트가 서로에게 봉을 넘기지 않도록."""
    _BARS_CACHE.clear()


def fetch_bars(ticker: str) -> dict:
    """티커의 일봉을 날짜 -> exit_rules.Bar 로 반환한다. 실패하면 빈 dict.

    실패(빈 dict)도 캐시한다. 안 그러면 조회가 안 되는 티커를 설정마다 다시
    때리고, 재시도할 때마다 같은 실패를 기다린다.
    """
    if ticker in _BARS_CACHE:
        return _BARS_CACHE[ticker]

    bars = _fetch_bars_uncached(ticker)
    _BARS_CACHE[ticker] = bars
    return bars


def _fetch_bars_uncached(ticker: str) -> dict:
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

- [ ] **Step 4: 테스트를 돌려 통과를 확인한다**

Run: `python -m pytest tests/test_backtest.py -v`
Expected: PASS (전부). 기존 테스트들은 `fetch_bars` 를 monkeypatch 로 통째로 갈아끼우므로 캐시를 타지 않는다.

- [ ] **Step 5: 커밋**

```bash
git add backtest.py tests/test_backtest.py
git commit -m "Cache fetched bars so two settings share one download"
```

---

### Task 5: 3단 폴백과 `--min-total`

현재 argparse 기본값이 `3.0`/`60` 으로 하드코딩돼 있어 "사용자가 명시했는가" 를 구분할 수 없다. 기본값을 `None` 으로 바꾸고 **CLI > 트랙 > 현행 기본값** 순으로 푼다.

**Files:**
- Modify: `backtest.py:350-407` (`main`), 그 위에 상수와 헬퍼 추가
- Test: `tests/test_backtest.py` 끝에 추가

- [ ] **Step 1: 실패하는 테스트를 추가한다**

```python
def _args(**over):
    import argparse
    base = dict(track=None, stop_atr_mult=None, trail_atr_mult=None,
                max_hold_days=60, exit_total=None, min_total=None,
                use_target=False)
    base.update(over)
    return argparse.Namespace(**base)


def test_resolve_falls_back_to_todays_defaults_without_a_track():
    # 트랙을 안 주면 예전 호출과 결과가 같아야 한다.
    params, min_total = bt.resolve_trade_params(_args())
    assert params.stop_atr_mult == 3.0
    assert params.trail_atr_mult == 3.0
    assert params.exit_total == 60
    assert min_total is None


def test_resolve_takes_the_track_defaults():
    params, min_total = bt.resolve_trade_params(_args(track="etf"))
    assert params.exit_total == 45
    assert min_total == 75


def test_an_explicit_argument_beats_the_track():
    # --track 은 기본값만 바꾼다. 명시한 값이 조용히 무시되면 안 된다.
    params, _ = bt.resolve_trade_params(
        _args(track="etf", stop_atr_mult=5.0, exit_total=55))
    assert params.stop_atr_mult == 5.0
    assert params.exit_total == 55


def test_an_explicit_min_total_beats_the_track():
    _, min_total = bt.resolve_trade_params(_args(track="etf", min_total=90))
    assert min_total == 90


def test_resolve_carries_use_target_through():
    params, _ = bt.resolve_trade_params(_args(track="etf", use_target=True))
    assert params.use_target is True


def test_the_stock_track_resolves_to_todays_behaviour():
    # 이번 변경으로 주식 트랙의 동작이 바뀌면 안 된다.
    params, min_total = bt.resolve_trade_params(_args(track="stocks"))
    assert params.exit_total == 60
    assert params.stop_atr_mult == 3.0
    assert min_total == 70
```

- [ ] **Step 2: 테스트를 돌려 실패를 확인한다**

Run: `python -m pytest tests/test_backtest.py -k resolve -v`
Expected: FAIL — `AttributeError: module 'backtest' has no attribute 'resolve_trade_params'`

- [ ] **Step 3: 상수와 `resolve_trade_params` 를 `backtest.py` 에 추가한다**

`def main():` 바로 위에 넣는다.

```python
# 트랙을 지정하지 않았을 때의 값. 현행 동작을 그대로 보존한다.
#
# min_total 이 None 인 것은 의도다 - 트랙 없이 돌리는 기존 호출에 진입
# 문턱을 새로 걸면 예전 결과의 의미가 조용히 바뀐다.
DEFAULT_TRADE_PARAMS = {
    "min_total": None,
    "exit_total": 60,
    "stop_atr_mult": 3.0,
    "trail_atr_mult": 3.0,
}


def resolve_trade_params(args) -> tuple:
    """CLI > 트랙 > 현행 기본값 순으로 매매 파라미터를 푼다.

    (exit_rules.Params, min_total) 을 돌려준다.

    --track 이 기본값만 바꾸고 명시 인자가 언제나 이기는 것은 --history 와
    --max-correlation 에 이미 적용된 규칙이다. 그러지 않으면 트랙을 준 순간
    사용자가 직접 준 값이 조용히 무시된다.

    tracks 의 키 이름은 소비자 파라미터 이름과 1:1 이라 번역이 없다.
    min_total 은 그대로 filter_rows(min_total=) 로 간다.

    주의: args.entry_total 은 여기 있는 min_total 과 **다른 것**이다. 그쪽은
    문턱을 내리는(BUY 로 승격) 비교용 손잡이라 방향이 정반대이고, 이 함수는
    그것을 건드리지 않는다 - 호출자가 run() 에 따로 넘긴다.

    main 이 너무 커서 테스트로 못 잡으므로 함수로 떼어 둔다
    (perf_report.track_limits 와 같은 이유).
    """
    base = (tracks.trade_params(args.track) if args.track
            else dict(DEFAULT_TRADE_PARAMS))

    def pick(cli, key):
        return cli if cli is not None else base[key]

    params = er.Params(
        stop_atr_mult=pick(args.stop_atr_mult, "stop_atr_mult"),
        trail_atr_mult=pick(args.trail_atr_mult, "trail_atr_mult"),
        max_hold_days=args.max_hold_days,
        exit_total=pick(args.exit_total, "exit_total"),
        use_target=args.use_target,
    )
    return params, pick(args.min_total, "min_total")
```

- [ ] **Step 4: argparse 기본값을 `None` 으로 바꾸고 `--min-total` 을 추가한다**

`backtest.py` 의 `main()` 안에서 해당 세 줄을 교체하고 한 줄을 추가한다.

바꿀 것 (기존 358-361 줄 중 셋):

```python
    p.add_argument("--stop-atr-mult", type=float, default=None,
                   help="손절 ATR 배수 (생략하면 --track 값, 없으면 3.0)")
    p.add_argument("--trail-atr-mult", type=float, default=None,
                   help="트레일 ATR 배수 (생략하면 --track 값, 없으면 3.0)")
    p.add_argument("--exit-total", type=int, default=None,
                   help="이 점수 아래로 떨어지면 청산한다\n"
                        "  (생략하면 --track 값, 없으면 60)")
```

`--entry-total` 바로 아래에 추가할 것:

```python
    p.add_argument("--min-total", type=int, default=None,
                   help="이 점수 미만인 BUY 를 버린다 (진입 문턱을 올린다).\n"
                        "  --entry-total 과 대칭이다 - 그쪽은 문턱을 내리고\n"
                        "  이쪽은 올린다. 둘 다 주면 이쪽이 이긴다.\n"
                        "  생략하면 --track 값, --track 도 없으면 안 건다")
```

`--max-hold-days` 는 그대로 `default=60` 을 유지한다. 트랙 파라미터에 없다.

- [ ] **Step 5: `main()` 의 Params 조립부를 교체한다**

`params = er.Params(...)` 블록(기존 395-401 줄)을 이 한 줄로 바꾼다.

```python
    params, min_total = resolve_trade_params(args)
```

그리고 마지막 `report(run(...))` 호출을 이렇게 바꾼다.

```python
    report(run(pattern, params, us_only=args.us_only,
               entry_total=args.entry_total, limits=limits,
               start_date=args.start_date, account=account,
               min_total=min_total))
```

- [ ] **Step 6: 테스트를 돌려 통과를 확인한다**

Run: `python -m pytest tests/test_backtest.py -v`
Expected: PASS (전부)

- [ ] **Step 7: CLI 가 실제로 도는지 확인한다**

Run: `python backtest.py --track etf --capital 10000`
Expected: 정상 종료. 커버리지 머리말이 나오고, `exit_total` 이 45 로 내려갔으므로 이전 실행에 있던 `SDOG ... SIGNAL` 청산이 **사라지고** 미결 포지션 수가 늘어야 한다.

Run: `python backtest.py --help`
Expected: `--min-total` 이 도움말에 보인다.

- [ ] **Step 8: 커밋**

```bash
git add backtest.py tests/test_backtest.py
git commit -m "Let the track set defaults that explicit flags still beat"
```

---

### Task 6: `--compare`

손절 배수를 바꾸면 `sizing.shares` 가 수량을 줄여 종목당 투입이 작아진다. R 만 보면 "손절을 넓혔더니 좋아졌다" 로 읽히지만 실제로는 베팅이 작아진 것이다. 비교표는 노출을 함께 내야 한다.

**Files:**
- Modify: `backtest.py` (`report` 아래에 함수 두 개 추가, `main` 에 분기 추가)
- Test: `tests/test_backtest.py` 끝에 추가

- [ ] **Step 1: 실패하는 테스트를 추가한다**

```python
def _fake_result(entered, qty, net_r, closed=0):
    import trade_sim as ts
    trades = [
        ts.Trade(ticker=f"T{i}", market="US", source="live",
                 entry_date="2026-01-02", entry_price=100.0, r_unit=2.0,
                 exit_date=None, exit_price=None, exit_reason=None,
                 bars_held=1, is_open=True, gross_r=0.0, cost_r=0.0,
                 net_r=0.0, mark_price=100.0, initial_stop=94.0,
                 high_since_entry=100.0, stop=94.0, target_price=None,
                 qty=qty)
        for i in range(entered)
    ]
    return {"trades": trades,
            "summary": {"closed": closed, "total_net_r": net_r,
                        "open_net_r": 0.0}}


def test_compare_row_reports_exposure():
    row = bt.compare_row(_fake_result(2, qty=10, net_r=-1.0),
                         er.Params(stop_atr_mult=3.0))
    assert row["entered"] == 2
    assert row["avg_position"] == 1000.0     # 100.0 x 10주


def test_compare_row_has_no_exposure_without_a_capital_account():
    # qty 는 자본 제약이 있을 때만 채워진다. 없으면 노출을 잴 수 없다.
    row = bt.compare_row(_fake_result(2, qty=None, net_r=-1.0),
                         er.Params())
    assert row["avg_position"] is None


def test_compare_warns_when_the_stop_multiple_differs(capsys):
    a = bt.compare_row(_fake_result(1, qty=10, net_r=-1.0),
                       er.Params(stop_atr_mult=3.0))
    b = bt.compare_row(_fake_result(2, qty=7, net_r=-0.5),
                       er.Params(stop_atr_mult=4.5))

    bt.compare_report(a, b, has_account=True)

    out = capsys.readouterr().out
    assert "stop_atr_mult" in out
    assert "포지션 축소" in out


def test_compare_stays_quiet_when_the_stop_multiple_matches(capsys):
    a = bt.compare_row(_fake_result(1, qty=10, net_r=-1.0), er.Params())
    b = bt.compare_row(_fake_result(2, qty=10, net_r=-0.5), er.Params())

    bt.compare_report(a, b, has_account=True)

    assert "포지션 축소" not in capsys.readouterr().out


def test_compare_refuses_to_guess_without_capital(capsys):
    # --capital 이 없으면 노출을 못 재고 R 의 분모만 바뀐다. 그 두 열은
    # 비교할 수 없으므로 그렇게 말해야 한다.
    a = bt.compare_row(_fake_result(1, qty=None, net_r=-1.0),
                       er.Params(stop_atr_mult=3.0))
    b = bt.compare_row(_fake_result(1, qty=None, net_r=-0.5),
                       er.Params(stop_atr_mult=4.5))

    bt.compare_report(a, b, has_account=False)

    assert "비교할 수 없다" in capsys.readouterr().out
```

테스트 파일 맨 위 `import backtest as bt` 아래에 `import exit_rules as er` 를 추가한다.

- [ ] **Step 2: 테스트를 돌려 실패를 확인한다**

Run: `python -m pytest tests/test_backtest.py -k compare -v`
Expected: FAIL — `AttributeError: module 'backtest' has no attribute 'compare_row'`

- [ ] **Step 3: `compare_row` 와 `compare_report` 를 `backtest.py` 의 `report()` 아래에 추가한다**

```python
def compare_row(result: dict, params: er.Params) -> dict:
    """비교표 한 열. 노출을 반드시 함께 낸다.

    손절 배수를 바꾸면 sizing.shares 가 r_unit 으로 수량을 역산하므로
    종목당 투입이 달라진다. R 만 보면 "손절을 넓혔더니 좋아졌다" 로 읽히지만
    실제로 일어난 일은 베팅이 작아진 것이고, 상승장에서는 대칭으로 덜 번다.
    노출을 옆에 두지 않으면 이 구분이 안 보인다.
    """
    trades = result["trades"]
    invested = [t.entry_price * t.qty for t in trades if t.qty]
    s = result["summary"]
    return {
        "entered": len({t.ticker for t in trades}),
        # qty 는 자본 제약이 있을 때만 채워진다. 없으면 노출을 잴 수 없다.
        "avg_position": sum(invested) / len(invested) if invested else None,
        "closed": s["closed"],
        "net_r": s["total_net_r"] + s["open_net_r"],
        "stop_atr_mult": params.stop_atr_mult,
    }


def compare_report(a: dict, b: dict, has_account: bool) -> None:
    """두 설정을 나란히 찍는다. A 가 기준, B 가 바꾼 쪽이다."""
    def cell(v, fmt):
        return "-" if v is None else format(v, fmt)

    print("=" * 62)
    print(f"{'':<22}{'A(기준)':>18}{'B(변경)':>18}")
    print("-" * 62)
    print(f"{'진입 종목':<22}{a['entered']:>18}{b['entered']:>18}")
    print(f"{'종목당 평균 투입':<22}"
          f"{cell(a['avg_position'], ',.0f'):>18}"
          f"{cell(b['avg_position'], ',.0f'):>18}")
    print(f"{'닫힌 트레이드':<22}{a['closed']:>18}{b['closed']:>18}")
    print(f"{'합계 R (미결 포함)':<22}"
          f"{a['net_r']:>+18.2f}{b['net_r']:>+18.2f}")

    if a["stop_atr_mult"] != b["stop_atr_mult"]:
        print()
        print(f"  !! stop_atr_mult 가 다르다 "
              f"({a['stop_atr_mult']} vs {b['stop_atr_mult']}).")
        if has_account:
            print("     R 차이에 포지션 축소 효과가 섞여 있다. 종목당 투입을")
            print("     함께 볼 것 - 상승장에서는 대칭으로 덜 번다.")
        else:
            print("     --capital 이 없어 노출을 잴 수 없고 R 의 분모만 바뀐다.")
            print("     이 두 열은 비교할 수 없다. --capital 을 주고 다시 돌릴 것.")
    print("=" * 62)
```

- [ ] **Step 4: 테스트를 돌려 통과를 확인한다**

Run: `python -m pytest tests/test_backtest.py -k compare -v`
Expected: PASS

- [ ] **Step 5: `--compare` 옵션과 분기를 `main()` 에 넣는다**

`--min-total` 아래에 옵션을 추가한다.

```python
    p.add_argument("--compare", action="store_true",
                   help="기준값과 지금 준 인자를 나란히 돌려 비교한다.\n"
                        "  기준은 --track 의 값이고, --track 이 없으면\n"
                        "  현행 기본값이다. 바꾼 인자가 하나도 없으면 거부한다")
```

`main()` 마지막의 `report(run(...))` 를 이 블록으로 교체한다.

```python
    kwargs = dict(us_only=args.us_only, entry_total=args.entry_total,
                  limits=limits, start_date=args.start_date, account=account)

    if args.compare:
        base_args = argparse.Namespace(
            track=args.track, stop_atr_mult=None, trail_atr_mult=None,
            max_hold_days=args.max_hold_days, exit_total=None,
            min_total=None, use_target=args.use_target)
        base_params, base_min = resolve_trade_params(base_args)
        if (base_params, base_min) == (params, min_total):
            raise SystemExit(
                "--compare 는 바꿀 값이 있어야 한다. "
                "--stop-atr-mult 같은 인자를 함께 줄 것.")
        a = run(pattern, base_params, min_total=base_min, **kwargs)
        b = run(pattern, params, min_total=min_total, **kwargs)
        compare_report(compare_row(a, base_params),
                       compare_row(b, params),
                       has_account=account is not None)
        return

    report(run(pattern, params, min_total=min_total, **kwargs))
```

- [ ] **Step 6: 비교가 실제로 도는지 확인한다**

Run: `python backtest.py --track etf --capital 10000 --stop-atr-mult 4.5 --trail-atr-mult 4.5 --compare`
Expected: 두 열이 찍히고, B 의 진입 종목이 A 보다 많고 종목당 평균 투입이 작으며, `stop_atr_mult 가 다르다` 경고가 나온다.

Run: `python backtest.py --track etf --capital 10000 --compare`
Expected: `--compare 는 바꿀 값이 있어야 한다` 로 종료 (exit code 1)

- [ ] **Step 7: 커밋**

```bash
git add backtest.py tests/test_backtest.py
git commit -m "Compare two settings with exposure next to R"
```

---

### Task 7: 일일 리포트를 트랙별 파라미터로 배선한다

`perf_report.main` 이 `er.Params(use_target=...)` 하나를 만들어 **두 트랙에 똑같이** 쓰고 있다. 이대로면 ETF 의 넓힌 밴드가 일일 리포트에는 반영되지 않는다 — 실제로 매일 도는 것이 이 경로다.

**Files:**
- Modify: `perf_report.py:491-504` 아래에 `track_params` 추가, `perf_report.py:521` 삭제, `perf_report.py:526-541` (트랙 루프)
- Test: `tests/test_perf_report.py` 끝에 추가

- [ ] **Step 1: 실패하는 테스트를 추가한다**

```python
def test_the_report_uses_the_etf_band():
    # main 이 두 트랙에 같은 Params 를 쓰면 ETF 의 넓힌 밴드가 리포트에
    # 반영되지 않는다. 매일 도는 것이 이 경로다.
    params, min_total = pr.track_params("etf")
    assert params.exit_total == 45
    assert min_total == 75


def test_the_report_leaves_the_stock_track_alone():
    params, min_total = pr.track_params("stocks")
    assert params.exit_total == 60
    assert min_total == 70


def test_the_report_keeps_the_atr_multiples_coupled():
    for key in ("stocks", "etf"):
        params, _ = pr.track_params(key)
        assert params.stop_atr_mult == params.trail_atr_mult, key


def test_the_report_carries_use_target_through():
    assert pr.track_params("etf", use_target=True)[0].use_target is True
    assert pr.track_params("etf")[0].use_target is False


def test_track_params_rejects_an_unknown_track():
    with pytest.raises(ValueError):
        pr.track_params("nope")
```

이 파일은 `perf_report` 를 `pr` 로, `exit_rules` 를 `er` 로 임포트하고 `pytest` 도 이미 들여온다. 새 임포트는 필요 없다.

- [ ] **Step 2: 테스트를 돌려 실패를 확인한다**

Run: `python -m pytest tests/test_perf_report.py -k track_params -v`
Expected: FAIL — `AttributeError: module 'perf_report' has no attribute 'track_params'`

- [ ] **Step 3: `track_params` 를 `perf_report.py` 의 `track_limits` 아래에 추가한다**

```python
def track_params(track: str, use_target: bool = False) -> tuple:
    """그 트랙의 청산 파라미터와 진입 문턱. (er.Params, min_total).

    track_limits 와 같은 이유로 함수로 떼어 둔다 - main 이 너무 커서
    테스트로 못 잡는다.

    두 트랙에 같은 Params 를 쓰면 ETF 의 넓힌 밴드(진입 75 / 청산 45)가
    리포트에 반영되지 않는다. 백테스트 CLI 에서만 보이고 매일 도는 리포트는
    옛 값으로 도는 상태가 되어, 두 산출물이 서로 다른 규칙을 보고하게 된다.

    반환형이 backtest.resolve_trade_params 와 같은 2-튜플인 것은 의도다.
    같은 일을 하는 함수 둘이 서로 다른 모양을 내면 두 호출부가 갈라진다.
    Params 만 돌려주면 호출자가 min_total 을 얻으려고 tracks.trade_params 를
    한 번 더 불러야 한다.

    max_hold_days 는 트랙 파라미터가 아니다. Params 기본값 60 을 쓴다.
    """
    p = tracks.trade_params(track)
    params = er.Params(stop_atr_mult=p["stop_atr_mult"],
                       trail_atr_mult=p["trail_atr_mult"],
                       exit_total=p["exit_total"],
                       use_target=use_target)
    return params, p["min_total"]
```

- [ ] **Step 4: `main()` 의 트랙 루프를 고친다**

`params = er.Params(use_target=args.use_target)` 한 줄을 **삭제**한다 (루프 밖에서 더 이상 쓰이지 않는다).

그리고 트랙 루프를 이렇게 바꾼다.

```python
    # 트랙마다 아카이브가 따로다. 한쪽이 비어도 나머지로 리포트를 낸다 -
    # ETF 아카이브는 2026-08-25 분리 시작이라 주식보다 이력이 짧다.
    by_track = {}
    for key, label in TRACK_SHEETS:
        pattern = tracks.history_glob(key)
        limits = track_limits(key)
        # 청산 규칙과 진입 문턱이 트랙마다 다르다. 하나를 돌려쓰면 ETF 의
        # 넓힌 밴드가 리포트에 반영되지 않는다.
        params, min_total = track_params(key, args.use_target)
        # us_only 로 돌린다. 리포트 금액이 전부 달러라 원화로 호가되는
        # 한국 종목이 섞이면 안 된다 - 아카이브 07-31~08-21 구간에 남아 있다.
        result = backtest.run(pattern, params, us_only=True,
                              start_date=args.start_date, limits=limits,
                              account=sizing.Account(capital=args.capital),
                              min_total=min_total)
        if not result["dates"]:
            print(f"[!] {label}: 아카이브가 비어 있다 ({pattern})")
            by_track[key] = None
            continue
        by_track[key] = build_rows(result, params=params,
                                   start_date=args.start_date)
        print(f"[*] {label}: 트레이드 {len(result['trades'])}건")
```

- [ ] **Step 5: 테스트를 돌려 통과를 확인한다**

Run: `python -m pytest tests/test_perf_report.py -v`
Expected: PASS (전부)

- [ ] **Step 6: 리포트가 실제로 도는지 확인한다**

Run: `python perf_report.py --out-dir "%TEMP%\band-check"`
Expected: `주식: 트레이드 N건` / `ETF: 트레이드 M건` 두 줄이 찍히고 xlsx 가 만들어진다. ETF 쪽 트레이드 수가 이전보다 늘어야 한다 (청산 문턱이 45 로 내려가 SIGNAL 청산이 줄고 미결이 늘어난다).

- [ ] **Step 7: 커밋**

```bash
git add perf_report.py tests/test_perf_report.py
git commit -m "Run each track's report with that track's own rules"
```

---

### Task 8: 전체 테스트와 마무리

- [ ] **Step 1: 전체 테스트를 돌린다**

Run: `python -m pytest tests/ -q`
Expected: PASS (전부). 실패가 있으면 그 테스트가 검증하던 계약이 무엇인지 읽고, 계획이 깨뜨린 것인지 계획이 고쳐야 할 것인지 판단한다 — 통과시키려고 단언을 지우지 말 것.

- [ ] **Step 2: 두 트랙을 실제로 돌려 본다**

Run: `python backtest.py --track stocks --capital 10000`
Expected: 이번 변경 **이전과 같은 결과**. 주식 트랙 값은 바뀌지 않았다.

단, "같다" 는 오늘 아카이브 기준이다. 주식 트랙에 `min_total=70` 이 새로
걸리는데 이것은 `calc_signal` 의 BUY 문턱과 같은 값이라 지금은 아무것도
거르지 않는다 (실측: `history/` 와 `history_etf/` 의 BUY·STRONG_BUY 837행이
전부 total>=70). 구조적 무해가 아니라 데이터가 그럴 뿐이므로, total 이 빈
BUY 행이 생기면 그때부터 결과가 갈린다.

Run: `python backtest.py --track etf --capital 10000`
Expected: 이전 실행에 있던 `SDOG ... SIGNAL ... -0.40R` 청산이 사라지고 미결 포지션이 하나 늘어난다.

- [ ] **Step 3: 설계 문서의 열린 질문을 갱신한다**

`docs/superpowers/specs/2026-08-28-etf-entry-exit-band-design.md` 의 "열린 질문" 에 다음 줄을 추가한다.

```markdown
- **구현 완료 2026-08-28.** 값이 실제로 반영된 경로는 `backtest.py`(CLI)와
  `perf_report.py`(일일 리포트) 둘이다. 아카이브가 30일을 넘기면
  `--compare` 로 `exit_total` 45 와 60 을 나란히 돌려 손실 확대분과
  회복분을 재 볼 것.
```

- [ ] **Step 4: 커밋**

```bash
git add docs/superpowers/specs/2026-08-28-etf-entry-exit-band-design.md
git commit -m "Record where the band values actually take effect"
```

---

## Self-Review 결과

**스펙 커버리지**

| 설계 절 | 담당 태스크 |
|---|---|
| A. tracks.py 트랙별 매매 파라미터 | Task 1 |
| B. 진입 강화는 매매 계층에서 (`min_total`) | Task 2, 3 |
| C. `--compare` 노출 병기 + 봉 캐시 | Task 4, 6 |
| D. `main()` 3단 폴백 | Task 5 |
| E. `exit_total` 만 변경 (ATR 3.0 유지) | Task 1 (값), Task 1 Step 2 (회귀 테스트) |
| 바꾸지 않는 것: `calc_signal` | 어느 태스크도 `stock_finder.py` 를 건드리지 않는다 |
| 바꾸지 않는 것: stop/trail 결합 | Task 1 의 `test_the_two_atr_multiples_stay_coupled` |
| 바꾸지 않는 것: 주식 트랙 | Task 5 의 `test_the_stock_track_resolves_to_todays_behaviour`, Task 8 Step 2 |

**설계에 없던 것을 하나 추가했다:** Task 7 (`perf_report` 배선). 설계 문서를 쓸 때 `perf_report.main` 이 두 트랙에 같은 `Params` 를 쓴다는 것을 확인하지 못했다. 이것을 빼면 매일 도는 리포트는 옛 값으로 돌고 백테스트 CLI 만 새 값으로 돌아, 두 산출물이 서로 다른 규칙을 보고한다.

**이름 일관성:** `tracks.trade_params` 는 dict 를 내고 `perf_report.track_params` 는 `er.Params` 를 낸다. 이름이 비슷하고 반환형이 달라 헷갈릴 수 있으나, 각각 `tracks.max_correlation`(스칼라)과 `perf_report.track_limits`(`pf.Limits`)의 기존 짝을 그대로 따른 것이다. 두 함수의 docstring 이 이 관계를 명시한다.
