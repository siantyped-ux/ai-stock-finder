# 주식 축 가중치 재조정 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 주식 총점의 filing 가중치를 0.30 → 0.20 으로 낮추고 value 를 0.20 → 0.30 으로 올리되, 그 판정을 나중에 되읽을 수 있도록 측정 도구를 같은 변경분에 넣는다.

**Architecture:** 순서가 중요하다. 먼저 `forward_returns.py` 에 축별 순위상관을 내는 `--by-axis` 를 만들고(Task 1~3), 그 도구로 **변경 전 기준선을 기록한 뒤**(Task 4), 가중치를 바꾸고(Task 5), 아카이브를 재계산한다(Task 6). 도구를 먼저 만드는 이유는 기준선이 도구의 출력이어야 나중에 같은 명령으로 비교되기 때문이다 — 일회성 분석으로 남긴 숫자는 재현되지 않는다.

**Tech Stack:** Python 3.11 표준 라이브러리 + pytest. 새 의존성 없다. 순위상관은 `statistics` 만으로 직접 구현한다 (scipy 를 끌어오지 않는다 — 이 저장소는 numpy/pandas/yfinance 외 과학 스택이 없다).

**설계 문서:** `docs/superpowers/specs/2026-09-07-stock-axis-reweight-design.md`

**봉 캐시는 갱신하지 않는다.** `.cache/recompute_frames.json` 이 2026-09-02 자라
최근 5거래일이 측정에서 빠지지만, **이 계획에서는 그대로 둔다.** 변경 전(Task 4)과
변경 후(Task 6)를 같은 캐시로 재야 둘이 비교되기 때문이다. 중간에 갱신하면 가중치
효과와 표본 변화가 섞여 어느 쪽인지 알 수 없다.

갱신은 트립와이어 판정일(2026-09-29) 직전에 한 번, 두 측정을 모두 다시 내면서
한다. `python recompute_history.py --refresh` 는 티커 1,685개를 다시 받으므로
rate limit 이 그날 스캔에 영향을 줄 수 있다 — 스캔 직후(KST 08:20 이후)에 돌릴 것.

---

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `forward_returns.py` | 선행 수익률 수집·출력 | 순위상관 4함수 + `--by-axis` 추가 |
| `tests/test_forward_returns.py` | 위의 경계 조건 | 새 함수 테스트 추가 |
| `stock_finder.py` | 점수 산식 | 주식 가중치 상수화 + 맞바꿈 |
| `tests/test_scoring.py` | 산식 회귀 | 손계산 기대값 갱신 + 부등호 고정 |
| `.gitignore` | 추적 제외 | `history_pre_reweight/` 추가 |
| `docs/superpowers/specs/2026-09-07-stock-axis-reweight-design.md` | 기준선·트립와이어 | 관측 로그에 도구 출력 기록 |

`forward_returns.py` 에 넣고 새 파일을 만들지 않는 이유는 그 스크립트가 이미 캐시 로딩(`load_prices`)과 선행 수익률 계산(`forward_return`)을 갖고 있어서다. 새 파일은 그 둘을 복제하거나 임포트해야 한다.

---

## Task 1: 순위상관 순수 함수

**Files:**
- Modify: `forward_returns.py` (`forward_return` 정의 뒤, `collect` 앞)
- Test: `tests/test_forward_returns.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_forward_returns.py` 끝에 추가:

```python
# ─── 순위상관 ───────────────────────────────────────────────
def test_ranks_handles_ties_with_average():
    """동점은 평균 순위를 나눠 갖는다. 안 그러면 입력 순서가 상관을 흔든다."""
    assert fr._ranks([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]


def test_spearman_of_a_perfect_increase_is_one():
    assert fr.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)


def test_spearman_of_a_perfect_decrease_is_minus_one():
    assert fr.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_uses_rank_not_magnitude():
    """단조증가면 값의 크기와 무관하게 1 이다. 이것이 피어슨과의 차이다."""
    assert fr.spearman([1, 2, 3, 4], [1, 2, 3, 1000]) == pytest.approx(1.0)


def test_spearman_of_a_constant_is_none():
    """한쪽이 상수면 분모가 0 이다. 0.0 을 내면 '무상관' 과 구별되지 않는다."""
    assert fr.spearman([5, 5, 5, 5], [1, 2, 3, 4]) is None


def test_spearman_needs_two_points():
    assert fr.spearman([1], [2]) is None


def test_spearman_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        fr.spearman([1, 2, 3], [1, 2])
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_forward_returns.py -q -k "ranks or spearman"`
Expected: FAIL — `AttributeError: module 'forward_returns' has no attribute '_ranks'`

- [ ] **Step 3: 최소 구현을 넣는다**

`forward_returns.py` 의 `def collect(` 바로 위에 추가:

```python
def _ranks(values: list) -> list:
    """동점을 평균 순위로 묶은 순위 목록.

    동점 처리를 직접 하는 것은 축 점수가 정수라 동점이 흔하기 때문이다.
    입력 순서대로 순위를 매기면 같은 데이터가 파일 정렬에 따라 다른 상관을
    낸다.
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(xs: list, ys: list):
    """순위상관. 잴 수 없으면 None.

    피어슨이 아니라 순위상관인 것은 축 점수가 0~100 로 잘린 값이고 수익률에
    꼬리가 두껍기 때문이다. 크기가 아니라 순서만 본다.

    None 을 내는 경우가 둘이다 - 표본이 2 미만이거나 한쪽이 상수다. 0.0 으로
    뭉개면 '무상관' 과 '못 쟀음' 이 구별되지 않고, 이 도구의 판정이 전부
    부호를 보는 것이라 그 구별이 필요하다.
    """
    if len(xs) != len(ys):
        raise ValueError(f"두 목록의 길이가 다르다: {len(xs)} vs {len(ys)}")
    if len(xs) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx)
           * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else None
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_forward_returns.py -q -k "ranks or spearman"`
Expected: PASS, 7 passed

- [ ] **Step 5: 커밋**

```bash
git add forward_returns.py tests/test_forward_returns.py
git commit -m "Add rank correlation to forward_returns"
```

---

## Task 2: 축별 표본 수집과 기간 분할

**Files:**
- Modify: `forward_returns.py`
- Test: `tests/test_forward_returns.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_forward_returns.py` 끝에 추가:

```python
# ─── 축별 수집 ──────────────────────────────────────────────
AXIS_CSV_HEADER = ("date,ticker,bar_date,tech,flow,filing,value,total\n")


def _write_axis_archive(tmp_path, rows):
    """축 열만 가진 최소 아카이브를 만든다."""
    p = tmp_path / "2026-08-03.csv"
    p.write_text(AXIS_CSV_HEADER + "".join(rows), encoding="utf-8")
    return str(tmp_path / "*.csv")


def test_collect_axes_pairs_each_axis_with_its_return(tmp_path):
    pattern = _write_axis_archive(tmp_path, [
        "2026-08-03,AAA,2026-08-03,70,60,50,40,58\n",
    ])
    got = fr.collect_axes(pattern, PRICES, (1,))
    # 축 다섯 개 × horizon 하나
    assert len(got) == 5
    assert ("2026-08-03", 1, "tech", 70, pytest.approx(10.0)) in got
    assert ("2026-08-03", 1, "total", 58, pytest.approx(10.0)) in got


def test_collect_axes_skips_blank_axis_values(tmp_path):
    """ETF 행은 filing·value 가 비어 있다. 0 으로 읽으면 상관이 오염된다."""
    pattern = _write_axis_archive(tmp_path, [
        "2026-08-03,AAA,2026-08-03,70,60,,,65\n",
    ])
    got = fr.collect_axes(pattern, PRICES, (1,))
    assert sorted(r[2] for r in got) == ["flow", "tech", "total"]


def test_collect_axes_skips_rows_without_a_forward_return(tmp_path):
    """마지막 봉 뒤의 행은 잴 수 없다. 마지막 값으로 대체하지 않는다."""
    pattern = _write_axis_archive(tmp_path, [
        "2026-08-07,AAA,2026-08-07,70,60,50,40,58\n",
    ])
    assert fr.collect_axes(pattern, PRICES, (1,)) == []


# ─── 기간 분할 ──────────────────────────────────────────────
def test_median_date_splits_distinct_dates():
    rows = [("2026-08-01", 1, "tech", 70, 1.0),
            ("2026-08-01", 1, "flow", 60, 1.0),
            ("2026-08-02", 1, "tech", 70, 1.0),
            ("2026-08-03", 1, "tech", 70, 1.0)]
    # 고유 날짜 3개의 가운데
    assert fr.median_date(rows) == "2026-08-02"


def test_median_date_of_nothing_is_empty():
    assert fr.median_date([]) == ""
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_forward_returns.py -q -k "collect_axes or median_date"`
Expected: FAIL — `AttributeError: module 'forward_returns' has no attribute 'collect_axes'`

- [ ] **Step 3: 최소 구현을 넣는다**

`forward_returns.py` 의 `BUY_SIGNALS = ("BUY", "STRONG_BUY")` 바로 아래에 추가:

```python
# 축별 상관을 잴 대상. total 을 함께 넣는 것은 의도다 - 축을 어떻게 섞었을 때
# 총점이 나아지는지가 이 기능을 만든 이유다.
AXES = ("tech", "flow", "filing", "value", "total")

# 이 아래로는 순위상관을 내지 않는다. 축 점수가 정수라 표본이 작으면 동점이
# 상관을 지배한다.
MIN_AXIS_SAMPLE = 200
```

`spearman` 정의 뒤에 추가:

```python
def collect_axes(pattern: str, prices: dict, horizons: tuple) -> list:
    """(날짜, horizon, 축 이름, 축 값, 선행수익률) 튜플 목록.

    축 값이 빈 행은 그 축에서만 빠진다. 빈 값을 0 으로 읽으면 ETF 의
    filing·value 가 최저점으로 들어가 상관이 통째로 오염된다.
    """
    out = []
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                bar_date = r.get("bar_date")
                if not bar_date:
                    continue
                for n in horizons:
                    ret = forward_return(prices, r["ticker"], bar_date, n)
                    if ret is None:
                        continue
                    for ax in AXES:
                        raw = r.get(ax)
                        if not raw:
                            continue
                        out.append((r["date"], n, ax, int(raw), ret))
    return out


def median_date(rows: list) -> str:
    """고유 날짜의 중앙값. 기간을 반으로 가르는 기준일이다.

    고정 날짜를 기본값으로 두지 않는 것은 아카이브가 매일 자라기 때문이다.
    한 번 적어 둔 날짜는 시간이 갈수록 후반을 비대칭으로 키운다.
    """
    dates = sorted({r[0] for r in rows})
    return dates[len(dates) // 2] if dates else ""
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_forward_returns.py -q -k "collect_axes or median_date"`
Expected: PASS, 5 passed

- [ ] **Step 5: 커밋**

```bash
git add forward_returns.py tests/test_forward_returns.py
git commit -m "Collect forward returns per scoring axis"
```

---

## Task 3: `--by-axis` 판정과 CLI

**Files:**
- Modify: `forward_returns.py`
- Test: `tests/test_forward_returns.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_forward_returns.py` 끝에 추가:

```python
# ─── 축 판정 ────────────────────────────────────────────────
def _axis_rows(n_each, early_sign, late_sign, cut="2026-08-15"):
    """전·후반 각각 지정한 부호의 완전 단조 표본을 만든다."""
    rows = []
    for i in range(n_each):
        rows.append(("2026-08-01", 5, "tech", i, i * early_sign))
        rows.append(("2026-08-20", 5, "tech", i, i * late_sign))
    return rows, cut


def test_axis_verdict_reports_rho_for_each_period():
    rows, cut = _axis_rows(200, +1, +1)
    got = fr.axis_verdict(rows, "tech", 5, cut)
    assert got["all"] == pytest.approx(1.0)
    assert got["early"] == pytest.approx(1.0)
    assert got["late"] == pytest.approx(1.0)
    assert got["flip"] is False
    assert got["n_early"] == 200 and got["n_late"] == 200


def test_axis_verdict_flags_a_sign_flip():
    """전·후반 부호가 다르면 신호로 보지 않는다. 이 표시가 판정의 핵심이다."""
    rows, cut = _axis_rows(200, +1, -1)
    got = fr.axis_verdict(rows, "tech", 5, cut)
    assert got["early"] > 0 and got["late"] < 0
    assert got["flip"] is True


def test_axis_verdict_returns_none_below_the_sample_floor():
    rows, cut = _axis_rows(5, +1, +1)
    got = fr.axis_verdict(rows, "tech", 5, cut)
    assert got["all"] is None
    assert got["flip"] is False


def test_axis_verdict_ignores_other_axes_and_horizons():
    rows = [("2026-08-01", 5, "tech", 1, 1.0),
            ("2026-08-01", 5, "flow", 1, 1.0),
            ("2026-08-01", 10, "tech", 1, 1.0)]
    got = fr.axis_verdict(rows, "tech", 5, "2026-08-01")
    assert got["n_all"] == 1


# ─── rho 하한 ───────────────────────────────────────────────
def test_rho_floor_shrinks_as_the_sample_grows():
    """표본이 클수록 0 과 구별할 수 있는 rho 가 작아진다."""
    assert fr._rho_floor(101) == pytest.approx(0.1)
    assert fr._rho_floor(10001) == pytest.approx(0.01)


def test_rho_floor_of_a_thin_sample_is_none():
    assert fr._rho_floor(1) is None
    assert fr._rho_floor(0) is None


def test_axis_verdict_reports_a_floor_for_each_period():
    """얇은 후반의 부호를 얼마나 믿을지 사람이 가늠할 재료를 함께 낸다."""
    rows, cut = _axis_rows(200, +1, +1)
    got = fr.axis_verdict(rows, "tech", 5, cut)
    assert got["floor_early"] == pytest.approx(199 ** -0.5)
    assert got["floor_late"] == pytest.approx(199 ** -0.5)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_forward_returns.py -q -k axis_verdict`
Expected: FAIL — `AttributeError: module 'forward_returns' has no attribute 'axis_verdict'`

- [ ] **Step 3: 판정 함수와 출력을 넣는다**

`median_date` 정의 뒤에 추가:

```python
def _rho_floor(n_rows: int):
    """그 표본에서 0 과 구별할 수 있는 rho 의 하한. 표본이 없으면 None.

    1/sqrt(n-1) 은 순위상관 표준오차의 통상 근사다. 이 값을 함께 내는 것은
    전·후반 표본 크기가 구조적으로 다르기 때문이다 - 아카이브 끝 2주는
    10거래일 선행 봉이 아직 없어 후반이 늘 얇다. 실측(2026-09-07): 5일은
    전반 15,073 / 후반 11,572 인데 10일은 15,034 / 4,649 다. 양쪽 다
    MIN_AXIS_SAMPLE 을 넘어서 표본 수만으로는 이 차이가 드러나지 않는다.

    **이 하한은 낙관적이다.** 같은 종목의 연속일 행이 겹쳐 실효 표본이 n
    보다 작으므로 진짜 오차는 이보다 크다. 판정을 자동화하는 데 쓰지 말고,
    얇은 쪽의 부호를 얼마나 믿을지 사람이 가늠하는 데만 쓴다.
    """
    return (n_rows - 1) ** -0.5 if n_rows > 1 else None


def axis_verdict(rows: list, axis: str, n: int, cut: str) -> dict:
    """한 축·한 horizon 의 전체·전반·후반 순위상관과 부호 뒤집힘 여부.

    부호 뒤집힘을 따로 내는 것은 이 도구의 판정이 상관의 크기가 아니라
    방향의 안정성이기 때문이다. 아카이브가 짧고 같은 종목의 연속일 행이
    겹쳐서 rho 의 절대값에는 의미를 두지 않는다.

    그래도 `floor_early` · `floor_late` 를 함께 내는 것은, 부호가 뒤집혔을
    때 그것이 신호인지 얇은 표본의 잡음인지를 사람이 구별해야 하기
    때문이다. rho 가 제 하한보다 작으면 그 부호는 읽지 않는다.
    """
    got = [r for r in rows if r[2] == axis and r[1] == n]
    early = [r for r in got if r[0] < cut]
    late = [r for r in got if r[0] >= cut]

    def rho(part):
        if len(part) < MIN_AXIS_SAMPLE:
            return None
        return spearman([r[3] for r in part], [r[4] for r in part])

    r_all, r_early, r_late = rho(got), rho(early), rho(late)
    flip = (r_early is not None and r_late is not None
            and r_early * r_late < 0)
    return {"all": r_all, "early": r_early, "late": r_late, "flip": flip,
            "n_all": len(got), "n_early": len(early), "n_late": len(late),
            "floor_early": _rho_floor(len(early)),
            "floor_late": _rho_floor(len(late))}


def show_axes(rows: list, horizons: tuple, cut: str) -> None:
    """축별 판정표. 부호가 전·후반 모두 유지되는 축만 신호로 본다."""
    print("\n" + "=" * 74)
    print(f"  축별 순위상관 · 기간 분할 기준일 {cut}")
    print("=" * 74)
    print("  부호가 전·후반 모두 유지되는 축만 신호로 본다. 크기는 보지 않는다 -")
    print(f"  같은 종목의 연속일 행이 겹쳐 실효 표본이 n 보다 훨씬 작다.")
    print(f"  표본 {MIN_AXIS_SAMPLE} 미만인 칸은 '-' 다.")
    print("  하한은 1/sqrt(n-1) 이다. rho 가 제 하한보다 작으면 그 부호는")
    print("  읽지 않는다 - 아카이브 끝 2주는 긴 horizon 의 선행 봉이 없어")
    print("  후반 표본이 구조적으로 얇다.")
    for n in horizons:
        print(f"\n  [{n}거래일]")
        print(f"    {'축':<8}{'전체':>10}{'전반':>10}{'후반':>10}"
              f"   {'표본(전/후)':<18}{'하한(전/후)':<18}")
        for ax in AXES:
            v = axis_verdict(rows, ax, n, cut)
            cells = "".join(
                f"{x:>+10.4f}" if x is not None else f"{'-':>10}"
                for x in (v["all"], v["early"], v["late"]))
            floors = "/".join(
                f"{x:.4f}" if x is not None else "-"
                for x in (v["floor_early"], v["floor_late"]))
            note = ""
            if v["flip"]:
                thin = [side for side in ("early", "late")
                        if v[side] is not None
                        and v[f"floor_{side}"] is not None
                        and abs(v[side]) < v[f"floor_{side}"]]
                note = ("  <- 부호 뒤집힘 (한쪽이 하한 미만 · 판정 보류)"
                        if thin else "  <- 부호 뒤집힘")
            print(f"    {ax:<8}{cells}   "
                  f"{str(v['n_early']) + '/' + str(v['n_late']):<18}"
                  f"{floors:<18}{note}")
```

`main()` 의 `p.add_argument("--horizons", ...)` 뒤에 추가:

```python
    p.add_argument("--by-axis", action="store_true",
                   help="신호 대신 축별 순위상관을 낸다 (기간을 반으로 갈라 "
                        "부호가 유지되는지 함께 본다)")
    p.add_argument("--cut", default="",
                   help="기간 분할 기준일 YYYY-MM-DD (생략하면 날짜 중앙값)")
```

`main()` 의 `prices = load_prices()` / `print(f"[*] 일봉 캐시 ...")` 두 줄 **뒤**, `pairs = []` **앞**에 추가:

```python
    if args.by_axis:
        rows = collect_axes(args.history, prices, horizons)
        if not rows:
            print("[!] 잴 수 있는 행이 없다. 캐시가 아카이브보다 오래된 것이 "
                  "가장 흔한 원인이다 - recompute_history.py --refresh 를 볼 것")
            return
        show_axes(rows, horizons, args.cut or median_date(rows))
        print("\n  주의: 전략은 3개월 보유인데 여기서 재는 것은 위 거래일 수다.")
        return
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_forward_returns.py -q`
Expected: PASS, 전체 통과 (기존 테스트 포함)

- [ ] **Step 5: 실제 아카이브에서 돌려 본다**

Run: `python forward_returns.py --history "history/*.csv" --by-axis`
Expected: 5·10거래일 표가 나오고, `filing` 행의 전체·전반·후반이 셋 다 음수,
`value` 행이 셋 다 양수, `tech`·`flow`·`total` 의 5거래일 행에 "부호 뒤집힘"
표시.

**10거래일 표본이 후반에서 얇은 것은 정상이다.** 실측 15,034 / 4,649 로
3배 넘게 차이 난다 — 아카이브 끝 2주는 10거래일 선행 봉이 아직 없기
때문이고, 봉 캐시가 2026-09-02 자라 더 두드러진다. 이것이 하한 열을 낸
이유다. filing 의 10일 후반은 rho -0.118 에 하한 약 0.0147 이라 하한을
넉넉히 넘으므로 그 부호는 읽어도 된다.

이와 다르면 멈추고 보고할 것. 설계 문서의 근거가 재현되지 않는다는 뜻이다.

- [ ] **Step 6: 커밋**

```bash
git add forward_returns.py tests/test_forward_returns.py
git commit -m "Report axis correlation with a period split"
```

---

## Task 4: 변경 전 기준선을 도구 출력으로 기록

가중치를 바꾸기 **전에** 한다. 이 순서가 이 계획의 요점이다.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-07-stock-axis-reweight-design.md`

- [ ] **Step 1: 기준선을 뽑는다**

Run: `python forward_returns.py --history "history/*.csv" --by-axis`

출력 전체를 복사해 둔다.

- [ ] **Step 2: 관측 로그에 도구 출력을 붙인다**

설계 문서 맨 끝 `## 관측 로그` 표 아래에 추가한다. `<분할 기준일>` 과 표
안의 값은 Step 1 의 실제 출력으로 채운다 — 지어내지 말 것.

```markdown
2026-09-07 기준선. 아래는 `python forward_returns.py --history "history/*.csv"
--by-axis` 의 출력을 그대로 옮긴 것이다. 이 문서를 쓸 때 쓴 일회성 분석과
달리 같은 명령으로 재현된다.

    (Step 1 의 출력 전체를 여기에 붙인다)

분할 기준일은 도구가 고른 날짜 중앙값이다. 문서 본문의 표는 2026-08-19 로
갈랐던 최초 분석이라 숫자가 조금 다를 수 있다. **판정에 쓰는 것은 이쪽이다** -
같은 명령으로 다시 낼 수 있는 값이라야 트립와이어를 읽을 수 있다.
```

- [ ] **Step 3: 커밋**

```bash
git add docs/superpowers/specs/2026-09-07-stock-axis-reweight-design.md
git commit -m "Record the pre-change baseline from the tool"
```

---

## Task 5: 가중치 상수화와 맞바꿈

**Files:**
- Modify: `stock_finder.py` (`calc_total` 정의부)
- Test: `tests/test_scoring.py:120-127`

- [ ] **Step 1: 테스트를 새 기대값으로 고친다**

`tests/test_scoring.py` 의 `test_stock_total_matches_hand_calculation` 을
아래로 **교체**하고, 그 뒤에 두 테스트를 추가한다:

```python
def test_stock_total_matches_hand_calculation():
    # 80*0.30 + 60*0.20 + 70*0.20 + 50*0.30 = 24 + 12 + 14 + 15 = 65
    assert sf.calc_total(80, 60, 70, 50) == 65


def test_stock_weights_sum_to_one():
    """합이 1 이 아니면 ETF 와 척도가 어긋나 70 문턱의 의미가 갈린다."""
    assert (sf.STOCK_TECH_WEIGHT + sf.STOCK_FLOW_WEIGHT
            + sf.STOCK_FILING_WEIGHT
            + sf.STOCK_VALUE_WEIGHT) == pytest.approx(1.0)


def test_value_outweighs_filing():
    """value 가 filing 보다 무겁다. 이 부등호가 2026-09-07 변경의 전부다.

    설계: docs/superpowers/specs/2026-09-07-stock-axis-reweight-design.md
    """
    assert sf.STOCK_VALUE_WEIGHT > sf.STOCK_FILING_WEIGHT
    # 같은 점수를 filing 이 아니라 value 가 들고 있을 때 총점이 더 높다
    assert sf.calc_total(50, 50, 80, 50) < sf.calc_total(50, 50, 50, 80)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_scoring.py -q -k "stock_total or weights or outweighs"`
Expected: FAIL — `assert 67 == 65` 와 `AttributeError: ... 'STOCK_TECH_WEIGHT'`

- [ ] **Step 3: 상수를 만들고 `calc_total` 을 고친다**

`stock_finder.py` 의 `def calc_total(` **바로 위**에 상수를 넣고, 함수 본문의
`return` 한 줄을 교체한다:

```python
# 주식 축 가중치. 2026-09-07 에 filing 과 value 의 몫을 맞바꿨다.
#
# filing 은 잰 네 칸(5·10거래일 × 전·후반) 전부에서 선행 수익률과 음의
# 순위상관이었고 value 는 네 칸 전부 양수였다. tech·flow 는 부호가 뒤집혀
# 신호로 볼 수 없다. 확인: forward_returns.py --by-axis
#
# 0.20/0.30 은 아카이브에서 고른 값이 아니다. "일관되게 해로운 축의 몫을
# 일관되게 이로운 축에 준다" 는 규칙이 부르는 숫자다. .15/.35 가 네 칸 전부
# 조금 더 좋았지만 차이가 노이즈와 구분되지 않아 쓰지 않는다 - 아카이브를
# 보고 소수점을 정하면 exit_total 45 처럼 판정 불가 상태가 된다.
#
# 이 변경은 BUY 개수를 줄이지 않고(69 -> 71종목) 총점에 우위를 만들지도
# 않는다. 목적은 해로운 입력의 몫을 줄이는 것 하나다.
#
# 설계·트립와이어: docs/superpowers/specs/2026-09-07-stock-axis-reweight-design.md
STOCK_TECH_WEIGHT = 0.30
STOCK_FLOW_WEIGHT = 0.20
STOCK_FILING_WEIGHT = 0.20
STOCK_VALUE_WEIGHT = 0.30
```

`calc_total` 의 마지막 줄을 교체:

```python
    return int(round(tech * STOCK_TECH_WEIGHT + flow_ * STOCK_FLOW_WEIGHT
                     + filing * STOCK_FILING_WEIGHT
                     + value * STOCK_VALUE_WEIGHT))
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_scoring.py -q`
Expected: PASS, 전체 통과

- [ ] **Step 5: 낡아진 문서 문자열을 고친다**

`recompute_history.py` 머리말이 가중치를 글로 적어 두고 있어 이제 틀렸다.
그 표의 `이후` 두 줄을 아래로 교체한다:

```
    2026-08-24  주식 tech .30 + flow .20 + filing .30 + value .20
                ETF  tech .60 + flow .40
    2026-09-07  주식 tech .30 + flow .20 + filing .20 + value .30  (ETF 그대로)
```

바로 아래 문단의 "이전/이후" 표현도 "2026-08-24 이전/이후" 로 읽히도록
그대로 두면 된다 — 재계산이 필요한 이유(두 척도 혼재)는 이번 변경에도 똑같이
적용된다.

- [ ] **Step 6: 전체 테스트를 돌린다**

Run: `python -m pytest -q`
Expected: PASS. `tests/test_recompute_history.py:99` 는 `sf.calc_total` 을
직접 불러 비교하므로 가중치가 바뀌어도 통과한다. 다른 곳이 깨지면 멈추고
보고할 것 — 총점 가중치를 하드코딩한 곳이 더 있다는 뜻이다.

- [ ] **Step 7: 커밋**

```bash
git add stock_finder.py tests/test_scoring.py recompute_history.py
git commit -m "Swap the filing and value weights"
```

---

## Task 6: 아카이브 재계산

여기서부터는 데이터 변경이다. 백업이 없으면 시작하지 말 것.

**Files:**
- Modify: `.gitignore`
- Modify: `history/*.csv` (재계산 결과)

- [ ] **Step 1: 백업 폴더를 추적 제외에 넣는다**

`.gitignore` 의 `history_pre_split/` 줄 **뒤**에 추가:

```
history_pre_reweight/
```

- [ ] **Step 2: 먼저 dry-run 으로 차이를 본다**

Run: `python recompute_history.py --pattern "history/*.csv"`
Expected: 재계산 전후 비교가 출력되고 **아무 파일도 쓰이지 않는다**.
total 이 대부분 ±3점 안에서 움직여야 한다. 10점 넘게 움직이는 행이 많으면
멈추고 보고할 것 — 설계의 "분포가 거의 안 움직인다" 가정이 깨진 것이다.

- [ ] **Step 3: 백업하고 기록한다**

Run:

```bash
python recompute_history.py --pattern "history/*.csv" --apply --backup-dir history_pre_reweight
```

Expected: `[*] 원본 38개를 history_pre_reweight 로 백업` 뒤 재계산 완료.
`--backup-dir` 을 반드시 줄 것 — 기본값 `history_pre_flow` 는 이미 있어서
거부된다.

- [ ] **Step 4: 아카이브 정합성을 확인한다**

Run: `python verify_archive.py`
Expected: 통과 (total 이 빈 BUY 행 0건)

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 5: 변경 후 측정치를 뽑아 관측 로그에 넣는다**

Run: `python forward_returns.py --history "history/*.csv" --by-axis`

설계 문서의 관측 로그 표에 행을 추가한다. 값은 실제 출력에서 옮긴다:

```markdown
| 2026-09-07 | 38일 | (출력값) | (출력값) | (출력값) | (실측) | 변경 후 (filing .20 / value .30) |
```

그리고 표 아래에 한 문단으로 적는다 — **총점 rho 가 기준선보다 나아졌는지,
filing·value 의 부호가 유지됐는지.** 나빠졌으면 T3 이므로 되돌린다.

- [ ] **Step 6: 커밋**

```bash
git add .gitignore history docs/superpowers/specs/2026-09-07-stock-axis-reweight-design.md
git commit -m "Recompute the stock archive on the new weights"
```

---

## 완료 확인

- [ ] `python -m pytest -q` 전체 통과
- [ ] `python verify_archive.py` 통과
- [ ] `python forward_returns.py --history "history/*.csv" --by-axis` 가 표를 낸다
- [ ] 설계 문서 관측 로그에 **변경 전·후 두 행**이 도구 출력으로 들어 있다
- [ ] `history_pre_reweight/` 가 존재하고 git 에 추적되지 않는다

## 되돌리기

되돌릴 이유가 생기면(T1~T3) 두 단계다.

```bash
git revert <Task 5 커밋>                       # 가중치 원복
python recompute_history.py --pattern "history/*.csv" --apply --backup-dir history_pre_reweight_undo
```

또는 백업에서 직접 복원한다: `cp history_pre_reweight/*.csv history/`.
설계 문서가 적었듯 **되돌리기를 주저하지 않는다** — 기대 이득이 작으므로
유지할 이유도 그만큼 약하다.
