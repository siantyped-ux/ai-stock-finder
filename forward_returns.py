"""신호별 선행 수익률로 스코어링 변경을 채점한다.

백테스트만으로는 스코어링 변경을 판정할 수 없다. 전략이 3개월 보유인데
아카이브가 22일이면 대부분이 미결로 남고, 닫힌 몇 건의 R 통계는 노이즈다
(2026-08-24 실측: 구 산식 5건 · 신 산식 24건).

대신 이렇게 묻는다 - 어떤 신호가 붙은 날 이후 그 종목은 실제로 올랐는가?
행이 24,212개라 통계력이 비교가 되지 않는다.

읽을 때의 함정 세 가지. 이 도구는 답을 주지 않고 증거만 준다.
  · 시간축   전략은 3개월인데 여기서 재는 것은 5~10거래일이다. 짧은 구간의
             역전이 전략의 실패를 뜻하지 않는다
  · 국면     아카이브 전 기간이 NEUTRAL 단일이었다. 한 국면의 결과는
             일반화되지 않는다
  · 표본     bar_date 로부터 n 거래일이 지나지 않은 행은 빠진다. 아카이브
             막바지에 처음 등장한 자산군(2026-08-22 의 ETF)은 통째로 빠진다

선행 수익률은 정의상 미래를 본다. 그것이 목적이다 - 신호를 만드는 데 쓰지
않고 신호를 채점하는 데만 쓴다.

일봉은 recompute_history 가 만든 캐시를 쓴다. 없으면 그쪽을 먼저 돌릴 것.

실행
    python forward_returns.py                                  # 신 산식만
    python forward_returns.py --compare history_pre_flow/*.csv # 구/신 비교
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

import console

CACHE = Path(".cache/recompute_frames.json")
BUY_SIGNALS = ("BUY", "STRONG_BUY")
SIGNAL_ORDER = ("STRONG_BUY", "BUY", "WATCH", "HOLD", "AVOID")

# 축별 상관을 잴 대상. total 을 함께 넣는 것은 의도다 - 축을 어떻게 섞었을 때
# 총점이 나아지는지가 이 기능을 만든 이유다.
AXES = ("tech", "flow", "filing", "value", "total")

# 이 아래로는 순위상관을 내지 않는다. 축 점수가 정수라 표본이 작으면 동점이
# 상관을 지배한다.
MIN_AXIS_SAMPLE = 200


def load_prices(cache: Path = CACHE) -> dict:
    """티커 -> (날짜 목록, 종가 목록). 둘 다 날짜 오름차순이다."""
    raw = json.loads(cache.read_text(encoding="utf-8"))
    return {t: (d["dates"], d["Close"]) for t, d in raw.items() if d}


def _index_at_or_before(dates: list, target: str) -> int:
    """target 이하인 마지막 인덱스. 없으면 -1.

    이진 탐색인 것은 이 함수가 행마다 · horizon 마다 불리기 때문이다.
    """
    lo, hi, out = 0, len(dates) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if dates[mid] <= target:
            out, lo = mid, mid + 1
        else:
            hi = mid - 1
    return out


def forward_return(prices: dict, ticker: str, bar_date: str, n: int):
    """bar_date 종가에서 n 거래일 뒤 종가까지의 수익률(%). 못 재면 None.

    n 거래일 뒤 봉이 아직 없으면 None 이다. 마지막 봉으로 대체하지 않는다 -
    그러면 구간이 짧아진 행이 섞여 평균이 왜곡된다.
    """
    got = prices.get(ticker)
    if not got:
        return None
    dates, closes = got
    i = _index_at_or_before(dates, bar_date)
    if i < 0 or i + n >= len(dates):
        return None
    base = closes[i]
    if base <= 0:
        return None
    return (closes[i + n] / base - 1) * 100


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
                        # int(raw) 는 stock_finder.py 의 calc_*_score 가 정수만
                        # 내고 history.py 가 그대로 적는다는 것을 믿는다. 그
                        # 약속이 깨져 셀이 "70.0" 같은 값이면 여기서 죽는 것이
                        # 맞다 - 조용히 건너뛰면 축 하나가 통째로 빠진 채
                        # 상관을 낸다.
                        out.append((r["date"], n, ax, int(raw), ret))
    return out


def median_date(rows: list) -> str:
    """고유 날짜의 중앙값. 기간을 반으로 가르는 기준일이다.

    고정 날짜를 기본값으로 두지 않는 것은 아카이브가 매일 자라기 때문이다.
    한 번 적어 둔 날짜는 시간이 갈수록 후반을 비대칭으로 키운다.
    """
    dates = sorted({r[0] for r in rows})
    return dates[len(dates) // 2] if dates else ""


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


def _part_rho(part: list):
    """표본이 충분할 때만 그 부분의 순위상관. 아니면 None.

    axis_verdict 와 sign_census 가 같은 규칙을 써야 해서 밖으로 뺐다.
    한쪽에만 문턱이 걸리면 단일 기준일 표와 census 표가 서로 다른 것을
    말하게 된다.
    """
    if len(part) < MIN_AXIS_SAMPLE:
        return None
    return spearman([r[3] for r in part], [r[4] for r in part])


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

    r_all, r_early, r_late = _part_rho(got), _part_rho(early), _part_rho(late)
    flip = (r_early is not None and r_late is not None
            and r_early * r_late < 0)
    return {"all": r_all, "early": r_early, "late": r_late, "flip": flip,
            "n_all": len(got), "n_early": len(early), "n_late": len(late),
            "floor_early": _rho_floor(len(early)),
            "floor_late": _rho_floor(len(late))}


def sign_census(rows: list, axis: str, n: int) -> dict:
    """모든 기준일로 갈라 전·후반 부호가 몇 번씩 나왔는지 센다.

    기준일 하나의 부호는 그 하루에 걸린다. 2026-09-07 실측이 그랬다 -
    value 는 08-15 이전에서 자르면 전반이 음수, 이후에서 자르면 양수였다.
    반면 filing 은 어디서 잘라도 음수였다. 그 차이가 이 도구가 내려야 할
    판정이고, 기준일 하나로는 둘을 구별할 수 없다.

    자기 하한(_rho_floor)을 못 넘는 칸은 unreadable 로 센다 - 부호가 있어도
    읽지 않는다는 뜻이다. 한쪽이 MIN_AXIS_SAMPLE 에 못 미치는 기준일은 rho
    가 None 이라 아예 세지 않는다. 그래서 훑을 기준일 범위를 손으로 정할
    필요가 없다.

    axis_verdict 를 부르지 않고 직접 도는 것은 비용 때문이다. 그쪽은 기준일과
    무관한 전체 rho 를 매번 다시 계산하고 행 목록 전체를 매번 다시 거른다.
    기준일이 26개면 그 둘이 26배로 붙는다 (실측 33초 -> 아래 방식으로 크게
    줄어든다).
    """
    got = [r for r in rows if r[2] == axis and r[1] == n]
    dates = sorted({r[0] for r in got})
    out = {side: {"neg": 0, "pos": 0, "unreadable": 0}
           for side in ("early", "late")}
    for cut in dates:
        parts = (("early", [r for r in got if r[0] < cut]),
                 ("late", [r for r in got if r[0] >= cut]))
        for side, part in parts:
            rho = _part_rho(part)
            floor = _rho_floor(len(part))
            if rho is None or floor is None:
                continue
            # 하한과 정확히 같은 값은 읽는 쪽에 넣는다. rho 는 순위합의 비,
            # 하한은 무리수라 실제로 같아지는 일은 없다 - 경계를 어느 쪽에
            # 두든 결과가 같으므로 굳이 <= 로 쓰지 않는다.
            if abs(rho) < floor:
                out[side]["unreadable"] += 1
            elif rho < 0:
                out[side]["neg"] += 1
            else:
                out[side]["pos"] += 1
    return out


def show_sign_census(rows: list, horizons: tuple) -> None:
    """기준일을 전부 훑은 부호 census. 판정은 이 표로 한다.

    위의 단일 기준일 표는 기록용이다. 한 축을 신호로 볼지 말지는 여기서
    한쪽 부호가 몰리는지로 본다.
    """
    print("\n" + "=" * 74)
    print("  기준일을 전부 훑은 부호 census")
    print("=" * 74)
    print("  기준일 하나의 부호는 그 하루에 걸린다. 아래는 양쪽이 다 표본을")
    print(f"  갖는 모든 기준일로 갈라 센 것이다. 0 은 |rho| 가 제 하한 미만이라")
    print("  부호를 읽지 않은 칸이다.")
    for n in horizons:
        print(f"\n  [{n}거래일]")
        print(f"    {'축':<8}{'전반 -':>8}{'전반 +':>8}{'전반 0':>8}"
              f"   {'후반 -':>8}{'후반 +':>8}{'후반 0':>8}")
        for ax in AXES:
            c = sign_census(rows, ax, n)
            e, l = c["early"], c["late"]
            print(f"    {ax:<8}{e['neg']:>8}{e['pos']:>8}{e['unreadable']:>8}"
                  f"   {l['neg']:>8}{l['pos']:>8}{l['unreadable']:>8}")


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


def collect(pattern: str, prices: dict, horizons: tuple) -> dict:
    """신호·자산군별 선행 수익률을 모은다."""
    out = defaultdict(lambda: defaultdict(list))
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                bar_date = r.get("bar_date")
                if not bar_date:
                    continue
                sig, at = r["signal"], (r.get("asset_type") or "STOCK")
                for n in horizons:
                    fr = forward_return(prices, r["ticker"], bar_date, n)
                    if fr is None:
                        continue
                    out[sig][n].append(fr)
                    out[f"전체·{at}"][n].append(fr)
                    out["전체"][n].append(fr)
                    if sig in BUY_SIGNALS:
                        out[f"BUY계열·{at}"][n].append(fr)
    return out


def stat_line(label: str, vals: list) -> str:
    if len(vals) < 2:
        return f"    {label:<16} n={len(vals):<6} (표본 부족)"
    se = st.stdev(vals) / (len(vals) ** 0.5)
    win = sum(1 for v in vals if v > 0) / len(vals) * 100
    return (f"    {label:<16} n={len(vals):<6} 평균 {st.mean(vals):+6.2f}% "
            f"(±{se:.2f}) · 중앙 {st.median(vals):+6.2f}% · 승률 {win:4.1f}%")


def show(title: str, data: dict, horizons: tuple) -> None:
    print("\n" + "=" * 74)
    print(f"  {title}")
    print("=" * 74)
    for n in horizons:
        print(f"\n  [{n}거래일 선행 수익률]")
        for key in SIGNAL_ORDER + ("전체",):
            if data.get(key, {}).get(n):
                print(stat_line(key, data[key][n]))
        print("    " + "-" * 66)
        for key in ("BUY계열·STOCK", "BUY계열·ETF", "전체·STOCK", "전체·ETF"):
            if data.get(key, {}).get(n):
                print(stat_line(key, data[key][n]))


def edge(data: dict, n: int, asset: str):
    """BUY 계열 평균 - 자산군 전체 평균(%p). 이것이 신호의 값어치다.

    자산군 전체를 기준으로 삼는 것은 시장 자체의 등락을 빼기 위해서다.
    양수여야 신호가 무작위 선택보다 낫다는 뜻이다.
    """
    buy = data.get(f"BUY계열·{asset}", {}).get(n, [])
    allr = data.get(f"전체·{asset}", {}).get(n, [])
    if len(buy) < 2 or len(allr) < 2:
        return None
    return st.mean(buy) - st.mean(allr), len(buy)


def show_edge(pairs: list, horizons: tuple) -> None:
    """(라벨, 데이터) 목록의 신호 값어치를 나란히 낸다."""
    print("\n" + "=" * 74)
    print("  신호의 값어치: BUY 계열 평균 - 자산군 전체 평균 (%p)")
    print("=" * 74)
    print("  양수여야 신호가 무작위보다 낫다.\n")
    for n in horizons:
        print(f"  [{n}거래일]")
        for asset in ("STOCK", "ETF"):
            cells = []
            for label, data in pairs:
                e = edge(data, n, asset)
                cells.append(f"{label} " +
                             (f"{e[0]:+.2f}%p (n={e[1]})" if e else "표본 없음"))
            print(f"    {asset:<6} " + "   ".join(f"{c:<26}" for c in cells))
        print()


def main() -> None:
    console.force_utf8()
    p = argparse.ArgumentParser(description="신호별 선행 수익률")
    p.add_argument("--history", default="history/*.csv")
    p.add_argument("--compare", default="",
                   help="대조군 아카이브 glob (예: history_pre_flow/*.csv)")
    p.add_argument("--horizons", default="5,10",
                   help="선행 거래일 수 (쉼표 구분, 기본 5,10)")
    p.add_argument("--by-axis", action="store_true",
                   help="신호 대신 축별 순위상관을 낸다 (기간을 반으로 갈라 "
                        "부호가 유지되는지 함께 본다)")
    p.add_argument("--cut", default="",
                   help="기간 분할 기준일 YYYY-MM-DD (생략하면 날짜 중앙값)")
    args = p.parse_args()

    if not CACHE.exists():
        print(f"[!] 일봉 캐시 {CACHE} 가 없다. recompute_history.py 를 먼저 돌릴 것")
        return
    horizons = tuple(int(x) for x in args.horizons.split(","))
    prices = load_prices()
    print(f"[*] 일봉 캐시 {len(prices)}종목 · horizon {horizons}")

    if args.by_axis:
        rows = collect_axes(args.history, prices, horizons)
        if not rows:
            print("[!] 잴 수 있는 행이 없다. 캐시가 아카이브보다 오래된 것이 "
                  "가장 흔한 원인이다 - recompute_history.py --refresh 를 볼 것")
            return
        show_axes(rows, horizons, args.cut or median_date(rows))
        show_sign_census(rows, horizons)
        print("\n  주의: 전략은 3개월 보유인데 여기서 재는 것은 위 거래일 수다.")
        return

    pairs = []
    if args.compare:
        old = collect(args.compare, prices, horizons)
        show(f"대조군 ({args.compare})", old, horizons)
        pairs.append(("대조", old))
    new = collect(args.history, prices, horizons)
    show(f"대상 ({args.history})", new, horizons)
    pairs.append(("대상", new))

    show_edge(pairs, horizons)
    print("  주의: 전략은 3개월 보유인데 여기서 재는 것은 위 거래일 수다.")
    print("        아카이브 막바지에 등장한 자산군은 선행 구간이 없어 빠진다.")


if __name__ == "__main__":
    main()
