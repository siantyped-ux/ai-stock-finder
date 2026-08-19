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
import history
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


def build_rows(result: dict, fx: dict, capital: int = CAPITAL_KRW,
               costs: ts.Costs = None) -> dict:
    """청산완료·미결·요약 세 덩어리로 나눈다.

    미결을 승률에 섞지 않는다. trade_sim.summarize 와 같은 원칙이다.
    """
    costs = costs or ts.Costs()
    mark_date = result["newest_bar"]

    closed, opened = [], []
    for t in result["trades"]:
        fx_entry = fx_on(fx, t.entry_date, t.market)
        if t.is_open:
            row = to_row(t, fx_entry, fx_on(fx, mark_date, t.market),
                         capital, costs)
            # 미결은 청산일이 없다. 평가 시점을 대신 넣는다.
            row["exit_date"] = mark_date
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


def _write_summary(ws, s: dict) -> None:
    """라벨-값 2열. 경고를 맨 위에 고정한다."""
    lines = [
        ("!! 경고", "이 리포트는 파이프라인 검증용이다. 시그널 성능의 근거가 아니다."),
        ("", f"아카이브의 {s['backfill_pct']:.0f}% 가 backfill 이라 "
             "스코어가 미확정 봉 결함에 오염돼 있다."),
        ("", "보유 상한 60거래일을 채운 표본이 나오기 전까지 승률·평균은 무의미하다."),
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
