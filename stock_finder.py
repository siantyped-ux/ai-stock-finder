"""
AI 3-Month Stock Finder — 실시간 데이터 수집 & 스코어링
xlsx v5 시스템의 4축 컨센서스 로직을 실제 데이터로 적용

의존성:
    pip install yfinance numpy requests

.env 파일에 API 키 입력 (선택):
    FMP_API_KEY=xxx   # 미국 SEC 공시 (13F, Form 4, 8-K)
    FRED_API_KEY=xxx  # 연준 경제 데이터

실행:
    python stock_finder.py

출력:
    dashboard_data.js  →  stock_finder_dashboard.html 이 자동 로드
"""

from __future__ import annotations
import argparse
import contextlib
import io
import json
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional

import console
import flow
import history

console.force_utf8()

# yfinance 상장폐지 종목에 대한 반복 ERROR 로그 억제
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

try:
    import yfinance as yf
    import numpy as np
    import requests
except ImportError:
    print("[!] 필수 패키지 설치 필요: pip install yfinance numpy requests")
    sys.exit(1)


# ─── .env 로더 (python-dotenv 의존성 없이 직접 파싱) ─────────
def load_env(path: str = ".env") -> dict[str, str]:
    env = {}
    full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if not os.path.exists(full_path):
        return env
    with open(full_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                value = value.strip().strip('"').strip("'")
                env[key.strip()] = value
                if value:
                    os.environ[key.strip()] = value
    return env


ENV = load_env()
# .env 우선, 없으면 시스템 환경변수 (GitHub Actions Secrets 지원)
FMP_KEY = (ENV.get("FMP_API_KEY") or os.environ.get("FMP_API_KEY", "")).strip()
FRED_KEY = (ENV.get("FRED_API_KEY") or os.environ.get("FRED_API_KEY", "")).strip()

FMP_BASE = "https://financialmodelingprep.com/stable"
FRED_BASE = "https://api.stlouisfed.org/fred"


# ─── 종목 유니버스 (폴백용 · FMP 조회 실패 시 사용) ──────────
FALLBACK_UNIVERSE = [
    # 미국
    ("NVDA",  "NVIDIA",              "US", "AI/반도체", "STOCK", "NASDAQ"),
    ("MSFT",  "Microsoft",           "US", "Software", "STOCK", "NASDAQ"),
    ("AMD",   "AMD",                 "US", "AI/반도체", "STOCK", "NASDAQ"),
    ("META",  "Meta Platforms",      "US", "인터넷", "STOCK", "NASDAQ"),
    ("GOOGL", "Alphabet",            "US", "인터넷", "STOCK", "NASDAQ"),
    ("AMZN",  "Amazon",              "US", "E-commerce/Cloud", "STOCK", "NASDAQ"),
    ("TSLA",  "Tesla",               "US", "EV/자동차", "STOCK", "NASDAQ"),
    ("AVGO",  "Broadcom",            "US", "반도체", "STOCK", "NASDAQ"),
    ("LLY",   "Eli Lilly",           "US", "제약/바이오", "STOCK", "NYSE"),
    ("COST",  "Costco",              "US", "소매", "STOCK", "NASDAQ"),
    ("CRWD",  "CrowdStrike",         "US", "사이버보안", "STOCK", "NASDAQ"),
    ("UBER",  "Uber",                "US", "플랫폼", "STOCK", "NYSE"),
    ("PANW",  "Palo Alto Networks",  "US", "사이버보안", "STOCK", "NASDAQ"),
    ("NOW",   "ServiceNow",          "US", "Software", "STOCK", "NYSE"),
    ("AXP",   "American Express",    "US", "금융", "STOCK", "NYSE"),
    ("NFLX",  "Netflix",             "US", "미디어", "STOCK", "NASDAQ"),
    ("ORCL",  "Oracle",              "US", "Software", "STOCK", "NYSE"),
    ("MU",    "Micron",              "US", "반도체", "STOCK", "NASDAQ"),
    ("AAPL",  "Apple",               "US", "IT하드웨어", "STOCK", "NASDAQ"),
    ("JPM",   "JPMorgan",            "US", "금융", "STOCK", "NYSE"),
    ("V",     "Visa",                "US", "금융", "STOCK", "NYSE"),
    ("F",     "Ford Motor",          "US", "자동차", "STOCK", "NYSE"),
    ("INTC",  "Intel",               "US", "반도체", "STOCK", "NASDAQ"),
    # ETF. 폴백에도 ETF 를 두는 것은 의도다 - --test 로 도는 스모크 실행이
    # ETF 분기(재정규화 점수·빈 filing/value)를 실제로 지나가야 한다.
    ("SPY",   "SPDR S&P 500 ETF Trust", "US", "미분류", "ETF", "AMEX"),
    ("QQQ",   "Invesco QQQ Trust",      "US", "미분류", "ETF", "NASDAQ"),
]


# ─── 기술적 지표 (numpy 만 사용) ─────────────────────────────
def sma(arr: np.ndarray, period: int) -> np.ndarray:
    if len(arr) < period:
        return np.full(len(arr), np.nan)
    result = np.full(len(arr), np.nan)
    for i in range(period - 1, len(arr)):
        result[i] = np.mean(arr[i - period + 1: i + 1])
    return result


def ema(arr: np.ndarray, period: int) -> np.ndarray:
    if len(arr) < period:
        return np.full(len(arr), np.nan)
    alpha = 2 / (period + 1)
    result = np.full(len(arr), np.nan)
    result[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
    return result


def rsi(close: np.ndarray, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    deltas = np.diff(close[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd_hist(close: np.ndarray) -> tuple[float, float, float]:
    if len(close) < 35:
        return 0.0, 0.0, 0.0
    e12 = ema(close, 12)
    e26 = ema(close, 26)
    macd = e12 - e26
    valid = macd[~np.isnan(macd)]
    if len(valid) < 9:
        return 0.0, 0.0, 0.0
    signal = ema(valid, 9)
    hist = valid[-len(signal):] - signal
    return float(macd[-1]), float(signal[-1]), float(hist[-1])


def bollinger_position(close: np.ndarray, period: int = 20) -> float:
    if len(close) < period:
        return 0.5
    mean = np.mean(close[-period:])
    std = np.std(close[-period:])
    if std == 0:
        return 0.5
    lower, upper = mean - 2 * std, mean + 2 * std
    pos = (close[-1] - lower) / (upper - lower)
    return float(np.clip(pos, 0, 1))


# ─── 스코어링 ─────────────────────────────────────────────────
def calc_tech_score(hist_df) -> tuple[int, list[str], float]:
    # 종가만 쓴다. 고저는 원래부터 쓰지 않았고, 거래량은 flow 축으로 옮겼다.
    close = hist_df["Close"].values.astype(float)

    if len(close) < 60:
        return 40, ["데이터 부족 (60봉 미만)"], 0.0

    score = 50.0
    reasons = []
    r3m = (close[-1] / close[-63] - 1) * 100 if len(close) >= 63 else 0

    s20 = np.mean(close[-20:])
    s50 = np.mean(close[-50:])
    s200 = np.mean(close[-200:]) if len(close) >= 200 else s50
    price = close[-1]

    if price > s20 > s50 > s200:
        score += 15
        reasons.append(f"이동평균 완벽 정배열 (P>{s20:.1f}>{s50:.1f}>{s200:.1f})")
    elif price > s20 > s50:
        score += 8
        reasons.append(f"단기 정배열 (P>{s20:.1f}>{s50:.1f})")
    elif price < s20 < s50:
        score -= 15
        reasons.append(f"단기 역배열 (P<{s20:.1f}<{s50:.1f})")

    r = rsi(close)
    if 55 <= r <= 70:
        score += 10
        reasons.append(f"RSI {r:.0f} · 강세 지속 구간")
    elif 40 <= r < 55:
        score += 3
        reasons.append(f"RSI {r:.0f} · 중립")
    elif r > 75:
        score -= 5
        reasons.append(f"RSI {r:.0f} · 과매수 경계")
    elif r < 30:
        score -= 8
        reasons.append(f"RSI {r:.0f} · 과매도")

    m, sig, h = macd_hist(close)
    if h > 0 and m > sig:
        score += 12
        reasons.append(f"MACD 골든크로스 · 히스토 +{h:.2f}")
    elif h < 0 and m < sig:
        score -= 10
        reasons.append(f"MACD 데드크로스 · 히스토 {h:.2f}")

    # 거래량 항목은 flow 축으로 옮겼다. 여기에 두면 flow 의 유동성·수급
    # 컴포넌트와 중복 계상되어 거래량이 총점에 두 번 반영된다.

    if r3m > 20:
        score += 8
        reasons.append(f"3개월 수익률 +{r3m:.1f}% · 강한 상승")
    elif r3m > 5:
        score += 4
        reasons.append(f"3개월 수익률 +{r3m:.1f}%")
    elif r3m < -15:
        score -= 10
        reasons.append(f"3개월 수익률 {r3m:.1f}% · 급락")

    bp = bollinger_position(close)
    if bp > 0.9:
        score -= 3
        reasons.append(f"Bollinger 상단 접근 ({bp*100:.0f}%)")
    elif bp < 0.2:
        score += 3
        reasons.append(f"Bollinger 하단 근접 · 반등 여지")

    if not reasons:
        reasons.append("특이 시그널 없음 · 중립")
    return int(np.clip(score, 0, 100)), reasons[:5], r3m


def calc_macro_score(vix: float, dxy: float, us10y: float, sector: str,
                     fred: dict = None) -> tuple[int, list[str], str]:
    reasons = []
    fred = fred or {}

    # 국면 판정 (기본)
    if vix < 18 and us10y < 4.5:
        regime = "RISK_ON"
        base = 80
        reasons.append(f"RISK_ON · VIX {vix:.1f} 안전지대")
    elif vix > 25 or us10y > 5.0:
        regime = "RISK_OFF"
        base = 30
        reasons.append(f"RISK_OFF · VIX {vix:.1f}" + (f" · US10Y {us10y:.2f}%" if us10y > 5 else ""))
    else:
        regime = "NEUTRAL"
        base = 55
        reasons.append(f"NEUTRAL · VIX {vix:.1f} · US10Y {us10y:.2f}%")

    growth = {"AI/반도체", "반도체", "Software", "인터넷", "사이버보안", "바이오", "제약/바이오"}
    defensive = {"금융", "소매", "미디어"}

    if regime == "RISK_ON" and sector in growth:
        base += 12
        reasons.append(f"{sector} · RISK_ON 성장주 로테이션 수혜")
    elif regime == "RISK_OFF" and sector in defensive:
        base += 10
        reasons.append(f"{sector} · 방어주 회피 수요")
    elif regime == "RISK_OFF" and sector in growth:
        base -= 10
        reasons.append(f"{sector} · RISK_OFF 성장주 소외")

    if dxy < 102 and sector in growth:
        base += 5
        reasons.append(f"DXY {dxy:.1f} 약세 · 성장주 유리")
    elif dxy > 106:
        base -= 3
        reasons.append(f"DXY {dxy:.1f} 강세 · 위험자산 부담")

    # ─── FRED 정밀 지표 반영 ───
    if fred.get("available"):
        # 1) Yield Curve 역전 감지 (침체 선행 지표)
        yc = fred.get("yield_curve")
        if yc is not None:
            if yc < 0:
                base -= 8
                reasons.append(f"Yield Curve 역전 ({yc:+.2f}%) · 침체 선행")
            elif yc < 0.3:
                base -= 3
                reasons.append(f"Yield Curve 평탄화 ({yc:+.2f}%)")
            elif yc > 1.0:
                base += 3
                reasons.append(f"Yield Curve 정상화 (+{yc:.2f}%)")

        # 2) Core CPI YoY (Fed 정책 방향)
        cpi_yoy = fred.get("core_cpi_yoy")
        if cpi_yoy is not None:
            if cpi_yoy < 2.5:
                base += 5
                reasons.append(f"Core CPI {cpi_yoy}% · 인플레 안정 (Fed 목표 근접)")
            elif cpi_yoy > 4.0:
                base -= 5
                reasons.append(f"Core CPI {cpi_yoy}% · 인플레 재점화 우려")

        # 3) 실업률 추세 (경기 사이클)
        unemp_change = fred.get("unemp_3m_change")
        unemp = fred.get("unemployment")
        if unemp is not None:
            if unemp < 4.0:
                base += 3
                reasons.append(f"실업률 {unemp}% · 노동시장 강세")
            elif unemp > 5.0:
                base -= 3
                reasons.append(f"실업률 {unemp}% · 노동시장 둔화")
        if unemp_change is not None and unemp_change > 0.3:
            base -= 5
            reasons.append(f"실업률 3M +{unemp_change}%p 급등 · Sahm Rule 경계")

        # 4) Fed Funds Rate (긴축 사이클 위치)
        ffr = fred.get("fed_funds")
        if ffr is not None:
            if ffr < 3.0:
                base += 3
                reasons.append(f"Fed Funds {ffr}% · 완화 사이클")
            elif ffr > 5.5:
                base -= 3
                reasons.append(f"Fed Funds {ffr}% · 긴축 정점")

    return int(np.clip(base, 0, 100)), reasons[:6], regime


def calc_value_score(info: dict, sector: str) -> tuple[int, list[str]]:
    score = 50
    reasons = []
    per = info.get("trailingPE") or info.get("forwardPE")
    fwd_per = info.get("forwardPE")
    pbr = info.get("priceToBook")
    peg = info.get("pegRatio")
    fcf_yield = None
    mkt_cap = info.get("marketCap")
    fcf = info.get("freeCashflow")
    if mkt_cap and fcf and mkt_cap > 0:
        fcf_yield = fcf / mkt_cap * 100

    if per and per > 0:
        if per < 12:
            score += 20; reasons.append(f"PER {per:.1f} · 극심 저평가")
        elif per < 18:
            score += 10; reasons.append(f"PER {per:.1f} · 저평가")
        elif per > 60:
            score -= 15; reasons.append(f"PER {per:.1f} · 극심 프리미엄")
        elif per > 40:
            score -= 8; reasons.append(f"PER {per:.1f} · 프리미엄")
        else:
            reasons.append(f"PER {per:.1f} · 적정")

    if fwd_per and per and fwd_per < per * 0.8:
        score += 8
        reasons.append(f"Forward PER {fwd_per:.1f} · 이익 성장 반영")

    if pbr and pbr > 0:
        if pbr < 1.0:
            score += 10; reasons.append(f"PBR {pbr:.2f} · 자산가치 이하")
        elif pbr > 8:
            score -= 8; reasons.append(f"PBR {pbr:.1f} · 부담")

    if peg and peg > 0:
        if peg < 1.0:
            score += 8; reasons.append(f"PEG {peg:.2f} · 성장률 대비 저평가")
        elif peg > 3:
            score -= 5; reasons.append(f"PEG {peg:.2f} · 성장률 대비 부담")

    if fcf_yield:
        if fcf_yield > 5:
            score += 8; reasons.append(f"FCF Yield {fcf_yield:.1f}% · 우수")
        elif fcf_yield < 0:
            score -= 8; reasons.append(f"FCF 음수 · 현금유출")

    if not reasons:
        reasons.append("밸류 데이터 미확인")
    return int(np.clip(score, 0, 100)), reasons[:4]


# ─── FMP API (미국 공시) ──────────────────────────────────────
def _fmp_get(endpoint: str, params: dict = None) -> Optional[list | dict]:
    if not FMP_KEY:
        return None
    params = params or {}
    params["apikey"] = FMP_KEY
    try:
        r = requests.get(f"{FMP_BASE}{endpoint}", params=params, timeout=8)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


def _latest_13f_quarter() -> tuple[int, int]:
    """13F는 분기말 후 45일 이내 공시 → 45일 지연 감안한 최근 분기 계산"""
    d = datetime.now() - timedelta(days=45)
    q = (d.month - 1) // 3 + 1
    y = d.year
    if q == 0:
        q = 4
        y -= 1
    return y, q


def fetch_fmp_filing_signals(ticker: str) -> dict:
    """미국 종목의 13F + Form 4 + 8-K 시그널 수집 (2025+ FMP API)"""
    signals = {"available": False, "reasons": [], "score_delta": 0}

    # 1) 13F 기관 보유 요약 (symbol-positions-summary)
    y, q = _latest_13f_quarter()
    ownership = _fmp_get("/institutional-ownership/symbol-positions-summary",
                          {"symbol": ticker, "year": y, "quarter": q})
    # 데이터가 없으면 이전 분기 시도
    if not ownership:
        pq = q - 1 if q > 1 else 4
        py = y if q > 1 else y - 1
        ownership = _fmp_get("/institutional-ownership/symbol-positions-summary",
                              {"symbol": ticker, "year": py, "quarter": pq})

    if ownership and isinstance(ownership, list) and len(ownership) > 0:
        signals["available"] = True
        d = ownership[0]
        # 신규 진입 (강한 시그널)
        new_change = d.get("newPositionsChange", 0)
        if new_change > 200:
            signals["score_delta"] += 15
            signals["reasons"].append(f"13F: 신규진입 급증 +{new_change}곳 (STRONG_CONSENSUS)")
        elif new_change > 50:
            signals["score_delta"] += 10
            signals["reasons"].append(f"13F: 신규진입 +{new_change}곳")
        elif new_change < -200:
            signals["score_delta"] -= 10
            signals["reasons"].append(f"13F: 신규진입 급감 {new_change}곳")

        # 증량 vs 감량 순변화
        inc_ch = d.get("increasedPositionsChange", 0)
        red_ch = d.get("reducedPositionsChange", 0)
        net_positions = inc_ch - red_ch
        if net_positions > 300:
            signals["score_delta"] += 8
            signals["reasons"].append(f"13F: 증량-감량 순 +{net_positions}")
        elif net_positions < -300:
            signals["score_delta"] -= 8
            signals["reasons"].append(f"13F: 증량-감량 순 {net_positions}")

        # 기관 지분율 변화
        own_ch = d.get("ownershipPercentChange", 0)
        if own_ch > 1.0:
            signals["score_delta"] += 5
            signals["reasons"].append(f"13F: 기관지분율 +{own_ch:.2f}%p")
        elif own_ch < -1.0:
            signals["score_delta"] -= 5
            signals["reasons"].append(f"13F: 기관지분율 {own_ch:.2f}%p")

        # 풋/콜 비율 (헤지 심리)
        pc_change = d.get("putCallRatioChange")
        pc_now = d.get("putCallRatio")
        if pc_change is not None and pc_change > 30:
            signals["score_delta"] -= 5
            signals["reasons"].append(f"P/C비율 급등 +{pc_change:.0f}% (헤지 강화)")
        elif pc_change is not None and pc_change < -30:
            signals["score_delta"] += 3
            signals["reasons"].append(f"P/C비율 하락 {pc_change:.0f}% (헤지 완화)")

        # 요약 정보
        signals["reasons"].append(
            f"13F: 보유기관 {d.get('investorsHolding','?')}곳 · 지분율 {d.get('ownershipPercent',0):.1f}% ({y}Q{q})")

    # 2) Form 4 내부자거래 (최근 90일)
    insider = _fmp_get("/insider-trading/search",
                        {"symbol": ticker, "page": 0, "limit": 100})
    if insider and isinstance(insider, list):
        signals["available"] = True
        cutoff = datetime.now() - timedelta(days=90)
        buys, sells, opts = 0, 0, 0
        for tx in insider:
            date_str = tx.get("transactionDate") or tx.get("filingDate")
            if not date_str:
                continue
            try:
                tx_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
                if tx_date < cutoff:
                    continue
            except Exception:
                continue
            code = (tx.get("transactionType") or "").upper()
            acq = tx.get("acquisitionOrDisposition", "")
            # P = Purchase (실제 매수), S = Sale, M = M-Exempt (옵션 행사, 제외)
            if code.startswith("P"):
                buys += 1
            elif code.startswith("S"):
                sells += 1
            elif code.startswith("M"):
                opts += 1

        net = buys - sells
        if buys >= 3 and net > 0:
            signals["score_delta"] += 12
            signals["reasons"].append(f"Form 4: 실제매수 클러스터 P{buys}/S{sells} (90일)")
        elif net > 0:
            signals["score_delta"] += 5
            signals["reasons"].append(f"Form 4: 순매수 P{buys}/S{sells}")
        elif net < -3:
            signals["score_delta"] -= 10
            signals["reasons"].append(f"Form 4: 매도 우세 P{buys}/S{sells}")

    # 3) 최근 8-K 이벤트 (60일, from/to 필수)
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    filings = _fmp_get("/sec-filings-search/symbol",
                        {"symbol": ticker, "from": from_date, "to": to_date, "limit": 30})
    if filings and isinstance(filings, list):
        signals["available"] = True
        recent_8k = [f for f in filings if "8-K" in str(f.get("formType", ""))]
        if len(recent_8k) >= 3:
            signals["score_delta"] += 3
            signals["reasons"].append(f"8-K: 최근 60일 이벤트 {len(recent_8k)}건 (활발)")
        elif len(recent_8k) == 0:
            signals["reasons"].append("8-K: 최근 60일 이벤트 없음")

    return signals


# ─── FRED API (연준 경제 데이터) ─────────────────────────────
def _fred_get_series(series_id: str, limit: int = 13) -> Optional[list]:
    """FRED 시리즈 관측치 조회 (최신순)"""
    if not FRED_KEY:
        return None
    try:
        r = requests.get(f"{FRED_BASE}/series/observations", params={
            "series_id": series_id,
            "api_key": FRED_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        }, timeout=8)
        if r.status_code == 200:
            data = r.json()
            return data.get("observations", [])
        return None
    except Exception:
        return None


def _latest_value(obs: list) -> Optional[float]:
    if not obs:
        return None
    for o in obs:
        v = o.get("value")
        if v and v != ".":
            try:
                return float(v)
            except ValueError:
                continue
    return None


def fetch_fred_macro() -> dict:
    """
    FRED에서 매크로 지표 수집:
    - VIXCLS: VIX (yfinance보다 정확)
    - DGS10: 10년물 국채금리
    - T10Y2Y: Yield Curve (10Y-2Y, 음수면 침체 시그널)
    - DFF: Fed Funds 실효금리
    - UNRATE: 실업률
    - CPILFESL: Core CPI (YoY 계산용)
    - DTWEXBGS: Broad Dollar Index
    """
    result = {"available": False}
    if not FRED_KEY:
        return result

    print("[*] FRED 정밀 매크로 수집...")

    # 단일 최신값 시리즈
    single_series = {
        "vix": "VIXCLS",
        "us10y": "DGS10",
        "yield_curve": "T10Y2Y",
        "fed_funds": "DFF",
        "unemployment": "UNRATE",
        "dxy_broad": "DTWEXBGS",
    }
    for key, sid in single_series.items():
        obs = _fred_get_series(sid, limit=5)
        result[key] = _latest_value(obs)
        time.sleep(0.05)

    # Core CPI YoY 계산 (최근 값 vs 12개월 전)
    core_cpi = _fred_get_series("CPILFESL", limit=15)
    if core_cpi and len(core_cpi) >= 13:
        latest = _latest_value(core_cpi[:1])
        year_ago = _latest_value(core_cpi[12:13])
        if latest and year_ago and year_ago > 0:
            result["core_cpi_yoy"] = round((latest / year_ago - 1) * 100, 2)

    # 실업률 3개월 변화 (추세)
    unrate_obs = _fred_get_series("UNRATE", limit=4)
    if unrate_obs and len(unrate_obs) >= 4:
        latest_u = _latest_value(unrate_obs[:1])
        m3_ago = _latest_value(unrate_obs[3:4])
        if latest_u and m3_ago:
            result["unemp_3m_change"] = round(latest_u - m3_ago, 2)

    result["available"] = True
    printable = {k: v for k, v in result.items() if k != "available" and v is not None}
    print(f"    FRED: {printable}")
    return result


# ─── 종목 유니버스 동적 조회 ──────────────────────────────────
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def _load_cache(name: str, ttl_hours: int = 24) -> Optional[list]:
    path = os.path.join(CACHE_DIR, f"{name}.json")
    if not os.path.exists(path):
        return None
    age = time.time() - os.path.getmtime(path)
    if age > ttl_hours * 3600:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(name: str, data: list) -> None:
    path = os.path.join(CACHE_DIR, f"{name}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


# 섹터 매핑 (영문 → 한글, xlsx v5 로직 정합성)
SECTOR_KR = {
    "Technology": "IT", "Communication Services": "인터넷",
    "Consumer Cyclical": "소비재", "Consumer Defensive": "소매",
    "Healthcare": "제약/바이오", "Financial Services": "금융",
    "Financial": "금융", "Industrials": "산업재",
    "Basic Materials": "소재", "Energy": "에너지",
    "Utilities": "유틸리티", "Real Estate": "리츠",
    "Semiconductors": "반도체",
}


# 레버리지·인버스 ETF 를 종목명으로 걸러낸다. ATR 3배 손절과 궁합이 나쁘다 -
# 일일 변동성이 기초자산의 배수라 손절폭이 비현실적으로 벌어지고, 장기 보유
# 시 변동성 감쇠 때문에 손익을 같은 척도로 해석할 수 없다.
#
# 단어 경계를 쓴다. 경계가 없으면 "Shortline" 같은 이름이 "Short" 로 걸린다.
#
# SHORT 뒤의 부정 전방탐색이 핵심이다. 채권 ETF 의 "Short" 는 인버스가 아니라
# 잔존만기를 뜻한다 - 이 예외가 없으면 "iShares Short Treasury Bond ETF" 나
# "Vanguard Short-Term Bond ETF" 같은 단기채 ETF 가 통째로 빠진다. 반면
# "ProShares Short S&P500" 은 진짜 인버스라 걸러야 한다.
_LEVERAGED_PATTERN = re.compile(
    r"\b(?:\d+X|ULTRA|ULTRAPRO|ULTRASHORT|INVERSE|BEAR|BULL|LEVERAGED"
    r"|SHORT\b(?![- ]?(?:TERM|DURATION|MATURITY|TREASURY)))\b",
    re.IGNORECASE,
)


def is_leveraged_or_inverse(name: str) -> bool:
    """종목명이 레버리지·인버스 ETF 를 가리키는지."""
    if not name:
        return False
    return bool(_LEVERAGED_PATTERN.search(name))


def fetch_us_universe(min_market_cap: float = 5e9, limit: int = 5000) -> list:
    """FMP stock-screener로 NYSE+NASDAQ 상장 종목 조회 (ETF 제외)"""
    # v2: 유니버스 튜플이 asset_type 을 포함하는 5칸으로 늘었다. 캐시 키를
    # 바꾸지 않으면 옛 4칸 캐시가 그대로 읽혀 스캔 루프 언패킹이 터진다.
    cache_key = f"us_universe_v3_{int(min_market_cap)}_{limit}"
    cached = _load_cache(cache_key)
    if cached:
        print(f"    [cache] 미국 유니버스 {len(cached)}종목 (캐시)")
        return cached

    if not FMP_KEY:
        print("    [!] FMP API 키 없음 → 폴백 사용")
        return []

    print(f"    [*] FMP 미국 유니버스 조회 (시총 ≥ ${min_market_cap/1e9:.1f}B)...")
    try:
        r = requests.get(f"{FMP_BASE}/company-screener", params={
            "marketCapMoreThan": int(min_market_cap),
            "exchange": "nyse,nasdaq",
            "isEtf": "false",
            "isFund": "false",
            "isActivelyTrading": "true",
            "limit": limit,
            "apikey": FMP_KEY,
        }, timeout=15)
        if r.status_code != 200:
            print(f"    [!] FMP 응답 {r.status_code} → 대체 엔드포인트 시도")
            r = requests.get(f"{FMP_BASE}/stock-screener", params={
                "marketCapMoreThan": int(min_market_cap),
                "exchange": "nyse,nasdaq",
                "isEtf": "false",
                "isActivelyTrading": "true",
                "limit": limit,
                "apikey": FMP_KEY,
            }, timeout=15)

        data = r.json() if r.status_code == 200 else []
        universe = []
        for item in data:
            symbol = item.get("symbol", "")
            if not symbol or "." in symbol or "-" in symbol:
                continue  # 우선주/특수클래스 제외
            name = item.get("companyName") or item.get("name") or symbol
            sector_raw = item.get("sector", "")
            industry_raw = item.get("industry", "")
            sector_kr = SECTOR_KR.get(sector_raw, sector_raw or "기타")
            if "Semi" in industry_raw:
                sector_kr = "반도체"
            universe.append((symbol, name[:40], "US", sector_kr, "STOCK",
                             item.get("exchangeShortName") or "?"))

        _save_cache(cache_key, universe)
        print(f"    [OK] 미국 {len(universe)}종목 수집 완료")
        return universe
    except Exception as e:
        print(f"    [!] FMP 조회 실패: {e}")
        return []


# ETF 를 남길 미국 거래소. 요청 파라미터로 거래소를 거르지 않고 응답에서
# 거르는 이유는 SPY 때문이다 - NYSE Arca 상장인데 FMP 는 AMEX 로 준다.
# exchange=nyse,nasdaq 으로 요청하면 AMEX 로 분류된 주요 ETF 가 통째로
# 빠진다 (2026-08-22 응답 기준 AMEX 657 · NASDAQ 207 · NYSE 10).
US_ETF_EXCHANGES = frozenset({"AMEX", "NASDAQ", "NYSE"})


def parse_etf_rows(data: list, min_aum: float) -> list:
    """FMP ETF 스크리너 응답을 유니버스 튜플 목록으로 바꾼다.

    네트워크와 분리해 두면 필터 규칙을 테스트할 수 있다.

    ETF 는 섹터를 '미분류' 로 둔다. calc_macro_score 가 미분류를 중립
    처리하므로 macro 점수가 왜곡되지 않는다.
    """
    rows = []
    for item in data:
        symbol = item.get("symbol", "")
        # 접미사가 붙은 티커는 해외 상장이다. 주식 경로와 같은 규칙을 쓴다.
        if not symbol or "." in symbol or "-" in symbol:
            continue
        # 거래소를 모르면 미국이라고 가정하지 않는다. 거르지 않으면 TSX 가
        # market=US 로 아카이브에 들어간다 (2026-08-22 실제 스캔 148건).
        exchange = item.get("exchangeShortName")
        if exchange not in US_ETF_EXCHANGES:
            continue
        name = item.get("name") or item.get("companyName") or symbol
        if is_leveraged_or_inverse(name):
            continue
        aum = item.get("marketCap")
        if aum is None or aum < min_aum:
            continue
        rows.append((symbol, name[:40], "US", "미분류", "ETF", exchange))
    return rows


# ETF 를 그 섹터로 볼 최소 비중(%). 스크리너의 sector 필드는 못 쓴다 -
# 운용사 업종이라 874건 전부 "Financial Services" 로 온다. 실제 노출은
# etf/sector-weightings 에 있다.
#
# 60% 로 잡은 근거는 실측이다. 광범위 지수는 최대 섹터가 VOO 38.6% ·
# SPY 37.4% · VTI 36.1% 수준이라 "IT ETF" 로 부르면 macro 로테이션 가점을
# 부당하게 받는다. 반면 QQQ 60.3% · XLB 83.8% 는 진짜 편중이다.
ETF_SECTOR_MIN_WEIGHT = 60.0


def dominant_sector(weightings: list,
                    min_weight: float = ETF_SECTOR_MIN_WEIGHT) -> str:
    """ETF 섹터 비중에서 지배 섹터를 고른다. 쏠림이 약하면 '미분류'.

    SECTOR_KR 에 없는 값("Cash & Others" 등 채권·현금)도 미분류로 둔다.
    섹터 로테이션은 주식 섹터에 대한 것이라 채권 ETF 에 적용할 수 없다.
    """
    if not weightings:
        return "미분류"

    def weight(item):
        try:
            return float(item.get("weightPercentage") or 0)
        except (TypeError, ValueError):
            return 0.0

    best = max(weightings, key=weight)
    if weight(best) < min_weight:
        return "미분류"
    return SECTOR_KR.get(best.get("sector"), "미분류")


def is_equity_asset_class(asset_class: str) -> bool:
    """이 ETF 가 주식형인지. 섹터 로테이션은 주식에만 의미가 있다.

    정확히 일치시키지 않고 "Equity" 포함 여부로 본다. 실제 값이
    Equity · Large Cap Equity · International Equity · Emerging Markets
    Equity · Sector Equity · Equity Income 처럼 갈래가 많아, 목록으로
    관리하면 새 값이 나올 때마다 조용히 빠진다.

    채권형이 걸러져야 하는 이유는 실측이다 - 채권 ETF 의 섹터 비중은 발행
    기업 업종이라, SPHY(고수익 채권)가 '금융', SJNK(정크본드)가 '인터넷'
    으로 잡혀 성장주 로테이션 가점을 받았다.
    """
    return "EQUITY" in (asset_class or "").upper()


def is_leveraged_asset_class(asset_class: str) -> bool:
    """assetClass 로 잡는 레버리지·인버스. 이름 규칙을 빠져나간 것을 막는다."""
    upper = (asset_class or "").upper()
    return "LEVERAGED" in upper or "INVERSE" in upper


def fetch_etf_profiles(symbols: list, workers: int = 10) -> dict:
    """티커 -> {"asset_class", "sector"}. 실패는 조용히 미분류로 둔다.

    자산군을 먼저 보고 주식형일 때만 섹터 비중을 부른다 - 채권·원자재는
    섹터 개념이 없어서 부를 이유가 없고, 호출 수도 줄어든다.

    실패해도 스캔을 막지 않는다. 섹터가 없으면 macro 가 중립으로 갈 뿐이고,
    그건 이 기능 도입 이전 상태다.
    """
    if not FMP_KEY or not symbols:
        return {}

    cache_key = f"etf_profiles_{len(symbols)}"
    cached = _load_cache(cache_key)
    if cached:
        print(f"    [cache] ETF 프로파일 {len(cached)}건 (캐시)")
        return cached

    def get(endpoint, symbol):
        r = requests.get(f"{FMP_BASE}/{endpoint}",
                         params={"symbol": symbol, "apikey": FMP_KEY},
                         timeout=12)
        data = r.json() if r.status_code == 200 else []
        return data if isinstance(data, list) else []

    def one(symbol):
        try:
            info = get("etf/info", symbol)
            asset_class = info[0].get("assetClass") if info else None
            if not is_equity_asset_class(asset_class):
                return symbol, {"asset_class": asset_class, "sector": "미분류"}
            weights = get("etf/sector-weightings", symbol)
            return symbol, {"asset_class": asset_class,
                            "sector": dominant_sector(weights)}
        except Exception:
            return symbol, {"asset_class": None, "sector": "미분류"}

    print(f"    [*] ETF 자산군·섹터 조회 ({len(symbols)}종목)...")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        profiles = dict(pool.map(one, symbols))

    named = sum(1 for p in profiles.values() if p["sector"] != "미분류")
    equity = sum(1 for p in profiles.values()
                 if is_equity_asset_class(p["asset_class"]))
    print(f"    [OK] 주식형 {equity}/{len(profiles)}종목 · "
          f"섹터 확정 {named}종목 (나머지는 채권·원자재·분산이라 미분류)")
    _save_cache(cache_key, profiles)
    return profiles


def fetch_us_etf_universe(min_aum: float = 1e9, limit: int = 3000) -> list:
    """FMP stock-screener 로 미국 ETF 조회.

    거래소 필터를 걸지 않는 것이 핵심이다. SPY·IWM 등 주요 ETF 는 NYSE Arca
    상장이라 exchange=nyse,nasdaq 으로 조회하면 QQQ 정도만 잡히고 대부분
    누락된다.
    """
    # v2: 미국 거래소 필터를 넣기 전 캐시에는 TSX 가 섞여 있다. 키를 바꿔
    # 그 캐시가 다시 읽히지 않게 한다.
    cache_key = f"us_etf_universe_v6_{int(min_aum)}_{limit}"
    cached = _load_cache(cache_key)
    if cached:
        print(f"    [cache] 미국 ETF {len(cached)}종목 (캐시)")
        return [tuple(row) for row in cached]

    if not FMP_KEY:
        print("    [!] FMP API 키 없음 → ETF 건너뜀")
        return []

    print(f"    [*] FMP 미국 ETF 조회 (AUM ≥ ${min_aum/1e9:.1f}B)...")
    try:
        r = requests.get(f"{FMP_BASE}/company-screener", params={
            "marketCapMoreThan": int(min_aum),
            "isEtf": "true",
            "isActivelyTrading": "true",
            "limit": limit,
            "apikey": FMP_KEY,
        }, timeout=15)
        data = r.json() if r.status_code == 200 else []
        universe = parse_etf_rows(data, min_aum)

        # 자산군과 섹터를 채운다. 스크리너가 주는 sector 는 운용사 업종이라
        # 못 쓰고, 실제 노출 섹터를 종목별로 따로 받는다 (dominant_sector).
        profiles = fetch_etf_profiles([row[0] for row in universe])
        if profiles:
            # assetClass 로 잡히는 레버리지·인버스를 여기서 한 번 더 막는다.
            # 이름 규칙(is_leveraged_or_inverse)을 빠져나간 것이 실측으로
            # 확인됐다 (표본 120개 중 1건).
            dropped = [t for t, *_ in universe
                       if is_leveraged_asset_class(
                           (profiles.get(t) or {}).get("asset_class"))]
            if dropped:
                print(f"    [!] 레버리지·인버스 {len(dropped)}종목 추가 제외 "
                      f"(자산군 기준): {', '.join(dropped[:8])}")

            # 주식형만 남긴다. 채권 ETF 는 주가 변동성이 낮아 기술적 지표가
            # 안정적으로 높게 나오는데, 이 스캐너의 tech 축은 그걸 강세로
            # 읽는다. 2026-08-22 실측에서 표시된 ETF 22개 중 8개가 채권·
            # 관리선물이었고 전부 BUY 였다 - "3개월 상승 후보" 의 근거로
            # 삼을 수 없는 종류다.
            #
            # 자산군을 못 받은 종목은 남긴다. 조회 실패로 유니버스가 조용히
            # 비는 것이 오분류보다 나쁘다.
            skipped = [t for t, *_ in universe
                       if t in profiles and t not in set(dropped)
                       and not is_equity_asset_class(
                           profiles[t].get("asset_class"))]
            if skipped:
                print(f"    [*] 비주식형 {len(skipped)}종목 제외 "
                      f"(채권·원자재·대체투자)")

            remove = set(dropped) | set(skipped)
            universe = [(t, n, m, (profiles.get(t) or {}).get("sector", sec),
                         at, ex)
                        for t, n, m, sec, at, ex in universe
                        if t not in remove]

        _save_cache(cache_key, universe)
        print(f"    [OK] 미국 ETF {len(universe)}종목 수집 완료")
        return universe
    except Exception as e:
        print(f"    [!] FMP ETF 조회 실패: {e}")
        return []


def load_universe(min_us_cap: float = 1e10,
                  min_etf_aum: float = 1e9,
                  include_etf: bool = True,
                  test_mode: bool = False) -> list:
    """미국 주식 + ETF 유니버스를 로드한다.

    반환 튜플은 (ticker, name, market, sector, asset_type) 5칸이다.
    asset_type 은 "STOCK" | "ETF" 이고, 스캔 루프가 이 값으로 스코어링을
    분기한다.

    한국은 다루지 않는다. pykrx 조회가 CI 에서 매번 실패해 하드코딩 폴백
    112종목이 고정돼 있었고, 그 리스트는 코스피 구성 변화를 반영하지 못했다.
    """
    if test_mode:
        return FALLBACK_UNIVERSE

    universe = list(fetch_us_universe(min_market_cap=min_us_cap))
    if not universe:
        universe = [s for s in FALLBACK_UNIVERSE if s[4] == "STOCK"]
        print(f"    [폴백] 미국 하드코딩 {len(universe)}종목 사용")

    if include_etf:
        universe.extend(fetch_us_etf_universe(min_aum=min_etf_aum))

    return universe


# ─── 공시 스코어 (API 우선, 실패 시 프록시) ───────────────────
def calc_filing_score(info: dict, hist_df, ticker: str, market: str) -> tuple[int, list[str]]:
    """API 데이터가 있으면 우선 사용, 없으면 yfinance 프록시로 대체

    market 은 지금 미국뿐이라 분기하지 않는다. 인자를 남겨 두는 것은 아카이브
    행과 호출부가 같은 값을 들고 다니기 때문이다.
    """
    score = 55
    reasons = []

    # 실제 API 시그널 우선 시도
    api_signals = {}
    if FMP_KEY:
        api_signals = fetch_fmp_filing_signals(ticker)

    if api_signals.get("available"):
        score += api_signals["score_delta"]
        reasons.extend(api_signals["reasons"])
        reasons.append("* FMP 실시간 공시 반영")
    else:
        # yfinance 프록시로 fallback
        inst_pct = info.get("heldPercentInstitutions")
        insider_pct = info.get("heldPercentInsiders")
        short_ratio = info.get("shortRatio")
        payout = info.get("payoutRatio")

        if inst_pct:
            p = inst_pct * 100
            if p > 75:
                score += 12
                reasons.append(f"기관 보유 {p:.0f}% · 강한 컨센서스")
            elif p > 60:
                score += 6
                reasons.append(f"기관 보유 {p:.0f}%")
            elif p < 30:
                score -= 8
                reasons.append(f"기관 보유 {p:.0f}% · 낮음")

        if insider_pct and insider_pct > 0.05:
            score += 8
            reasons.append(f"내부자 보유 {insider_pct*100:.1f}%")

        if short_ratio:
            if short_ratio > 10:
                score -= 10
                reasons.append(f"공매도비율 {short_ratio:.1f} · 매도압력")
            elif short_ratio < 2:
                score += 5
                reasons.append(f"공매도비율 {short_ratio:.1f} · 낮음")

        if payout is not None and 0 < payout < 0.6:
            score += 5
            reasons.append(f"배당성향 {payout*100:.0f}% · 안정")

        close = hist_df["Close"].values.astype(float)
        if len(close) >= 63:
            high_63 = np.max(close[-63:])
            prox = close[-1] / high_63 * 100
            if prox > 98:
                score += 8
                reasons.append(f"3개월 신고가 근접 ({prox:.0f}%)")
            elif prox < 85:
                score -= 5
                reasons.append(f"고점 대비 {prox:.0f}%")

        if not FMP_KEY:
            reasons.append("* FMP API 키 미설정 · 프록시 사용")
        else:
            reasons.append("* FMP 최근 공시 없음 · 프록시 보조")

    if not reasons:
        reasons.append("공시 데이터 부족")

    return int(np.clip(score, 0, 100)), reasons[:5]


# ─── 종합 판정 ────────────────────────────────────────────────
def calc_consensus(tech, flow_, filing, value):
    return sum(1 for v in (tech, flow_, filing, value) if v >= 70)


def calc_consensus_etf(tech, flow_):
    """ETF 합의 개수. 축이 tech/flow 둘뿐이므로 최대 2 다.

    개수를 그대로 아카이브에 저장한다. 판정은 calc_signal 이 n_axes=2 로
    비율을 계산한다.
    """
    return sum(1 for v in (tech, flow_) if v >= 70)


def calc_total(tech, flow_, filing, value):
    """주식 종합점수. 네 축 모두 실측값이다.

    macro 는 축이 아니다 - 시장 전체 점수라 종목 변별력이 0 이다. 2026-08-22
    아카이브 1,520행에서 macro >= 70 인 행이 한 건도 없었다. 국면 판정은
    매매 계층의 게이트로 쓰고 점수에서는 뺀다.
    """
    return int(round(tech * 0.30 + flow_ * 0.20 + filing * 0.30 + value * 0.20))


# ETF 가중치. 주식 가중치에서 filing(0.30)·value(0.20) 를 빼고 남은 0.50 으로
# 나눈 값이다. ETF 에는 개별기업 재무·공시 데이터가 없어 두 축을 계산할 수 없다.
#
# 재정규화를 유지하는 근거는 실측이다. 결측 축을 상수 50 으로 채우면 총점
# 분산이 압축되어 ETF 총점 최대가 66점에 그친다 - 70점 문턱에 영원히 닿지
# 못해 사실상 배제가 된다 (2026-08-24 표본 120종목).
#
# 재정규화가 예전에 문제였던 것은 대상이 tech 와 죽은 macro 였기 때문이다.
# 살아 있는 축이 tech 하나뿐이라 ETF 에 유리한 tech 편향이 그대로 총점 편향이
# 되었다. tech·flow 로 바꾸면 두 축의 자산군 편향이 부호가 반대라 상쇄된다 -
# 실측 격차 tech +7.7 · flow -8.0, 그 결과 총점 평균이 ETF 57.2 · 주식 56.7 로
# 맞는다.
ETF_TECH_WEIGHT = 0.30 / 0.50
ETF_FLOW_WEIGHT = 0.20 / 0.50


def calc_total_etf(tech, flow_):
    """ETF 종합점수. tech/flow 두 축만 쓰고 가중치를 재정규화한다."""
    return int(round(tech * ETF_TECH_WEIGHT + flow_ * ETF_FLOW_WEIGHT))


# BUY 에 필요한 합의 비율. 0.75 는 기존 cons>=3 (3/4) 과 정확히 같다.
#
# 자산군별로 다른 값을 쓰지 않는다. ETF 전용 완화(0.50)는 macro 가 죽어 있어
# ETF 가 구조적으로 BUY 를 못 받던 것을 풀려던 것이었는데, 그 순간 ETF 판정이
# tech 단일 축 도장으로 붕괴했다 - 2026-08-22 실측 BUY 88건 전부 tech 하나로만
# 통과했다. macro 를 축에서 빼고 flow 를 넣으면 두 축 다 살아 있으므로 완화가
# 필요 없다. ETF 는 가진 축의 100%(2/2)가 70 이상이어야 BUY 이며, 이는 주식의
# 75%(3/4)보다 엄격하다.
STOCK_BUY_RATIO = 0.75


def calc_signal(total, cons, n_axes=4, buy_ratio=STOCK_BUY_RATIO):
    """종합점수와 합의 비율로 신호를 낸다.

    cons 는 70점 이상인 축의 개수, n_axes 는 축의 총 개수다. 개수가 아니라
    비율로 판정하는 것은 ETF 때문이다 - ETF 는 filing/value 데이터가 없어
    축이 tech/flow 둘뿐이라, 개수 기준(cons>=3)으로는 BUY 가 영원히 나오지
    않는다.
    """
    ratio = cons / n_axes if n_axes else 0.0
    if total >= 80 and ratio >= STOCK_BUY_RATIO:
        return "STRONG_BUY"
    if total >= 70 and ratio >= buy_ratio:
        return "BUY"
    if total >= 60 and ratio >= 0.50:
        return "WATCH"
    if total >= 45:
        return "HOLD"
    return "AVOID"


def calc_hitl(signal, total, tech):
    if signal == "AVOID":
        return True
    if signal == "STRONG_BUY" and tech > 85:
        return True
    return False


def scan_summary(results: list, shown: list) -> dict:
    """대시보드 지표 카드용 요약. 필터 **이전** 전체를 기준으로 센다.

    출력 필터를 켜면 지표 카드가 구조적으로 0이 된다. HITL 은 AVOID 이거나
    STRONG_BUY & tech>85 일 때만 켜지는데, AVOID 는 정의상 총점 45 미만이라
    70점 필터를 절대 통과하지 못한다. 표시 목록만 보고 세면 검토 대기열이
    있는데도 0으로 보인다 (2026-08-22 실측: 아카이브 151건 vs 표시 0건).
    """
    def count(rows, pred):
        return sum(1 for r in rows if pred(r))

    return {
        "scanned": len(results),
        "shown": len(shown),
        "strong_buy": count(results, lambda r: r["signal"] == "STRONG_BUY"),
        "buy": count(results, lambda r: r["signal"] == "BUY"),
        "watch": count(results, lambda r: r["signal"] == "WATCH"),
        "avoid": count(results, lambda r: r["signal"] == "AVOID"),
        "hitl": count(results, lambda r: r["hitl"]),
    }


def filter_for_output(rows: list, min_total: int) -> list:
    """대시보드·콘솔에 낼 행만 남긴다.

    아카이브(history/*.csv)에는 적용하지 않는다. exit_rules.evaluate() 가
    보유 종목의 그날 total 이 exit_total 미만이면 SIGNAL 청산하는데, 점수가
    떨어진 행이 아카이브에서 사라지면 그 판정을 할 수 없게 된다.

    ETF 에 별도 임계(min_total_etf=78)를 두던 것은 제거했다. 그것은 두 점수가
    같은 척도가 아니어서 생긴 증상을 표시 단계에서 가리던 땜질이었고, 순위는
    고치지 못했다 - 주식 최고점이 77인데 77점 ETF 는 여전히 전 종목 위에 섰다.
    flow 축으로 척도를 맞춘 지금은 한 임계로 충분하다.
    """
    return [r for r in rows
            if r.get("total") is not None and r["total"] >= min_total]


def calc_ev_and_target(tech, flow_, filing, value, r3m) -> tuple[float, int]:
    """기대값과 목표 수익률. macro 자리를 flow 가 받았다.

    macro 는 전 종목이 같은 값을 받아 종목 간 기대값을 벌리지 못했다.
    """
    # NaN 방어: 입력값 중 하나라도 NaN/None이면 중립값(0)으로 대체
    def _safe(v):
        if v is None:
            return 0.0
        try:
            if np.isnan(v):
                return 0.0
        except (TypeError, ValueError):
            pass
        return v
    tech, flow_, filing, value, r3m = _safe(tech), _safe(flow_), _safe(filing), _safe(value), _safe(r3m)
    strength = (tech * 0.4 + filing * 0.3 + flow_ * 0.2 + value * 0.1) / 100
    momentum_adj = np.clip(r3m / 30, -0.5, 0.5)
    ev = round((strength - 0.5) * 3.5 + momentum_adj * 0.4, 2)
    if np.isnan(ev):
        return 0.0, 0
    target = int(round(ev * 12))
    return ev, int(np.clip(target, -15, 30))


# ─── 데이터 수집 ──────────────────────────────────────────────
def fetch_macro() -> tuple[float, float, float]:
    print("[*] 거시 지표 수집...")
    try:
        vix = yf.Ticker("^VIX").history(period="5d")["Close"].iloc[-1]
    except Exception:
        vix = 16.0
    try:
        dxy = yf.Ticker("DX-Y.NYB").history(period="5d")["Close"].iloc[-1]
    except Exception:
        dxy = 103.5
    try:
        us10y = yf.Ticker("^TNX").history(period="5d")["Close"].iloc[-1]
    except Exception:
        us10y = 4.2
    print(f"    VIX={vix:.2f} · DXY={dxy:.2f} · US10Y={us10y:.2f}%")
    return float(vix), float(dxy), float(us10y)


def is_scan_complete(n_collected: int, n_total: int, min_rate: float) -> bool:
    """수집률이 기준 이상인지. 유니버스가 비면 False."""
    if n_total <= 0:
        return False
    return (n_collected / n_total) >= min_rate


def drop_unsettled_bars(hist_df):
    """종가가 NaN인 미확정 봉을 제거한다.

    yfinance는 미국 종목에 대해 OHLC가 NaN이고 거래량만 채워진 마지막 봉을
    붙여 보내는 경우가 잦다. 이 봉을 그대로 두면 calc_tech_score의 종가
    배열이 오염돼 이동평균·MACD 판정이 통째로 무너지고(NVDA 실측 tech 72->45),
    이력 CSV에는 종가 없이 거래량만 있는 행이 남는다.
    """
    return hist_df[hist_df["Close"].notna()]


def fetch_stock(ticker: str, retries: int = 4) -> Optional[dict]:
    """yfinance 조회. 429(rate limit) 시 지수 백오프로 재시도.

    병렬 스캔에서는 429가 필연적으로 발생하므로, 재시도 없이는 종목이
    조용히 누락된다. 백오프로 흡수해 유니버스 완결성을 보장한다.
    """
    backoff = 5.0
    for attempt in range(retries):
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="1y", auto_adjust=True)
            hist = drop_unsettled_bars(hist)
            if hist.empty or len(hist) < 60:
                print(f"[!] {ticker}: 히스토리 부족")
                return None
            info = t.info if hasattr(t, "info") else {}
            return {"hist": hist, "info": info}
        except Exception as e:
            msg = str(e)
            rate_limited = ("Too Many Requests" in msg or "Rate limited" in msg
                            or "429" in msg)
            if rate_limited and attempt < retries - 1:
                time.sleep(backoff)
                backoff *= 2.2
                continue
            print(f"[!] {ticker}: {msg[:70]}")
            return None
    return None


def _save_intermediate(results: list, vix: float, dxy: float, us10y: float,
                       fred_data: dict) -> None:
    """장시간 스캔 중 중간 저장 (크래시 방지)"""
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_data.js")
    fred_json = json.dumps(fred_data, ensure_ascii=False)
    js = f"""// AI 3-Month Stock Finder - Intermediate Save
// Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (진행 중)
window.LIVE_MACRO = {{
  vix: {vix:.2f}, dxy: {dxy:.2f}, us10y: {us10y:.2f},
  generated_at: "{datetime.now().isoformat()}",
  fmp_active: {str(bool(FMP_KEY)).lower()},
  fred_active: {str(bool(FRED_KEY)).lower()},
  fred: {fred_json},
  intermediate: true, count: {len(results)}
}};
window.LIVE_STOCKS = {json.dumps(results, ensure_ascii=False)};
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(js)


# ─── 메인 ──────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(
        description="AI 3-Month Stock Finder · 전체 유가증권 스캔",
        formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--min-us-cap", type=float, default=1e10,
                   help="미국 주식 최소 시가총액 USD (기본: 1e10 = $10B)\n"
                        "  1e9 = $1B (~2500종목, 2시간+)\n"
                        "  5e9 = $5B (~800종목, 30분)\n"
                        "  1e10 = $10B (~1000종목, 15분)")
    p.add_argument("--min-etf-aum", type=float, default=1e9,
                   help="미국 ETF 최소 AUM USD (기본: 1e9 = $1B)")
    p.add_argument("--no-etf", action="store_true",
                   help="ETF 를 유니버스에서 제외한다")
    p.add_argument("--min-total", type=int, default=70,
                   help="대시보드·콘솔 출력 최소 종합점수 (기본 70).\n"
                        "  주식·ETF 에 같은 값을 쓴다 - flow 축으로 두 척도를\n"
                        "  맞췄으므로 자산군별 임계가 필요 없다.\n"
                        "  아카이브에는 적용되지 않는다")
    p.add_argument("--limit", type=int, default=0,
                   help="종류별 상위 N개로 제한 (0=제한없음)")
    p.add_argument("--test", action="store_true",
                   help="테스트 모드 (폴백 종목만)")
    p.add_argument("--sleep", type=float, default=0.3,
                   help="종목간 대기 (초, 기본 0.3 · 워커 스레드별로 적용)")
    p.add_argument("--workers", type=int, default=4,
                   help="동시 조회 스레드 수 (기본 4, 1=순차 실행)")
    p.add_argument("--min-success", type=float, default=0.90,
                   help="최소 수집률 (기본 0.90 · 미달 시 저장 없이 실패)")
    p.add_argument("--all", action="store_true",
                   help="시총 필터 없이 진짜 모든 종목 (매우 오래 걸림)")
    return p.parse_args()


def main():
    args = parse_args()

    # --all 옵션 시 규모 필터 제거. ETF 의 AUM 하한도 같이 푼다 - 주식만
    # 풀고 ETF 를 남겨 두면 "전체"라는 이름과 어긋난다.
    if args.all:
        args.min_us_cap = 0
        args.min_etf_aum = 0

    print("=" * 65)
    print("  AI 3-Month Stock Finder v5 · 전체 유가증권 스캔")
    print("=" * 65)

    # API 키 상태 표시
    print(f"[env] FMP API : {'✓ 활성' if FMP_KEY else '✗ 미설정 (프록시 사용)'}")
    print(f"[env] FRED API: {'✓ 활성 (매크로 정밀화)' if FRED_KEY else '✗ 미설정 (yfinance 사용)'}")
    if not FMP_KEY and not FRED_KEY:
        print("      → .env 파일에 API 키를 추가하면 실제 시그널이 반영됩니다")
    print("-" * 65)

    # 유니버스 로드
    print("[*] 종목 유니버스 로드...")
    universe = load_universe(
        min_us_cap=args.min_us_cap,
        min_etf_aum=args.min_etf_aum,
        include_etf=not args.no_etf,
        test_mode=args.test,
    )
    if args.limit > 0:
        stocks = [s for s in universe if s[4] == "STOCK"][:args.limit]
        etfs = [s for s in universe if s[4] == "ETF"][:args.limit]
        universe = stocks + etfs
        print(f"    [limit] 종류별 상위 {args.limit}개로 제한 → 총 {len(universe)}종목")

    if not universe:
        print("[!] 유니버스가 비었습니다. --test 옵션으로 폴백 모드 시도")
        sys.exit(1)

    n_stock = sum(1 for s in universe if s[4] == "STOCK")
    n_etf = sum(1 for s in universe if s[4] == "ETF")
    est_sec = len(universe) * (2.5 + args.sleep) / max(1, args.workers)
    est_min = est_sec / 60
    print(f"    총 {len(universe)}종목 (주식: {n_stock}, ETF: {n_etf})")
    print(f"    예상 소요시간: 약 {est_min:.1f}분 ({est_sec/3600:.1f}시간)")
    print("-" * 65)

    vix, dxy, us10y = fetch_macro()

    # FRED 정밀 매크로 수집
    fred_data = fetch_fred_macro()
    if fred_data.get("available"):
        if fred_data.get("vix") is not None:
            vix = fred_data["vix"]
        if fred_data.get("us10y") is not None:
            us10y = fred_data["us10y"]
        print(f"    → 오버라이드: VIX={vix:.2f} · US10Y={us10y:.2f}%")

    print("-" * 65)

    results = []
    start_time = time.time()
    scan_started_kst = history.kst_now()
    total_n = len(universe)

    n_workers = max(1, args.workers)

    # 진행 상황 공유 상태 (워커 스레드에서 갱신)
    state = {"done": 0}
    state_lock = threading.Lock()
    collected = []   # (universe 인덱스, 결과) — 마지막에 원래 순서로 정렬
    hist_rows = []   # (universe 인덱스, 이력 행)

    def _scan_one(ticker: str, name: str, market: str, sector: str,
                  asset_type: str, exchange: str):
        """종목 1개 스코어링. 실패 시 None 반환. (워커 스레드에서 실행)

        ETF 는 filing/value 를 계산하지 않는다. 개별기업 재무·공시 데이터가
        없어서다. 두 축을 빼고 tech/flow 만 재정규화해 총점을 낸다.

        macro 는 계속 계산해 아카이브에 남기지만 총점에는 넣지 않는다.
        regime 도 함께 남긴다 - 매매 계층의 국면 게이트가 그 값을 읽는다.
        """
        try:
            data = fetch_stock(ticker)
            if not data:
                return None

            hist = data["hist"]
            info = data["info"]

            tech, tech_r, r3m = calc_tech_score(hist)
            flow_score, flow_r = flow.calc_flow_score(hist)
            macro, macro_r, regime = calc_macro_score(vix, dxy, us10y, sector, fred_data)

            if asset_type == "ETF":
                value, value_r = None, []
                filing, filing_r = None, []
                total = calc_total_etf(tech, flow_score)
                cons = calc_consensus_etf(tech, flow_score)
                n_axes = 2
                # 네 축을 받는 함수라 뒤 두 자리에 tech/flow 를 다시 넣는다.
                # 사실상 tech/flow 평균이 되어 재정규화와 방향이 일치한다.
                ev, target = calc_ev_and_target(tech, flow_score, tech, flow_score, r3m)
            else:
                value, value_r = calc_value_score(info, sector)
                filing, filing_r = calc_filing_score(info, hist, ticker, market)
                total = calc_total(tech, flow_score, filing, value)
                cons = calc_consensus(tech, flow_score, filing, value)
                n_axes = 4
                ev, target = calc_ev_and_target(tech, flow_score, filing, value, r3m)

            signal = calc_signal(total, cons, n_axes=n_axes)
            hitl = calc_hitl(signal, total, tech)

            dash_row = {
                "t": ticker, "n": name, "m": market, "sec": sector,
                "at": asset_type, "ex": exchange,
                "tech": tech, "flow": flow_score, "macro": macro,
                "filing": filing, "value": value,
                "total": total, "consensus": cons, "signal": signal,
                "ev": ev, "target": target, "hitl": hitl,
                "regime": regime,
                "reasons": {
                    "tech": tech_r, "flow": flow_r, "macro": macro_r,
                    "filing": filing_r, "value": value_r,
                }
            }
            # 이력 행 생성 실패가 이미 완성된 대시보드 행을 버리지 않도록 격리한다
            try:
                hist_row = {
                    "ticker": ticker, "name": name, "market": market, "sector": sector,
                    "asset_type": asset_type,
                    "tech": tech, "macro": macro, "filing": filing, "value": value,
                    "total": total, "consensus": cons, "signal": signal,
                    "ev": ev, "target": target, "hitl": hitl,
                    "source": "live", "flow": flow_score, "regime": regime,
                    **history.price_fields(hist, info),
                }
            except Exception as e:
                print(f"[!] {ticker}: 이력 행 생성 실패 {str(e)[:60]}")
                hist_row = None
            return dash_row, hist_row
        except Exception as e:
            print(f"[!] {ticker}: 스코어링 실패 {str(e)[:60]}")
            return None
        finally:
            # 워커별 쓰로틀 (API rate limit 방어)
            time.sleep(args.sleep)

    print(f"[*] 스캔 시작 (동시 {n_workers} 스레드)")

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {
            pool.submit(_scan_one, ticker, name, market, sector, asset_type,
                        exchange): (i, ticker, name)
            for i, (ticker, name, market, sector, asset_type, exchange)
            in enumerate(universe, 1)
        }

        for fut in as_completed(futures):
            i, ticker, name = futures[fut]
            res = fut.result()

            with state_lock:
                state["done"] += 1
                done = state["done"]
                if res:
                    collected.append((i, res[0]))
                    if res[1] is not None:
                        hist_rows.append((i, res[1]))
                # 중간 저장 (200종목마다) - 크래시 방지
                snapshot = None
                if done % 200 == 0 and done < total_n:
                    snapshot = [r for _, r in sorted(collected, key=lambda x: x[0])]

            elapsed = time.time() - start_time
            rate = done / elapsed if elapsed > 0 else 0
            eta = (total_n - done) / rate if rate > 0 else 0
            pct = done / total_n * 100

            if total_n > 100 and done % 25 != 0 and done != total_n:
                # 대량 스캔 시 간략 표시
                print(f"\r[{done:5d}/{total_n}] {pct:5.1f}% · ETA {eta/60:5.1f}분 · {ticker:12s}",
                      end="", flush=True)
            else:
                print(f"\r[{done:5d}/{total_n}] {pct:5.1f}% · ETA {eta/60:5.1f}분 · {ticker:12s} ({name[:25]})...",
                      flush=True)

            if snapshot is not None:
                _save_intermediate(snapshot, vix, dxy, us10y, fred_data)

    # 스레드 완료 순서가 아니라 유니버스 원래 순서로 복원
    results = [r for _, r in sorted(collected, key=lambda x: x[0])]
    history_rows = [r for _, r in sorted(hist_rows, key=lambda x: x[0])]

    print()  # 진행률 라인 개행

    # 완결성 가드 - rate limit 등으로 대량 유실된 결과를 배포하지 않도록 차단
    ok_rate = len(results) / total_n if total_n else 0
    print(f"[*] 수집 {len(results)}/{total_n}종목 ({ok_rate*100:.1f}%)")
    if not is_scan_complete(len(results), total_n, args.min_success):
        print(f"[!] 수집률 {ok_rate*100:.1f}% < 기준 {args.min_success*100:.0f}% · "
              f"결과를 저장하지 않고 실패 처리합니다 (--workers 를 낮추세요)")
        sys.exit(1)

    if len(history_rows) != len(results):
        print(f"[!] 이력 행 {len(history_rows)}개 < 스코어 {len(results)}개 · "
              f"{len(results) - len(history_rows)}종목 가격 필드 산출 실패")

    # 이력 적재 - 가드를 통과한 결과만 기록한다
    try:
        hist_path = history.write_snapshot(history_rows, scan_started_kst)
        print(f"[*] 이력 기록: {hist_path} ({len(history_rows)}행)")
    except Exception as e:
        print(f"[!] 이력 기록 실패: {e}")
        sys.exit(1)

    # 아카이브를 기록한 뒤에 필터한다. 순서를 바꾸면 아카이브가 잘려
    # 백테스트의 SIGNAL 청산 판정이 불가능해진다.
    shown = filter_for_output(results, args.min_total)
    n_stock = sum(1 for r in shown if r.get("at") != "ETF")
    n_etf = len(shown) - n_stock
    print(f"[*] 출력 필터: {args.min_total}점 이상 → "
          f"{len(shown)}/{len(results)}종목 (주식 {n_stock}, ETF {n_etf})")

    # 지표 카드는 필터 이전 전체를 기준으로 센다 (scan_summary 참조).
    summary = scan_summary(results, shown)
    summary_json = json.dumps(summary, ensure_ascii=False)
    print(f"[*] 스캔 전체 기준: HITL {summary['hitl']}건 · "
          f"AVOID {summary['avoid']}건 (표시 목록에는 안 뜬다)")

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_data.js")
    fred_json = json.dumps(fred_data, ensure_ascii=False)
    js_content = f"""// AI 3-Month Stock Finder - Live Data
// Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
// Macro: VIX={vix:.2f}, DXY={dxy:.2f}, US10Y={us10y:.2f}%
// FMP: {'active' if FMP_KEY else 'off'} · FRED: {'active' if FRED_KEY else 'off'}
window.LIVE_MACRO = {{
  vix: {vix:.2f},
  dxy: {dxy:.2f},
  us10y: {us10y:.2f},
  generated_at: "{datetime.now().isoformat()}",
  fmp_active: {str(bool(FMP_KEY)).lower()},
  fred_active: {str(bool(FRED_KEY)).lower()},
  fred: {fred_json}
}};
window.LIVE_SUMMARY = {summary_json};
window.LIVE_STOCKS = {json.dumps(shown, ensure_ascii=False, indent=2)};
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(js_content)

    print()
    print("=" * 65)
    print(f"  [완료] {len(shown)}개 종목 · {os.path.basename(output_path)}")
    print(f"  STRONG_BUY: {sum(1 for r in shown if r['signal']=='STRONG_BUY')}개")
    print(f"  BUY:        {sum(1 for r in shown if r['signal']=='BUY')}개")
    print(f"  WATCH:      {sum(1 for r in shown if r['signal']=='WATCH')}개")
    print(f"  HITL 필요:  {sum(1 for r in shown if r['hitl'])}개")
    print("=" * 65)
    print("  브라우저에서 stock_finder_dashboard.html 을 새로고침하세요")
    print("=" * 65)


if __name__ == "__main__":
    main()
