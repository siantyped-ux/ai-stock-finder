"""FMP 호가(bid/ask) 엔드포인트가 실행 레이어에 쓸 만한지 판정한다.

스코어링에는 호가를 쓰지 않는다. 스캔이 프리마켓(ET 05:23·06:57)에 도는데
그 시각 호가창은 비어 있고, 무엇보다 과거 호가 데이터가 없어 백테스트로
검증할 수 없다. 검증할 수 없는 신호는 점수에 넣지 않는다.

호가가 값어치를 갖는 곳은 주문 직전의 실행 레이어 세 가지뿐이다.
  · 스프레드 가드 - (ask-bid)/mid 가 임계 초과면 주문 보류
  · 지정가 산출   - 최우선 잔량비로 공격/수동 지정가 결정
  · 주문 분할     - 주문 수량이 최우선 잔량 대비 크면 쪼갠다

세 용도 모두 최우선호가 1단계(Level 1)면 충분하다. 10단계 호가창(Level 2)은
FMP 에 아예 없다 - nbbo·orderbook·level2·market-depth 전부 404 이고 bid/ask 를
주는 엔드포인트는 aftermarket-quote 계열 하나뿐이다 (2026-08-24 실측).

이 스크립트가 판정하려는 것은 딱 하나다.
    "aftermarket" 이라는 이름의 그 엔드포인트가 정규장에도 갱신되는가?

장외에 돌리면 답이 나오지 않는다. 2026-08-24 휴장 중 실측에서 스프레드
중앙값이 주식 1.515% · ETF 4.172% 였고 0.3% 초과가 85~93% 였다 - 장이
닫혀 호가창이 빈 상태라 어느 제공자를 써도 같은 값이 나온다. 반드시
정규장(ET 09:30~16:00 = KST 22:30~05:00)에 실행할 것.

실행:  python verify_quotes.py
"""
from __future__ import annotations

import csv
import json
import os
import statistics as st
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import envfile


def api_key() -> str:
    """.env 우선, 없으면 환경변수. GitHub Actions Secrets 로 돌리기 위해서다.

    CI 에는 .env 가 없다. envfile.load 는 그 경우 빈 dict 를 내므로 여기서
    환경변수로 넘어간다 (stock_finder.FMP_KEY 와 같은 규칙).
    """
    return (envfile.load(".env").get("FMP_API_KEY")
            or os.environ.get("FMP_API_KEY", "")).strip()

BASE = "https://financialmodelingprep.com/stable"
ET = timezone(timedelta(hours=-4))    # EDT. 겨울(EST)에는 -5 로 바꿀 것
KST = timezone(timedelta(hours=9))

# 실행 레이어의 스프레드 가드 임계 후보. 이 값을 넘는 종목은 주문을 보류한다.
# trade_sim.Costs.slippage_pct 가 0.05% 를 가정하는데, 스프레드가 이보다
# 크게 벌어진 종목에서는 그 가정이 깨진다.
SPREAD_GUARD_PCT = 0.3

# 정규장에서 이 정도보다 오래된 호가면 "갱신되지 않는다" 로 본다.
STALE_MINUTES = 5


def get(path: str, **params) -> tuple:
    """stable 엔드포인트 호출. (데이터, 오류) 를 낸다."""
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    key = api_key()
    try:
        with urllib.request.urlopen(f"{BASE}/{path}?{qs}&apikey={key}", timeout=30) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, str(e)[:80]


def session_now() -> tuple[str, datetime]:
    """지금이 미국 시장의 어느 세션인지. 공휴일은 보지 않는다.

    FMP 의 exchange-market-hours 에 isMarketOpen 이 있지만 그 값에만 기대지
    않는다 - 프리마켓·애프터마켓을 구분해 주지 않아서, 정규장 판정에 쓰면
    시간외를 정규장으로 잘못 읽는다.
    """
    now_et = datetime.now(timezone.utc).astimezone(ET)
    if now_et.weekday() >= 5:
        return "휴장(주말)", now_et
    hm = now_et.hour * 60 + now_et.minute
    if 4 * 60 <= hm < 9 * 60 + 30:
        return "프리마켓", now_et
    if 9 * 60 + 30 <= hm < 16 * 60:
        return "정규장", now_et
    if 16 * 60 <= hm < 20 * 60:
        return "애프터마켓", now_et
    return "휴장(야간)", now_et


def universe_symbols(path: str) -> tuple[list, list]:
    """아카이브에서 실제 스캔 대상 티커를 꺼낸다. (주식, ETF)

    임의의 대형주 목록을 쓰지 않는 것은 의도다. 커버리지는 우리 유니버스에
    대해서만 의미가 있고, 소형주·비인기 ETF 에서 먼저 깨진다.
    """
    stocks, etfs = [], []
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                (etfs if row.get("asset_type") == "ETF" else stocks).append(row["ticker"])
    except FileNotFoundError:
        return [], []
    return stocks, etfs


def fetch_quotes(symbols: list, batch: int = 500) -> dict:
    """티커 목록의 호가를 받는다. {symbol: row}."""
    got = {}
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        data, err = get("batch-aftermarket-quote", symbols=",".join(chunk))
        if err or not isinstance(data, list):
            print(f"      배치 {i // batch + 1} 실패: {err}")
            continue
        for row in data:
            if isinstance(row, dict) and row.get("symbol"):
                got[row["symbol"]] = row
        time.sleep(0.4)
    return got


def spreads_of(rows: list) -> list:
    """유효한 bid/ask 만 골라 스프레드%(mid 대비) 목록을 낸다."""
    out = []
    for r in rows:
        b, a = r.get("bidPrice"), r.get("askPrice")
        if isinstance(b, (int, float)) and isinstance(a, (int, float)) and b > 0 and a > b:
            out.append((a - b) / ((a + b) / 2) * 100)
    return sorted(out)


def ages_minutes(rows: list) -> list:
    """호가 timestamp 가 몇 분 전 것인지."""
    now = time.time()
    return sorted((now - r["timestamp"] / 1000) / 60
                  for r in rows if isinstance(r.get("timestamp"), (int, float)))


def report(label: str, symbols: list, rows_map: dict) -> dict:
    """자산군 하나의 커버리지·스프레드·신선도·잔량단위를 찍고 요약을 낸다."""
    rows = list(rows_map.values())
    cov = len(rows) / len(symbols) * 100 if symbols else 0.0
    print(f"\n  [{label}] 커버리지 {len(rows)}/{len(symbols)} ({cov:.1f}%)")
    missing = [s for s in symbols if s not in rows_map]
    if missing:
        print(f"           누락 예시: {missing[:8]}")
    if not rows:
        return {}

    sp = spreads_of(rows)
    ag = ages_minutes(rows)
    summary = {"coverage": cov, "n": len(rows)}

    if sp:
        def pct(p):
            return sp[min(int(len(sp) * p), len(sp) - 1)]
        over = sum(1 for s in sp if s > SPREAD_GUARD_PCT) / len(sp) * 100
        print(f"           스프레드%: 중앙 {st.median(sp):.3f} · p75 {pct(.75):.3f} "
              f"· p95 {pct(.95):.3f} · 최대 {max(sp):.2f}")
        print(f"           {SPREAD_GUARD_PCT}% 초과: {over:.1f}%  "
              f"(가드를 걸면 이 비율만큼 주문이 보류된다)")
        summary["spread_median"] = st.median(sp)
        summary["spread_over_guard"] = over

    if ag:
        print(f"           신선도: 최신 {min(ag):.1f}분 전 · 중앙 {st.median(ag):.1f}분 전")
        summary["age_median"] = st.median(ag)

    sizes = [r[k] for r in rows for k in ("bidSize", "askSize")
             if isinstance(r.get(k), (int, float)) and r[k] > 0]
    if sizes:
        under100 = sum(1 for s in sizes if s < 100) / len(sizes) * 100
        mult100 = sum(1 for s in sizes if s % 100 == 0) / len(sizes) * 100
        unit = "lot(100주)" if under100 > 60 else "주(share)" if mult100 > 60 else "판별 불가"
        print(f"           잔량: 중앙 {st.median(sizes):.0f} · 100미만 {under100:.0f}% "
              f"· 100의배수 {mult100:.0f}%  -> {unit}")
        summary["size_unit"] = unit
    return summary


def verdict(sess: str, stock: dict, etf: dict) -> None:
    """실행 레이어에 쓸 수 있는지 최종 판정."""
    print("\n" + "=" * 68)
    print("  판정")
    print("=" * 68)

    if sess != "정규장":
        print(f"  [보류] 지금은 {sess} 이라 판정할 수 없다.")
        print("         장외에는 호가창이 비어 스프레드가 구조적으로 벌어진다.")
        print("         정규장(KST 22:30~05:00)에 다시 실행할 것.")
        return

    age = stock.get("age_median")
    if age is None:
        print("  [실패] timestamp 를 읽지 못했다.")
        return

    if age > STALE_MINUTES:
        print(f"  [실패] 정규장인데 호가가 {age:.0f}분 전 값이다 "
              f"({STALE_MINUTES}분 초과).")
        print("         batch-aftermarket-quote 는 시간외 전용이다.")
        print("         -> FMP 로는 실행 레이어를 만들 수 없다. Polygon.io 또는")
        print("            IBKR 로 가야 한다.")
        return

    print(f"  [통과] 정규장 중 호가가 갱신된다 (중앙 {age:.1f}분 전).")
    med = stock.get("spread_median")
    over = stock.get("spread_over_guard")
    if med is not None:
        print(f"         주식 스프레드 중앙값 {med:.3f}%")
        if med > SPREAD_GUARD_PCT:
            print(f"         ! 중앙값이 가드 임계({SPREAD_GUARD_PCT}%)를 넘는다.")
            print(f"           임계를 올리거나(예: p75 값) 데이터 품질을 의심할 것.")
        else:
            print(f"         가드 임계 {SPREAD_GUARD_PCT}% 기준 주문 보류율 "
                  f"{over:.1f}% - 실사용 가능.")
    if etf.get("spread_median") is not None:
        print(f"         ETF 스프레드 중앙값 {etf['spread_median']:.3f}% "
              f"· 보류율 {etf.get('spread_over_guard', 0):.1f}%")
    print(f"         잔량 단위: 주식 {stock.get('size_unit','?')} "
          f"/ ETF {etf.get('size_unit','?')}")
    print("\n         남는 한계: 최우선호가 1단계뿐이다. 10단계 잔량이 필요하면")
    print("         IBKR(실시간) 또는 Databento(과거 검증용)가 필요하다.")


def main() -> None:
    import argparse
    import glob
    p = argparse.ArgumentParser(description="FMP 호가 엔드포인트 검증")
    p.add_argument("--archive", default="",
                   help="유니버스를 꺼낼 아카이브 CSV (기본: history 의 최신 파일)")
    p.add_argument("--sample", type=int, default=200,
                   help="자산군별 표본 종목 수 (기본 200 · 0=전체)")
    args = p.parse_args()

    # 키가 없으면 커버리지가 0으로 나와 "FMP 가 이 종목들을 모른다" 처럼 보인다.
    # CI 에서 시크릿이 빠졌을 때 그 오독을 막으려고 먼저 끊는다.
    if not api_key():
        print("[!] FMP_API_KEY 가 없다 (.env 또는 환경변수)")
        raise SystemExit(1)

    archive = args.archive
    if not archive:
        files = sorted(glob.glob("history/*.csv"))
        if not files:
            print("[!] history/*.csv 가 없다")
            return
        archive = files[-1]

    sess, now_et = session_now()
    print("=" * 68)
    print("  FMP 호가 엔드포인트 검증")
    print("=" * 68)
    print(f"  세션: {sess}")
    print(f"  ET  {now_et:%Y-%m-%d %H:%M:%S}  |  "
          f"KST {now_et.astimezone(KST):%Y-%m-%d %H:%M:%S}")
    print(f"  아카이브: {archive}")

    stocks, etfs = universe_symbols(archive)
    if not stocks and not etfs:
        print("[!] 아카이브를 읽지 못했다")
        return
    if args.sample > 0:
        stocks, etfs = stocks[:args.sample], etfs[:args.sample]
    print(f"  표본: 주식 {len(stocks)} · ETF {len(etfs)}")

    s_sum = report("주식", stocks, fetch_quotes(stocks)) if stocks else {}
    e_sum = report("ETF ", etfs, fetch_quotes(etfs)) if etfs else {}
    verdict(sess, s_sum, e_sum)


if __name__ == "__main__":
    main()
