import pathlib

import yaml

WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _crons(path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    # YAML 1.1 에서 따옴표 없는 on: 은 키가 아니라 불린 True 로 파싱된다.
    triggers = doc.get("on", doc.get(True)) or {}
    return [entry["cron"] for entry in triggers.get("schedule", [])]


def test_no_cron_sits_on_the_hour():
    # 2026-08-26 사고: '0 22 * * *' 스케줄 이벤트가 통째로 유실됐다. 실행이
    # 아예 생성되지 않아 notify-failure 도 울리지 않았다. 정각은 Actions
    # 스케줄러가 가장 붐비는 슬롯이라 밀리다 못해 버려진다 - 직전 나흘의
    # 실제 시작 시각도 22:25 → 22:26 → 22:29 → 22:31 로 계속 밀렸다.
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for cron in _crons(path):
            minute = cron.split()[0]
            assert minute != "0", f"{path.name}: '{cron}' 은 정각 슬롯이다"


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
