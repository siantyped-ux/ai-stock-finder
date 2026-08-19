"""스코어 아카이브를 트레이드로 재현하고 R 통계를 출력한다.

history/*.csv 의 시그널과 yfinance 로 재조회한 OHLC 를 결합해 trade_sim 에
넘긴다. 규칙은 전부 trade_sim/exit_rules 에 있고 여기서는 데이터만 모은다.
"""
from __future__ import annotations

import argparse
import csv
import glob
from collections import defaultdict

import numpy as np
import yfinance as yf

import console
import exit_rules as er
import trade_sim as ts


def load_archive(pattern: str = "history/*.csv") -> list:
    """아카이브 전체를 날짜 오름차순 행 목록으로 읽는다."""
    rows = []
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8", newline="") as f:
            rows.extend(csv.DictReader(f))
    rows.sort(key=lambda r: (r["date"], r["ticker"]))
    return rows


def atr_series(hist_df, period: int = 14) -> dict:
    """날짜 -> 그 날짜 '전일까지'의 ATR.

    당일 고저를 포함해 계산하면 개장 전에 정해져 있어야 할 손절선이 미래
    정보를 쓰게 된다. exit_rules.Bar 가 명시한 타이밍 계약이다.
    """
    high = hist_df["High"].values.astype(float)
    low = hist_df["Low"].values.astype(float)
    close = hist_df["Close"].values.astype(float)
    dates = [f"{d:%Y-%m-%d}" for d in hist_df.index]

    tr = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - close[:-1]),
        np.abs(low[1:] - close[:-1]),
    ])

    out = {}
    for i in range(len(dates)):
        # tr[k] 는 봉 k+1 의 TR 이다(봉 k 와 k+1 로 계산). 따라서 봉 i 가
        # 열리기 전에 알 수 있는 TR 은 봉 1..i-1 의 것, 즉 tr[:i-1] 이다.
        # tr[:i] 로 자르면 tr[i-1] = 봉 i 자신의 TR 이 섞여 그 봉의 고저가
        # 자기 손절선 계산에 들어간다.
        available = tr[:max(i - 1, 0)]
        if len(available) >= period:
            out[dates[i]] = round(float(np.mean(available[-period:])), 4)
    return out


def fetch_bars(ticker: str) -> dict:
    """티커의 일봉을 날짜 -> exit_rules.Bar 로 반환한다. 실패하면 빈 dict."""
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


def run(pattern: str = "history/*.csv", params: er.Params = None,
        costs: ts.Costs = None) -> dict:
    """아카이브 전체를 시뮬레이션하고 트레이드·통계·커버리지를 돌려준다."""
    params = params or er.Params()
    costs = costs or ts.Costs()

    rows = load_archive(pattern)

    # 아카이브에 (ticker, date) 중복이 실제로 존재한다. 그대로 두면
    # simulate_ticker 가 같은 봉을 두 번 처리해 진입 봉까지 평가하게 되고
    # bars_held 가 부풀려진다. 먼저 나온 행을 남긴다.
    seen = set()
    deduped = []
    for r in rows:
        key = (r["ticker"], r["date"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    rows = deduped

    by_ticker = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(r)

    # 한 번이라도 BUY 였던 티커만 시세를 받는다.
    candidates = {t for t, rs in by_ticker.items()
                  if any(r["signal"] in ts.BUY_SIGNALS for r in rs)}

    trades, failed = [], []
    newest_bar = None
    for ticker in sorted(candidates):
        bars = fetch_bars(ticker)
        if not bars:
            failed.append(ticker)
            continue
        latest = max(bars)
        newest_bar = latest if newest_bar is None else max(newest_bar, latest)
        rs = by_ticker[ticker]
        market = rs[0]["market"]
        prepared = [{"date": r["date"], "signal": r["signal"],
                     "total": int(r["total"]) if r["total"] else None,
                     "source": r["source"]} for r in rs]
        trades.extend(ts.simulate_ticker(ticker, market, prepared, bars,
                                         params, costs))

    dates = sorted({r["date"] for r in rows})
    sources = [r["source"] for r in rows]
    return {
        "trades": trades,
        "summary": ts.summarize(trades),
        "dates": dates,
        "live_rows": sum(1 for s in sources if s == "live"),
        "backfill_rows": sum(1 for s in sources if s == "backfill"),
        "candidates": sorted(candidates),
        "failed": failed,
        "newest_bar": newest_bar,
        # 전환은 났지만 진입할 세션이 아직 없어 트레이드가 안 생긴 종목.
        # 이 줄이 없으면 "후보 N 인데 트레이드 M" 이 결함처럼 보인다.
        "never_entered": sorted(candidates - {t.ticker for t in trades}
                                - set(failed)),
    }


def report(result: dict) -> None:
    """데이터 출처를 가장 먼저 출력한다.

    이 경고가 없으면 몇 달 뒤 결과만 보고 시그널이 검증됐다고 오독한다.
    backfill 행의 스코어는 그때 대시보드가 보여준 값이지 올바른 값이 아니다.
    """
    dates, s = result["dates"], result["summary"]
    live, back = result["live_rows"], result["backfill_rows"]

    print("=" * 60)
    print("[데이터 커버리지]")
    print(f"  아카이브 {len(dates)}일 ({dates[0]} ~ {dates[-1]})")
    print(f"  source: backfill {back}행 / live {live}행")
    if live == 0:
        print("  !! 전부 backfill 이다. 이 스코어는 미확정 봉 결함에 오염된")
        print("     값이므로, 아래 결과는 파이프라인 검증용이며 시그널 성능의")
        print("     근거가 아니다.")
    elif back:
        print(f"  !! backfill 이 섞여 있다 (전체의 {back/(back+live)*100:.0f}%).")
    if result["failed"]:
        print(f"  시세 조회 실패: {', '.join(result['failed'])}")

    traded = len(result["candidates"]) - len(result["never_entered"])         - len(result["failed"])
    print(f"  BUY 후보 {len(result['candidates'])}종목 중 {traded}종목 진입")
    if result["newest_bar"]:
        print(f"  최신 봉 {result['newest_bar']} (아카이브 마지막 {dates[-1]})")
    if result["never_entered"]:
        print(f"  전환 후 세션이 없어 대기 중: {', '.join(result['never_entered'])}")

    print()
    print(f"[닫힌 트레이드] {s['closed']}건")
    if s["closed"]:
        print(f"  승률 {s['win_rate']*100:.1f}% · 평균 {s['avg_net_r']:+.2f}R"
              f" · 합계 {s['total_net_r']:+.2f}R")
        print(f"  청산사유: {s['by_reason']}")
        for t in result["trades"]:
            if not t.is_open:
                print(f"    {t.ticker:8s} {t.entry_date} @{t.entry_price:.2f}"
                      f" -> {t.exit_date} @{t.exit_price:.2f}"
                      f" · {t.exit_reason:6s} · {t.net_r:+.2f}R")

    print(f"[미결 포지션] {s['open']}건 · 평가 {s['open_net_r']:+.2f}R")
    for t in result["trades"]:
        if t.is_open:
            print(f"    {t.ticker:8s} {t.entry_date} @{t.entry_price:.2f}"
                  f" · {t.bars_held}봉 · {t.net_r:+.2f}R")
    print("=" * 60)


def main():
    console.force_utf8()
    p = argparse.ArgumentParser(description="스코어 아카이브 백테스트")
    p.add_argument("--history", default="history/*.csv")
    p.add_argument("--stop-atr-mult", type=float, default=3.0)
    p.add_argument("--trail-atr-mult", type=float, default=3.0)
    p.add_argument("--max-hold-days", type=int, default=60)
    p.add_argument("--exit-total", type=int, default=60)
    args = p.parse_args()

    params = er.Params(
        stop_atr_mult=args.stop_atr_mult,
        trail_atr_mult=args.trail_atr_mult,
        max_hold_days=args.max_hold_days,
        exit_total=args.exit_total,
    )
    report(run(args.history, params))


if __name__ == "__main__":
    main()
