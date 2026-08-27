from datetime import datetime, timezone

import scan_due


def test_a_missing_archive_means_the_scan_is_due(tmp_path):
    assert scan_due.is_scan_needed("schedule", tmp_path, "2026-08-27") is True


def test_an_existing_archive_means_it_already_ran(tmp_path):
    (tmp_path / "2026-08-27.csv").write_text("ticker\nAAPL\n", encoding="utf-8")
    assert scan_due.is_scan_needed("schedule", tmp_path, "2026-08-27") is False


def test_a_manual_run_is_always_due(tmp_path):
    # 사람이 직접 부른 것은 그날 결과가 있어도 다시 돌리라는 뜻이다. 따라잡기
    # 가드가 재실행까지 막으면 손으로 복구할 방법이 없어진다.
    (tmp_path / "2026-08-27.csv").write_text("ticker\nAAPL\n", encoding="utf-8")
    assert scan_due.is_scan_needed("workflow_dispatch", tmp_path, "2026-08-27") is True


def test_yesterdays_archive_does_not_satisfy_today(tmp_path):
    (tmp_path / "2026-08-26.csv").write_text("ticker\nAAPL\n", encoding="utf-8")
    assert scan_due.is_scan_needed("schedule", tmp_path, "2026-08-27") is True


def test_an_empty_archive_does_not_count_as_a_run(tmp_path):
    # 스캔이 헤더만 쓰고 죽은 경우까지 '돌았다'로 보면 따라잡기가 막힌다.
    (tmp_path / "2026-08-27.csv").write_text("", encoding="utf-8")
    assert scan_due.is_scan_needed("schedule", tmp_path, "2026-08-27") is True


def test_a_missing_archive_directory_means_the_scan_is_due(tmp_path):
    assert scan_due.is_scan_needed("schedule", tmp_path / "none", "2026-08-27") is True


def test_the_kst_date_is_utc_plus_nine():
    # 본 실행은 22:37 UTC 다. KST 로는 다음 날 07:37 이므로 그날 아카이브
    # 이름은 하루 뒤가 된다 - 여기서 날짜를 UTC 로 잡으면 가드가 매번
    # 전날 파일을 찾아 영원히 '돌아야 한다'고 답한다.
    now = datetime(2026, 8, 26, 22, 37, tzinfo=timezone.utc)
    assert scan_due.kst_date(now) == "2026-08-27"


def test_the_catch_up_run_still_lands_on_the_same_kst_date():
    # 따라잡기는 01:17 UTC, 곧 같은 날 10:17 KST 다. 본 실행과 같은 날짜를
    # 봐야 유실을 알아챈다.
    now = datetime(2026, 8, 27, 1, 17, tzinfo=timezone.utc)
    assert scan_due.kst_date(now) == "2026-08-27"
