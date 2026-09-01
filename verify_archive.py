"""아카이브 정합성 검사. 매일 스캔 직후에 돈다.

성능이 아니라 **정합성**만 본다. 값이 좋은지 나쁜지는 표본이 쌓여야
판단할 수 있고 사람이 볼 일이지만, 데이터가 조용히 갈리는 것은 기계가
매일 봐야 한다 - 눈치채는 시점이 늦을수록 갈린 구간이 길어진다.

현재 검사는 하나다.

  · 총점이 빈 BUY 행 — filter_rows 가 HOLD 로 강등하므로 그날의 진입이
    흔적 없이 사라진다. 근거는 backtest.blank_total_buys 머리말에 있다.

여기에 넣지 않은 것: 봉 캐시 회귀(--compare 두 열이 서로 다른 데이터 위에서
비교되는 것). 그것은 코드 회귀이지 데이터 문제가 아니라 tests.yml 이
PR 마다 잡는다 (tests/test_backtest.py 의 캐시 테스트 3건). 매일 돌리면
yfinance 를 왕복시켜 rate limit 에 걸릴 뿐 새로 잡는 것이 없다.

실행:  python verify_archive.py
종료코드: 정상 0, 이상 1.
"""
from __future__ import annotations

import argparse
import sys

import backtest as bt
import tracks

TRACKS = ("stocks", "etf")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="아카이브 정합성 검사")
    ap.parse_args(argv)

    bad = 0
    for track in TRACKS:
        pattern = tracks.history_glob(track)
        # load_archive 를 트랙마다 따로 부른다. 한 번에 읽으면 어느 트랙이
        # 갈렸는지 알 수 없고, 고칠 때 봐야 할 아카이브가 둘로 늘어난다.
        rows = bt.load_archive(pattern)
        blanks = bt.blank_total_buys(rows)
        if not blanks:
            print(f"[*] {track}: 총점 빈 BUY 없음 ({len(rows)}행)")
            continue

        bad += len(blanks)
        print(f"[!] {track}: 총점이 빈 BUY {len(blanks)}건 ({pattern})")
        # 전부 찍는다. 잘라 놓으면 하필 잘린 쪽이 원인일 때 로그만 보고는
        # 못 고치고 다시 돌려야 한다.
        for r in blanks:
            print(f"    {r.get('ticker')} {r.get('date')} "
                  f"signal={r.get('signal')}")

    if bad:
        print(f"\n총 {bad}건. 이 행들의 진입은 백테스트·리포트에서 "
              "조용히 사라진다.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
