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
