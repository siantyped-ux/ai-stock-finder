import verify_quotes


def _healthy(age=1.2):
    return {"age_median": age, "spread_median": 0.05,
            "spread_over_guard": 3.0, "size_unit": "주(share)"}


def test_a_live_endpoint_passes():
    assert verify_quotes.verdict("정규장", _healthy(), {}) == "pass"


def test_stale_quotes_in_regular_hours_fail():
    # 이것이 이 스크립트가 존재하는 이유다. aftermarket 엔드포인트가 정규장에
    # 갱신되지 않으면 실행 레이어를 FMP 로 만들 수 없다.
    assert verify_quotes.verdict("정규장", _healthy(age=42), {}) == "fail"


def test_a_missing_timestamp_fails():
    assert verify_quotes.verdict("정규장", {}, {}) == "fail"


def test_the_staleness_boundary_still_passes():
    age = verify_quotes.STALE_MINUTES
    assert verify_quotes.verdict("정규장", _healthy(age=age), {}) == "pass"


def test_outside_regular_hours_it_holds_rather_than_fails():
    # 장외에는 호가창이 비어 판정 자체가 불가능하다. 이걸 실패로 올리면
    # 주말마다 알림이 울리고, 매일 오는 알림은 곧 아무도 안 본다.
    for session in ("휴장(주말)", "프리마켓", "애프터마켓", "휴장(야간)"):
        assert verify_quotes.verdict(session, _healthy(), {}) == "hold"


def test_a_hold_does_not_fail_the_run(monkeypatch, tmp_path, capsys):
    # 종료 코드까지 확인한다. 판정이 반환값에만 남고 종료 코드로 올라가지
    # 않으면 워크플로는 초록으로 끝나고, 그 사이 스캔은 계속 성공하면서
    # 잘못된 가격을 아카이브에 쌓는다.
    assert _exit_code(monkeypatch, tmp_path, "hold") == 0


def test_a_failed_verdict_fails_the_run(monkeypatch, tmp_path):
    assert _exit_code(monkeypatch, tmp_path, "fail") == 1


def test_a_missing_archive_fails_the_run(monkeypatch, tmp_path):
    # 아카이브를 못 읽으면 아무것도 검증하지 못한 것이다. 조용히 0으로
    # 끝나면 '검증 통과' 와 구분되지 않는다.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(verify_quotes, "api_key", lambda: "k")
    monkeypatch.setattr("sys.argv", ["verify_quotes.py"])
    assert verify_quotes.main() == 1


def _exit_code(monkeypatch, tmp_path, want):
    archive = tmp_path / "archive.csv"
    archive.write_text("ticker,asset_type\nAAPL,STOCK\n", encoding="utf-8")
    monkeypatch.setattr(verify_quotes, "api_key", lambda: "k")
    monkeypatch.setattr(verify_quotes, "fetch_quotes", lambda syms, **kw: {})
    monkeypatch.setattr(verify_quotes, "report", lambda *a, **kw: {})
    monkeypatch.setattr(verify_quotes, "verdict", lambda *a, **kw: want)
    monkeypatch.setattr(verify_quotes, "session_now",
                        lambda: ("정규장", verify_quotes.datetime.now(verify_quotes.ET)))
    monkeypatch.setattr("sys.argv",
                        ["verify_quotes.py", "--archive", str(archive)])
    return verify_quotes.main()
