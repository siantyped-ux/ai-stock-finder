"""스캔이 목표 시각 안에 끝났는지 재서 남긴다.

지금까지 이 저장소의 스캔 감시는 "그날 아카이브가 있는가" 하나였다. 그래서
9시간 55분 밀려 도착한 실행도 '정상'으로 집계됐다 - 파일은 생겼으니까.
정시성이 요구사항인데 정시성을 재는 곳이 없었다.

이 모듈이 그 한 줄을 남긴다. 매일 스캔이 끝날 때 목표 시각과 실제 완료
시각의 차이를 `scan_latency.csv` 에 적는다. 하루치 판단이 아니라 추세를
보려는 것이다 - 저장소 크론을 계속 폴백으로 둘지, 아예 걷어낼지는 이
숫자가 쌓여야 답할 수 있다.

## 목표 시각을 08:15 KST 로 잡은 이유

정시성을 책임지는 것은 저장소 밖 클라우드 루틴이다 (07:55 KST 에 아카이브를
보고 없으면 workflow_dispatch 로 직접 돌린다). 스캔은 보통 13분 걸리므로
그 경로로 복구해도 08:10 경에는 끝난다. 08:15 는 그 경로가 제대로 도는 한
언제나 지켜지는 선이고, 넘겼다면 복구 경로까지 늦었거나 실패했다는 뜻이다.

저장소 크론(`37 22`, `17 1` UTC)이 제때 오면 07:50 경에 끝나 여유롭게
통과한다. 그쪽은 폴백이라 목표의 기준이 아니다.

## 왜 history/ 에 두지 않는가

`history/*.csv` 는 아카이브 전용 글롭이다 (verify_quotes.py, backtest.py,
forward_returns.py, recompute_history.py, tracks.history_glob). 그 안에
날짜가 아닌 파일을 하나 두면 다섯 군데가 그걸 하루치 아카이브로 읽으려
든다. 그래서 저장소 루트에 둔다.

표준 라이브러리만 쓴다. scan_due.py / mailer.py 와 같은 이유로 의도된
제약이다 - 의존성 설치가 깨진 날에도 이 기록은 남아야 한다.
"""

import argparse
import csv
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

# KST 는 서머타임이 없어 UTC+9 고정. TZ=Asia/Seoul 은 tzdata 가 없는 러너에서
# 조용히 UTC 를 KST 라고 찍으므로 오프셋으로 계산한다 (scan_due.py 와 같은 이유).
KST = timezone(timedelta(hours=9))

# 그날 스캔이 끝나 있어야 하는 KST 시각.
TARGET_HOUR, TARGET_MINUTE = 8, 15

LOG_PATH = "scan_latency.csv"
FIELDS = ["kst_date", "event", "target_utc", "finished_utc", "late_minutes"]


def kst_date(now=None):
    """실행 시점의 KST 날짜를 YYYY-MM-DD 로 돌려준다."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(KST).strftime("%Y-%m-%d")


def target_utc(date):
    """그 KST 날짜의 목표 완료 시각을 UTC 로 돌려준다.

    08:15 KST 는 전날 23:15 UTC 다. 날짜가 하루 밀리는 지점이라, 여기서
    UTC 로 그냥 08:15 을 잡으면 목표가 9시간 뒤로 가서 어떤 지연도
    잡히지 않는다.
    """
    day = datetime.strptime(date, "%Y-%m-%d")
    aim = datetime(day.year, day.month, day.day, TARGET_HOUR, TARGET_MINUTE, tzinfo=KST)
    return aim.astimezone(timezone.utc)


def late_minutes(finished, date):
    """목표보다 몇 분 늦게 끝났는지. 목표 안에 끝났으면 0 이하.

    올림도 내림도 하지 않고 분 단위로 자른다. 초 단위 정밀도는 이 지표가
    답하려는 질문(며칠째 몇 시간씩 밀리는가)에 아무 의미가 없다.
    """
    return int((finished - target_utc(date)).total_seconds() // 60)


def record(path, date, event, finished):
    """지연 한 줄을 append 하고 그 행을 돌려준다.

    파일이 없으면 헤더부터 쓴다. 이미 있으면 헤더를 다시 쓰지 않는다.
    같은 날짜가 두 번 들어오는 것은 막지 않는다 - 따라잡기나 수동 복구로
    하루에 두 번 도는 것은 정상이고, 그 두 번이 각각 언제 끝났는지가
    바로 보고 싶은 정보다.
    """
    path = pathlib.Path(path)
    row = {
        "kst_date": date,
        "event": event,
        "target_utc": target_utc(date).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished_utc": finished.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "late_minutes": late_minutes(finished, date),
    }

    is_new = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=LOG_PATH, help="지연 기록 파일")
    parser.add_argument("--event", default="schedule", help="GitHub 이벤트 이름")
    parser.add_argument(
        "--at",
        default=None,
        help="완료 시각 (ISO8601 UTC). 생략하면 지금. 테스트와 소급 기록용.",
    )
    args = parser.parse_args(argv)

    if args.at:
        finished = datetime.fromisoformat(args.at.replace("Z", "+00:00"))
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
    else:
        finished = datetime.now(timezone.utc)

    date = kst_date(finished)
    row = record(args.out, date, args.event, finished)
    late = row["late_minutes"]

    if late > 0:
        hours, minutes = divmod(late, 60)
        # 워크플로 로그에 경고로 띄운다. 이것만으로 사람이 알아채리라 기대하지
        # 않는다 - 도달을 보장하는 것은 클라우드 루틴의 메일이고, 이 줄은
        # 실행 로그를 나중에 들여다볼 때의 표식이다.
        print(
            f"::warning::{date} (KST) 스캔이 목표 {TARGET_HOUR:02d}:{TARGET_MINUTE:02d} KST "
            f"보다 {hours}시간 {minutes}분 늦게 끝났습니다.",
        )
        print(f"{date} (KST) 목표보다 {late}분 늦었다.", file=sys.stderr)
    else:
        print(f"{date} (KST) 목표 안에 끝났다 ({-late}분 여유).", file=sys.stderr)

    # guard 잡과 같은 방식으로 스텝 출력에 실어 보낸다.
    out = os.environ.get("GITHUB_OUTPUT")
    lines = f"late_minutes={late}\nlate={'true' if late > 0 else 'false'}\n"
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(lines)
    else:
        print(lines, end="")

    # 늦었다고 스캔을 실패시키지 않는다. 늦게라도 들어온 그날 데이터는
    # 살려야 하고, 여기서 exit 1 을 내면 커밋 뒤에 붙은 리포트 잡이
    # 통째로 건너뛴다.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
