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
