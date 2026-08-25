"""ETF 유니버스에서 복제본을 골라내 제외 목록을 만든다.

같은 지수를 추종하는 상품이 유니버스에 여럿 들어 있으면 tech·flow 가 전부
가격·거래량 기반이라 점수가 똑같이 나오고, 상위 목록이 사실상 한 개의 베팅
으로 채워진다. 2026-08-25 실측에서 ETF 539종목 중 284종목(53%)이 (tech, flow,
total) 이 완전히 같은 106개 묶음에 속했다.

## 임계 0.99 의 근거 (실측, 2026-08-25)

528종목 1년 일간수익률 139,128쌍의 상관을 재고 구간별로 무엇이 걸리는지
직접 봤다.

- **0.99 이상** — 전부 같은 시장을 다른 포장으로 판 것이다. QQQ·QQQM(0.9998),
  SPY·SPYM·VOO·IVV(S&P500 네 종), IJH·MDY·IVOO(S&P400), FTEC·VGT(기술),
  ITOT·VTI·SCHB·IWV(전체시장), SLYV·VIOV(S&P600 소형가치).
- **0.985~0.99** — 대부분 같은 베팅이지만 IWB(러셀1000)·IWL(러셀Top200)처럼
  담는 범위가 다른 쌍이 섞이기 시작한다.
- **0.98~0.985** — 실제로 다른 베팅이 걸린다. EMXC(신흥국 ex중국)·JEMA(신흥국)
  는 중국 포함 여부가 다르고, FESM(액티브 소형)·VTWO(러셀2000)는 운용 방식이
  다르다. 여기서 자르면 멀쩡한 상품을 지운다.

그래서 0.99 다. 이 임계는 **"같은 상품인가"** 를 묻는 것이지 "같은 테마인가"
를 묻는 것이 아니다. 헬스케어 ETF 들은 서로 0.86~0.97 대역이라 여기서 걸리지
않는다 - 테마 쏠림은 유니버스가 아니라 랭킹 계층에서 다뤄야 한다.

## 왜 연결요소가 아니라 탐욕법인가

A-B 와 B-C 가 복제본이어도 A-C 는 아닐 수 있다. 연결요소로 묶으면 체인을 타고
관련 없는 상품까지 한 묶음이 되어 지워진다. 거래대금이 큰 순으로 대표를 정하고
"이미 뽑힌 대표와 임계 이상이면 제외" 하면 그 문제가 없고, 무엇이 무엇 때문에
빠졌는지도 남는다. portfolio._too_correlated 와 같은 방식이다.
"""
from __future__ import annotations

import argparse
import json
from typing import Callable

DEFAULT_THRESHOLD = 0.99
DUPES_PATH = "etf_dupes.json"


def dedupe(tickers: list, correlator: Callable, turnover: dict,
           threshold: float = DEFAULT_THRESHOLD) -> tuple:
    """거래대금이 큰 순으로 대표를 정하고 복제본을 걸러 낸다.

    correlator(a, b) 는 두 종목의 일간수익률 상관을 돌려주고, 측정할 수 없으면
    None 을 돌려준다. 측정 불가는 막지 않는다 - 상장 직후라 이력이 짧은 종목을
    조용히 지우면 유니버스가 이유 없이 빈다.

    반환은 (유지 목록, {제외 티커: (대표 티커, 상관)}) 이다. 무엇이 왜 빠졌는지
    남기지 않으면 유니버스가 조용히 줄어든 것을 눈치채지 못한다.

    거래대금이 같으면 티커 순으로 갈라 결과를 결정적으로 만든다. dict 순서에
    맡기면 실행마다 다른 유니버스가 나온다.
    """
    kept, dropped = [], {}
    for ticker in sorted(tickers, key=lambda t: (-turnover.get(t, 0.0), t)):
        clash = None
        for rep in kept:
            rho = correlator(ticker, rep)
            if rho is not None and rho >= threshold:
                clash = (rep, rho)
                break
        if clash is None:
            kept.append(ticker)
        else:
            dropped[ticker] = clash
    return kept, dropped


def excluded_tickers(path: str = DUPES_PATH) -> set:
    """제외 목록 파일을 읽는다. 없거나 깨졌으면 빈 집합.

    목록이 없다고 스캔을 멈추지 않는다. 중복을 남긴 채 도는 편이 그날 스캔이
    통째로 비는 것보다 낫다.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f).get("excluded", {}))
    except (OSError, ValueError):
        return set()


# ─── 목록 생성 (수동 실행) ────────────────────────────────────

def build(tickers: list, threshold: float, period: str = "1y",
          min_periods: int = 120) -> dict:
    """가격을 받아 상관을 재고 제외 목록을 만든다.

    스캔 경로에서 부르지 않는다. 539종목 1년치를 매일 받으면 스캔이 그만큼
    길어지는데, 복제본 관계는 하루 만에 바뀌지 않는다.
    """
    import yfinance as yf

    raw = yf.download(tickers, period=period, interval="1d", auto_adjust=True,
                      progress=False, threads=True)
    close = raw["Close"].dropna(axis=1, thresh=int(len(raw) * 0.8))
    corr = close.pct_change().dropna(how="all").corr(min_periods=min_periods)

    priced = list(corr.columns)
    missing = sorted(set(tickers) - set(priced))
    rho = corr.to_numpy()
    index = {t: i for i, t in enumerate(priced)}

    def correlator(a, b):
        value = rho[index[a], index[b]]
        return None if value != value else float(value)   # NaN 검사

    # 거래대금이다 - 종가와 거래량을 곱한다. 종가만으로 정렬하면 주가가 비싼
    # 쪽이 대표가 되어 정작 유동성이 없는 상품이 살아남는다.
    volume = raw["Volume"]
    turnover = {}
    for t in priced:
        value = (close[t].tail(20) * volume[t].tail(20)).mean()
        turnover[t] = 0.0 if value != value else float(value)

    kept, dropped = dedupe(priced, correlator, turnover, threshold)
    return {
        "threshold": threshold,
        "universe": len(tickers),
        "priced": len(priced),
        # 가격을 못 받은 종목은 판정하지 않고 남긴다.
        "unpriced": missing,
        "kept": len(kept) + len(missing),
        "excluded": {t: {"kept": rep, "rho": round(r, 5)}
                     for t, (rep, r) in sorted(dropped.items())},
    }


def main():
    p = argparse.ArgumentParser(description="ETF 복제본 제외 목록 생성")
    p.add_argument("--history", default="history/*.csv",
                   help="ETF 티커를 읽을 아카이브 (가장 최근 파일을 쓴다)")
    p.add_argument("--out", default=DUPES_PATH)
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p.add_argument("--period", default="1y")
    args = p.parse_args()

    import csv
    import glob

    latest = sorted(glob.glob(args.history))[-1]
    with open(latest, encoding="utf-8", newline="") as f:
        tickers = sorted({r["ticker"] for r in csv.DictReader(f)
                          if r.get("asset_type") == "ETF"})
    print(f"[*] {latest} 에서 ETF {len(tickers)}종목")

    result = build(tickers, args.threshold, args.period)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print(f"[OK] 임계 {result['threshold']} · 가격 {result['priced']}종목 "
          f"· 제외 {len(result['excluded'])} · 유지 {result['kept']}")
    if result["unpriced"]:
        print(f"    가격 미확보 {len(result['unpriced'])}종목은 유지: "
              f"{', '.join(result['unpriced'][:8])}")
    for t, info in list(result["excluded"].items())[:10]:
        print(f"    {t:6s} -> {info['kept']:6s} ({info['rho']})")
    print(f"    ... {args.out} 에 저장")


if __name__ == "__main__":
    main()
