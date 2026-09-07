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


# ─── 공휴일 (2026-09-07 노동절 오탐) ─────────────────────────
def test_a_holiday_in_regular_hours_becomes_a_closed_session():
    """평일 정규장 시간인데 거래소가 닫혀 있으면 공휴일이다.

    2026-09-07 노동절에 이 구분이 없어 직전 거래일 종가가 "4,037분 전
    호가" 로 읽혀 실패 이슈와 메일이 나갔다.
    """
    assert verify_quotes.resolve_session("정규장", False) == "휴장(공휴일)"


def test_a_holiday_verdict_holds_rather_than_fails():
    """공휴일은 보류다. 실패로 올리면 연 9~10회 오탐이 쌓인다."""
    sess = verify_quotes.resolve_session("정규장", False)
    assert verify_quotes.verdict(sess, {"age_median": 4037.0}, {}) == "hold"


def test_an_open_market_stays_a_regular_session():
    assert verify_quotes.resolve_session("정규장", True) == "정규장"


def test_an_unknown_market_state_keeps_the_clock_verdict():
    """못 물어봤다고 보류로 넘기지 않는다. 진짜 고장을 삼키게 된다."""
    assert verify_quotes.resolve_session("정규장", None) == "정규장"
    assert verify_quotes.verdict("정규장", {"age_median": 4037.0}, {}) == "fail"


def test_the_market_state_cannot_promote_a_closed_session():
    """시계가 장외라고 하면 거래소가 열려 있어도 정규장이 되지 않는다.

    isMarketOpen 은 프리·애프터마켓을 구분해 주지 않는다. 그 값으로 세션을
    올리면 시간외를 정규장으로 잘못 읽는다 - session_now 가 경계한 바로 그
    문제다.
    """
    for sess in ("프리마켓", "애프터마켓", "휴장(주말)", "휴장(야간)"):
        assert verify_quotes.resolve_session(sess, True) == sess
