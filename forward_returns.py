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
