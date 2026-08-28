"""스캔 스케줄에 관한 규칙.

여기 있던 `test_no_cron_sits_on_the_hour` 는 2026-08-28 에 지웠다. "정각은
Actions 스케줄러가 가장 붐비는 슬롯이라 밀리다 못해 버려진다"는 전제를
규칙으로 못 박아 뒀는데, 실측이 그걸 지지하지 않는다.

2026-08-01 ~ 08-28 의 schedule 실행 32건, 예정 시각 대비 실제 지연:

    '30 2 * * *'     n=17   중앙값 1h52m   (42m ~ 3h27m)
    '0 22 * * *'     n=8    중앙값   29m   (26m ~ 31m)
    '0 15 * * 1-5'   n=3    중앙값   41m   (40m ~ 1h17m)
    '37 22' + '17 1' n=1           9h55m
    '26 15 * * 1-5'  n=1           9h01m

가장 정확했던 설정이 오히려 정각인 '0 22' 다 (26~31분, 8일 연속). :37 로
옮긴 판단 자체는 정각을 피하라는 GitHub 문서 권고를 따른 것이라 타당했지만,
9~10시간 지연으로의 악화는 그 변경 하루 전인 08-26 에 구 크론에서 이미
시작됐다. 표본이 작아 어느 쪽이 낫다고 말할 수 없다 - 그래서 규칙을 두지
않는다. 근거 없는 단언을 테스트로 박아 두면 다음 사람이 또 크론 분을
만지는 데 시간을 쓴다.

정시성 자체는 저장소 밖 클라우드 루틴이 책임진다. 아래 두 테스트가 지키는
것은 그 루틴이 실패했을 때의 폴백이 살아 있는지다.
"""

import pathlib

import yaml

WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _crons(path):
    doc = _load(path)
    # YAML 1.1 에서 따옴표 없는 on: 은 키가 아니라 불린 True 로 파싱된다.
    triggers = doc.get("on", doc.get(True)) or {}
    return [entry["cron"] for entry in triggers.get("schedule", [])]


def test_the_scan_has_a_catch_up_schedule():
    # 크론이 하나뿐이면 그 하나가 유실될 때 그날 시점 데이터가 영구히
    # 사라진다. 스코어는 나중에 다시 계산할 수 없다.
    assert len(_crons(WORKFLOWS / "scan.yml")) >= 2


def test_the_catch_up_runs_after_the_primary_finishes():
    # 따라잡기가 본 실행과 겹치면 같은 날을 두 번 스캔한다. 본 실행은 최대
    # 120분 상한에 스케줄 지연까지 얹히므로 최소 두 시간은 떼어 둔다.
    crons = _crons(WORKFLOWS / "scan.yml")
    minutes = sorted(int(c.split()[1]) * 60 + int(c.split()[0]) for c in crons)
    gaps = [b - a for a, b in zip(minutes, minutes[1:])]
    gaps.append(24 * 60 - minutes[-1] + minutes[0])
    assert min(gaps) >= 120, f"크론 간격이 너무 좁다: {crons}"


def test_the_scan_records_how_late_it_finished():
    # 이게 빠지면 감시가 다시 '그날 아카이브가 있는가' 하나로 돌아간다.
    # 그 기준으로는 9시간 55분 밀려 도착한 실행도 정상으로 집계된다.
    doc = _load(WORKFLOWS / "scan.yml")
    runs = [
        step.get("run", "")
        for step in doc["jobs"]["scan"]["steps"]
    ]
    assert any("scan_latency.py" in run for run in runs), \
        "scan 잡이 scan_latency.py 를 부르지 않는다 - 지연이 기록되지 않는다"


def test_the_latency_log_is_committed():
    # 기록만 하고 커밋하지 않으면 러너가 사라질 때 같이 사라진다.
    doc = _load(WORKFLOWS / "scan.yml")
    commits = [
        step.get("run", "")
        for step in doc["jobs"]["scan"]["steps"]
        if "git add" in step.get("run", "")
    ]
    assert commits, "커밋 스텝을 찾지 못했다"
    assert any("scan_latency.csv" in run for run in commits)


def test_the_latency_log_stays_out_of_the_archive_glob():
    # history/*.csv 는 아카이브 전용 글롭이다 (verify_quotes, backtest,
    # forward_returns, recompute_history, tracks.history_glob). 날짜가 아닌
    # 파일을 그 안에 두면 다섯 군데가 하루치 아카이브로 읽으려 든다.
    import scan_latency

    assert not scan_latency.LOG_PATH.startswith("history")
