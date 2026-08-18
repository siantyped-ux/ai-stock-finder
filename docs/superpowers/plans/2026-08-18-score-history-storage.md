# 스캔 스코어 이력 적재 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 매일 스캔하는 전체 유니버스의 스코어와 가격 스냅샷을 일별 CSV로 영구 보존하고, git에 남은 과거 스냅샷을 소급 적재한다.

**Architecture:** `history.py`가 이력 행의 스키마·필드 산출·CSV 기록을 단독으로 소유한다. `stock_finder.py`는 스캔 결과를 넘기기만 하고, `backfill_history.py`는 git 스냅샷을 파싱해 같은 모듈로 기록한다. 손상된 스캔이 이력에 유입되지 않도록 완결성 가드 뒤에서만 기록한다.

**Tech Stack:** Python 3.11, pandas (yfinance 의존), numpy, pytest, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-08-18-score-history-storage-design.md`

---

## File Structure

| 파일 | 책임 |
|---|---|
| `history.py` (신규) | 23열 스키마 상수, KST 시각, 가격 필드 산출(ATR·평균거래량), CSV 기록 |
| `backfill_history.py` (신규) | git 스냅샷 파싱, 손상본 판정, 과거 가격 재조회, 소급 기록 |
| `tests/test_history.py` (신규) | `history.py` 왕복·가격 필드 테스트 |
| `tests/test_backfill.py` (신규) | 손상본 판정·룩어헤드 방지 테스트 |
| `tests/test_scan_guard.py` (신규) | 완결성 가드 순수 함수 테스트 |
| `stock_finder.py` (수정) | `_scan_one` 반환값, 가드 함수 분리, 가드 뒤 기록 호출 |
| `.github/workflows/scan.yml` (수정) | `git add`에 `history/` 추가 |
| `requirements.txt` (수정) | `pytest` 추가 |
| `conftest.py` (신규) | 프로젝트 루트를 테스트 임포트 경로에 추가 |

`history.py`가 ATR을 소유하는 이유: `stock_finder.py`에 두면 `backfill_history.py` → `stock_finder` → `history` 순환 임포트가 생긴다.

---

## Task 1: pytest 도입

테스트가 프로젝트 루트의 `history.py`·`stock_finder.py`를 임포트해야 한다. pytest는 rootdir을 자동으로 `sys.path`에 넣지 않으므로 루트 `conftest.py`로 명시한다. 이게 없으면 `pytest`로 실행할 때 `ModuleNotFoundError`가 난다.

**Files:**
- Modify: `requirements.txt`
- Create: `conftest.py`

- [ ] **Step 1: requirements.txt에 pytest 추가**

`requirements.txt` 전체를 다음으로 교체한다:

```
yfinance>=0.2.30
numpy>=1.24
requests>=2.28
pykrx>=1.0
pytest>=8.0
```

- [ ] **Step 2: 루트 conftest.py 생성**

`conftest.py` 신규 생성:

```python
"""pytest가 프로젝트 루트를 임포트 경로에 넣도록 한다.

테스트는 루트의 history.py / stock_finder.py 를 임포트한다.
이 파일이 없으면 `pytest` 실행 시 ModuleNotFoundError가 난다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
```

- [ ] **Step 3: 설치 및 확인**

Run: `python -m pip install -r requirements.txt && python -m pytest --version`
Expected: `pytest 8.x.x` 출력

- [ ] **Step 4: 커밋**

```bash
git add requirements.txt conftest.py
git commit -m "Add pytest and put the project root on the test import path"
```

---

## Task 2: history.py 스키마와 CSV 기록

**Files:**
- Create: `history.py`
- Test: `tests/test_history.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_history.py` 신규 생성:

```python
import csv
from datetime import datetime, timedelta, timezone

import history


KST = timezone(timedelta(hours=9))


def _row(**over):
    row = {
        "ticker": "NVDA", "name": "NVIDIA", "market": "US", "sector": "반도체",
        "bar_date": "2026-08-17", "close": 183.22, "volume": 41203000,
        "avg_vol20": 38500000.0, "atr14": 4.81, "market_cap": 4500000000000,
        "tech": 72, "macro": 65, "filing": 71, "value": 58, "total": 68,
        "consensus": 2, "signal": "WATCH", "ev": 0.66, "target": 8,
        "hitl": False, "source": "live",
    }
    row.update(over)
    return row


def test_write_snapshot_roundtrip(tmp_path):
    scan_ts = datetime(2026, 8, 19, 6, 0, 30, tzinfo=KST)
    rows = [_row(), _row(ticker="005930.KS", market="KR", signal="BUY")]

    path = history.write_snapshot(rows, scan_ts, out_dir=tmp_path)

    assert path.name == "2026-08-19.csv"

    with open(path, encoding="utf-8", newline="") as f:
        got = list(csv.DictReader(f))

    assert list(got[0].keys()) == list(history.FIELDS)
    assert len(got) == 2
    assert got[0]["ticker"] == "NVDA"
    assert got[0]["date"] == "2026-08-19"
    assert got[0]["scan_ts_kst"] == "2026-08-19T06:00:30+09:00"
    assert got[0]["close"] == "183.22"
    assert got[0]["hitl"] == "False"
    assert got[1]["ticker"] == "005930.KS"


def test_write_snapshot_missing_price_fields_are_blank(tmp_path):
    scan_ts = datetime(2026, 8, 19, 6, 0, 30, tzinfo=KST)
    rows = [_row(close=None, atr14=None, market_cap=None)]

    path = history.write_snapshot(rows, scan_ts, out_dir=tmp_path)

    with open(path, encoding="utf-8", newline="") as f:
        got = list(csv.DictReader(f))

    assert got[0]["close"] == ""
    assert got[0]["atr14"] == ""
    assert got[0]["market_cap"] == ""
    assert got[0]["ticker"] == "NVDA"


def test_write_snapshot_rejects_unknown_field(tmp_path):
    scan_ts = datetime(2026, 8, 19, 6, 0, 30, tzinfo=KST)
    rows = [_row(bogus=1)]

    try:
        history.write_snapshot(rows, scan_ts, out_dir=tmp_path)
    except ValueError as e:
        assert "bogus" in str(e)
    else:
        raise AssertionError("ValueError가 발생해야 한다")


def test_kst_now_has_offset():
    now = history.kst_now()
    assert now.utcoffset() == timedelta(hours=9)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_history.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'history'`

- [ ] **Step 3: history.py 구현**

`history.py` 신규 생성:

```python
"""스캔 스코어 이력 적재.

일별 CSV 한 파일에 전체 유니버스의 스코어와 가격 스냅샷을 기록한다.
날짜 관련 열은 모두 KST 기준이며, bar_date만 거래소 세션 날짜를 그대로 쓴다.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

KST = timezone(timedelta(hours=9))

# 열 순서 고정. 변경 시 기존 CSV와 호환이 깨지므로 끝에만 추가할 것.
FIELDS = (
    "scan_ts_kst", "date", "ticker", "name", "market", "sector",
    "bar_date", "close", "volume", "avg_vol20", "atr14", "market_cap",
    "tech", "macro", "filing", "value", "total", "consensus", "signal",
    "ev", "target", "hitl", "source",
)

# write_snapshot이 채우므로 호출자가 넘기지 않는 열
_AUTO_FIELDS = ("scan_ts_kst", "date")
_ROW_FIELDS = tuple(f for f in FIELDS if f not in _AUTO_FIELDS)


def kst_now() -> datetime:
    """현재 시각을 KST aware datetime으로 반환.

    CI 러너는 UTC, 개발 머신은 KST라서 naive datetime.now()는 환경마다
    다른 값을 낸다. 항상 명시 변환한다.
    """
    return datetime.now(timezone.utc).astimezone(KST)


def write_snapshot(rows: list[dict], scan_ts: datetime, out_dir="history") -> Path:
    """이력 행들을 history/<KST날짜>.csv 로 기록하고 경로를 반환한다.

    같은 KST 날짜에 다시 호출되면 덮어쓴다.
    """
    scan_kst = scan_ts.astimezone(KST)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{scan_kst:%Y-%m-%d}.csv"

    auto = {
        "scan_ts_kst": scan_kst.isoformat(),
        "date": f"{scan_kst:%Y-%m-%d}",
    }

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            unknown = set(row) - set(_ROW_FIELDS)
            if unknown:
                raise ValueError(f"알 수 없는 열: {sorted(unknown)}")
            out = dict(auto)
            for field in _ROW_FIELDS:
                value = row.get(field)
                out[field] = "" if value is None else value
            writer.writerow(out)

    return path
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_history.py -v`
Expected: 4 passed

- [ ] **Step 5: 커밋**

```bash
git add history.py tests/test_history.py
git commit -m "Add history module with fixed 23-column CSV schema"
```

---

## Task 3: 가격 필드 산출 (ATR·평균거래량)

**Files:**
- Modify: `history.py`
- Modify: `tests/test_history.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_history.py` 끝에 추가한다. 파일 상단 import에 `import pandas as pd`를 추가한다:

```python
def _hist_df(n=60):
    """등차로 오르는 합성 일봉. high-low = 2.0 고정이라 ATR이 정확히 2.0이 된다."""
    idx = pd.date_range("2026-06-01", periods=n, freq="D")
    close = [100.0 + i for i in range(n)]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [c + 1.0 for c in close],
            "Low": [c - 1.0 for c in close],
            "Close": close,
            "Volume": [1000 + i for i in range(n)],
        },
        index=idx,
    )


def test_price_fields_basic():
    df = _hist_df()
    got = history.price_fields(df, {"marketCap": 123456})

    assert got["bar_date"] == "2026-07-30"
    assert got["close"] == 159.0
    assert got["volume"] == 1059
    assert got["market_cap"] == 123456
    assert got["avg_vol20"] == round(sum(range(1040, 1060)) / 20, 2)


def test_price_fields_atr_is_true_range_average():
    df = _hist_df()
    got = history.price_fields(df, {})
    # high-low = 2.0, 전일종가 대비 갭 1.0 -> TR = max(2.0, 2.0, 0.0) = 2.0
    assert got["atr14"] == 2.0


def test_price_fields_short_history_returns_none_for_indicators():
    df = _hist_df(n=5)
    got = history.price_fields(df, {})

    assert got["close"] == 104.0        # 종가는 있음
    assert got["atr14"] is None         # 14봉 미만
    assert got["avg_vol20"] is None     # 20봉 미만


def test_price_fields_no_info():
    df = _hist_df()
    got = history.price_fields(df, None)
    assert got["market_cap"] is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_history.py -v`
Expected: FAIL — `AttributeError: module 'history' has no attribute 'price_fields'`

- [ ] **Step 3: price_fields 구현**

`history.py`의 `write_snapshot` 위에 추가한다. 파일 상단 import에 `import numpy as np`를 추가한다:

```python
def _atr(high, low, close, period: int = 14) -> Optional[float]:
    """단순평균 ATR. 봉 수가 period+1 미만이면 None."""
    if len(close) < period + 1:
        return None
    prev_close = close[:-1]
    tr = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - prev_close),
        np.abs(low[1:] - prev_close),
    ])
    return round(float(np.mean(tr[-period:])), 4)


def price_fields(hist_df, info: Optional[dict] = None) -> dict:
    """이력 행의 가격 관련 필드를 산출한다.

    스캔이 이미 받아온 데이터에서 뽑으므로 API 호출이 늘지 않는다.
    산출 불가한 값은 None으로 두고 호출자가 그대로 넘긴다.
    """
    info = info or {}
    close = hist_df["Close"].values.astype(float)
    high = hist_df["High"].values.astype(float)
    low = hist_df["Low"].values.astype(float)
    volume = hist_df["Volume"].values.astype(float)

    last_index = hist_df.index[-1]
    avg_vol20 = round(float(np.mean(volume[-20:])), 2) if len(volume) >= 20 else None

    return {
        "bar_date": f"{last_index:%Y-%m-%d}",
        "close": round(float(close[-1]), 4),
        "volume": int(volume[-1]),
        "avg_vol20": avg_vol20,
        "atr14": _atr(high, low, close),
        "market_cap": info.get("marketCap"),
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_history.py -v`
Expected: 8 passed

- [ ] **Step 5: 커밋**

```bash
git add history.py tests/test_history.py
git commit -m "Compute ATR, average volume and price fields for history rows"
```

---

## Task 4: 완결성 가드를 순수 함수로 분리

현재 가드는 `main()` 안에 인라인으로 있어 테스트할 수 없다. 순수 함수로 빼서 이력 기록이 가드 뒤에 온다는 사실을 테스트로 고정한다.

**Files:**
- Modify: `stock_finder.py` (가드 블록)
- Test: `tests/test_scan_guard.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_scan_guard.py` 신규 생성:

```python
import stock_finder


def test_guard_rejects_the_773_of_1103_regression():
    # 2026-08-18 실제 사고: 429 rate limit으로 322종목 유실
    assert stock_finder.is_scan_complete(773, 1103, 0.90) is False


def test_guard_accepts_normal_run():
    assert stock_finder.is_scan_complete(1087, 1103, 0.90) is True


def test_guard_accepts_exactly_at_threshold():
    assert stock_finder.is_scan_complete(90, 100, 0.90) is True


def test_guard_rejects_empty_universe():
    assert stock_finder.is_scan_complete(0, 0, 0.90) is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_scan_guard.py -v`
Expected: FAIL — `AttributeError: module 'stock_finder' has no attribute 'is_scan_complete'`

- [ ] **Step 3: 함수 추가**

`stock_finder.py`에서 `def fetch_stock(` 정의 바로 위에 추가한다:

```python
def is_scan_complete(n_collected: int, n_total: int, min_rate: float) -> bool:
    """수집률이 기준 이상인지. 유니버스가 비면 False."""
    if n_total <= 0:
        return False
    return (n_collected / n_total) >= min_rate
```

- [ ] **Step 4: main()의 가드가 이 함수를 쓰도록 교체**

`stock_finder.py`에서 다음 블록을 찾아

```python
    # 완결성 가드 - rate limit 등으로 대량 유실된 결과를 배포하지 않도록 차단
    ok_rate = len(results) / total_n if total_n else 0
    print(f"[*] 수집 {len(results)}/{total_n}종목 ({ok_rate*100:.1f}%)")
    if ok_rate < args.min_success:
        print(f"[!] 수집률 {ok_rate*100:.1f}% < 기준 {args.min_success*100:.0f}% · "
              f"결과를 저장하지 않고 실패 처리합니다 (--workers 를 낮추세요)")
        sys.exit(1)
```

다음으로 교체한다:

```python
    # 완결성 가드 - rate limit 등으로 대량 유실된 결과를 배포하지 않도록 차단
    ok_rate = len(results) / total_n if total_n else 0
    print(f"[*] 수집 {len(results)}/{total_n}종목 ({ok_rate*100:.1f}%)")
    if not is_scan_complete(len(results), total_n, args.min_success):
        print(f"[!] 수집률 {ok_rate*100:.1f}% < 기준 {args.min_success*100:.0f}% · "
              f"결과를 저장하지 않고 실패 처리합니다 (--workers 를 낮추세요)")
        sys.exit(1)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/ -v`
Expected: 12 passed

- [ ] **Step 6: 커밋**

```bash
git add stock_finder.py tests/test_scan_guard.py
git commit -m "Extract completeness guard into a testable function"
```

---

## Task 5: 스캔에서 이력 행 수집

`_scan_one`이 대시보드 행과 이력 행을 함께 반환하도록 바꾼다. 대시보드 페이로드는 변하지 않는다.

**Files:**
- Modify: `stock_finder.py`

- [ ] **Step 1: history 임포트 추가**

`stock_finder.py` 상단 import 블록에서 `from typing import Optional` 다음 줄에 추가한다:

```python
import history
```

- [ ] **Step 2: `_scan_one`이 튜플을 반환하도록 수정**

`_scan_one` 안의 `return {` 로 시작하는 블록을 찾아 다음으로 교체한다:

```python
            dash_row = {
                "t": ticker, "n": name, "m": market, "sec": sector,
                "tech": tech, "macro": macro, "filing": filing, "value": value,
                "total": total, "consensus": cons, "signal": signal,
                "ev": ev, "target": target, "hitl": hitl,
                "reasons": {
                    "tech": tech_r, "macro": macro_r,
                    "filing": filing_r, "value": value_r,
                }
            }
            hist_row = {
                "ticker": ticker, "name": name, "market": market, "sector": sector,
                "tech": tech, "macro": macro, "filing": filing, "value": value,
                "total": total, "consensus": cons, "signal": signal,
                "ev": ev, "target": target, "hitl": hitl,
                "source": "live",
                **history.price_fields(hist, info),
            }
            return dash_row, hist_row
```

- [ ] **Step 3: 수집부가 튜플을 풀도록 수정**

`for fut in as_completed(futures):` 블록에서 다음을 찾아

```python
            with state_lock:
                state["done"] += 1
                done = state["done"]
                if res:
                    collected.append((i, res))
```

다음으로 교체한다:

```python
            with state_lock:
                state["done"] += 1
                done = state["done"]
                if res:
                    collected.append((i, res[0]))
                    hist_rows.append((i, res[1]))
```

- [ ] **Step 4: hist_rows 선언 추가**

`collected = []` 로 시작하는 줄 바로 다음에 추가한다:

```python
    hist_rows = []   # (universe 인덱스, 이력 행)
```

- [ ] **Step 5: 정렬부에 이력 행 정렬 추가**

다음 줄을 찾아

```python
    results = [r for _, r in sorted(collected, key=lambda x: x[0])]
```

다음으로 교체한다:

```python
    results = [r for _, r in sorted(collected, key=lambda x: x[0])]
    history_rows = [r for _, r in sorted(hist_rows, key=lambda x: x[0])]
```

- [ ] **Step 6: 구문 확인**

Run: `python -c "import ast, io; ast.parse(io.open('stock_finder.py', encoding='utf-8').read()); print('AST OK')"`
Expected: `AST OK`

- [ ] **Step 7: 커밋**

```bash
git add stock_finder.py
git commit -m "Carry history rows alongside dashboard rows in the scan loop"
```

---

## Task 6: 가드 뒤에서 이력 기록

**Files:**
- Modify: `stock_finder.py`

- [ ] **Step 1: 가드 직후에 기록 호출 추가**

Task 4에서 수정한 가드 블록의 `sys.exit(1)` 다음 줄에 추가한다. `output_path = os.path.join(` 줄보다 **위**여야 한다:

```python
    # 이력 적재 - 가드를 통과한 결과만 기록한다
    try:
        hist_path = history.write_snapshot(history_rows, scan_started_kst)
        print(f"[*] 이력 기록: {hist_path} ({len(history_rows)}행)")
    except Exception as e:
        print(f"[!] 이력 기록 실패: {e}")
        sys.exit(1)
```

- [ ] **Step 2: 스캔 시작 시각 기록 추가**

`start_time = time.time()` 줄을 찾아 다음으로 교체한다:

```python
    start_time = time.time()
    scan_started_kst = history.kst_now()
```

- [ ] **Step 3: 소규모 실전 검증**

`dashboard_data.js`가 덮어써지므로 먼저 백업한다:

```bash
cp dashboard_data.js /tmp/dashboard_data.js.bak
python stock_finder.py --test --workers 4 --sleep 0.1
```

Expected: `[*] 이력 기록: history\<오늘 KST 날짜>.csv (34행)` 출력

- [ ] **Step 4: CSV 내용 확인**

```bash
python -c "
import csv, glob
p = sorted(glob.glob('history/*.csv'))[-1]
rows = list(csv.DictReader(open(p, encoding='utf-8')))
print('파일:', p, '· 행수:', len(rows))
print('열수:', len(rows[0]))
print({k: rows[0][k] for k in ('scan_ts_kst','date','ticker','bar_date','close','atr14','market_cap','source')})
"
```
Expected: 행수 34, 열수 23, `source=live`이고 `close`·`atr14`·`market_cap`이 채워져 있음

- [ ] **Step 5: dashboard_data.js 복원**

```bash
cp /tmp/dashboard_data.js.bak dashboard_data.js
git diff --stat dashboard_data.js
```
Expected: 출력 없음 (복원 성공)

- [ ] **Step 6: 커밋**

```bash
git add stock_finder.py history/
git commit -m "Write the score history snapshot after the completeness guard"
```

---

## Task 7: 소급 적재 — 손상 스냅샷 판정

**Files:**
- Create: `backfill_history.py`
- Test: `tests/test_backfill.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backfill.py` 신규 생성:

```python
import backfill_history as bf


def _snap(date, count, sha):
    return {"date": date, "count": count, "sha": sha, "ts": date + "T03:00:00"}


def test_drops_snapshots_below_half_the_median():
    # 실제 git 이력의 종목 수. 3 / 133 / 773 이 손상본이다.
    snaps = [
        _snap("2026-07-31", 3, "a"),
        _snap("2026-07-31", 1064, "b"),
        _snap("2026-08-01", 1061, "c"),
        _snap("2026-08-02", 1061, "d"),
        _snap("2026-08-03", 133, "e"),
        _snap("2026-08-04", 1071, "f"),
        _snap("2026-08-17", 1093, "g"),
        _snap("2026-08-18", 1091, "h"),
        _snap("2026-08-18", 773, "i"),
        _snap("2026-08-18", 1087, "j"),
    ]

    kept = bf.drop_corrupt(snaps)
    kept_shas = {s["sha"] for s in kept}

    assert "a" not in kept_shas   # 3종목
    assert "e" not in kept_shas   # 133종목
    assert "i" not in kept_shas   # 773종목
    assert "b" in kept_shas
    assert "j" in kept_shas


def test_dedup_keeps_latest_per_date():
    snaps = [
        _snap("2026-08-18", 1091, "h"),
        _snap("2026-08-18", 1087, "j"),
    ]
    snaps[1]["ts"] = "2026-08-18T10:54:00"
    snaps[0]["ts"] = "2026-08-18T09:20:00"

    picked = bf.dedup_by_date(snaps)

    assert len(picked) == 1
    assert picked[0]["sha"] == "j"


def test_corrupt_run_does_not_shadow_good_snapshot_same_day():
    # 2026-08-18 실제 순서: 1091 -> 773(손상) -> 1087
    # 손상본 제거를 먼저 해야 1087이 살아남는다.
    snaps = [
        _snap("2026-08-17", 1093, "g"),
        _snap("2026-08-18", 1091, "h"),
        _snap("2026-08-18", 1087, "j"),
        _snap("2026-08-18", 773, "corrupt"),
    ]
    snaps[1]["ts"] = "2026-08-18T09:20:00"
    snaps[2]["ts"] = "2026-08-18T10:54:00"
    snaps[3]["ts"] = "2026-08-18T11:30:00"   # 손상본이 가장 늦음

    picked = bf.dedup_by_date(bf.drop_corrupt(snaps))
    by_date = {s["date"]: s["sha"] for s in picked}

    assert by_date["2026-08-18"] == "j"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backfill_history'`

- [ ] **Step 3: backfill_history.py 골격 구현**

`backfill_history.py` 신규 생성:

```python
"""git에 남은 dashboard_data.js 스냅샷을 이력 CSV로 소급 적재한다.

1회성 스크립트. 이미 존재하는 history/*.csv는 건드리지 않는다.
"""
from __future__ import annotations

import json
import re
import statistics
import subprocess
from datetime import datetime, timedelta, timezone

import history

KST = history.KST
CORRUPT_RATIO = 0.8   # 중앙값 대비 이 비율 미만이면 손상으로 본다


def drop_corrupt(snaps: list[dict]) -> list[dict]:
    """종목 수가 전체 중앙값의 CORRUPT_RATIO(80%) 미만인 스냅샷을 제거한다."""
    if not snaps:
        return []
    median = statistics.median(s["count"] for s in snaps)
    floor = median * CORRUPT_RATIO
    return [s for s in snaps if s["count"] >= floor]


def dedup_by_date(snaps: list[dict]) -> list[dict]:
    """같은 KST 날짜에 여러 스냅샷이 있으면 가장 늦은 것만 남긴다.

    반드시 drop_corrupt 뒤에 호출할 것. 순서를 바꾸면 그날 마지막 실행이
    손상본일 때 정상 스냅샷이 가려진다.
    """
    latest: dict[str, dict] = {}
    for s in snaps:
        cur = latest.get(s["date"])
        if cur is None or s["ts"] > cur["ts"]:
            latest[s["date"]] = s
    return [latest[d] for d in sorted(latest)]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_backfill.py -v`
Expected: 3 passed (Task 9에서 4건 추가되어 최종 7건)

- [ ] **Step 5: 커밋**

```bash
git add backfill_history.py tests/test_backfill.py
git commit -m "Add corrupt-snapshot filtering for history backfill"
```

---

## Task 8: 소급 적재 — git 스냅샷 파싱

**Files:**
- Modify: `backfill_history.py`

- [ ] **Step 1: 파싱 함수 추가**

`backfill_history.py`의 `drop_corrupt` 위에 추가한다:

```python
def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def load_snapshots() -> list[dict]:
    """git 이력의 dashboard_data.js 스냅샷을 모두 읽어온다.

    generated_at은 CI 러너가 만든 naive UTC 값이므로 UTC로 간주해 KST로 변환한다.
    """
    shas = _git("log", "--format=%H", "--", "dashboard_data.js").decode().split()
    snaps = []
    for sha in shas:
        blob = _git("show", f"{sha}:dashboard_data.js").decode("utf-8", "replace")

        m = re.search(r'window\.LIVE_STOCKS = (\[.*\]);', blob, re.S)
        if not m:
            continue
        try:
            stocks = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue

        g = re.search(r'generated_at: "([^"]+)"', blob)
        if not g:
            continue
        naive = datetime.fromisoformat(g.group(1))
        ts_kst = naive.replace(tzinfo=timezone.utc).astimezone(KST)

        snaps.append({
            "sha": sha,
            "ts": ts_kst.isoformat(),
            "date": f"{ts_kst:%Y-%m-%d}",
            "count": len(stocks),
            "stocks": stocks,
            "scan_ts": ts_kst,
        })
    return snaps
```

- [ ] **Step 2: 실제 git 이력으로 확인**

```bash
python -c "
import backfill_history as bf
snaps = bf.load_snapshots()
print('전체 스냅샷:', len(snaps))
kept = bf.dedup_by_date(bf.drop_corrupt(snaps))
print('사용할 날짜:', len(kept))
dropped = {s['sha'] for s in snaps} - {s['sha'] for s in bf.drop_corrupt(snaps)}
print('손상 제외:', sorted((s['date'], s['count']) for s in snaps if s['sha'] in dropped))
"
```
Expected: 손상 제외에 `('2026-07-31', 3)`, `('2026-08-03', 133)`, `('2026-08-18', 773)` 3건이 나옴

- [ ] **Step 3: 커밋**

```bash
git add backfill_history.py
git commit -m "Parse dashboard snapshots out of git history"
```

---

## Task 9: 소급 적재 — 과거 가격 채우기와 실행

**Files:**
- Modify: `backfill_history.py`
- Modify: `tests/test_backfill.py`

- [ ] **Step 1: 룩어헤드 방지 테스트 작성**

`tests/test_backfill.py` 끝에 추가한다. 파일 상단에 `import pandas as pd`를 추가한다:

```python
def test_bar_limit_is_the_day_before_the_scan():
    # KST 8/19 06:00 스캔이 볼 수 있었던 마지막 봉은 8/18 세션이다.
    assert bf.bar_limit_for("2026-08-19") == "2026-08-18"


def test_slice_to_date_excludes_future_bars():
    idx = pd.date_range("2026-08-14", periods=5, freq="D")   # 14,15,16,17,18
    df = pd.DataFrame(
        {"Open": range(5), "High": range(5), "Low": range(5),
         "Close": range(5), "Volume": range(5)},
        index=idx,
    )

    sliced = bf.slice_to_date(df, bf.bar_limit_for("2026-08-17"))

    # 8/17 스캔 -> 8/16 봉까지만. 8/17·8/18 봉은 그 시점에 없었다.
    assert len(sliced) == 3
    assert f"{sliced.index[-1]:%Y-%m-%d}" == "2026-08-16"


def test_slice_to_date_returns_none_when_all_bars_are_future():
    idx = pd.date_range("2026-08-20", periods=3, freq="D")
    df = pd.DataFrame(
        {"Open": range(3), "High": range(3), "Low": range(3),
         "Close": range(3), "Volume": range(3)},
        index=idx,
    )

    assert bf.slice_to_date(df, bf.bar_limit_for("2026-08-17")) is None


def test_slice_to_date_handles_empty_input():
    assert bf.slice_to_date(None, "2026-08-17") is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_backfill.py -v`
Expected: FAIL — `AttributeError: module 'backfill_history' has no attribute 'bar_limit_for'`

- [ ] **Step 3: 가격 조회와 메인 로직 추가**

`backfill_history.py` 끝에 추가한다. 파일 상단 import에 다음을 추가한다:

```python
import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yfinance as yf
```

본문:

```python
def fetch_history_only(ticker: str, retries: int = 4):
    """1년 일봉만 조회한다. info는 받지 않아 정규 스캔보다 빠르다."""
    backoff = 5.0
    for attempt in range(retries):
        try:
            df = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
            return df if not df.empty else None
        except Exception as e:
            msg = str(e)
            if ("Too Many Requests" in msg or "Rate limited" in msg or "429" in msg) \
                    and attempt < retries - 1:
                time.sleep(backoff)
                backoff *= 2.2
                continue
            return None
    return None


def bar_limit_for(scan_date: str) -> str:
    """스캔이 실제로 볼 수 있었던 마지막 봉 날짜(= 스캔 KST 날짜 - 1일).

    룩어헤드 방지용이다. KST 06:00 스캔 시점에 미국 세션은 전일(ET) 종가가
    마지막이고, 한국 세션도 전일 종가가 마지막이다. 스캔 당일 날짜의 봉은
    그 시점에 아직 존재하지 않았거나 미완성이므로 써서는 안 된다.
    """
    d = datetime.strptime(scan_date, "%Y-%m-%d") - timedelta(days=1)
    return f"{d:%Y-%m-%d}"


def slice_to_date(df, bar_limit: str):
    """bar_limit(YYYY-MM-DD) 이하의 봉만 남긴다. 없으면 None."""
    if df is None or df.empty:
        return None
    dates = [f"{d:%Y-%m-%d}" for d in df.index]
    keep = [i for i, d in enumerate(dates) if d <= bar_limit]
    if not keep:
        return None
    return df.iloc[: keep[-1] + 1]


def main():
    p = argparse.ArgumentParser(description="스캔 스코어 이력 소급 적재")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--out-dir", default="history")
    p.add_argument("--dry-run", action="store_true", help="기록하지 않고 대상만 출력")
    args = p.parse_args()

    snaps = dedup_by_date(drop_corrupt(load_snapshots()))
    out_dir = Path(args.out_dir)

    todo = [s for s in snaps if not (out_dir / f"{s['date']}.csv").exists()]
    print(f"[*] 스냅샷 {len(snaps)}일 중 {len(todo)}일이 미적재")
    if args.dry_run:
        for s in todo:
            print(f"    {s['date']} · {s['count']}종목")
        return

    if not todo:
        print("[*] 적재할 대상이 없습니다")
        return

    tickers = sorted({x["t"] for s in todo for x in s["stocks"]})
    print(f"[*] 고유 티커 {len(tickers)}개 일봉 조회 (동시 {args.workers})")

    frames: dict = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_history_only, t): t for t in tickers}
        for n, fut in enumerate(as_completed(futures), 1):
            t = futures[fut]
            frames[t] = fut.result()
            if n % 50 == 0 or n == len(tickers):
                print(f"\r    {n}/{len(tickers)}", end="", flush=True)
    print()

    for s in todo:
        rows = []
        for x in s["stocks"]:
            price = {
                "bar_date": None, "close": None, "volume": None,
                "avg_vol20": None, "atr14": None, "market_cap": None,
            }
            sliced = slice_to_date(frames.get(x["t"]), bar_limit_for(s["date"]))
            if sliced is not None:
                price = history.price_fields(sliced, None)
                price["market_cap"] = None   # 소급분은 복원 불가
            rows.append({
                "ticker": x["t"], "name": x["n"], "market": x["m"], "sector": x["sec"],
                "tech": x["tech"], "macro": x["macro"], "filing": x["filing"],
                "value": x["value"], "total": x["total"], "consensus": x["consensus"],
                "signal": x["signal"], "ev": x["ev"], "target": x["target"],
                "hitl": x["hitl"], "source": "backfill",
                **price,
            })
        path = history.write_snapshot(rows, s["scan_ts"], out_dir=args.out_dir)
        print(f"    기록 {path} ({len(rows)}행)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: dry-run 확인**

Run: `python backfill_history.py --dry-run`
Expected: 미적재 날짜 목록이 출력됨 (Task 6에서 만든 오늘 날짜 CSV는 제외됨)

- [ ] **Step 5: 실제 소급 적재 실행**

Run: `python backfill_history.py --workers 4`
Expected: 티커 약 1,200개 조회 후 날짜별 `기록 history\<날짜>.csv (N행)` 출력. 10~20분 소요.

- [ ] **Step 6: 결과 검증**

```bash
python -c "
import csv, glob
for p in sorted(glob.glob('history/*.csv')):
    rows = list(csv.DictReader(open(p, encoding='utf-8')))
    src = {r['source'] for r in rows}
    filled = sum(1 for r in rows if r['close'])
    print(f'{p} · {len(rows)}행 · source={src} · close채움 {filled}')
"
```
Expected: 각 파일의 열 수가 23이고, `source=backfill` 파일들은 `market_cap`이 비어 있으며 `close`가 대부분 채워짐

- [ ] **Step 7: 커밋**

```bash
git add backfill_history.py tests/test_backfill.py history/
git commit -m "Backfill score history from 23 archived git snapshots"
```

---

## Task 10: CI에 이력 커밋 연결

**Files:**
- Modify: `.github/workflows/scan.yml`

- [ ] **Step 1: git add 대상에 history/ 추가**

`.github/workflows/scan.yml`에서 다음 줄을 찾아

```yaml
          git add dashboard_data.js
```

다음으로 교체한다:

```yaml
          git add dashboard_data.js history/
```

- [ ] **Step 2: 커밋 메시지도 함께 수정**

같은 파일에서 다음 줄을 찾아

```yaml
            git commit -m "Auto scan: $(date -u +'%Y-%m-%d %H:%M UTC')"
```

다음으로 교체한다:

```yaml
            git commit -m "Auto scan: $(TZ=Asia/Seoul date +'%Y-%m-%d %H:%M KST')"
```

- [ ] **Step 3: YAML 유효성 확인**

Run: `python -c "import yaml, io; yaml.safe_load(io.open('.github/workflows/scan.yml', encoding='utf-8')); print('YAML OK')"`
Expected: `YAML OK`

- [ ] **Step 4: 전체 테스트 실행**

Run: `python -m pytest tests/ -v`
Expected: 19 passed

- [ ] **Step 5: 커밋 및 푸시**

```bash
git add .github/workflows/scan.yml
git commit -m "Commit the history directory from CI scans"
git push origin main
```

- [ ] **Step 6: CI에서 전체 유니버스로 검증**

```bash
gh workflow run scan.yml --ref main -f market=ALL
gh run watch $(gh run list --workflow=scan.yml --limit 1 --json databaseId -q '.[0].databaseId') --exit-status
```
Expected: 성공. 완료 후 원격에 `history/<KST날짜>.csv`가 1,000행 이상으로 커밋되어 있어야 한다.

- [ ] **Step 7: 원격 결과 확인**

```bash
git pull --rebase origin main
python -c "
import csv, glob
p = sorted(glob.glob('history/*.csv'))[-1]
rows = list(csv.DictReader(open(p, encoding='utf-8')))
print(p, len(rows), '행 ·', len(rows[0]), '열 · source:', {r['source'] for r in rows})
"
```
Expected: 1,000행 이상, 23열, `source={'live'}`

---

## 완료 기준 점검

- [ ] 정규 스캔 1회 실행 시 `history/<KST날짜>.csv`가 생성되고 행 수가 수집 종목 수와 일치한다 (Task 6 Step 4, Task 10 Step 7)
- [ ] 소급 적재 실행 후 손상 스냅샷 3건을 제외한 날짜별 CSV가 생성된다 (Task 9 Step 4)
- [ ] pytest 19건 통과 (Task 10 Step 4)
- [ ] CI 스캔 성공 후 `history/` 파일이 리포에 커밋된다 (Task 10 Step 6)
