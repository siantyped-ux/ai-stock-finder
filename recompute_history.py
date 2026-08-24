"""아카이브 스코어를 신 산식으로 재계산한다.

2026-08-24 에 축 구성이 바뀌었다 (docs/superpowers/specs/2026-08-24-flow-axis-design.md).

    이전  주식 tech .35 + macro .20 + filing .30 + value .15
          ETF  tech .636 + macro .364
    이후  주식 tech .30 + flow .20 + filing .30 + value .20
          ETF  tech .60 + flow .40

재계산하지 않으면 아카이브에 두 척도가 섞인다. exit_rules.evaluate() 는 보유
종목의 그날 total 이 exit_total 미만이면 청산하는데, 진입일은 구 척도 · 청산
판정일은 신 척도가 되어 서로 다른 자로 잰 값을 비교하게 된다. backtest.py 도
아카이브 전체를 재생하므로 같은 문제를 겪는다.

backfill_history.py 로는 할 수 없다. 그 스크립트는 git 에 남은 dashboard_data.js
스냅샷을 재생할 뿐 점수를 재계산하지 않고, 옛 스냅샷에는 flow 를 만들 재료가 없다.

무엇을 다시 계산하고 무엇을 그대로 두는가
  다시 계산  tech  - 거래량 항목(±8)이 flow 로 빠져 옛 값과 다르다
             flow  - 신설 축
             total · consensus · signal · ev · target · hitl
             regime - ^VIX/^TNX 종가로 복원한다
  그대로 둠  filing · value - 아카이브 값을 쓴다. FMP 재조회는 비싸고, 무엇보다
                              시점 공시 데이터를 지금 다시 받을 수 없다
             macro          - 총점에서 빠졌을 뿐 그날의 기록으로 남긴다.
                              FRED 시점 데이터가 없어 어차피 복원 불가다
             가격 열        - bar_date · close · volume · atr14 등

룩어헤드 방지: 각 행의 bar_date 까지만 잘라 쓴다. 그 값이 그 스캔이 실제로 본
마지막 봉이다. 스캔 날짜에서 파생하지 않는 것은 휴장일 때문이다.

기본은 dry-run 이다. --apply 를 줘야 기록하며, 그 때 원본을 먼저 백업한다.

실행
    python recompute_history.py              # 비교만 출력
    python recompute_history.py --apply      # 백업 후 기록
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics as st
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

import console
import flow
import history
import stock_finder as sf
from backfill_history import slice_to_date

# 캐시는 JSON 이다. pickle 을 쓰지 않는 것은 의도다 - 역직렬화가 임의 코드를
# 실행할 수 있고, 저장소의 다른 캐시(_save_cache)도 JSON 을 쓴다. 우리에게
# 필요한 것은 날짜와 OHLCV 뿐이라 표만 실으면 충분하다.
CACHE = Path(".cache/recompute_frames.json")
BACKUP_DIR = Path("history_pre_flow")

# 국면 판정용 지수. calc_macro_score 의 분기와 같은 자료를 쓴다.
VIX_TICKER = "^VIX"
TNX_TICKER = "^TNX"


def archive_files(pattern: str = "history/*.csv") -> list[Path]:
    return sorted(Path().glob(pattern))


def read_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def fetch_frame(ticker: str, retries: int = 3):
    """1년 일봉. 실패하면 None.

    auto_adjust=True 는 조회 시점 기준으로 분할·배당을 소급 반영한다. 원 스캔
    당시의 조정가와 완전히 같지는 않지만, 신·구 산식을 같은 자료 위에서 비교하는
    것이 목적이므로 문제가 되지 않는다.
    """
    for attempt in range(retries):
        try:
            df = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
            if df is None or df.empty:
                return None
            return sf.drop_unsettled_bars(df)
        except Exception as e:
            if ("429" in str(e) or "Too Many" in str(e)) and attempt < retries - 1:
                time.sleep(4 * (attempt + 1))
                continue
            return None
    return None


def _to_jsonable(df) -> dict | None:
    """DataFrame 을 날짜 + OHLCV 표로 줄인다. tech/flow 가 쓰는 열만 남긴다."""
    if df is None or df.empty:
        return None
    return {
        "dates": [f"{d:%Y-%m-%d}" for d in df.index],
        "High": [float(x) for x in df["High"].values],
        "Low": [float(x) for x in df["Low"].values],
        "Close": [float(x) for x in df["Close"].values],
        "Volume": [float(x) for x in df["Volume"].values],
    }


def _from_jsonable(d: dict | None):
    """캐시 표를 다시 DataFrame 으로. slice_to_date 가 DatetimeIndex 를 쓴다."""
    if not d:
        return None
    return pd.DataFrame(
        {k: d[k] for k in ("High", "Low", "Close", "Volume")},
        index=pd.to_datetime(d["dates"]),
    )


def load_frames(tickers: list[str], workers: int, refresh: bool) -> dict:
    """티커별 일봉을 받아 캐시한다. 재실행이 싸야 비교를 반복할 수 있다."""
    raw = {}
    if CACHE.exists() and not refresh:
        try:
            raw = json.loads(CACHE.read_text(encoding="utf-8"))
            print(f"[*] 캐시에서 {len(raw)}개 프레임 로드")
        except Exception:
            raw = {}

    todo = [t for t in tickers if t not in raw]
    if todo:
        print(f"[*] 일봉 조회 {len(todo)}종목 (동시 {workers})")
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(fetch_frame, t): t for t in todo}
            for fu in as_completed(futs):
                raw[futs[fu]] = _to_jsonable(fu.result())
                done += 1
                if done % 100 == 0 or done == len(todo):
                    print(f"\r    {done}/{len(todo)}", end="", flush=True)
        print()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(raw), encoding="utf-8")

    return {t: _from_jsonable(raw.get(t)) for t in tickers}


def classify_regime(vix: float, us10y: float) -> str:
    """국면 문자열. 임계를 복사하지 않고 calc_macro_score 를 그대로 부른다.

    복사해 두면 원본 임계가 바뀔 때 조용히 어긋난다. dxy 와 sector 는 국면
    분기에 관여하지 않으므로 중립값을 넣는다 (calc_macro_score 는 셋째 값으로
    regime 을 낸다).
    """
    return sf.calc_macro_score(vix, 100.0, us10y, "미분류", None)[2]


def load_regimes() -> dict:
    """날짜 -> 국면. ^VIX / ^TNX 종가로 복원한다.

    원 스캔은 FRED 값으로 VIX·US10Y 를 덮어썼는데 FRED 의 VIXCLS 는 일 종가라
    ^VIX 종가와 사실상 같다. 시점 FRED 자료를 지금 받을 수 없으므로 이 근사를
    쓰고, 되살린 값은 게이트 백테스트용이지 그날 판정의 복제가 아니다.
    """
    out = {}
    try:
        vix = yf.Ticker(VIX_TICKER).history(period="1y")["Close"]
        tnx = yf.Ticker(TNX_TICKER).history(period="1y")["Close"]
    except Exception as e:
        print(f"[!] 국면 지수 조회 실패: {str(e)[:60]} - regime 은 비워 둔다")
        return out

    tnx_by_date = {f"{d:%Y-%m-%d}": float(v) for d, v in tnx.items()}
    for d, v in vix.items():
        key = f"{d:%Y-%m-%d}"
        y = tnx_by_date.get(key)
        if y is not None:
            out[key] = classify_regime(float(v), y)
    return out


def recompute_row(row: dict, frames: dict, regimes: dict) -> tuple[dict, str]:
    """행 하나를 신 산식으로 다시 채운다. (행, 상태) 를 낸다.

    상태는 'ok' 또는 건너뛴 이유다. 건너뛴 행은 원본을 그대로 돌려준다 -
    일봉을 못 받는 티커는 backtest 도 시세를 못 받아 어차피 거래되지 않는다.
    """
    df = frames.get(row["ticker"])
    if df is None:
        return row, "일봉 없음"

    sliced = slice_to_date(df, row.get("bar_date") or "")
    if sliced is None or len(sliced) < 60:
        return row, "봉 부족"

    tech, _, r3m = sf.calc_tech_score(sliced)
    flow_score, _ = flow.calc_flow_score(sliced)

    out = dict(row)
    out["tech"] = tech
    out["flow"] = flow_score
    out["regime"] = regimes.get(row.get("bar_date", ""), "")

    if row.get("asset_type") == "ETF":
        total = sf.calc_total_etf(tech, flow_score)
        cons = sf.calc_consensus_etf(tech, flow_score)
        n_axes = 2
        ev, target = sf.calc_ev_and_target(tech, flow_score, tech, flow_score, r3m)
    else:
        filing, value = int(row["filing"]), int(row["value"])
        total = sf.calc_total(tech, flow_score, filing, value)
        cons = sf.calc_consensus(tech, flow_score, filing, value)
        n_axes = 4
        ev, target = sf.calc_ev_and_target(tech, flow_score, filing, value, r3m)

    signal = sf.calc_signal(total, cons, n_axes=n_axes)
    out["total"] = total
    out["consensus"] = cons
    out["signal"] = signal
    out["ev"] = ev
    out["target"] = target
    out["hitl"] = sf.calc_hitl(signal, total, tech)
    return out, "ok"


def describe(label: str, rows: list[dict], key: str = "total") -> None:
    vals = [int(r[key]) for r in rows if r.get(key) not in (None, "")]
    if not vals:
        print(f"    {label}: 없음")
        return
    v = sorted(vals)
    print(f"    {label}: 평균 {st.mean(v):5.1f} · 중앙 {st.median(v):3d} "
          f"· 최대 {max(v):3d} · 70이상 {sum(1 for x in v if x >= 70):>5}"
          f" ({sum(1 for x in v if x >= 70)/len(v)*100:.1f}%)")


def compare(before: list[dict], after: list[dict]) -> None:
    """신·구 분포와 신호 변화를 나란히 낸다."""
    print("\n" + "=" * 70)
    print("  총점 분포")
    print("=" * 70)
    for at in ("STOCK", "ETF"):
        b = [r for r in before if r.get("asset_type") == at]
        a = [r for r in after if r.get("asset_type") == at]
        if not b:
            continue
        print(f"\n  [{at}] n={len(b)}")
        describe("이전", b)
        describe("이후", a)

    print("\n" + "=" * 70)
    print("  신호 분포")
    print("=" * 70)
    bs, as_ = Counter(r["signal"] for r in before), Counter(r["signal"] for r in after)
    print(f"    {'신호':<12}{'이전':>8}{'이후':>8}{'차이':>8}")
    for s in ("STRONG_BUY", "BUY", "WATCH", "HOLD", "AVOID"):
        print(f"    {s:<12}{bs[s]:>8}{as_[s]:>8}{as_[s]-bs[s]:>+8}")

    changed = sum(1 for b, a in zip(before, after) if b["signal"] != a["signal"])
    print(f"\n    신호가 바뀐 행: {changed}/{len(before)} ({changed/len(before)*100:.1f}%)")

    # BUY 는 진입을 만드는 신호라 자산군별로 따로 본다
    print("\n    BUY 계열(BUY+STRONG_BUY) 자산군별")
    for at in ("STOCK", "ETF"):
        b = sum(1 for r in before
                if r.get("asset_type") == at and r["signal"] in ("BUY", "STRONG_BUY"))
        a = sum(1 for r in after
                if r.get("asset_type") == at and r["signal"] in ("BUY", "STRONG_BUY"))
        n = sum(1 for r in before if r.get("asset_type") == at)
        if n:
            print(f"      {at:<6} {b:>5} -> {a:>5}   (전체 {n}행)")


def main() -> None:
    console.force_utf8()
    p = argparse.ArgumentParser(description="아카이브 스코어 신 산식 재계산")
    p.add_argument("--apply", action="store_true",
                   help="실제로 기록한다 (기본은 비교만)")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--refresh", action="store_true", help="일봉 캐시를 무시하고 다시 받는다")
    p.add_argument("--pattern", default="history/*.csv")
    args = p.parse_args()

    files = archive_files(args.pattern)
    if not files:
        print("[!] 대상 CSV 가 없다")
        return

    per_file = {f: read_rows(f) for f in files}
    before = [r for rows in per_file.values() for r in rows]
    tickers = sorted({r["ticker"] for r in before})
    print(f"[*] 파일 {len(files)}개 · {len(before)}행 · 고유 티커 {len(tickers)}개")

    frames = load_frames(tickers, args.workers, args.refresh)
    regimes = load_regimes()
    print(f"[*] 국면 복원 {len(regimes)}일")

    after_per_file = {}
    status = Counter()
    for f, rows in per_file.items():
        out = []
        for r in rows:
            new, st_ = recompute_row(r, frames, regimes)
            status[st_] += 1
            out.append(new)
        after_per_file[f] = out
    after = [r for rows in after_per_file.values() for r in rows]

    print(f"\n[*] 재계산 {status['ok']}행 · 건너뜀 {sum(status.values()) - status['ok']}행")
    for k, v in status.items():
        if k != "ok":
            print(f"      {k}: {v}행 (원본 유지 - 일봉이 없어 backtest 도 거래하지 못한다)")

    compare(before, after)

    if not args.apply:
        print("\n[*] dry-run 입니다. 기록하려면 --apply 를 주세요.")
        return

    if BACKUP_DIR.exists():
        print(f"\n[!] 백업 폴더 {BACKUP_DIR} 가 이미 있습니다. "
              f"먼저 옮기거나 지운 뒤 다시 실행하세요.")
        sys.exit(1)
    BACKUP_DIR.mkdir(parents=True)
    for f in files:
        shutil.copy2(f, BACKUP_DIR / f.name)
    print(f"\n[*] 원본 {len(files)}개를 {BACKUP_DIR} 로 백업")

    for f, rows in after_per_file.items():
        with open(f, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=history.FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in history.FIELDS})
    print(f"[*] {len(files)}개 파일 재기록 완료")


if __name__ == "__main__":
    main()
