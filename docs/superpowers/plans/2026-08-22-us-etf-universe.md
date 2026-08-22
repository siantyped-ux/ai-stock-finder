# 미국 단독 유니버스와 ETF 편입 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 한국 주식을 스캔에서 빼고 미국 ETF 를 편입하며, 스캔 결과 출력에 종합점수 70점 필터를 걸고, 바뀐 조건에서 손절·익절 성과를 다시 계산한다.

**Architecture:** 점수·판정 로직은 `stock_finder.py` 안의 순수 함수(`calc_total` / `calc_consensus` / `calc_signal`)라 먼저 테스트로 고정한 뒤 ETF 분기를 넣는다. 유니버스 조회는 주식과 ETF 를 별도 함수로 나누고, 유니버스 튜플에 `asset_type` 을 한 칸 추가해 스캔 루프가 분기하도록 한다. 아카이브 스키마는 `history.FIELDS` 끝에 컬럼을 붙여 기존 CSV 와의 호환을 유지한다. 70점 필터는 `dashboard_data.js` 생성 직전에만 적용하고 아카이브 기록에는 손대지 않는다.

**Tech Stack:** Python 3.11, pytest, FMP API (company-screener), yfinance, GitHub Actions

**설계 문서:** `docs/superpowers/specs/2026-08-22-us-etf-universe-design.md`

---

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `stock_finder.py` | 유니버스 조회, 스코어링, 판정, 대시보드 출력 | 수정 |
| `history.py` | 아카이브 CSV 스키마와 기록 | 수정 (`asset_type` 추가) |
| `backtest.py` | 아카이브 재생 시뮬레이션 | 수정 (필터 옵션) |
| `tests/test_scoring.py` | 점수·판정 순수 함수 | **신규** |
| `tests/test_universe.py` | 유니버스 필터 순수 함수 | **신규** |
| `tests/test_history.py` | 아카이브 스키마 | 수정 |
| `tests/test_backtest.py` | 백테스트 필터 | 수정 |
| `.github/workflows/scan.yml` | 스캔 인자 | 수정 |
| `requirements.txt` | 의존성 | 수정 (`pykrx` 제거) |

`stock_finder.py` 는 이미 1,500줄이 넘지만 이번 변경으로 쪼개지 않는다. 유니버스·스코어링·출력이 한 `main()` 흐름으로 묶여 있어 분리하면 이번 작업 범위를 크게 넘는다. 대신 **새로 추가하는 순수 함수는 전부 독립 테스트가 가능한 형태**로 만든다.

---

### Task 1: 신호 판정을 비율 기반으로 (주식 판정 불변 회귀 테스트)

이번 변경에서 가장 위험한 부분이다. 먼저 **현재 동작을 테스트로 못 박고** 나서 리팩터링한다.

**Files:**
- Create: `tests/test_scoring.py`
- Modify: `stock_finder.py:1163-1170` (`calc_signal`)

- [ ] **Step 1: 현재 주식 판정을 고정하는 회귀 테스트를 쓴다**

`tests/test_scoring.py` 를 새로 만든다.

```python
"""종합점수·합의·신호 판정 순수 함수 테스트.

ETF 편입으로 판정 로직에 손을 대므로, 주식 판정이 한 건도 바뀌지 않는지를
회귀 테스트로 고정한다. 설계: docs/superpowers/specs/2026-08-22-us-etf-universe-design.md
"""
import pytest

import stock_finder as sf


# ─── 주식 신호 판정 회귀 (변경 전후로 동일해야 한다) ───────────
@pytest.mark.parametrize("total,cons,expected", [
    (80, 3, "STRONG_BUY"),
    (85, 4, "STRONG_BUY"),
    (80, 2, "WATCH"),      # cons 부족 → 강등
    (70, 3, "BUY"),
    (79, 3, "BUY"),
    (70, 2, "WATCH"),      # cons 부족 → 강등
    (60, 2, "WATCH"),
    (60, 1, "HOLD"),
    (45, 0, "HOLD"),
    (44, 4, "AVOID"),
    (35, 0, "AVOID"),
])
def test_stock_signal_unchanged(total, cons, expected):
    assert sf.calc_signal(total, cons) == expected
```

- [ ] **Step 2: 테스트가 통과하는지 확인한다 (현재 동작 고정)**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: 11 passed. 아직 코드를 안 바꿨으므로 전부 통과해야 한다. 하나라도 실패하면 내가 현재 동작을 잘못 읽은 것이니 멈추고 확인할 것.

- [ ] **Step 3: ETF 판정 요구사항을 테스트로 추가한다 (실패하는 테스트)**

`tests/test_scoring.py` 끝에 붙인다.

```python
# ─── ETF 신호 판정 (축이 2개) ──────────────────────────────
@pytest.mark.parametrize("total,cons,expected", [
    (80, 2, "STRONG_BUY"),   # 2/2 = 1.00 >= 0.75
    (80, 1, "WATCH"),        # 1/2 = 0.50 < 0.75 → 강등
    (70, 2, "BUY"),
    (70, 1, "WATCH"),        # 1/2 = 0.50 → cons>=2 상당도 못 채움
    (60, 1, "WATCH"),        # 0.50 >= 0.50 → WATCH 는 통과
    (60, 0, "HOLD"),
    (44, 2, "AVOID"),
])
def test_etf_signal_uses_ratio(total, cons, expected):
    assert sf.calc_signal(total, cons, n_axes=2) == expected


def test_signal_ratio_thresholds_match_stock_counts():
    """비율 임계가 기존 개수 임계와 정확히 대응하는지."""
    # 주식 3/4 = 0.75 (BUY 기준), 2/4 = 0.50 (WATCH 기준)
    assert sf.calc_signal(70, 3, n_axes=4) == "BUY"
    assert sf.calc_signal(70, 2, n_axes=4) == "WATCH"
    assert sf.calc_signal(60, 2, n_axes=4) == "WATCH"
    assert sf.calc_signal(60, 1, n_axes=4) == "HOLD"
```

- [ ] **Step 4: 실패를 확인한다**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: `test_etf_signal_uses_ratio` 7건이 `TypeError: calc_signal() got an unexpected keyword argument 'n_axes'` 로 FAIL.

- [ ] **Step 5: `calc_signal` 을 비율 기반으로 고친다**

`stock_finder.py` 의 `calc_signal` 을 통째로 교체한다.

```python
def calc_signal(total, cons, n_axes=4):
    """종합점수와 합의 비율로 신호를 낸다.

    cons 는 70점 이상인 축의 개수, n_axes 는 축의 총 개수다. 개수가 아니라
    비율로 판정하는 것은 ETF 때문이다 - ETF 는 filing/value 데이터가 없어
    축이 tech/macro 둘뿐이라, 개수 기준(cons>=3)으로는 BUY 가 영원히 나오지
    않는다.

    임계 0.75 / 0.50 은 주식의 3/4, 2/4 와 정확히 같다. 주식 판정은 이
    변경으로 한 건도 바뀌지 않는다 (tests/test_scoring.py 회귀 테스트).
    """
    ratio = cons / n_axes if n_axes else 0.0
    if total >= 80 and ratio >= 0.75:
        return "STRONG_BUY"
    if total >= 70 and ratio >= 0.75:
        return "BUY"
    if total >= 60 and ratio >= 0.50:
        return "WATCH"
    if total >= 45:
        return "HOLD"
    return "AVOID"
```

- [ ] **Step 6: 테스트를 돌린다**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: 19 passed. 특히 Step 1 의 주식 회귀 11건이 그대로 통과해야 한다.

- [ ] **Step 7: 전체 테스트를 돌린다**

Run: `python -m pytest -q`
Expected: 227 passed (기존 208 + 신규 19).

- [ ] **Step 8: 커밋**

```bash
git add tests/test_scoring.py stock_finder.py
git commit -m "Judge the signal by consensus ratio instead of count"
```

---

### Task 2: ETF 종합점수 재정규화

**Files:**
- Modify: `stock_finder.py:1157-1158` (`calc_total` 아래에 추가)
- Test: `tests/test_scoring.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_scoring.py` 끝에 붙인다.

```python
# ─── ETF 종합점수 (tech/macro 재정규화) ─────────────────────
def test_etf_total_renormalizes_two_axes():
    # 0.35/0.55 = 0.63636..., 0.20/0.55 = 0.36363...
    assert sf.calc_total_etf(100, 100) == 100
    assert sf.calc_total_etf(0, 0) == 0


def test_etf_total_weights_tech_more_than_macro():
    # tech 가 높을 때가 macro 가 높을 때보다 점수가 높아야 한다.
    assert sf.calc_total_etf(80, 40) > sf.calc_total_etf(40, 80)


def test_etf_total_matches_hand_calculation():
    # 80*0.63636 + 60*0.36363 = 50.909 + 21.818 = 72.727 → 73
    assert sf.calc_total_etf(80, 60) == 73


def test_etf_total_can_reach_the_70_filter():
    """중립값 50 방식이었다면 못 넘었을 구간을 넘는지."""
    assert sf.calc_total_etf(75, 62) >= 70


def test_etf_total_returns_int():
    assert isinstance(sf.calc_total_etf(71, 63), int)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_scoring.py -k etf_total -v`
Expected: 5건 FAIL, `AttributeError: module 'stock_finder' has no attribute 'calc_total_etf'`.

- [ ] **Step 3: `calc_total_etf` 를 추가한다**

`stock_finder.py` 의 `calc_total` 정의 바로 아래에 넣는다.

```python
# ETF 가중치. 주식 가중치에서 filing(0.30)·value(0.15) 를 빼고 남은 0.55 로
# 나눈 값이다. ETF 에는 개별기업 재무·공시 데이터가 없어 두 축을 계산할 수
# 없다. 중립값 50 으로 채우지 않는 것은 의도다 - 그러면 두 축이 22.5점으로
# 고정돼 70점을 넘으려면 macro 70일 때 tech 95.7 이상이 필요한데, 관측된
# 개별주식 최고점이 77인 분포에서는 사실상 나오지 않는다.
ETF_TECH_WEIGHT = 0.35 / 0.55
ETF_MACRO_WEIGHT = 0.20 / 0.55


def calc_total_etf(tech, macro):
    """ETF 종합점수. tech/macro 두 축만 쓰고 가중치를 재정규화한다."""
    return int(round(tech * ETF_TECH_WEIGHT + macro * ETF_MACRO_WEIGHT))
```

- [ ] **Step 4: 테스트를 돌린다**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: 24 passed.

- [ ] **Step 5: 커밋**

```bash
git add tests/test_scoring.py stock_finder.py
git commit -m "Score ETFs on the technical and macro axes only"
```

---

### Task 3: 레버리지·인버스 ETF 판별

**Files:**
- Modify: `stock_finder.py` (`fetch_us_universe` 위에 추가)
- Test: `tests/test_universe.py` (신규)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_universe.py` 를 새로 만든다.

```python
"""유니버스 필터 순수 함수 테스트.

설계: docs/superpowers/specs/2026-08-22-us-etf-universe-design.md
"""
import pytest

import stock_finder as sf


@pytest.mark.parametrize("name", [
    "Direxion Daily Semiconductor Bull 3X Shares",
    "ProShares Ultra QQQ",
    "ProShares UltraPro Short QQQ",
    "ProShares Short S&P500",
    "Direxion Daily Financial Bear 3X Shares",
    "ProShares UltraShort Bloomberg Crude Oil",
    "Simplify Inverse Treasury ETF",
    "Amplify 2X Covered Call ETF",
])
def test_rejects_leveraged_and_inverse(name):
    assert sf.is_leveraged_or_inverse(name) is True


@pytest.mark.parametrize("name", [
    "SPDR S&P 500 ETF Trust",
    "Invesco QQQ Trust",
    "iShares Russell 2000 ETF",
    "Vanguard Total Stock Market ETF",
    "Schwab U.S. Dividend Equity ETF",
    "SPDR Gold Shares",
])
def test_accepts_plain_etfs(name):
    assert sf.is_leveraged_or_inverse(name) is False


@pytest.mark.parametrize("name", [
    "iShares Short Treasury Bond ETF",
    "Vanguard Short-Term Bond ETF",
    "SPDR Portfolio Short Term Corporate Bond ETF",
    "iShares 1-3 Year Treasury Bond ETF",
])
def test_keeps_short_duration_bond_etfs(name):
    """'Short' 가 만기를 뜻하는 채권 ETF 는 인버스가 아니다.

    이 구분이 없으면 단기채 ETF 수십 개가 통째로 빠진다.
    """
    assert sf.is_leveraged_or_inverse(name) is False


def test_matching_is_case_insensitive():
    assert sf.is_leveraged_or_inverse("direxion daily 3x bull") is True


def test_does_not_match_inside_a_word():
    """'Shortline' 같은 단어 안의 우연한 일치를 걸러내면 안 된다."""
    assert sf.is_leveraged_or_inverse("Shortline Logistics ETF") is False


def test_handles_empty_name():
    assert sf.is_leveraged_or_inverse("") is False
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_universe.py -v`
Expected: 16건 FAIL, `AttributeError: module 'stock_finder' has no attribute 'is_leveraged_or_inverse'`.

- [ ] **Step 3: 판별 함수를 추가한다**

`stock_finder.py` 의 `def fetch_us_universe` 바로 위에 넣는다. 파일 상단에 `import re` 가 없으면 함께 추가한다.

```python
# 레버리지·인버스 ETF 를 종목명으로 걸러낸다. ATR 3배 손절과 궁합이 나쁘다 -
# 일일 변동성이 기초자산의 배수라 손절폭이 비현실적으로 벌어지고, 장기 보유
# 시 변동성 감쇠 때문에 손익을 같은 척도로 해석할 수 없다.
#
# 단어 경계를 쓴다. 경계가 없으면 "Shortline" 같은 이름이 "Short" 로 걸린다.
#
# SHORT 뒤의 부정 전방탐색이 핵심이다. 채권 ETF 의 "Short" 는 인버스가 아니라
# 잔존만기를 뜻한다 - 이 예외가 없으면 "iShares Short Treasury Bond ETF" 나
# "Vanguard Short-Term Bond ETF" 같은 단기채 ETF 가 통째로 빠진다. 반면
# "ProShares Short S&P500" 은 진짜 인버스라 걸러야 한다.
_LEVERAGED_PATTERN = re.compile(
    r"\b(?:\d+X|ULTRA|ULTRAPRO|ULTRASHORT|INVERSE|BEAR|BULL|LEVERAGED"
    r"|SHORT\b(?![- ]?(?:TERM|DURATION|MATURITY|TREASURY)))\b",
    re.IGNORECASE,
)


def is_leveraged_or_inverse(name: str) -> bool:
    """종목명이 레버리지·인버스 ETF 를 가리키는지."""
    if not name:
        return False
    return bool(_LEVERAGED_PATTERN.search(name))
```

- [ ] **Step 4: 테스트를 돌린다**

Run: `python -m pytest tests/test_universe.py -v`
Expected: 20 passed.

`ProShares Ultra QQQ` 는 `ULTRA` 로, `Amplify 2X Covered Call ETF` 는 `\d+X` 로 잡힌다. `SPDR Gold Shares` 의 `Shares` 는 `\bSHORT\b` 에 걸리지 않는다. `iShares Short Treasury Bond ETF` 는 전방탐색으로 살아남고 `ProShares Short S&P500` 은 걸린다.

- [ ] **Step 5: 커밋**

```bash
git add tests/test_universe.py stock_finder.py
git commit -m "Reject leveraged and inverse ETFs by name"
```

---

### Task 4: ETF 유니버스 조회

**Files:**
- Modify: `stock_finder.py` (`fetch_us_universe` 아래에 추가)
- Test: `tests/test_universe.py`

유니버스 튜플이 4칸(`ticker, name, market, sector`)에서 **5칸**(`ticker, name, market, sector, asset_type`)으로 늘어난다. 이 Task 에서 ETF 함수를 추가하고, Task 6 에서 주식 쪽과 호출부를 함께 맞춘다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_universe.py` 끝에 붙인다.

```python
# ─── ETF 유니버스 파싱 (네트워크 없이 응답만 넣는다) ──────────
ETF_RESPONSE = [
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "marketCap": 6.2e11},
    {"symbol": "QQQ", "name": "Invesco QQQ Trust", "marketCap": 3.5e11},
    {"symbol": "TQQQ", "name": "ProShares UltraPro QQQ", "marketCap": 2.6e10},
    {"symbol": "TINY", "name": "Tiny Niche ETF", "marketCap": 4.0e8},
    {"symbol": "", "name": "No Symbol ETF", "marketCap": 9.9e10},
]


def test_parse_etf_rows_keeps_plain_large_etfs():
    rows = sf.parse_etf_rows(ETF_RESPONSE, min_aum=1e9)
    tickers = [r[0] for r in rows]
    assert tickers == ["SPY", "QQQ"]


def test_parse_etf_rows_drops_leveraged():
    rows = sf.parse_etf_rows(ETF_RESPONSE, min_aum=1e9)
    assert "TQQQ" not in [r[0] for r in rows]


def test_parse_etf_rows_drops_below_min_aum():
    rows = sf.parse_etf_rows(ETF_RESPONSE, min_aum=1e9)
    assert "TINY" not in [r[0] for r in rows]


def test_parse_etf_rows_drops_missing_symbol():
    rows = sf.parse_etf_rows(ETF_RESPONSE, min_aum=1e9)
    assert all(r[0] for r in rows)


def test_parse_etf_rows_shape_is_five_tuple():
    rows = sf.parse_etf_rows(ETF_RESPONSE, min_aum=1e9)
    assert rows[0] == ("SPY", "SPDR S&P 500 ETF Trust", "US", "미분류", "ETF")


def test_parse_etf_rows_handles_missing_market_cap():
    rows = sf.parse_etf_rows([{"symbol": "AAA", "name": "A ETF"}], min_aum=1e9)
    assert rows == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_universe.py -k parse_etf -v`
Expected: 6건 FAIL, `AttributeError: module 'stock_finder' has no attribute 'parse_etf_rows'`.

- [ ] **Step 3: 파싱 함수와 조회 함수를 추가한다**

`stock_finder.py` 의 `fetch_us_universe` 정의가 끝난 바로 다음에 넣는다.

```python
def parse_etf_rows(data: list, min_aum: float) -> list:
    """FMP ETF 스크리너 응답을 유니버스 튜플 목록으로 바꾼다.

    네트워크와 분리해 두면 필터 규칙을 테스트할 수 있다.

    ETF 는 섹터를 '미분류' 로 둔다. calc_macro_score 가 미분류를 중립
    처리하므로 macro 점수가 왜곡되지 않는다.
    """
    rows = []
    for item in data:
        symbol = item.get("symbol", "")
        if not symbol:
            continue
        name = item.get("name") or item.get("companyName") or symbol
        if is_leveraged_or_inverse(name):
            continue
        aum = item.get("marketCap")
        if aum is None or aum < min_aum:
            continue
        rows.append((symbol, name[:40], "US", "미분류", "ETF"))
    return rows


def fetch_us_etf_universe(min_aum: float = 1e9, limit: int = 3000) -> list:
    """FMP stock-screener 로 미국 ETF 조회.

    거래소 필터를 걸지 않는 것이 핵심이다. SPY·IWM 등 주요 ETF 는 NYSE Arca
    상장이라 exchange=nyse,nasdaq 으로 조회하면 QQQ 정도만 잡히고 대부분
    누락된다.
    """
    cache_key = f"us_etf_universe_{int(min_aum)}_{limit}"
    cached = _load_cache(cache_key)
    if cached:
        print(f"    [cache] 미국 ETF {len(cached)}종목 (캐시)")
        return [tuple(row) for row in cached]

    if not FMP_KEY:
        print("    [!] FMP API 키 없음 → ETF 건너뜀")
        return []

    print(f"    [*] FMP 미국 ETF 조회 (AUM ≥ ${min_aum/1e9:.1f}B)...")
    try:
        r = requests.get(f"{FMP_BASE}/company-screener", params={
            "marketCapMoreThan": int(min_aum),
            "isEtf": "true",
            "isActivelyTrading": "true",
            "limit": limit,
            "apikey": FMP_KEY,
        }, timeout=15)
        data = r.json() if r.status_code == 200 else []
        universe = parse_etf_rows(data, min_aum)
        _save_cache(cache_key, universe)
        print(f"    [OK] 미국 ETF {len(universe)}종목 수집 완료")
        return universe
    except Exception as e:
        print(f"    [!] FMP ETF 조회 실패: {e}")
        return []
```

- [ ] **Step 4: 테스트를 돌린다**

Run: `python -m pytest tests/test_universe.py -v`
Expected: 26 passed.

- [ ] **Step 5: 커밋**

```bash
git add tests/test_universe.py stock_finder.py
git commit -m "Fetch the US ETF universe without an exchange filter"
```

---

### Task 5: 한국 유니버스 제거

**Files:**
- Modify: `stock_finder.py:923-1036` (`KOSPI_EXPANDED`, `fetch_kr_universe` 삭제)
- Modify: `stock_finder.py:1039-1066` (`load_universe`)
- Modify: `stock_finder.py:1296-1306` (`--market`, `--min-kr-cap`)
- Modify: `stock_finder.py:97` 근처 (`FALLBACK_UNIVERSE` 의 KR 항목)
- Modify: `requirements.txt:4`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_universe.py` 끝에 붙인다.

```python
# ─── 한국 제거 ─────────────────────────────────────────────
def test_fallback_universe_has_no_korean_tickers():
    assert all(not t.endswith(".KS") for t, *_ in sf.FALLBACK_UNIVERSE)


def test_fallback_universe_is_all_us():
    assert all(row[2] == "US" for row in sf.FALLBACK_UNIVERSE)


def test_fallback_universe_rows_are_five_tuples():
    assert all(len(row) == 5 for row in sf.FALLBACK_UNIVERSE)


def test_kr_universe_function_is_gone():
    assert not hasattr(sf, "fetch_kr_universe")
    assert not hasattr(sf, "KOSPI_EXPANDED")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_universe.py -k "fallback or kr_universe" -v`
Expected: 4건 FAIL.

- [ ] **Step 3: 한국 관련 코드를 지운다**

1. `KOSPI_EXPANDED = [` 부터 그 리스트가 닫히는 `]` 까지 삭제한다 (`stock_finder.py:923` 부터).
2. `def fetch_kr_universe(...)` 함수 전체를 삭제한다 (`stock_finder.py:977-1036`).
3. `FALLBACK_UNIVERSE` 에서 `market` 이 `"KR"` 인 항목을 전부 지우고, 남은 US 항목의 튜플 끝에 `"STOCK"` 을 붙여 5칸으로 만든다.

확인 명령:

```bash
grep -n "KOSPI_EXPANDED\|fetch_kr_universe\|\.KS" stock_finder.py
```

Expected: 아무것도 출력되지 않아야 한다.

- [ ] **Step 4: `load_universe` 를 미국 전용으로 고친다**

`load_universe` 전체를 교체한다.

```python
def load_universe(min_us_cap: float = 1e10,
                  min_etf_aum: float = 1e9,
                  include_etf: bool = True,
                  test_mode: bool = False) -> list:
    """미국 주식 + ETF 유니버스를 로드한다.

    반환 튜플은 (ticker, name, market, sector, asset_type) 5칸이다.
    asset_type 은 "STOCK" | "ETF" 이고, 스캔 루프가 이 값으로 스코어링을
    분기한다.

    한국은 다루지 않는다. pykrx 조회가 CI 에서 매번 실패해 하드코딩 폴백
    112종목이 고정돼 있었고, 그 리스트는 코스피 구성 변화를 반영하지 못했다.
    """
    if test_mode:
        return FALLBACK_UNIVERSE

    universe = list(fetch_us_universe(min_market_cap=min_us_cap))
    if not universe:
        universe = list(FALLBACK_UNIVERSE)
        print(f"    [폴백] 미국 하드코딩 {len(universe)}종목 사용")

    if include_etf:
        universe.extend(fetch_us_etf_universe(min_aum=min_etf_aum))

    return universe
```

- [ ] **Step 5: `fetch_us_universe` 의 반환 튜플을 5칸으로 늘린다**

`stock_finder.py` 의 `fetch_us_universe` 안, `universe.append((symbol, name[:40], "US", sector_kr))` 를 교체한다.

```python
            universe.append((symbol, name[:40], "US", sector_kr, "STOCK"))
```

캐시된 옛 4칸 데이터가 섞이지 않도록 캐시 키도 바꾼다. 같은 함수 안의
`cache_key = f"us_universe_{int(min_market_cap)}_{limit}"` 를 교체한다.

```python
    cache_key = f"us_universe_v2_{int(min_market_cap)}_{limit}"
```

- [ ] **Step 6: CLI 인자를 정리한다**

`stock_finder.py` 의 인자 정의에서 `--market` 과 `--min-kr-cap` 두 줄을 지우고, ETF 옵션을 넣는다. `--min-us-cap` 의 기본값도 워크플로가 넘기는 값과 맞춘다.

```python
    p.add_argument("--min-us-cap", type=float, default=1e10,
                   help="미국 주식 최소 시가총액 (USD, 기본 1e10 = $10B)")
    p.add_argument("--min-etf-aum", type=float, default=1e9,
                   help="미국 ETF 최소 AUM (USD, 기본 1e9 = $1B)")
    p.add_argument("--no-etf", action="store_true",
                   help="ETF 를 유니버스에서 제외한다")
```

- [ ] **Step 7: `requirements.txt` 에서 `pykrx` 를 지운다**

`pykrx>=1.0` 줄을 삭제한다. `fetch_kr_universe()` 안에서만 쓰이던 의존성이다.

확인 명령:

```bash
grep -rn "pykrx" --include=*.py --include=*.txt .
```

Expected: 아무것도 출력되지 않아야 한다.

- [ ] **Step 8: 테스트를 돌린다**

Run: `python -m pytest tests/test_universe.py -v`
Expected: 30 passed.

- [ ] **Step 9: 커밋**

```bash
git add tests/test_universe.py stock_finder.py requirements.txt
git commit -m "Drop the Korean universe and its dead pykrx fallback"
```

---

### Task 6: 아카이브에 `asset_type` 컬럼 추가

**Files:**
- Modify: `history.py:22-27` (`FIELDS`), `history.py:44-47` (`_NULLABLE_FIELDS`)
- Test: `tests/test_history.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_history.py` 끝에 붙인다. 기존 파일의 임포트(`history`, `pytest` 등)를 그대로 쓴다.

```python
# ─── asset_type 컬럼 (ETF 편입) ────────────────────────────
def test_fields_end_with_asset_type():
    """컬럼은 끝에만 추가한다. 중간에 넣으면 기존 CSV 와 호환이 깨진다."""
    assert history.FIELDS[-1] == "asset_type"


def test_filing_and_value_are_nullable_for_etfs():
    """ETF 는 filing/value 를 계산할 수 없어 빈 값으로 기록된다."""
    assert "filing" in history._NULLABLE_FIELDS
    assert "value" in history._NULLABLE_FIELDS


def test_etf_row_writes_with_blank_filing_and_value(tmp_path):
    row = {
        "ticker": "SPY", "name": "SPDR S&P 500 ETF Trust", "market": "US",
        "sector": "미분류", "asset_type": "ETF",
        "tech": 78, "macro": 64, "filing": None, "value": None,
        "total": 73, "consensus": 2, "signal": "BUY",
        "ev": 1.2, "target": 14, "hitl": False, "source": "live",
        "bar_date": "2026-08-22", "close": 640.0, "volume": 1000,
        "avg_vol20": 900.0, "atr14": 5.0, "market_cap": 6.2e11,
    }
    path = history.write_snapshot([row], history.kst_now(), out_dir=tmp_path)

    import csv
    written = list(csv.DictReader(open(path, encoding="utf-8")))
    assert written[0]["asset_type"] == "ETF"
    assert written[0]["filing"] == ""
    assert written[0]["value"] == ""


def test_stock_row_still_requires_filing_and_value_values(tmp_path):
    """주식 행은 여전히 두 축을 채워야 한다 - 빈 값은 계산 실패를 뜻한다."""
    row = {
        "ticker": "AAPL", "name": "Apple Inc.", "market": "US",
        "sector": "IT", "asset_type": "STOCK",
        "tech": 70, "macro": 60, "filing": 65, "value": 55,
        "total": 64, "consensus": 1, "signal": "WATCH",
        "ev": 0.8, "target": 10, "hitl": False, "source": "live",
        "bar_date": "2026-08-22", "close": 230.0, "volume": 1000,
        "avg_vol20": 900.0, "atr14": 3.0, "market_cap": 3.4e12,
    }
    path = history.write_snapshot([row], history.kst_now(), out_dir=tmp_path)

    import csv
    written = list(csv.DictReader(open(path, encoding="utf-8")))
    assert written[0]["asset_type"] == "STOCK"
    assert written[0]["filing"] == "65"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_history.py -k asset_type -v`
Expected: `test_fields_end_with_asset_type` FAIL (`'source' != 'asset_type'`), 나머지도 `ValueError: 알 수 없는 열: ['asset_type']` 으로 FAIL.

- [ ] **Step 3: 스키마를 고친다**

`history.py` 의 `FIELDS` 를 교체한다. **`asset_type` 은 반드시 끝에 붙인다.**

```python
# 열 순서 고정. 변경 시 기존 CSV와 호환이 깨지므로 끝에만 추가할 것.
FIELDS = (
    "scan_ts_kst", "date", "ticker", "name", "market", "sector",
    "bar_date", "close", "volume", "avg_vol20", "atr14", "market_cap",
    "tech", "macro", "filing", "value", "total", "consensus", "signal",
    "ev", "target", "hitl", "source", "asset_type",
)
```

같은 파일의 `_NULLABLE_FIELDS` 를 교체한다.

```python
# 값이 비어도 되는 열. 시세 조회 실패나 소급 적재에서는 정상적으로 빈다.
# filing/value 는 ETF 때문에 비워질 수 있다 - ETF 에는 개별기업 재무·공시
# 데이터가 없다. 0 을 넣지 않는 것은 "0점을 받았다" 와 구분하기 위해서다.
_NULLABLE_FIELDS = frozenset({
    "bar_date", "close", "volume", "avg_vol20", "atr14", "market_cap",
    "filing", "value",
})
```

- [ ] **Step 4: 테스트를 돌린다**

Run: `python -m pytest tests/test_history.py -v`
Expected: 전부 통과.

- [ ] **Step 5: 전체 테스트를 돌린다**

Run: `python -m pytest -q`
Expected: 전부 통과. 실패가 나면 기존 테스트가 `FIELDS` 길이나 순서를 가정하고 있는 것이므로, 그 테스트를 `asset_type` 포함으로 고친다.

- [ ] **Step 6: 커밋**

```bash
git add history.py tests/test_history.py
git commit -m "Record the asset type in the score archive"
```

---

### Task 7: 스캔 루프에 ETF 분기 배선

**Files:**
- Modify: `stock_finder.py:1356-1380` (유니버스 로드·집계 출력)
- Modify: `stock_finder.py:1404-1450` (`_scan_one`)
- Modify: `stock_finder.py:1461-1465` (`pool.submit` 언패킹)

- [ ] **Step 1: 유니버스 로드 호출부를 5칸 튜플에 맞춘다**

`load_universe(...)` 호출과 `--limit` 처리, 집계 출력을 교체한다.

```python
    universe = load_universe(
        min_us_cap=args.min_us_cap,
        min_etf_aum=args.min_etf_aum,
        include_etf=not args.no_etf,
        test_mode=args.test,
    )
    if args.limit > 0:
        stocks = [s for s in universe if s[4] == "STOCK"][:args.limit]
        etfs = [s for s in universe if s[4] == "ETF"][:args.limit]
        universe = stocks + etfs
        print(f"    [limit] 종류별 상위 {args.limit}개로 제한 → 총 {len(universe)}종목")

    if not universe:
        print("[!] 유니버스가 비었습니다. --test 옵션으로 폴백 모드 시도")
        sys.exit(1)

    n_stock = sum(1 for s in universe if s[4] == "STOCK")
    n_etf = sum(1 for s in universe if s[4] == "ETF")
    est_sec = len(universe) * (2.5 + args.sleep) / max(1, args.workers)
    est_min = est_sec / 60
    print(f"    총 {len(universe)}종목 (주식: {n_stock}, ETF: {n_etf})")
    print(f"    예상 소요시간: 약 {est_min:.1f}분 ({est_sec/3600:.1f}시간)")
    print("-" * 65)
```

- [ ] **Step 2: `_scan_one` 이 `asset_type` 을 받아 분기하게 한다**

`_scan_one` 의 시그니처와 스코어링 블록을 교체한다. 함수 나머지(`dash_row` 생성 이후)는 그대로 두되 `dash_row` 와 `hist_row` 에 `asset_type` 을 추가한다.

```python
    def _scan_one(ticker: str, name: str, market: str, sector: str,
                  asset_type: str):
        """종목 1개 스코어링. 실패 시 None 반환. (워커 스레드에서 실행)

        ETF 는 filing/value 를 계산하지 않는다. 개별기업 재무·공시 데이터가
        없어서다. 두 축을 빼고 tech/macro 만 재정규화해 총점을 낸다.
        """
        try:
            data = fetch_stock(ticker)
            if not data:
                return None

            hist = data["hist"]
            info = data["info"]

            tech, tech_r, r3m = calc_tech_score(hist)
            macro, macro_r, regime = calc_macro_score(vix, dxy, us10y, sector, fred_data)

            if asset_type == "ETF":
                value, value_r = None, []
                filing, filing_r = None, []
                total = calc_total_etf(tech, macro)
                cons = calc_consensus_etf(tech, macro)
                n_axes = 2
                ev, target = calc_ev_and_target(tech, macro, tech, macro, r3m)
            else:
                value, value_r = calc_value_score(info, sector)
                filing, filing_r = calc_filing_score(info, hist, ticker, market)
                total = calc_total(tech, macro, filing, value)
                cons = calc_consensus(tech, macro, filing, value)
                n_axes = 4
                ev, target = calc_ev_and_target(tech, macro, filing, value, r3m)

            signal = calc_signal(total, cons, n_axes=n_axes)
            hitl = calc_hitl(signal, total, tech)

            dash_row = {
                "t": ticker, "n": name, "m": market, "sec": sector,
                "at": asset_type,
                "tech": tech, "macro": macro, "filing": filing, "value": value,
                "total": total, "consensus": cons, "signal": signal,
                "ev": ev, "target": target, "hitl": hitl,
                "reasons": {
                    "tech": tech_r, "macro": macro_r,
                    "filing": filing_r, "value": value_r,
                }
            }
            # 이력 행 생성 실패가 이미 완성된 대시보드 행을 버리지 않도록 격리한다
            try:
                hist_row = {
                    "ticker": ticker, "name": name, "market": market,
                    "sector": sector, "asset_type": asset_type,
                    "tech": tech, "macro": macro, "filing": filing, "value": value,
                    "total": total, "consensus": cons, "signal": signal,
                    "ev": ev, "target": target, "hitl": hitl,
                    "source": "live",
                    **history.price_fields(hist, info),
                }
            except Exception as e:
                print(f"[!] {ticker}: 이력 행 생성 실패 {str(e)[:60]}")
                hist_row = None
            return dash_row, hist_row
        except Exception as e:
            print(f"[!] {ticker}: 스코어링 실패 {str(e)[:60]}")
            return None
        finally:
            # 워커별 쓰로틀 (API rate limit 방어)
            time.sleep(args.sleep)
```

`calc_ev_and_target` 에 ETF 는 `tech, macro, tech, macro` 를 넘긴다. 이 함수는 네 축의 가중 평균으로 기대수익을 내는데, ETF 에는 뒤 두 축이 없다. 같은 값을 반복해 넣으면 사실상 tech/macro 평균이 되어 재정규화와 방향이 일치한다.

- [ ] **Step 3: `calc_consensus_etf` 를 추가한다**

`stock_finder.py` 의 `calc_consensus` 바로 아래에 넣는다.

```python
def calc_consensus_etf(tech, macro):
    """ETF 합의 개수. 축이 tech/macro 둘뿐이므로 최대 2 다.

    개수를 그대로 아카이브에 저장한다. 판정은 calc_signal 이 n_axes=2 로
    비율을 계산한다.
    """
    return sum(1 for v in (tech, macro) if v >= 70)
```

- [ ] **Step 4: 워커 제출부의 언패킹을 5칸으로 바꾼다**

```python
        futures = {
            pool.submit(_scan_one, ticker, name, market, sector, asset_type): (i, ticker, name)
            for i, (ticker, name, market, sector, asset_type) in enumerate(universe, 1)
        }
```

- [ ] **Step 5: 스코어링 함수 테스트를 추가한다**

`tests/test_scoring.py` 끝에 붙인다.

```python
# ─── ETF 합의 개수 ─────────────────────────────────────────
def test_etf_consensus_counts_two_axes():
    assert sf.calc_consensus_etf(80, 75) == 2
    assert sf.calc_consensus_etf(80, 60) == 1
    assert sf.calc_consensus_etf(50, 60) == 0


def test_etf_consensus_boundary_is_inclusive():
    assert sf.calc_consensus_etf(70, 70) == 2
    assert sf.calc_consensus_etf(69, 69) == 0
```

- [ ] **Step 6: 테스트를 돌린다**

Run: `python -m pytest -q`
Expected: 전부 통과.

- [ ] **Step 7: 폴백 유니버스로 실제 스캔을 돌려 본다**

Run: `python stock_finder.py --test --limit 5 --workers 2 --sleep 0.5`
Expected: 오류 없이 완주하고 `[완료] N개 종목` 이 찍힌다. `history/` 에 오늘 날짜 CSV 가 생기고 `asset_type` 컬럼이 있어야 한다. **이 실행은 오늘자 아카이브를 덮어쓰므로, 먼저 `history/` 를 백업하고 끝나면 되돌린다.**

```bash
cp history/2026-08-22.csv /tmp/backup-2026-08-22.csv
python stock_finder.py --test --limit 5 --workers 2 --sleep 0.5
head -1 history/2026-08-22.csv
cp /tmp/backup-2026-08-22.csv history/2026-08-22.csv
```

- [ ] **Step 8: 커밋**

```bash
git add stock_finder.py tests/test_scoring.py
git commit -m "Score ETFs on their own axes in the scan loop"
```

---

### Task 8: 출력에 70점 필터

**Files:**
- Modify: `stock_finder.py` (인자 추가, `dashboard_data.js` 생성 직전)
- Test: `tests/test_scoring.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_scoring.py` 끝에 붙인다.

```python
# ─── 출력 필터 ─────────────────────────────────────────────
ROWS = [
    {"t": "AAA", "total": 77, "signal": "BUY"},
    {"t": "BBB", "total": 70, "signal": "WATCH"},
    {"t": "CCC", "total": 69, "signal": "WATCH"},
    {"t": "DDD", "total": 35, "signal": "AVOID"},
]


def test_filter_keeps_at_and_above_threshold():
    kept = sf.filter_for_output(ROWS, min_total=70)
    assert [r["t"] for r in kept] == ["AAA", "BBB"]


def test_filter_threshold_zero_keeps_everything():
    assert len(sf.filter_for_output(ROWS, min_total=0)) == 4


def test_filter_does_not_mutate_input():
    sf.filter_for_output(ROWS, min_total=70)
    assert len(ROWS) == 4


def test_filter_drops_rows_without_total():
    rows = [{"t": "EEE", "signal": "HOLD"}]
    assert sf.filter_for_output(rows, min_total=70) == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_scoring.py -k filter -v`
Expected: 4건 FAIL, `AttributeError: module 'stock_finder' has no attribute 'filter_for_output'`.

- [ ] **Step 3: 필터 함수를 추가한다**

`stock_finder.py` 의 `calc_hitl` 아래에 넣는다.

```python
def filter_for_output(rows: list, min_total: int) -> list:
    """대시보드·콘솔에 낼 행만 남긴다.

    아카이브(history/*.csv)에는 적용하지 않는다. exit_rules.evaluate() 가
    보유 종목의 그날 total 이 exit_total 미만이면 SIGNAL 청산하는데, 점수가
    떨어진 행이 아카이브에서 사라지면 그 판정을 할 수 없게 된다.
    """
    return [r for r in rows
            if r.get("total") is not None and r["total"] >= min_total]
```

- [ ] **Step 4: 테스트를 돌린다**

Run: `python -m pytest tests/test_scoring.py -k filter -v`
Expected: 4 passed.

- [ ] **Step 5: CLI 인자를 추가한다**

`stock_finder.py` 의 인자 정의에 넣는다.

```python
    p.add_argument("--min-total", type=int, default=70,
                   help="대시보드·콘솔 출력 최소 종합점수 (기본 70). "
                        "아카이브에는 적용되지 않는다")
```

- [ ] **Step 6: 대시보드 생성 직전에 필터를 건다**

`stock_finder.py` 에서 `output_path = os.path.join(...)` 줄 **바로 위**에 넣는다. 이력 기록(`history.write_snapshot`)보다 뒤여야 아카이브가 전량 남는다.

```python
    # 아카이브를 기록한 뒤에 필터한다. 순서를 바꾸면 아카이브가 잘려
    # 백테스트의 SIGNAL 청산 판정이 불가능해진다.
    shown = filter_for_output(results, args.min_total)
    print(f"[*] 출력 필터: 종합점수 {args.min_total}점 이상 "
          f"{len(shown)}/{len(results)}종목")
```

이어서 `js_content` 안의 `window.LIVE_STOCKS = {json.dumps(results, ...)}` 를 `shown` 으로 바꾸고, 마지막 요약 출력 다섯 줄의 `results` 도 전부 `shown` 으로 바꾼다.

```python
window.LIVE_STOCKS = {json.dumps(shown, ensure_ascii=False, indent=2)};
```

```python
    print(f"  [완료] {len(shown)}개 종목 · {os.path.basename(output_path)}")
    print(f"  STRONG_BUY: {sum(1 for r in shown if r['signal']=='STRONG_BUY')}개")
    print(f"  BUY:        {sum(1 for r in shown if r['signal']=='BUY')}개")
    print(f"  WATCH:      {sum(1 for r in shown if r['signal']=='WATCH')}개")
    print(f"  HITL 필요:  {sum(1 for r in shown if r['hitl'])}개")
```

**`history.write_snapshot(history_rows, ...)` 는 건드리지 않는다.** 완결성 가드(`is_scan_complete`)도 `results` 기준 그대로 둔다 — 필터 후 개수로 판정하면 점수가 낮은 날마다 스캔이 실패 처리된다.

- [ ] **Step 7: 전체 테스트를 돌린다**

Run: `python -m pytest -q`
Expected: 전부 통과.

- [ ] **Step 8: 커밋**

```bash
git add stock_finder.py tests/test_scoring.py
git commit -m "Show only scores at or above the minimum total"
```

---

### Task 9: 백테스트에 시장·점수 필터 옵션

**Files:**
- Modify: `backtest.py:88-153` (`run`), `backtest.py:203-220` (`main`)
- Test: `tests/test_backtest.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_backtest.py` 끝에 붙인다. 기존 파일의 임포트(`backtest` 등)를 그대로 쓴다.

```python
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
    kept = backtest.filter_rows(ARCHIVE, us_only=True, entry_total=None)
    assert [r["ticker"] for r in kept] == ["AAPL", "MSFT"]


def test_no_filters_keeps_everything():
    kept = backtest.filter_rows(ARCHIVE, us_only=False, entry_total=None)
    assert len(kept) == 3


def test_entry_total_promotes_high_scores_to_buy():
    """entry_total 을 주면 그 점수 이상인 행의 signal 을 BUY 로 바꾼다."""
    kept = backtest.filter_rows(ARCHIVE, us_only=False, entry_total=70)
    assert all(r["signal"] == "BUY" for r in kept)


def test_entry_total_leaves_low_scores_alone():
    rows = [{"ticker": "X", "market": "US", "date": "2026-08-01",
             "total": "69", "signal": "HOLD", "source": "live"}]
    kept = backtest.filter_rows(rows, us_only=False, entry_total=70)
    assert kept[0]["signal"] == "HOLD"


def test_entry_total_handles_blank_total():
    rows = [{"ticker": "X", "market": "US", "date": "2026-08-01",
             "total": "", "signal": "HOLD", "source": "live"}]
    kept = backtest.filter_rows(rows, us_only=False, entry_total=70)
    assert kept[0]["signal"] == "HOLD"


def test_filter_does_not_mutate_the_input_rows():
    backtest.filter_rows(ARCHIVE, us_only=False, entry_total=70)
    assert ARCHIVE[2]["signal"] == "WATCH"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_backtest.py -k "us_only or entry_total or filter" -v`
Expected: 6건 FAIL, `AttributeError: module 'backtest' has no attribute 'filter_rows'`.

- [ ] **Step 3: `filter_rows` 를 추가한다**

`backtest.py` 의 `def run(` 바로 위에 넣는다.

```python
def filter_rows(rows: list, us_only: bool = False,
                entry_total: int = None) -> list:
    """아카이브 행을 진입 조건에 맞게 걸러 낸다.

    us_only 는 과거 아카이브에 남아 있는 한국 행을 뺀다. 7/31~8/22 데이터에는
    KR 이 들어 있어서, 미국 단독 성과를 보려면 여기서 빼야 한다.

    entry_total 은 그 점수 이상인 행의 signal 을 BUY 로 올린다. 원래 진입
    조건은 signal in (BUY, STRONG_BUY) 이고 BUY 정의가 total>=70 and cons>=3
    이라, consensus 를 무시했을 때 성과가 어떻게 달라지는지 보려는 것이다.
    진입 규칙을 바꾸자는 제안이 아니라 비교용이다.

    입력 행을 바꾸지 않는다. 같은 아카이브로 여러 케이스를 돌리기 때문이다.
    """
    out = []
    for r in rows:
        if us_only and r.get("market") != "US":
            continue
        if entry_total is not None:
            total = r.get("total")
            if total not in (None, "") and int(total) >= entry_total:
                r = {**r, "signal": "BUY"}
        out.append(r)
    return out
```

- [ ] **Step 4: `run` 이 필터를 받게 한다**

`backtest.py` 의 `run` 시그니처와 `rows = load_archive(pattern)` 줄을 교체한다.

```python
def run(pattern: str = "history/*.csv", params: er.Params = None,
        costs: ts.Costs = None, us_only: bool = False,
        entry_total: int = None) -> dict:
    """아카이브 전체를 시뮬레이션하고 트레이드·통계·커버리지를 돌려준다."""
    params = params or er.Params()
    costs = costs or ts.Costs()

    rows = filter_rows(load_archive(pattern), us_only=us_only,
                       entry_total=entry_total)
```

- [ ] **Step 5: CLI 인자를 추가한다**

`backtest.py` 의 `main()` 에서 인자 정의에 넣고, `run(...)` 호출에 전달한다.

```python
    p.add_argument("--us-only", action="store_true",
                   help="아카이브의 한국 행을 제외한다")
    p.add_argument("--entry-total", type=int, default=None,
                   help="이 점수 이상이면 BUY 로 간주해 진입한다 (비교용)")
```

`run` 호출을 교체한다.

```python
    result = run(args.history, params, us_only=args.us_only,
                 entry_total=args.entry_total)
```

- [ ] **Step 6: 테스트를 돌린다**

Run: `python -m pytest tests/test_backtest.py -v`
Expected: 전부 통과.

- [ ] **Step 7: 전체 테스트를 돌린다**

Run: `python -m pytest -q`
Expected: 전부 통과.

- [ ] **Step 8: 커밋**

```bash
git add backtest.py tests/test_backtest.py
git commit -m "Filter the archive by market and entry score"
```

---

### Task 10: 워크플로 인자 갱신

**Files:**
- Modify: `.github/workflows/scan.yml` (`workflow_dispatch` 입력, 스캔 실행 스텝)

- [ ] **Step 1: `workflow_dispatch` 입력을 교체한다**

`min_kr_cap` 과 `market` 입력을 지우고 ETF 입력을 넣는다.

```yaml
  workflow_dispatch:
    inputs:
      min_us_cap:
        description: '미국 주식 최소 시가총액 (USD, 기본 1e10 = $10B)'
        default: '1e10'
        required: false
      min_etf_aum:
        description: '미국 ETF 최소 AUM (USD, 기본 1e9 = $1B)'
        default: '1e9'
        required: false
      min_total:
        description: '출력 최소 종합점수 (기본 70)'
        default: '70'
        required: false
```

- [ ] **Step 2: 스캔 실행 스텝을 교체한다**

```yaml
        run: |
          python stock_finder.py \
            --min-us-cap ${{ github.event.inputs.min_us_cap || '1e10' }} \
            --min-etf-aum ${{ github.event.inputs.min_etf_aum || '1e9' }} \
            --min-total ${{ github.event.inputs.min_total || '70' }} \
            --sleep 0.2 \
            --workers 4
```

- [ ] **Step 3: YAML 이 유효한지 확인한다**

```bash
python -c "
import yaml
d = yaml.safe_load(open('.github/workflows/scan.yml', encoding='utf-8'))
on = d.get('on', d.get(True))
print('inputs:', list(on['workflow_dispatch']['inputs']))
print('jobs:', list(d['jobs']))
"
```

Expected: `inputs: ['min_us_cap', 'min_etf_aum', 'min_total']`, `jobs: ['scan', 'deploy-pages', 'report', 'notify-failure']`

- [ ] **Step 4: `--market` 이 남아 있지 않은지 확인한다**

```bash
grep -n "market\|min_kr_cap" .github/workflows/scan.yml
```

Expected: 아무것도 출력되지 않아야 한다.

- [ ] **Step 5: 커밋**

```bash
git add .github/workflows/scan.yml
git commit -m "Pass the US and ETF scan options from the workflow"
```

---

### Task 11: 손절·익절 재계산 (백테스트 3케이스)

코드 변경이 아니라 **분석**이다. 앞 Task 들이 전부 끝난 뒤에 돌린다.

**Files:**
- Create: `docs/superpowers/specs/2026-08-22-exit-recalc-results.md`

- [ ] **Step 1: 기준선을 돌린다 (현행, KR 포함)**

```bash
python backtest.py --history "history/*.csv" > /tmp/case1-baseline.txt 2>&1
python backtest.py --history "history/*.csv" --use-target > /tmp/case1-target.txt 2>&1
tail -30 /tmp/case1-baseline.txt
```

- [ ] **Step 2: US 단독을 돌린다**

```bash
python backtest.py --history "history/*.csv" --us-only > /tmp/case2-baseline.txt 2>&1
python backtest.py --history "history/*.csv" --us-only --use-target > /tmp/case2-target.txt 2>&1
tail -30 /tmp/case2-baseline.txt
```

- [ ] **Step 3: 70점 완화를 돌린다**

```bash
python backtest.py --history "history/*.csv" --us-only --entry-total 70 > /tmp/case3-baseline.txt 2>&1
python backtest.py --history "history/*.csv" --us-only --entry-total 70 --use-target > /tmp/case3-target.txt 2>&1
tail -30 /tmp/case3-baseline.txt
```

- [ ] **Step 4: 현재 보유 포지션의 손절선을 뽑는다**

```bash
python stops.py --history "history/*.csv"
```

- [ ] **Step 5: 결과를 문서로 남긴다**

`docs/superpowers/specs/2026-08-22-exit-recalc-results.md` 를 만들고, 6개 실행의 다음 값을 표로 정리한다: 트레이드 수, 청산 건수, 승률, 평균 순수익률, 합계 R, 청산 사유별 분포(TIME/SIGNAL/STOP/TRAIL/TARGET), 미결 포지션 수.

문서 맨 위에 다음 경고를 그대로 적는다.

```markdown
> 이 결과는 파이프라인 검증용이다. 시그널 성능의 근거가 아니다.
> 아카이브의 82%가 backfill 이라 스코어가 미확정 봉 결함에 오염돼 있고,
> 청산 표본이 한 자릿수다. 보유 상한 60거래일을 채운 표본이 나오기 전까지
> 승률·평균은 무의미하다. ATR 배수는 이 결과를 근거로 바꾸지 않는다.
```

- [ ] **Step 6: 커밋**

```bash
git add docs/superpowers/specs/2026-08-22-exit-recalc-results.md
git commit -m "Record the exit rule recalculation across three entry cases"
```

---

### Task 12: 첫 실전 스캔 확인

- [ ] **Step 1: 푸시한다**

```bash
git pull --rebase origin main
git push
```

- [ ] **Step 2: 수동으로 스캔을 돌린다**

```bash
gh workflow run "AI Stock Finder Auto Scan" --ref main
gh run watch "$(gh run list --workflow='AI Stock Finder Auto Scan' --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status --interval 30
```

- [ ] **Step 3: 로그에서 유니버스 구성을 확인한다**

```bash
gh run view <RUN_ID> --log | sed -e 's/\x1b\[[0-9;]*m//g' | grep -E "유니버스|종목 수집|총 [0-9]+종목|출력 필터|수집률"
```

확인할 것:
- `pykrx` 관련 줄이 **하나도 없어야** 한다.
- `미국 ETF N종목 수집 완료` 가 있어야 한다. N 이 0이면 FMP 의 `isEtf` 파라미터가 안 먹은 것이므로 응답을 직접 확인한다.
- `총 N종목 (주식: X, ETF: Y)` 의 Y 가 0이 아니어야 한다.
- `출력 필터: 종합점수 70점 이상 N/M종목` 이 있어야 한다.
- 소요 시간이 120분 상한에 여유가 있는지. 없으면 `--workers` 를 올리거나 `--min-etf-aum` 을 높인다.

- [ ] **Step 4: 아카이브를 확인한다**

```bash
git pull
python -c "
import csv, collections
rows = list(csv.DictReader(open('history/2026-08-22.csv', encoding='utf-8')))
print('총', len(rows))
print('asset_type', dict(collections.Counter(r['asset_type'] for r in rows)))
print('market', dict(collections.Counter(r['market'] for r in rows)))
etf = [r for r in rows if r['asset_type']=='ETF']
print('ETF filing 빈 값:', all(r['filing']=='' for r in etf))
print('ETF total>=70:', sum(1 for r in etf if int(r['total'])>=70), '/', len(etf))
"
```

확인할 것: `market` 은 `US` 만, `asset_type` 에 `ETF` 가 있고, ETF 의 `filing` 이 전부 빈 값이며, **아카이브 행 수가 70점 필터와 무관하게 전량**이어야 한다.

- [ ] **Step 5: 대시보드가 필터된 결과를 보여주는지 확인한다**

```bash
python -c "
import re
src = open('dashboard_data.js', encoding='utf-8').read()
m = re.search(r'window\.LIVE_STOCKS = (\[.*)', src, re.S)
import json
rows = json.loads(m.group(1).rstrip().rstrip(';'))
print('대시보드 행:', len(rows))
print('최저 total:', min(r['total'] for r in rows))
print('ETF:', sum(1 for r in rows if r.get('at')=='ETF'))
"
```

Expected: `최저 total` 이 70 이상.

---

## 자체 점검

**스펙 커버리지**

| 스펙 항목 | Task |
|---|---|
| 설계 결정 1 — 한국 제거, ETF 조회, 거래소 필터 없음, 레버리지 제외 | 3, 4, 5 |
| 설계 결정 2 — ETF 두 축 재정규화 | 2 |
| 설계 결정 3 — consensus 비율화, 주식 판정 불변 | 1 |
| 설계 결정 4 — `asset_type` 컬럼, filing/value 빈 값 | 6 |
| 설계 결정 5 — 70점 출력 필터, 아카이브 전량 | 8 |
| 설계 결정 6 — 백테스트 3케이스 | 9, 11 |
| 테스트 7항목 | 1, 2, 3, 4, 5, 6, 8, 9 |
| `pykrx` 제거 | 5 |
| ETF 스캔 소요시간 확인 | 12 |

**이름 일관성**

`calc_signal(total, cons, n_axes)` · `calc_total_etf(tech, macro)` · `calc_consensus_etf(tech, macro)` · `is_leveraged_or_inverse(name)` · `parse_etf_rows(data, min_aum)` · `fetch_us_etf_universe(min_aum, limit)` · `filter_for_output(rows, min_total)` · `backtest.filter_rows(rows, us_only, entry_total)` — Task 간 표기가 일치한다.

유니버스 튜플은 Task 4 에서 5칸으로 정의하고 Task 5·7 에서 같은 순서(`ticker, name, market, sector, asset_type`)로 쓴다.

**순서 의존성**

Task 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 순으로 진행한다. Task 7 은 4·5·6 이 끝나야 하고(5칸 튜플과 `asset_type` 컬럼이 있어야 한다), Task 11 은 9 가 끝나야 한다.
