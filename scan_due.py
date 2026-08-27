"""그날 스캔이 이미 돌았는지 판단한다.

GitHub Actions 의 schedule 이벤트는 보장되지 않는다. 부하가 걸리면 밀리고,
밀다 못하면 아예 배달되지 않는다. 2026-08-26 22:00 UTC 크론이 그렇게 통째로
유실됐다 - 실행이 생성조차 되지 않았으므로 notify-failure 도 울리지 않았다.
그 잡은 scan 이 '실패'해야 도는데, 시작하지 않은 잡에는 결과가 없다. 결국
사람이 다음 날 대시보드를 보고서야 알아챘다.

그래서 크론을 두 번 건다. 본 실행이 유실되면 몇 시간 뒤 따라잡기 실행이 같은
스캔을 돌린다. 이 모듈은 그 두 번째 실행이 이미 끝난 하루를 다시 돌지 않도록
막는다. 판단 기준은 그날 KST 날짜의 아카이브 파일이다 - 스캔이 성공했다는
사실을 남기는 것이 그 파일이고, 리포트도 같은 파일을 읽는다.

표준 라이브러리만 쓴다. mailer.py 와 같은 이유로 의도된 제약이다. 의존성
설치가 깨진 날에도 이 판단은 서야 한다.
"""

import argparse
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

# KST 는 서머타임이 없어 UTC+9 고정. TZ=Asia/Seoul 은 tzdata 가 없는 러너에서
# 조용히 UTC 를 KST 라고 찍으므로 오프셋으로 계산한다 (scan.yml 커밋 스텝과
# 같은 이유, 같은 방식).
KST = timezone(timedelta(hours=9))


def kst_date(now=None):
    """실행 시점의 KST 날짜를 YYYY-MM-DD 로 돌려준다."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(KST).strftime("%Y-%m-%d")


def is_scan_needed(event_name, archive_dir, date=None):
    """그날 스캔을 (다시) 돌려야 하면 True.

    workflow_dispatch 는 언제나 True 다. 사람이 직접 부른 것은 그날 결과가
    있어도 다시 돌리라는 뜻이고, 여기서 막으면 손으로 복구할 방법이 없어진다.
    """
    if event_name == "workflow_dispatch":
        return True

    archive = pathlib.Path(archive_dir) / f"{date or kst_date()}.csv"
    # 크기까지 본다. 스캔이 헤더만 쓰고 죽은 파일을 '돌았다'로 세면 따라잡기가
    # 막혀, 복구할 수 있었던 하루를 그대로 흘려보낸다.
    return not (archive.is_file() and archive.stat().st_size > 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", default="history", help="아카이브 디렉터리")
    parser.add_argument("--event", default="schedule", help="GitHub 이벤트 이름")
    args = parser.parse_args(argv)

    date = kst_date()
    needed = is_scan_needed(args.event, args.archive, date)

    # 표준출력은 GITHUB_OUTPUT 으로 리다이렉트되므로 설명은 stderr 로 보낸다.
    if needed:
        print(f"{date} (KST) 아카이브가 없다. 스캔을 돌린다.", file=sys.stderr)
    else:
        print(f"{date} (KST) 스캔은 이미 끝났다. 건너뛴다.", file=sys.stderr)

    print(f"needed={'true' if needed else 'false'}")
    print(f"kst_date={date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
