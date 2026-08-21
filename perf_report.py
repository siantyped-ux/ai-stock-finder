"""가상매매 성과를 원화 XLSX 리포트로 낸다.

backtest.run() 이 낸 트레이드를 종목당 정액 1,000만원 투자로 환산한다.
R 배수는 리스크 정규화 단위여서 "얼마 벌었나" 에 답하지 못한다.

설계: docs/superpowers/specs/2026-08-19-perf-report-design.md
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

import backtest
import console
import exit_rules as er
import history
import mailer
import stops
import trade_sim as ts

CAPITAL_KRW = 10_000_000

# 2구획 커스텀 서식. 음수 구획에 - 를 명시하므로 회계 서식의 괄호 표기
# (636,000) 는 나오지 않는다.
MONEY_FMT = '#,##0;-#,##0'
PRICE_FMT = '#,##0.00;-#,##0.00'
QTY_FMT = '#,##0'
RATE_FMT = '#,##0.00'
# 값은 12.34 로 저장하고 서식으로 % 를 붙인다. Excel 기본 0.00% 서식은
# 값이 0.1234 여야 해서, 셀을 직접 읽는 쪽이 100배 틀린다.
PCT_FMT = '0.00"%";-0.00"%"'


def warning_lines(backfill_pct: float) -> list[str]:
    """숫자만 보고 성능으로 오독하는 것을 막는 경고문.

    요약 시트와 메일 본문이 같은 문장을 써야 한다. 한쪽만 고치는 사고를
    막으려고 여기 한 벌만 둔다. 접두사(!!, 들여쓰기)는 각 렌더러가 붙인다 -
    시트는 라벨-값 2열, 본문은 들여쓴 텍스트라 모양이 다르다.
    """
    return [
        "이 리포트는 파이프라인 검증용이다. 시그널 성능의 근거가 아니다.",
        f"아카이브의 {backfill_pct:.0f}% 가 backfill 이라 스코어가 "
        "미확정 봉 결함에 오염돼 있다.",
        "보유 상한 60거래일을 채운 표본이 나오기 전까지 승률·평균은 무의미하다.",
    ]


def fx_on(fx: dict, date: str, market: str) -> float:
    """해당 날짜의 원/달러 환율. 휴일이면 직전 영업일로 소급한다.

    한국 종목은 이미 원화라 1.0 이다. 호출부마다 분기를 두지 않으려고
    여기서 흡수한다.
    """
    if market == "KR":
        return 1.0
    if date in fx:
        return fx[date]
    earlier = [d for d in fx if d < date]
    if not earlier:
        raise ValueError(f"{date} 이전의 환율이 없다 (조회 범위를 늘려야 한다)")
    return fx[max(earlier)]


def to_row(trade, fx_entry: float, fx_exit: float,
           capital: int = CAPITAL_KRW, costs: ts.Costs = None) -> dict:
    """트레이드 1건을 원화 손익 행으로 환산한다.

    미결 포지션은 청산가 자리에 평가가격(mark_price)이 들어오고 매도비용도
    똑같이 뺀다 - 지금 팔면 손에 남는 돈이 평가액이다.

    두 퍼센트의 분모는 모두 투자원금이다. 순수익%의 분모에 매수비용을
    더하면 두 컬럼을 나란히 비교할 수 없다.
    """
    costs = costs or ts.Costs()

    entry_krw = trade.entry_price * fx_entry
    # 0주면 손익이 0이라 트레이드가 조용히 사라진다. 1주로 올린다.
    qty = max(1, int(capital // entry_krw))
    principal = entry_krw * qty

    exit_price = (trade.exit_price if trade.exit_price is not None
                  else trade.mark_price)
    gross = exit_price * fx_exit * qty - principal

    buy_side, sell_side = ts.cost_amount(trade.entry_price, exit_price,
                                         trade.market, costs)
    cost = buy_side * fx_entry * qty + sell_side * fx_exit * qty
    net = gross - cost

    return {
        "ticker": trade.ticker,
        "entry_date": trade.entry_date,
        "entry_price": trade.entry_price,
        "exit_date": trade.exit_date,
        "exit_price": exit_price,
        "gross_krw": gross,
        "gross_pct": gross / principal * 100.0,
        "net_krw": net,
        "net_pct": net / principal * 100.0,
        "qty": qty,
        "fx_entry": fx_entry,
        "fx_exit": fx_exit,
        "reason": trade.exit_reason,
        "bars_held": trade.bars_held,
    }


def target_cols(trade) -> dict:
    """미결 포지션의 목표가 관련 네 값. 목표가가 없으면 전부 빈칸이다.

    목표(%) 는 Trade.target_price 에서 되계산한다. 아카이브의 target 정수를
    따로 싣지 않는다 - 같은 사실을 두 곳에 두면 어긋날 수 있고, 목표가가
    유일한 진실이어야 한다.

    달성률은 목표폭 대비 어디까지 왔는지다. 진입가 아래면 음수가 나오고,
    그것이 맞다 - 목표에서 멀어졌다는 뜻이다.
    """
    tp = trade.target_price
    if tp is None:
        return {"target_pct": None, "target_price": None,
                "target_progress_pct": None, "reward_risk": None}

    entry = trade.entry_price
    return {
        "target_pct": (tp / entry - 1) * 100.0,
        "target_price": tp,
        "target_progress_pct": (trade.mark_price - entry) / (tp - entry) * 100.0,
        # 목표폭 ÷ 손절폭. 1 미만이면 목표를 다 채워도 손절 한 번보다 덜 번다.
        "reward_risk": (tp - entry) / trade.r_unit,
    }


def build_rows(result: dict, fx: dict, capital: int = CAPITAL_KRW,
               costs: ts.Costs = None, params: er.Params = None) -> dict:
    """청산완료·미결·요약 세 덩어리로 나눈다.

    미결을 승률에 섞지 않는다. trade_sim.summarize 와 같은 원칙이다.

    params 는 미결 포지션의 손절 컬럼에만 쓰인다. 트레일 판정은
    stops.stop_view 에 맡긴다 - 여기서 다시 구현하면 두 벌이 된다.
    """
    costs = costs or ts.Costs()
    params = params or er.Params()
    mark_date = result["newest_bar"]

    closed, opened = [], []
    for t in result["trades"]:
        fx_entry = fx_on(fx, t.entry_date, t.market)
        if t.is_open:
            row = to_row(t, fx_entry, fx_on(fx, mark_date, t.market),
                         capital, costs)
            # 미결은 청산일이 없다. 평가 시점을 대신 넣는다.
            row["exit_date"] = mark_date
            sv = stops.stop_view(t, params)
            row["stop"] = sv["stop"]
            # 손절선은 현재가 아래에 있다. 부호가 없으면 상승 여력처럼 읽힌다.
            row["stop_pct"] = -sv["room_pct"]
            row["trail_trigger"] = sv["trail_trigger"]
            row["trail"] = "ON" if sv["trail_active"] else "off"
            row.update(target_cols(t))
            opened.append(row)
        else:
            fx_exit = fx_on(fx, t.exit_date, t.market)
            closed.append(to_row(t, fx_entry, fx_exit, capital, costs))

    closed.sort(key=lambda r: (r["exit_date"], r["ticker"]))
    opened.sort(key=lambda r: (r["entry_date"], r["ticker"]))

    wins = sum(1 for r in closed if r["net_krw"] > 0)
    total_rows = result["live_rows"] + result["backfill_rows"]
    return {
        "closed": closed,
        "open": opened,
        "summary": {
            "generated": history.kst_now().strftime("%Y-%m-%d %H:%M KST"),
            "archive_from": result["dates"][0],
            "archive_to": result["dates"][-1],
            "live_rows": result["live_rows"],
            "backfill_rows": result["backfill_rows"],
            "backfill_pct": (result["backfill_rows"] / total_rows * 100.0)
                            if total_rows else 0.0,
            "mark_date": mark_date,
            "failed": result["failed"],
            "closed_n": len(closed),
            "win_rate": (wins / len(closed) * 100.0) if closed else None,
            "gross_krw": sum(r["gross_krw"] for r in closed),
            "net_krw": sum(r["net_krw"] for r in closed),
            # 트레이드별 순수익률의 단순평균이다. 금액 가중이 아니다.
            "avg_net_pct": (sum(r["net_pct"] for r in closed) / len(closed))
                           if closed else None,
            "open_n": len(opened),
            "open_net_krw": sum(r["net_krw"] for r in opened),
            "capital": capital,
            "use_target": params.use_target,
        },
    }


# (헤더, 행 키, 숫자서식). 앞 9개가 요청받은 컬럼이고, 뒤 4개는 검증용이다 -
# 원화 손익은 가격 x 수량 x 환율의 곱이라 셋이 다 보여야 검산이 된다.
CLOSED_COLS = [
    ("상품티커", "ticker", None),
    ("진입일자", "entry_date", None),
    ("진입가격", "entry_price", PRICE_FMT),
    ("청산일자", "exit_date", None),
    ("청산가격", "exit_price", PRICE_FMT),
    ("총수익(원)", "gross_krw", MONEY_FMT),
    ("총수익(%)", "gross_pct", PCT_FMT),
    ("순수익(원)", "net_krw", MONEY_FMT),
    ("순수익(%)", "net_pct", PCT_FMT),
    ("수량", "qty", QTY_FMT),
    ("진입환율", "fx_entry", RATE_FMT),
    ("청산환율", "fx_exit", RATE_FMT),
    ("청산사유", "reason", None),
]

OPEN_COLS = [
    ("상품티커", "ticker", None),
    ("진입일자", "entry_date", None),
    ("진입가격", "entry_price", PRICE_FMT),
    ("평가기준일", "exit_date", None),
    ("현재가", "exit_price", PRICE_FMT),
    ("평가 총수익(원)", "gross_krw", MONEY_FMT),
    ("평가 총수익(%)", "gross_pct", PCT_FMT),
    ("평가 순수익(원)", "net_krw", MONEY_FMT),
    ("평가 순수익(%)", "net_pct", PCT_FMT),
    ("수량", "qty", QTY_FMT),
    ("진입환율", "fx_entry", RATE_FMT),
    ("평가환율", "fx_exit", RATE_FMT),
    ("보유봉수", "bars_held", QTY_FMT),
    # 이 전략에는 고정 익절가가 없다. 팔리는 가격은 손절선 하나뿐이고
    # 그것도 고점과 ATR 을 따라 매일 움직이므로 리포트에 실어 둔다.
    ("손절가", "stop", PRICE_FMT),
    ("손절까지(%)", "stop_pct", PCT_FMT),
    ("트레일 발동가", "trail_trigger", PRICE_FMT),
    ("트레일", "trail", None),
    # 손절선이 "어디서 잘리나" 라면 이쪽은 "어디까지 가면 되나" 다.
    # use_target 이 꺼져 있어도 표시한다 - 규칙과 무관한 위치 정보다.
    ("목표(%)", "target_pct", PCT_FMT),
    ("목표가", "target_price", PRICE_FMT),
    ("달성률(%)", "target_progress_pct", PCT_FMT),
    ("위험보상", "reward_risk", RATE_FMT),
]


def _write_sheet(ws, cols, rows) -> None:
    """헤더 + 데이터 행 + 열별 숫자서식. 행이 없어도 헤더는 쓴다."""
    ws.append([title for title, _key, _fmt in cols])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"

    for row in rows:
        ws.append([row[key] for _title, key, _fmt in cols])

    for i, (title, _key, fmt) in enumerate(cols, start=1):
        letter = get_column_letter(i)
        if fmt:
            for cell in ws[letter][1:]:
                cell.number_format = fmt
        ws.column_dimensions[letter].width = max(len(title) + 4, 12)


def summary_text(s: dict) -> str:
    """요약 딕셔너리를 콘솔·메일 공용 본문으로 만든다.

    win_rate 와 avg_net_pct 는 청산완료가 0건이면 None 이다. 포맷하면
    터지므로 건수만 적는다.

    총 수익률(%) 은 싣지 않는다. capital 은 종목당 투자금이라 총 투입금
    대비 수익률을 내려면 청산 자금을 재투자하지 않는다는 가정을 몰래
    들여오게 된다.
    """
    if s["closed_n"]:
        closed_note = (f"청산 {s['closed_n']}건, 승률 {s['win_rate']:.1f}%, "
                       f"평균 순수익률 {s['avg_net_pct']:+.2f}%")
    else:
        closed_note = "청산 0건"

    failed = ", ".join(s["failed"]) if s["failed"] else "없음"
    total = s["net_krw"] + s["open_net_krw"]

    warn = warning_lines(s["backfill_pct"])
    return "\n".join([
        f"!! {warn[0]}",
        f"   {warn[1]}",
        f"   {warn[2]}",
        "",
        f"총 손익      {total:+,.0f}원",
        f"  └ 실현     {s['net_krw']:+,.0f}원  ({closed_note})",
        f"  └ 미실현   {s['open_net_krw']:+,.0f}원  (보유 {s['open_n']}종목)",
        "",
        f"평가기준일   {s['mark_date']}",
        f"시세 조회 실패: {failed}",
    ])


def _write_summary(ws, s: dict) -> None:
    """라벨-값 2열. 경고를 맨 위에 고정한다."""
    warn = warning_lines(s["backfill_pct"])
    lines = [
        ("!! 경고", warn[0]),
        ("", warn[1]),
        ("", warn[2]),
        ("", ""),
        ("리포트 생성", s["generated"]),
        ("아카이브 기간", f"{s['archive_from']} ~ {s['archive_to']}"),
        ("live 행수", s["live_rows"]),
        ("backfill 행수", s["backfill_rows"]),
        ("평가기준일", s["mark_date"]),
        ("시세 조회 실패", ", ".join(s["failed"]) if s["failed"] else "없음"),
        ("", ""),
        ("[청산완료]", ""),
        ("건수", s["closed_n"]),
        ("승률(%)", s["win_rate"]),
        ("누적 총수익(원)", s["gross_krw"]),
        ("누적 순수익(원)", s["net_krw"]),
        ("평균 순수익률(%)", s["avg_net_pct"]),
        ("", ""),
        ("[미결포지션]", ""),
        ("보유 건수", s["open_n"]),
        ("평가 순손익(원)", s["open_net_krw"]),
        ("", ""),
        ("[가정]", ""),
        ("종목당 투자금(원)", s["capital"]),
        ("매수 수량", "정액 ÷ 원화진입가, 소수점 내림 (최소 1주)"),
        ("비용", "미국 편도 0.10% · 한국 편도 0.02% + 거래세 0.15% · "
                 "슬리피지 편도 0.05%"),
        ("환율", "yfinance USDKRW=X 일봉 종가. 휴일은 직전 영업일로 소급"),
        ("승률·평균", "청산완료만으로 계산한다. 미결은 제외"),
        # 목표 컬럼은 규칙이 꺼져 있어도 보인다. 어느 쪽인지 밝히지 않으면
        # 목표가가 익절 예고처럼 읽힌다.
        ("목표가 익절", "사용함 (목표가 도달 시 청산)" if s["use_target"]
                      else "사용 안 함 (--use-target 으로 켬)"),
    ]

    for label, value in lines:
        ws.append([label, value])

    for row in ws.iter_rows(min_col=1, max_col=2):
        label = row[0].value or ""
        if label.endswith("(원)"):
            row[1].number_format = MONEY_FMT
        elif label.endswith("(%)"):
            row[1].number_format = PCT_FMT

    ws["A1"].font = Font(bold=True)
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 80


def write_xlsx(path, built: dict) -> None:
    """청산완료 / 미결포지션 / 요약 3시트를 쓴다."""
    wb = Workbook()
    closed_ws = wb.active
    closed_ws.title = "청산완료"
    _write_sheet(closed_ws, CLOSED_COLS, built["closed"])
    _write_sheet(wb.create_sheet("미결포지션"), OPEN_COLS, built["open"])
    _write_summary(wb.create_sheet("요약"), built["summary"])

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def fetch_fx(start: str, end: str) -> dict:
    """USDKRW=X 일봉 종가를 {YYYY-MM-DD: 환율} 로 받는다.

    실패하면 예외를 올린다. 고정환율로 대체하면 조용히 틀린 금액이
    리포에 커밋된다 - 리포트가 없는 편이 낫다.
    """
    df = yf.Ticker("USDKRW=X").history(start=start, end=end, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError(f"USDKRW=X 환율 조회 실패 ({start} ~ {end})")

    # NaN 종가는 버린다. fx_on 이 직전 영업일로 소급한다.
    return {d.strftime("%Y-%m-%d"): float(c)
            for d, c in zip(df.index, df["Close"]) if c == c}


def main():
    console.force_utf8()
    p = argparse.ArgumentParser(description="가상매매 성과 누적 리포트")
    p.add_argument("--history", default="history/*.csv")
    p.add_argument("--out-dir", default="reports")
    p.add_argument("--capital", type=int, default=CAPITAL_KRW)
    p.add_argument("--mail", action="store_true",
                   help="리포트를 메일로 보낸다 (SMTP_* 환경변수 또는 .env 필요)")
    p.add_argument("--use-target", action="store_true",
                   help="목표가 도달 시 익절한 결과로 리포트를 낸다")
    args = p.parse_args()

    params = er.Params(use_target=args.use_target)
    result = backtest.run(args.history, params)
    if not result["dates"]:
        raise SystemExit("아카이브가 비어 있다")

    # 첫 진입일이 환율 휴일이어도 소급할 값이 있도록 10일 앞에서 시작한다.
    fx_start = (datetime.strptime(result["dates"][0], "%Y-%m-%d")
                - timedelta(days=10)).strftime("%Y-%m-%d")
    fx_end = (history.kst_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    fx = fetch_fx(fx_start, fx_end)

    built = build_rows(result, fx, args.capital, params=params)
    stamp = history.kst_now().strftime("%Y-%m-%d")
    path = Path(args.out_dir) / f"perf_{stamp}.xlsx"
    write_xlsx(path, built)

    body = summary_text(built["summary"])
    print(f"{path} 작성 완료")
    print(body)

    if args.mail:
        # 발송 실패를 삼키지 않는다. 조용히 안 보내는 것보다 잡이 실패하는
        # 편이 낫다 - fetch_fx 가 고정환율로 대체하지 않는 것과 같다.
        subject = f"[성과리포트] {stamp} (KST)"
        mailer.send(subject, f"{body}\n\n상세는 첨부된 {path.name} 참고",
                    [path], **mailer.creds_from_env())
        print(f"메일 발송 완료: {subject}")


if __name__ == "__main__":
    main()
