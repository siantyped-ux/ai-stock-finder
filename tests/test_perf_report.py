import sys

import pytest
from openpyxl import load_workbook

import exit_rules as er
import perf_report as pr
import trade_sim as ts


FX = {"2026-08-03": 1300.0, "2026-08-05": 1350.0}


def _trade(**kw):
    """기본은 AAA 를 08-03 @100 에 사서 08-05 @110 에 판 트레이드."""
    base = dict(
        ticker="AAA", market="US", source="live",
        entry_date="2026-08-03", entry_price=100.0, r_unit=6.0,
        exit_date="2026-08-05", exit_price=110.0, mark_price=110.0,
        exit_reason="TRAIL", bars_held=2, is_open=False,
        gross_r=1.67, cost_r=0.05, net_r=1.62,
        initial_stop=94.0, high_since_entry=110.0, stop=94.0,
        target_price=None,
    )
    base.update(kw)
    return ts.Trade(**base)


def test_fx_on_exact_date():
    assert pr.fx_on(FX, "2026-08-03", "US") == 1300.0


def test_fx_on_holiday_falls_back_to_the_previous_session():
    # 08-04 는 환율 데이터가 없다. 08-03 으로 소급한다.
    assert pr.fx_on(FX, "2026-08-04", "US") == 1300.0


def test_fx_on_raises_when_nothing_earlier_exists():
    # 조용히 아무 환율이나 쓰면 틀린 금액이 리포에 커밋된다.
    with pytest.raises(ValueError):
        pr.fx_on(FX, "2026-08-01", "US")


def test_fx_on_is_one_for_kr_tickers():
    assert pr.fx_on(FX, "2026-08-01", "KR") == 1.0


def test_quantity_floors_to_whole_shares():
    # 1,000만원 / (100 x 1300) = 76.9 -> 76주. 잔액은 미투자.
    assert pr.to_row(_trade(), 1300.0, 1300.0)["qty"] == 76


def test_quantity_is_at_least_one_share():
    # 원화진입가가 정액보다 크면 0주가 되고 트레이드가 조용히 사라진다.
    assert pr.to_row(_trade(entry_price=10000.0), 1300.0, 1300.0)["qty"] == 1


def test_us_trade_converts_with_both_fx_rates():
    # 원금 100x1300x76 = 9,880,000 / 회수 110x1350x76 = 11,286,000
    # 매수비용 0.15x1300x76 = 14,820 / 매도비용 0.165x1350x76 = 16,929
    row = pr.to_row(_trade(), 1300.0, 1350.0)

    assert row["qty"] == 76
    assert row["gross_krw"] == pytest.approx(1_406_000.0)
    assert row["gross_pct"] == pytest.approx(14.2308, abs=1e-4)
    assert row["net_krw"] == pytest.approx(1_374_251.0)
    assert row["net_pct"] == pytest.approx(13.9094, abs=1e-4)


def test_loss_stays_negative_and_costs_make_it_worse():
    row = pr.to_row(_trade(exit_price=90.0, mark_price=90.0), 1300.0, 1300.0)

    assert row["gross_krw"] < 0
    assert row["net_krw"] < row["gross_krw"]


def test_kr_trade_needs_no_fx():
    # 1,000만원 / 50,000 = 200주. 원금 정확히 1,000만원.
    row = pr.to_row(_trade(market="KR", entry_price=50000.0,
                           exit_price=55000.0, mark_price=55000.0),
                    1.0, 1.0)

    assert row["qty"] == 200
    assert row["gross_krw"] == pytest.approx(1_000_000.0)


def test_krw_cost_agrees_with_cost_r():
    # 환율 1.0, 1주면 원화 비용은 cost_r x r_unit 과 같아야 한다.
    # 요율 분기가 두 곳에 복제되면 이 등식이 깨진다.
    t = _trade(market="KR", entry_price=50000.0, exit_price=55000.0,
               mark_price=55000.0, r_unit=3000.0)
    row = pr.to_row(t, 1.0, 1.0, capital=50000)

    assert row["qty"] == 1
    expected = ts.cost_r(50000.0, 55000.0, 3000.0, "KR", ts.Costs()) * 3000.0
    assert row["gross_krw"] - row["net_krw"] == pytest.approx(expected)


def test_open_position_uses_the_mark_price_and_still_pays_the_sell_side():
    t = _trade(is_open=True, exit_date=None, exit_price=None, mark_price=105.0)
    row = pr.to_row(t, 1300.0, 1300.0)

    assert row["exit_price"] == 105.0
    # 매도비용을 빼지 않으면 net == gross 가 된다
    assert row["net_krw"] < row["gross_krw"]


def _result(trades, **kw):
    base = dict(
        trades=trades, dates=["2026-08-03", "2026-08-05"],
        live_rows=10, backfill_rows=90, failed=[],
        newest_bar="2026-08-05",
    )
    base.update(kw)
    return base


def test_open_positions_never_land_in_the_closed_sheet():
    built = pr.build_rows(_result([
        _trade(),
        _trade(ticker="BBB", is_open=True, exit_date=None,
               exit_price=None, mark_price=105.0),
    ]), FX)

    assert [r["ticker"] for r in built["closed"]] == ["AAA"]
    assert [r["ticker"] for r in built["open"]] == ["BBB"]


def test_open_position_is_marked_to_the_newest_bar_date():
    built = pr.build_rows(_result([
        _trade(is_open=True, exit_date=None, exit_price=None, mark_price=105.0),
    ]), FX)

    assert built["open"][0]["exit_date"] == "2026-08-05"


def test_win_rate_ignores_open_positions():
    # 닫힌 2건 중 1승. 미결은 큰 이익이지만 승률에 들어가면 안 된다 -
    # "아직 손절되지 않았을 뿐" 인 포지션이다.
    built = pr.build_rows(_result([
        _trade(ticker="WIN"),
        _trade(ticker="LOSS", exit_price=90.0, mark_price=90.0),
        _trade(ticker="OPEN", is_open=True, exit_date=None,
               exit_price=None, mark_price=200.0),
    ]), FX)
    s = built["summary"]

    assert s["closed_n"] == 2
    assert s["win_rate"] == pytest.approx(50.0)
    assert s["open_n"] == 1


def test_closed_rows_sort_by_exit_date_then_ticker():
    built = pr.build_rows(_result([
        _trade(ticker="ZZZ", exit_date="2026-08-05"),
        _trade(ticker="AAA", exit_date="2026-08-05"),
        _trade(ticker="MMM", exit_date="2026-08-03"),
    ]), FX)

    assert [r["ticker"] for r in built["closed"]] == ["MMM", "AAA", "ZZZ"]


def test_summary_survives_zero_closed_trades():
    s = pr.build_rows(_result([]), FX)["summary"]

    assert s["closed_n"] == 0
    assert s["win_rate"] is None
    assert s["avg_net_pct"] is None


def test_xlsx_has_three_sheets_with_the_requested_columns_first(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([_trade()]), FX))

    wb = load_workbook(path)
    assert wb.sheetnames == ["청산완료", "미결포지션", "요약"]

    header = [c.value for c in wb["청산완료"][1]]
    assert header[:9] == ["상품티커", "진입일자", "진입가격",
                          "청산일자", "청산가격", "총수익(원)",
                          "총수익(%)", "순수익(원)", "순수익(%)"]
    assert header[9:] == ["수량", "진입환율", "청산환율", "청산사유"]


def test_negative_money_renders_with_a_minus_sign(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(
        _result([_trade(exit_price=90.0, mark_price=90.0)]), FX))

    cell = load_workbook(path)["청산완료"]["F2"]

    assert cell.value < 0
    assert cell.number_format == "#,##0;-#,##0"
    # 회계 서식의 괄호 표기여서는 안 된다
    assert "(" not in cell.number_format


def test_percent_cells_store_the_readable_number_not_a_fraction(tmp_path):
    # 값이 0.1423 이면 셀을 직접 읽는 쪽이 100배 틀린다.
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([_trade()]), FX))

    cell = load_workbook(path)["청산완료"]["G2"]

    assert cell.value > 1.0
    assert cell.number_format == '0.00"%";-0.00"%"'


def test_closed_sheet_keeps_its_header_when_there_are_no_trades(tmp_path):
    # 시트가 없으면 파일이 깨진 것인지 트레이드가 없는 것인지 구분되지 않는다.
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([]), FX))

    ws = load_workbook(path)["청산완료"]

    assert ws.max_row == 1
    assert ws["A1"].value == "상품티커"


def test_open_sheet_labels_the_valuation_columns(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([
        _trade(is_open=True, exit_date=None, exit_price=None, mark_price=105.0),
    ]), FX))

    header = [c.value for c in load_workbook(path)["미결포지션"][1]]

    assert header[3] == "평가기준일"
    assert header[4] == "현재가"
    assert header[12] == "보유봉수"


def test_summary_leads_with_the_contamination_warning(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([_trade()]), FX))

    ws = load_workbook(path)["요약"]

    assert ws["A1"].value == "!! 경고"
    assert "파이프라인 검증용" in ws["B1"].value


def _held(**kw):
    """미결 포지션 기본형. 진입 100 · 1R=6 · 손절 94 · 최고 102 · 평가 101.5."""
    base = dict(is_open=True, exit_date=None, exit_price=None,
                mark_price=101.5, exit_reason=None,
                initial_stop=94.0, high_since_entry=102.0, stop=94.0)
    base.update(kw)
    return _trade(**base)


def test_open_sheet_appends_the_stop_columns(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([_held()]), FX))

    header = [c.value for c in load_workbook(path)["미결포지션"][1]]

    assert header[13:17] == ["손절가", "손절까지(%)", "트레일 발동가", "트레일"]


def test_stop_distance_is_negative_because_it_is_a_drop(tmp_path):
    # (101.5 - 94) / 101.5 = 7.39% 아래. 부호가 없으면 상승 여력처럼 읽힌다.
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([_held()]), FX))

    ws = load_workbook(path)["미결포지션"]

    assert ws["N2"].value == pytest.approx(94.0)            # 손절가
    assert ws["O2"].value == pytest.approx(-7.3892, abs=1e-4)
    assert ws["O2"].number_format == '0.00"%";-0.00"%"'


def test_trail_trigger_is_entry_plus_one_r(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([_held()]), FX))

    ws = load_workbook(path)["미결포지션"]

    assert ws["P2"].value == pytest.approx(106.0)           # 100 + 6
    assert ws["Q2"].value == "off"                          # 최고 102 < 106


def test_trail_shows_on_once_the_high_reaches_the_trigger(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(
        _result([_held(high_since_entry=106.0, stop=100.0)]), FX))

    ws = load_workbook(path)["미결포지션"]

    assert ws["Q2"].value == "ON"
    assert ws["N2"].value == pytest.approx(100.0)           # 본전으로 올라온 손절선


def test_closed_sheet_is_untouched_by_the_stop_columns(tmp_path):
    # 청산된 트레이드에는 "지금 어디서 잘리는가" 가 없다.
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([_trade()]), FX))

    header = [c.value for c in load_workbook(path)["청산완료"][1]]

    assert len(header) == 13
    assert "손절가" not in header


def test_open_sheet_appends_the_target_columns(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(
        _result([_held(target_price=109.0)]), FX))

    header = [c.value for c in load_workbook(path)["미결포지션"][1]]

    assert header[17:] == ["목표(%)", "목표가", "달성률(%)", "위험보상"]


def test_target_progress_measures_the_way_to_the_target():
    # 진입 100 · 목표 109 · 평가 101.5 → 목표폭 9 중 1.5 만큼 왔다.
    # 위험보상 = 목표폭 9 / 1R 6 = 1.5
    row = pr.build_rows(_result([_held(target_price=109.0)]), FX)["open"][0]

    assert row["target_pct"] == pytest.approx(9.0)
    assert row["target_price"] == pytest.approx(109.0)
    assert row["target_progress_pct"] == pytest.approx(16.6667, abs=1e-4)
    assert row["reward_risk"] == pytest.approx(1.5)


def test_target_progress_is_negative_below_the_entry():
    # 진입가 아래면 목표에서 멀어진 것이다. 부호가 없으면 진행처럼 읽힌다.
    row = pr.build_rows(
        _result([_held(mark_price=97.0, target_price=109.0)]), FX)["open"][0]

    assert row["target_progress_pct"] == pytest.approx(-33.3333, abs=1e-4)


def test_target_columns_are_blank_without_a_target():
    row = pr.build_rows(_result([_held()]), FX)["open"][0]

    assert row["target_pct"] is None
    assert row["target_price"] is None
    assert row["target_progress_pct"] is None
    assert row["reward_risk"] is None


def test_closed_sheet_is_untouched_by_the_target_columns(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(
        _result([_trade(target_price=109.0)]), FX))

    header = [c.value for c in load_workbook(path)["청산완료"][1]]

    assert "목표가" not in header
    assert header[-1] == "청산사유"


def _summary_rows(path):
    return {r[0].value: r[1].value
            for r in load_workbook(path)["요약"].iter_rows(min_col=1,
                                                          max_col=2)}


def test_summary_says_the_target_exit_is_off_by_default(tmp_path):
    # 컬럼은 항상 보이는데 규칙은 꺼져 있다. 어느 쪽인지 적어 두지 않으면
    # 목표가가 익절 예고처럼 읽힌다.
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([_trade()]), FX))

    assert _summary_rows(path)["목표가 익절"] == "사용 안 함 (--use-target 으로 켬)"


def test_summary_says_the_target_exit_is_on_when_enabled(tmp_path):
    path = tmp_path / "r.xlsx"
    pr.write_xlsx(path, pr.build_rows(_result([_trade()]), FX,
                                      params=er.Params(use_target=True)))

    assert _summary_rows(path)["목표가 익절"] == "사용함 (목표가 도달 시 청산)"


SUMMARY = {
    "generated": "2026-08-20 10:00 KST",
    "archive_from": "2026-08-01", "archive_to": "2026-08-19",
    "live_rows": 300, "backfill_rows": 800, "backfill_pct": 72.7,
    "mark_date": "2026-08-19", "failed": [],
    "closed_n": 12, "win_rate": 58.333,
    "gross_krw": 1_500_000, "net_krw": 1_180_000, "avg_net_pct": 2.1,
    "open_n": 8, "open_net_krw": 2_240_000, "capital": 10_000_000,
}


def test_summary_text_totals_realised_and_unrealised():
    text = pr.summary_text(SUMMARY)

    # 총 손익 = 실현 + 미실현
    assert "+3,420,000원" in text
    assert "+1,180,000원" in text
    assert "+2,240,000원" in text


def test_summary_text_carries_the_closed_stats():
    text = pr.summary_text(SUMMARY)

    assert "청산 12건" in text
    assert "58.3%" in text
    assert "+2.10%" in text
    assert "보유 8종목" in text


def test_summary_text_survives_zero_closed_trades():
    s = dict(SUMMARY, closed_n=0, win_rate=None, avg_net_pct=None,
             net_krw=0)

    # None 을 포맷하면 터진다. 청산 표본이 없는 지금이 바로 그 상태다.
    text = pr.summary_text(s)
    assert "청산 0건" in text
    assert "None" not in text


def test_summary_text_lists_the_failed_tickers():
    assert "없음" in pr.summary_text(SUMMARY)
    assert "AAA, BBB" in pr.summary_text(dict(SUMMARY, failed=["AAA", "BBB"]))


def test_summary_text_leads_with_the_warning():
    text = pr.summary_text(SUMMARY)

    # 메일은 첨부를 안 열고 본문만 훑게 만든다. 경고가 첨부 안에만
    # 있으면 없는 것과 같다.
    assert text.startswith("!!")
    assert "파이프라인 검증용" in text
    assert "73% 가 backfill" in text
    assert "60거래일" in text


def test_summary_text_renders_losses_with_a_minus_sign():
    # 성과리포트의 핵심은 손실이다. +,.0f 서식의 부호 처리는 양수만
    # 테스트해서는 검증되지 않는다.
    s = dict(SUMMARY, net_krw=-1_180_000, open_net_krw=-2_240_000)
    text = pr.summary_text(s)

    assert "-3,420,000원" in text
    assert "-1,180,000원" in text
    assert "-2,240,000원" in text


def _stub_run(*_a, **_kw):
    """backtest.run() 최소 결과. 트레이드 0건이라 청산 0건 경로도 탄다."""
    return _result([])


def test_mail_flag_sends_the_same_body_that_gets_printed(monkeypatch, tmp_path,
                                                          capsys):
    sent = []
    creds = {"to": "a@b.c", "user": "a@b.c", "password": "x"}
    monkeypatch.setattr(pr.backtest, "run", _stub_run)
    monkeypatch.setattr(pr, "fetch_fx", lambda start, end: FX)
    monkeypatch.setattr(pr.mailer, "send",
                        lambda *a, **kw: sent.append((a, kw)))
    # 실 환경변수가 없어도 되도록 자격증명 조회 자체를 스텁한다.
    monkeypatch.setattr(pr.mailer, "creds_from_env", lambda: creds)
    monkeypatch.setattr(sys, "argv",
                        ["perf_report.py", "--mail", "--out-dir", str(tmp_path)])

    pr.main()

    printed = capsys.readouterr().out
    assert len(sent) == 1
    (subject, body, attachments), kw = sent[0]
    # 제목에 트랙이 들어간다. 두 리포트가 같은 날 도착하는데 제목이 같으면
    # 어느 쪽인지 열어 봐야 안다.
    assert subject.startswith("[성과리포트·미국 주식]")

    written = list(tmp_path.glob("*.xlsx"))
    assert len(written) == 1
    assert written[0].name.startswith("perf_2026")

    # 메일 본문은 "콘솔에 찍힌 본문 + 첨부 안내" 여야 한다. 둘이 갈라지면
    # summary_text() 를 공유하는 이유가 사라진다 - 부분 문자열 두 개가
    # 우연히 들어 있는 걸로는 이 관계를 못 잡는다.
    suffix = f"\n\n상세는 첨부된 {written[0].name} 참고"
    assert body.endswith(suffix)
    printed_body = body[:-len(suffix)]
    assert printed_body in printed

    # creds_from_env() 가 실제로 send() 까지 전달되는지 확인한다.
    # 스텁이 아무 kwargs 나 받아주므로, 여기서 안 짚으면 **creds_from_env()
    # 가 통째로 빠져도 테스트가 통과한다.
    assert kw == creds

    assert attachments == [written[0]]


def test_without_mail_flag_nothing_gets_sent(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(pr.backtest, "run", _stub_run)
    monkeypatch.setattr(pr, "fetch_fx", lambda start, end: FX)
    monkeypatch.setattr(pr.mailer, "send",
                        lambda *a, **kw: sent.append((a, kw)))
    monkeypatch.setattr(sys, "argv",
                        ["perf_report.py", "--out-dir", str(tmp_path)])

    pr.main()

    assert sent == []
