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
    args = p.parse_args()

    if not CACHE.exists():
        print(f"[!] 일봉 캐시 {CACHE} 가 없다. recompute_history.py 를 먼저 돌릴 것")
        return
    horizons = tuple(int(x) for x in args.horizons.split(","))
    prices = load_prices()
    print(f"[*] 일봉 캐시 {len(prices)}종목 · horizon {horizons}")

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
