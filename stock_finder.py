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
    close = hist_df["Close"].values.astype(float)
    high = hist_df["High"].values.astype(float)
    low = hist_df["Low"].values.astype(float)
    vol = hist_df["Volume"].values.astype(float)

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

    if len(vol) >= 80:
        recent_vol = np.mean(vol[-20:])
        prev_vol = np.mean(vol[-80:-20])
        if prev_vol > 0:
            vol_ratio = recent_vol / prev_vol
            if vol_ratio > 1.3:
                score += 8
                reasons.append(f"거래량 급증 · 최근 20일 평균 {vol_ratio:.1f}배")
            elif vol_ratio < 0.6:
                score -= 5
                reasons.append(f"거래량 감소 · {vol_ratio:.1f}배")

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


def fetch_us_etf_universe(min_aum: float = 1e9, limit: int = 3000) -> list:
    """FMP stock-screener 로 미국 ETF 조회.

    거래소 필터를 걸지 않는 것이 핵심이다. SPY·IWM 등 주요 ETF 는 NYSE Arca
    상장이라 exchange=nyse,nasdaq 으로 조회하면 QQQ 정도만 잡히고 대부분
    누락된다.
    """
    # v2: 미국 거래소 필터를 넣기 전 캐시에는 TSX 가 섞여 있다. 키를 바꿔
    # 그 캐시가 다시 읽히지 않게 한다.
    cache_key = f"us_etf_universe_v3_{int(min_aum)}_{limit}"
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
def calc_consensus(tech, macro, filing, value):
    return sum(1 for v in (tech, macro, filing, value) if v >= 70)


def calc_consensus_etf(tech, macro):
    """ETF 합의 개수. 축이 tech/macro 둘뿐이므로 최대 2 다.

    개수를 그대로 아카이브에 저장한다. 판정은 calc_signal 이 n_axes=2 로
    비율을 계산한다.
    """
    return sum(1 for v in (tech, macro) if v >= 70)


def calc_total(tech, macro, filing, value):
    return int(round(tech * 0.35 + macro * 0.20 + filing * 0.30 + value * 0.15))


# ETF 가중치. 주식 가중치에서 filing(0.30)·value(0.15) 를 빼고 남은 0.55 로
# 나눈 값이다. ETF 에는 개별기업 재무·공시 데이터가 없어 두 축을 계산할 수
# 없다. 중립값 50 으로 채우지 않는 것은 의도다 - 그러면 두 축이 22.5점으로
# 고정돼 70점을 넘으려면 macro 70일 때 tech 95.7 이상이 필요한데, 관측된
# 개별주식 최고점이 77인 분포에서는 사실상 나오지 않는다.
ETF_TECH_WEIGHT = 0.35 / 0.55
ETF_MACRO_WEIGHT = 0.20 / 0.55


def calc_total_etf(tech, macro):
    """ETF 종합점수. tech/macro 두 축만 쓰고 가중치를 재정규화한다."""
    return int(round(tech * ETF_TECH_WEIGHT + macro * ETF_MACRO_WEIGHT))


def calc_signal(total, cons, n_axes=4):
    """종합점수와 합의 비율로 신호를 낸다.

    cons 는 70점 이상인 축의 개수, n_axes 는 축의 총 개수다. 개수가 아니라
    비율로 판정하는 것은 ETF 때문이다 - ETF 는 filing/value 데이터가 없어
    축이 tech/macro 둘뿐이라, 개수 기준(cons>=3)으로는 BUY 가 영원히 나오지
    않는다.

    임계 0.75 / 0.50 은 주식의 3/4, 2/4 와 정확히 같다. 주식 판정은 이
    변경으로 한 건도 바뀌지 않는다 (tests/test_scoring.py 회귀 테스트).
    """
    ratio = cons / n_axes if n_axes else 0.0
    if total >= 80 and ratio >= 0.75:
        return "STRONG_BUY"
    if total >= 70 and ratio >= 0.75:
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


def filter_for_output(rows: list, min_total: int, min_total_etf: int) -> list:
    """대시보드·콘솔에 낼 행만 남긴다.

    아카이브(history/*.csv)에는 적용하지 않는다. exit_rules.evaluate() 가
    보유 종목의 그날 total 이 exit_total 미만이면 SIGNAL 청산하는데, 점수가
    떨어진 행이 아카이브에서 사라지면 그 판정을 할 수 없게 된다.

    ETF 에 별도 임계를 두는 것은 두 점수가 같은 척도가 아니기 때문이다.
    ETF 는 분산 효과로 변동성이 낮아 tech 점수가 높게 나오고, filing/value
    없이 두 축만 재정규화하므로 분포가 위로 밀린다. 2026-08-22 첫 실전
    스캔 실측으로 같은 70점 선에서 주식은 4.7%(46/981), ETF 는
    16.8%(136/811)가 통과해 대시보드가 ETF 로 뒤덮였다.

    at 키가 없으면 STOCK 으로 본다. 옛 행에는 그 시절 유니버스가 전부
    개별주식이었다.
    """
    out = []
    for r in rows:
        total = r.get("total")
        if total is None:
            continue
        floor = min_total_etf if r.get("at") == "ETF" else min_total
        if total >= floor:
            out.append(r)
    return out


def calc_ev_and_target(tech, macro, filing, value, r3m) -> tuple[float, int]:
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
    tech, macro, filing, value, r3m = _safe(tech), _safe(macro), _safe(filing), _safe(value), _safe(r3m)
    strength = (tech * 0.4 + filing * 0.3 + macro * 0.2 + value * 0.1) / 100
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
                        "  아카이브에는 적용되지 않는다")
    p.add_argument("--min-total-etf", type=int, default=78,
                   help="ETF 출력 최소 종합점수 (기본 78).\n"
                        "  ETF 는 두 축 재정규화라 점수가 위로 밀린다.\n"
                        "  2026-08-22 실측 기준 77점에 군집이 있고 78점부터\n"
                        "  통과율이 주식(4.7%%)과 비슷해진다")
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
        없어서다. 두 축을 빼고 tech/macro 만 재정규화해 총점을 낸다.
        """
        try:
            data = fetch_stock(ticker)
            if not data:
                return None

            hist = data["hist"]
            info = data["info"]

            tech, tech_r, r3m = calc_tech_score(hist)
            macro, macro_r, regime = calc_macro_score(vix, dxy, us10y, sector, fred_data)

            if asset_type == "ETF":
                value, value_r = None, []
                filing, filing_r = None, []
                total = calc_total_etf(tech, macro)
                cons = calc_consensus_etf(tech, macro)
                n_axes = 2
                # 네 축을 받는 함수라 뒤 두 자리에 tech/macro 를 다시 넣는다.
                # 사실상 tech/macro 평균이 되어 재정규화와 방향이 일치한다.
                ev, target = calc_ev_and_target(tech, macro, tech, macro, r3m)
            else:
                value, value_r = calc_value_score(info, sector)
                filing, filing_r = calc_filing_score(info, hist, ticker, market)
                total = calc_total(tech, macro, filing, value)
                cons = calc_consensus(tech, macro, filing, value)
                n_axes = 4
                ev, target = calc_ev_and_target(tech, macro, filing, value, r3m)

            signal = calc_signal(total, cons, n_axes=n_axes)
            hitl = calc_hitl(signal, total, tech)

            dash_row = {
                "t": ticker, "n": name, "m": market, "sec": sector,
                "at": asset_type, "ex": exchange,
                "tech": tech, "macro": macro, "filing": filing, "value": value,
                "total": total, "consensus": cons, "signal": signal,
                "ev": ev, "target": target, "hitl": hitl,
                "reasons": {
                    "tech": tech_r, "macro": macro_r,
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
                    "source": "live",
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
    shown = filter_for_output(results, args.min_total, args.min_total_etf)
    n_stock = sum(1 for r in shown if r.get("at") != "ETF")
    n_etf = len(shown) - n_stock
    print(f"[*] 출력 필터: 주식 {args.min_total}점 이상 · "
          f"ETF {args.min_total_etf}점 이상 → "
          f"{len(shown)}/{len(results)}종목 (주식 {n_stock}, ETF {n_etf})")

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
