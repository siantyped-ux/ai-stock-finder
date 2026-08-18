"""git에 남은 dashboard_data.js 스냅샷을 이력 CSV로 소급 적재한다.

1회성 스크립트. 이미 존재하는 history/*.csv는 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

import history
from stock_finder import drop_unsettled_bars

KST = history.KST
CORRUPT_RATIO = 0.8   # 중앙값 대비 이 비율 미만이면 손상으로 본다


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


def fetch_history_only(ticker: str, retries: int = 4):
    """1년 일봉만 조회한다. info는 받지 않아 정규 스캔보다 빠르다.

    미확정 봉(종가 NaN)은 제거한다. 그냥 두면 slice 결과가 NaN 봉에서 끝나
    종가 없는 이력 행이 만들어진다.
    """
    backoff = 5.0
    for attempt in range(retries):
        try:
            df = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
            if df.empty:
                return None
            df = drop_unsettled_bars(df)
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

    frames = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_history_only, t): t for t in tickers}
        for n, fut in enumerate(as_completed(futures), 1):
            t = futures[fut]
            frames[t] = fut.result()
            if n % 50 == 0 or n == len(tickers):
                print(f"\r    {n}/{len(tickers)}", end="", flush=True)
    print()

    missing = sum(1 for v in frames.values() if v is None)
    if missing:
        print(f"[!] 일봉 조회 실패 {missing}종목 · 해당 행은 가격 열이 빈다")

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
                price["market_cap"] = None   # 소급분은 시점 시총 복원 불가
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
