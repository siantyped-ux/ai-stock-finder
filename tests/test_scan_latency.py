from datetime import datetime, timedelta, timezone

import scan_latency

UTC = timezone.utc


def _at(*args):
    return datetime(*args, tzinfo=UTC)


def test_the_target_is_the_previous_day_in_utc():
    # 08:15 KST 는 전날 23:15 UTC 다. 목표를 UTC 로 08:15 라고 잡으면 9시간
    # 뒤가 되어 어떤 지연도 걸리지 않는다 - 이 지표가 통째로 무력해진다.
    assert scan_latency.target_utc("2026-08-28") == _at(2026, 8, 27, 23, 15)


def test_finishing_on_the_target_is_not_late():
    assert scan_latency.late_minutes(_at(2026, 8, 27, 23, 15), "2026-08-28") == 0


def test_finishing_early_reports_slack_as_negative():
    # 저장소 크론이 제때 오면 07:50 KST 쯤 끝난다. 25분 여유다.
    assert scan_latency.late_minutes(_at(2026, 8, 27, 22, 50), "2026-08-28") == -25


def test_a_delayed_scan_is_counted_in_minutes():
    # 2026-08-28 실측: 수동 복구가 09:27 KST 에 끝났다. 목표보다 72분.
    assert scan_latency.late_minutes(_at(2026, 8, 28, 0, 27), "2026-08-28") == 72


def test_the_nine_hour_delay_that_started_this_is_measured(tmp_path):
    # 2026-08-27 01:17 UTC 크론이 11:12 UTC 에 배달됐다. 그 실행이 그대로
    # 스캔을 돌렸다면 목표보다 12시간 넘게 늦은 것으로 잡혀야 한다.
    late = scan_latency.late_minutes(_at(2026, 8, 27, 11, 25), "2026-08-27")
    assert late > 12 * 60


def test_record_writes_a_header_once(tmp_path):
    log = tmp_path / "scan_latency.csv"
    scan_latency.record(log, "2026-08-27", "schedule", _at(2026, 8, 26, 22, 50))
    scan_latency.record(log, "2026-08-28", "schedule", _at(2026, 8, 27, 23, 40))

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0].startswith("kst_date,")
    assert len(lines) == 3
    assert lines[1].startswith("2026-08-27,schedule,")


def test_record_returns_the_row_it_wrote(tmp_path):
    row = scan_latency.record(
        tmp_path / "log.csv", "2026-08-28", "workflow_dispatch", _at(2026, 8, 28, 0, 27)
    )
    assert row["late_minutes"] == 72
    assert row["event"] == "workflow_dispatch"
    assert row["target_utc"] == "2026-08-27T23:15:00Z"
    assert row["finished_utc"] == "2026-08-28T00:27:00Z"


def test_two_runs_on_the_same_day_both_get_a_row(tmp_path):
    # 따라잡기나 수동 복구로 하루에 두 번 도는 것은 정상이다. 각각 언제
    # 끝났는지가 보고 싶은 정보라 덮어쓰지 않고 둘 다 남긴다.
    log = tmp_path / "log.csv"
    scan_latency.record(log, "2026-08-28", "schedule", _at(2026, 8, 27, 23, 10))
    scan_latency.record(log, "2026-08-28", "workflow_dispatch", _at(2026, 8, 28, 0, 27))

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


def test_the_kst_date_comes_from_the_finish_time(tmp_path):
    # 본 실행은 22:37 UTC 다. 그날 KST 날짜는 하루 뒤이므로, 완료 시각을
    # UTC 날짜로 읽으면 매번 전날 목표와 대조해 12시간 늦은 것처럼 나온다.
    assert scan_latency.kst_date(_at(2026, 8, 27, 22, 50)) == "2026-08-28"


def test_the_cli_appends_and_reports_lateness(tmp_path, capsys, monkeypatch):
    log = tmp_path / "log.csv"
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    assert scan_latency.main(
        ["--out", str(log), "--event", "workflow_dispatch", "--at", "2026-08-28T00:27:00Z"]
    ) == 0

    captured = capsys.readouterr()
    assert "late_minutes=72" in captured.out
    assert "late=true" in captured.out
    assert "::warning::" in captured.out
    assert log.exists()


def test_the_cli_does_not_warn_when_the_scan_was_on_time(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    scan_latency.main(["--out", str(tmp_path / "log.csv"), "--at", "2026-08-27T22:50:00Z"])

    captured = capsys.readouterr()
    assert "late=false" in captured.out
    assert "::warning::" not in captured.out


def test_the_cli_writes_to_github_output_when_it_exists(tmp_path, monkeypatch):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))

    scan_latency.main(["--out", str(tmp_path / "log.csv"), "--at", "2026-08-28T00:27:00Z"])

    assert "late_minutes=72" in out.read_text(encoding="utf-8")


def test_a_late_scan_does_not_fail_the_step(tmp_path, monkeypatch):
    # 늦었다고 exit 1 을 내면 커밋 뒤에 붙은 report 잡이 통째로 건너뛴다.
    # 늦게라도 들어온 그날 데이터는 살려야 한다.
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert scan_latency.main(
        ["--out", str(tmp_path / "log.csv"), "--at", "2026-08-28T09:00:00Z"]
    ) == 0
