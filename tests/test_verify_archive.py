import pytest

import backtest as bt
import verify_archive as va


def _row(ticker="AAA", date="2026-09-01", signal="BUY", total="80"):
    return dict(ticker=ticker, date=date, signal=signal, total=total,
                market="US")


# ─── blank_total_buys ──────────────────────────────────────────

def test_it_finds_a_buy_with_an_empty_total():
    rows = [_row(total="")]
    assert [r["ticker"] for r in bt.blank_total_buys(rows)] == ["AAA"]


def test_it_finds_a_strong_buy_too():
    # 진입 후보는 BUY 와 STRONG_BUY 둘 다다. 한쪽만 보면 절반을 놓친다.
    rows = [_row(signal="STRONG_BUY", total="")]
    assert len(bt.blank_total_buys(rows)) == 1


def test_it_counts_a_row_with_no_total_column_at_all():
    rows = [dict(ticker="AAA", date="2026-09-01", signal="BUY")]
    assert len(bt.blank_total_buys(rows)) == 1


def test_it_ignores_a_hold_with_a_blank_total():
    # HOLD 는 어차피 진입하지 않는다. 총점이 없어도 결과가 갈리지 않는다 -
    # 여기까지 잡으면 고칠 이유가 없는 행으로 매일 빨간불이 켜진다.
    rows = [_row(signal="HOLD", total="")]
    assert bt.blank_total_buys(rows) == []


def test_it_ignores_a_buy_that_has_a_total():
    assert bt.blank_total_buys([_row(total="72")]) == []


def test_a_total_that_is_not_a_number_still_raises():
    # 빈 값과 쓰레기 값은 다른 고장이다. 빈 값은 세어서 보고하고, 쓰레기는
    # _score 가 어느 행인지 말하며 터뜨린다 - 조용히 "빈 값 0건" 으로
    # 보고되면 검사가 통과했다고 읽힌다.
    with pytest.raises(ValueError, match="AAA"):
        bt.blank_total_buys([_row(total="abc")])


def test_the_blank_total_buy_really_does_vanish_from_the_backtest():
    # 이 검사가 존재하는 이유다. filter_rows 는 총점이 빈 BUY 를 HOLD 로
    # 강등하므로, 그 행의 진입은 흔적 없이 사라진다. 검사가 잡지 않으면
    # 주식 트랙 결과가 조용히 갈린 것을 아무도 모른다.
    blank = _row(total="")
    kept = bt.filter_rows([blank], min_total=70)

    assert kept[0]["signal"] == "HOLD"
    assert bt.blank_total_buys([blank]) != []


# ─── CLI ───────────────────────────────────────────────────────

def test_main_passes_on_a_clean_archive(monkeypatch, capsys):
    monkeypatch.setattr(bt, "load_archive", lambda pattern: [_row()])

    assert va.main([]) == 0


def test_main_fails_when_a_track_has_a_blank_total(monkeypatch):
    monkeypatch.setattr(bt, "load_archive", lambda pattern: [_row(total="")])

    assert va.main([]) == 1


def test_main_names_the_offending_row(monkeypatch, capsys):
    # 종목과 날짜가 없으면 32,000행에서 어느 행인지 찾을 수가 없다.
    monkeypatch.setattr(bt, "load_archive",
                        lambda pattern: [_row(ticker="SDOG",
                                              date="2026-09-01", total="")])

    va.main([])

    out = capsys.readouterr().out
    assert "SDOG" in out and "2026-09-01" in out


def test_main_checks_both_tracks(monkeypatch):
    # 한 트랙만 보면 나머지 트랙이 갈린 것을 놓친다.
    seen = []
    monkeypatch.setattr(bt, "load_archive",
                        lambda pattern: seen.append(pattern) or [])

    va.main([])

    assert seen == ["history/*.csv", "history_etf/*.csv"]
