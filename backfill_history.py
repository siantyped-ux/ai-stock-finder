"""git에 남은 dashboard_data.js 스냅샷을 이력 CSV로 소급 적재한다.

1회성 스크립트. 이미 존재하는 history/*.csv는 건드리지 않는다.
"""
from __future__ import annotations

import json
import re
import statistics
import subprocess
from datetime import datetime, timedelta, timezone

import history

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
