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
import portfolio as pf
import sizing
import tracks
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


def venue_of(row: dict) -> str:
    """그 종목을 어느 시장 상품으로 표시할지. 모르면 빈 문자열.

    ETF 는 거래소보다 자산군이 중요하다 - FMP 가 NYSE Arca 를 AMEX 로 주기
    때문에 거래소만 적으면 무엇인지 알 수 없다. 2026-08-25 이전 아카이브에는
    exchange 열이 없으므로 지어내지 않고 비워 둔다.
    """
    if row.get("asset_type") == "ETF":
        return "ETF"
    return row.get("exchange") or ""


def universe_exit_dates(rows: list) -> dict:
    """시장이 유니버스에서 빠진 것을 알아차린 첫 스캔일을 market -> 날짜로 낸다.

    개별 종목의 결측으로는 이탈을 판정하지 않는다. 실측상 중간 결측이 최장 16
    스캔일까지 있고 그 뒤 정상 복귀한다(KRYS 2026-08-02 -> 08-20, 1~2일 결측은
    85건). 어떤 유예 기간을 잡아도 짧으면 멀쩡한 종목을 강제청산하고 길면
    감지가 3주 늦는데, 25일 표본으로는 그 값을 고를 수 없다.

    반면 한 시장의 행이 통째로 0 이 되는 것은 조회 실패가 아니라 유니버스
    결정이다. 2026-08-22 에 KR 110종목이 한 번에 사라진 것이 그 경우다.
    마지막으로 등장한 뒤 계속 없을 때만 이탈로 본다 - 하루 비었다가 돌아오면
    그것도 조회 실패다.
    """
    dates = sorted({r["date"] for r in rows})
    last_seen = {}
    for r in rows:
        market = r["market"]
        if r["date"] > last_seen.get(market, ""):
            last_seen[market] = r["date"]

    out = {}
    for market, last in last_seen.items():
        i = dates.index(last)
        if i + 1 < len(dates):
            out[market] = dates[i + 1]
    return out


def run(pattern: str = "history/*.csv", params: er.Params = None,
        costs: ts.Costs = None, us_only: bool = False,
        entry_total: int = None, limits: pf.Limits = None,
        start_date: str = None, account: sizing.Account = None) -> dict:
    """아카이브 전체를 시뮬레이션하고 트레이드·통계·커버리지를 돌려준다."""
    params = params or er.Params()
    costs = costs or ts.Costs()

    rows = filter_rows(load_archive(pattern), us_only=us_only,
                       entry_total=entry_total, start_date=start_date)

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

    exits = universe_exit_dates(rows)

    by_ticker = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(r)

    # 한 번이라도 BUY 였던 티커만 시세를 받는다.
    candidates = {t for t, rs in by_ticker.items()
                  if any(r["signal"] in ts.BUY_SIGNALS for r in rs)}

    trades, failed = [], []
    newest_bar = None
    prepared_by_ticker, bars_by_ticker, markets, venues = {}, {}, {}, {}
    for ticker in sorted(candidates):
        bars = fetch_bars(ticker)
        if not bars:
            failed.append(ticker)
            continue
        latest = max(bars)
        newest_bar = latest if newest_bar is None else max(newest_bar, latest)
        rs = by_ticker[ticker]
        prepared = [{"date": r["date"], "signal": r["signal"],
                     "total": int(r["total"]) if r["total"] else None,
                     # 목표 상승률(%). 예전 백필 파일에는 컬럼이 없을 수 있다.
                     "target": int(r["target"]) if r.get("target") else None,
                     "source": r["source"]} for r in rs]
        prepared_by_ticker[ticker] = prepared
        bars_by_ticker[ticker] = bars
        markets[ticker] = rs[0]["market"]
        venues[ticker] = venue_of(rs[0])

    # 제약이 없으면 종목별 시뮬레이션을 그대로 쓴다. 포트폴리오 경로와 결과가
    # 같아야 하지만(tests/test_portfolio.py 회귀), 굳이 우회할 이유도 없다.
    # 계좌가 있으면 반드시 포트폴리오 경로다. 종목을 따로 보면 "다른 종목이
    # 현금을 이미 썼다" 를 표현할 수 없다.
    unconstrained = (account is None and
                     (limits is None or (not limits.max_positions
                                         and limits.max_correlation >= 1.0)))
    rejected = {"capacity": 0, "correlation": 0, "cash": 0}
    rejected_pairs = []
    rejected_cash = []
    cash = capital = None
    if unconstrained:
        for ticker, prepared in prepared_by_ticker.items():
            trades.extend(ts.simulate_ticker(ticker, markets[ticker], prepared,
                                             bars_by_ticker[ticker], params, costs,
                                             exits.get(markets[ticker])))
    else:
        correlator = pf.build_correlator(
            {t: [b.close for b in sorted(bs.values(), key=lambda x: x.date)]
             for t, bs in bars_by_ticker.items()})
        out = pf.simulate(prepared_by_ticker, bars_by_ticker, markets,
                          params, costs, limits, correlator, exits, account)
        trades = out["trades"]
        rejected = out["rejected"]
        rejected_pairs = out["rejected_pairs"]
        rejected_cash = out.get("rejected_cash") or []
        cash = out["cash"]
        capital = out["capital"]

    dates = sorted({r["date"] for r in rows})
    sources = [r["source"] for r in rows]
    traded_tickers = {t.ticker for t in trades}
    skipped_cash = {t for _, t in rejected_cash} - traded_tickers
    return {
        "trades": trades,
        "summary": ts.summarize(trades),
        "dates": dates,
        "live_rows": sum(1 for s in sources if s == "live"),
        "backfill_rows": sum(1 for s in sources if s == "backfill"),
        "candidates": sorted(candidates),
        "failed": failed,
        "newest_bar": newest_bar,
        # 티커 -> NYSE|NASDAQ|AMEX|ETF. 리포트의 시장 열이 이걸 쓴다.
        "venues": venues,
        # 전환은 났지만 진입할 세션이 아직 없어 트레이드가 안 생긴 종목.
        # 이 줄이 없으면 "후보 N 인데 트레이드 M" 이 결함처럼 보인다.
        # 현금부족으로 잘린 종목은 빼야 한다 - 그쪽은 봉이 있었는데도 못 산
        # 것이라 다음 세션을 기다리는 중이 아니다. 섞으면 죽은 시그널이
        # 내일 진입 후보처럼 보인다.
        "never_entered": sorted(candidates - {t.ticker for t in trades}
                                - set(failed) - skipped_cash),
        # 봉이 있었는데 돈이 모자라 끝내 못 산 종목. 나중에 자리가 나
        # 진입했다면 트레이드가 있으므로 여기 없다.
        "skipped_cash": sorted(skipped_cash),
        # 상한이 조용히 기회를 죽이면 알 수 없으므로 무엇을 왜 막았는지 센다
        "rejected": rejected,
        "rejected_pairs": rejected_pairs,
        # 자본 사용률은 리포트가 낸다. account 없이 돌리면 둘 다 None 이다.
        "cash": cash,
        "capital": capital,
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

    # 트레이드가 진입 종목 수를 정한다. 후보에서 빼는 식으로 세면 진입하지
    # 못한 이유가 하나 늘 때마다 이 숫자가 조용히 틀린다.
    traded = len({t.ticker for t in result["trades"]})
    print(f"  BUY 후보 {len(result['candidates'])}종목 중 {traded}종목 진입")
    if result["newest_bar"]:
        print(f"  최신 봉 {result['newest_bar']} (아카이브 마지막 {dates[-1]})")
    if result["never_entered"]:
        print(f"  전환 후 세션이 없어 대기 중: {', '.join(result['never_entered'])}")
    if result.get("skipped_cash"):
        print(f"  현금이 모자라 건너뜀: {', '.join(result['skipped_cash'])}")

    # 상한이 조용히 기회를 죽이면 결과만 보고는 알 수 없다
    rej = result.get("rejected") or {}
    if rej.get("capacity") or rej.get("correlation") or rej.get("cash"):
        print(f"  진입 거절: 자리부족 {rej.get('capacity', 0)}건 · "
              f"상관중복 {rej.get('correlation', 0)}건 · "
              f"현금부족 {rej.get('cash', 0)}건")
        for date, blocked, held, rho in (result.get("rejected_pairs") or [])[:8]:
            print(f"    {date} {blocked} 차단 (보유 {held} 와 rho={rho})")

    if result.get("capital"):
        used = result["capital"] - result["cash"]
        print(f"  자본: ${result['capital']:,.0f} 중 ${used:,.0f} 사용 "
              f"({used / result['capital'] * 100:.1f}%) · "
              f"잔여 ${result['cash']:,.0f}")

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
                      f" · {t.exit_reason:8s} · {t.net_r:+.2f}R")

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
    p.add_argument("--track", choices=sorted(tracks.TRACKS),
                   help="트랙을 지정한다. 아카이브 경로와 상관 상한을 그 트랙의\n"
                        "  기본값으로 맞춘다 (--history / --max-correlation 을\n"
                        "  직접 주면 그쪽이 이긴다)")
    p.add_argument("--stop-atr-mult", type=float, default=3.0)
    p.add_argument("--trail-atr-mult", type=float, default=3.0)
    p.add_argument("--max-hold-days", type=int, default=60)
    p.add_argument("--exit-total", type=int, default=60)
    p.add_argument("--use-target", action="store_true",
                   help="목표가 도달 시 익절한다 (기본: 사용 안 함)")
    p.add_argument("--us-only", action="store_true",
                   help="아카이브의 한국 행을 제외한다")
    p.add_argument("--start-date", default=None,
                   help="이 날짜부터의 아카이브만 본다 (YYYY-MM-DD)")
    p.add_argument("--capital", type=float, default=None,
                   help="초기 자본 USD. 주면 자본 제약이 켜진다 (예: 10000)")
    p.add_argument("--risk-pct", type=float, default=1.0,
                   help="거래당 리스크 (초기 자본 대비 %%, 기본 1.0)")
    p.add_argument("--max-weight-pct", type=float, default=20.0,
                   help="한 종목 투입 상한 (초기 자본 대비 %%, 기본 20.0)")
    p.add_argument("--entry-total", type=int, default=None,
                   help="이 점수 이상이면 BUY 로 간주해 진입한다 (비교용)")
    p.add_argument("--max-positions", type=int, default=0,
                   help="동시 보유 상한 (0=무제한, 기본).\n"
                        "  자리가 모자라면 그날 총점이 높은 종목이 가져간다")
    p.add_argument("--max-correlation", type=float, default=None,
                   help="이미 보유한 종목과의 일간수익률 상관 상한 (1.0=끔).\n"
                        "  0.90 이면 XLV·VHT·IYH 같은 사실상 같은 베팅을 막는다.\n"
                        "  생략하면 --track 의 기본값, --track 도 없으면 1.0")
    args = p.parse_args()

    # --track 은 기본값만 바꾼다. 명시된 인자가 언제나 이긴다 - 그러지 않으면
    # 트랙을 준 순간 사용자가 직접 준 값이 조용히 무시된다.
    pattern = tracks.history_glob(args.track) if args.track else args.history
    if args.max_correlation is not None:
        max_corr = args.max_correlation
    elif args.track:
        max_corr = tracks.max_correlation(args.track)
    else:
        max_corr = 1.0

    params = er.Params(
        stop_atr_mult=args.stop_atr_mult,
        trail_atr_mult=args.trail_atr_mult,
        max_hold_days=args.max_hold_days,
        exit_total=args.exit_total,
        use_target=args.use_target,
    )
    limits = pf.Limits(max_positions=args.max_positions,
                       max_correlation=max_corr)
    account = None
    if args.capital:
        account = sizing.Account(capital=args.capital,
                                 risk_pct=args.risk_pct,
                                 max_weight_pct=args.max_weight_pct)

    report(run(pattern, params, us_only=args.us_only,
               entry_total=args.entry_total, limits=limits,
               start_date=args.start_date, account=account))


if __name__ == "__main__":
    main()
