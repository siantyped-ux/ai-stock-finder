"""
AI 3-Month Stock Finder — 실시간 데이터 수집 & 스코어링
xlsx v5 시스템의 4축 컨센서스 로직을 실제 데이터로 적용

의존성:
    pip install yfinance numpy requests

.env 파일에 API 키 입력 (선택):
    FMP_API_KEY=xxx   # 미국 SEC 공시 (13F, Form 4, 8-K)
    DART_API_KEY=xxx  # 한국 공시 (5%보고, 임원매매, 자기주식)

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
DART_KEY = (ENV.get("DART_API_KEY") or os.environ.get("DART_API_KEY", "")).strip()
FRED_KEY = (ENV.get("FRED_API_KEY") or os.environ.get("FRED_API_KEY", "")).strip()

FMP_BASE = "https://financialmodelingprep.com/stable"
DART_BASE = "https://opendart.fss.or.kr/api"
FRED_BASE = "https://api.stlouisfed.org/fred"

# 한국 종목의 corp_code 매핑 (DART 조회에 필요)
# 런타임에 load_dart_corpcode()가 opendart corpCode.xml에서 전체 상장사를 병합
CORP_CODE_MAP: dict[str, str] = {
    "005930.KS": "00126380",  # 삼성전자
    "000660.KS": "00164779",  # SK하이닉스
    "035420.KS": "00266961",  # NAVER
    "035720.KS": "00918444",  # 카카오
    "005380.KS": "00164742",  # 현대차
    "373220.KS": "01515323",  # LG에너지솔루션
    "207940.KS": "00877059",  # 삼성바이오로직스
    "068270.KS": "00421045",  # 셀트리온
    "105560.KS": "00688996",  # KB금융
    "055550.KS": "00518902",  # 신한지주
    "010130.KS": "00113059",  # 고려아연
}


# ─── 종목 유니버스 (폴백용 · pykrx/FMP 실패 시 사용) ─────────
FALLBACK_UNIVERSE = [
    # 미국
    ("NVDA",  "NVIDIA",              "US", "AI/반도체"),
    ("MSFT",  "Microsoft",           "US", "Software"),
    ("AMD",   "AMD",                 "US", "AI/반도체"),
    ("META",  "Meta Platforms",      "US", "인터넷"),
    ("GOOGL", "Alphabet",            "US", "인터넷"),
    ("AMZN",  "Amazon",              "US", "E-commerce/Cloud"),
    ("TSLA",  "Tesla",               "US", "EV/자동차"),
    ("AVGO",  "Broadcom",            "US", "반도체"),
    ("LLY",   "Eli Lilly",           "US", "제약/바이오"),
    ("COST",  "Costco",              "US", "소매"),
    ("CRWD",  "CrowdStrike",         "US", "사이버보안"),
    ("UBER",  "Uber",                "US", "플랫폼"),
    ("PANW",  "Palo Alto Networks",  "US", "사이버보안"),
    ("NOW",   "ServiceNow",          "US", "Software"),
    ("AXP",   "American Express",    "US", "금융"),
    ("NFLX",  "Netflix",             "US", "미디어"),
    ("ORCL",  "Oracle",              "US", "Software"),
    ("MU",    "Micron",              "US", "반도체"),
    ("AAPL",  "Apple",               "US", "IT하드웨어"),
    ("JPM",   "JPMorgan",            "US", "금융"),
    ("V",     "Visa",                "US", "금융"),
    ("F",     "Ford Motor",          "US", "자동차"),
    ("INTC",  "Intel",               "US", "반도체"),
    # 한국
    ("005930.KS", "삼성전자",           "KR", "반도체"),
    ("000660.KS", "SK하이닉스",         "KR", "반도체"),
    ("035420.KS", "NAVER",             "KR", "인터넷"),
    ("035720.KS", "카카오",             "KR", "인터넷"),
    ("005380.KS", "현대차",             "KR", "자동차"),
    ("373220.KS", "LG에너지솔루션",     "KR", "2차전지"),
    ("207940.KS", "삼성바이오로직스",   "KR", "바이오"),
    ("068270.KS", "셀트리온",           "KR", "바이오"),
    ("105560.KS", "KB금융",             "KR", "금융"),
    ("055550.KS", "신한지주",           "KR", "금융"),
    ("010130.KS", "고려아연",           "KR", "비철금속"),
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


# ─── DART API (한국 공시) ─────────────────────────────────────
def load_dart_corpcode() -> dict[str, str]:
    """
    DART OpenAPI corpCode.xml(ZIP)을 받아 티커→corp_code 매핑을 반환.
    - stock_code가 있는 상장사만 대상 (비상장 법인 제외)
    - KOSPI(.KS), KOSDAQ(.KQ) 두 suffix 모두 등록 (한국 단축코드는 6자리 유일)
    - 7일 TTL로 .cache/dart_corpcode.json 캐시
    - DART_KEY 없거나 다운로드 실패 시 빈 dict 반환 (하드코딩 매핑 폴백)
    """
    if not DART_KEY:
        return {}

    cache_key = "dart_corpcode"
    cached = _load_cache(cache_key, ttl_hours=24 * 7)
    if isinstance(cached, dict) and cached:
        return cached

    import zipfile
    import xml.etree.ElementTree as ET

    try:
        r = requests.get(
            f"{DART_BASE}/corpCode.xml",
            params={"crtfc_key": DART_KEY},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"    [!] DART corpCode HTTP {r.status_code}")
            return {}
        content = r.content
        # ZIP 매직 넘버 확인 (에러 시 JSON 응답)
        if content[:2] != b"PK":
            try:
                err = r.json()
                print(f"    [!] DART corpCode 오류: {err.get('message', 'unknown')}")
            except Exception:
                print("    [!] DART corpCode: ZIP 응답 아님")
            return {}

        mapping: dict[str, str] = {}
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            xml_name = next(
                (n for n in zf.namelist() if n.upper().endswith(".XML")), None
            )
            if xml_name is None:
                return {}
            with zf.open(xml_name) as xf:
                for _ev, elem in ET.iterparse(xf, events=("end",)):
                    if elem.tag != "list":
                        continue
                    stock_code = (elem.findtext("stock_code") or "").strip()
                    corp_code = (elem.findtext("corp_code") or "").strip()
                    if (
                        stock_code
                        and corp_code
                        and stock_code.isdigit()
                        and len(stock_code) == 6
                    ):
                        mapping[f"{stock_code}.KS"] = corp_code
                        mapping[f"{stock_code}.KQ"] = corp_code
                    elem.clear()

        if mapping:
            _save_cache(cache_key, mapping)
            print(f"    [OK] DART 회사코드 {len(mapping) // 2}개 상장사 로드")
        return mapping
    except Exception as e:
        print(f"    [!] DART corpCode 로드 실패: {str(e)[:80]}")
        return {}


def _dart_get(endpoint: str, params: dict = None) -> Optional[dict]:
    if not DART_KEY:
        return None
    params = params or {}
    params["crtfc_key"] = DART_KEY
    try:
        r = requests.get(f"{DART_BASE}{endpoint}", params=params, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "000":
                return data
        return None
    except Exception:
        return None


def fetch_dart_filing_signals(ticker: str) -> dict:
    """한국 종목의 5%보고, 임원매매, 자기주식, 증자 시그널"""
    signals = {"available": False, "reasons": [], "score_delta": 0}
    corp_code = CORP_CODE_MAP.get(ticker)
    if not corp_code:
        return signals

    bgn_de = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
    end_de = datetime.now().strftime("%Y%m%d")

    # 1) 대량보유(5%) 최근 90일
    major = _dart_get("/majorstock.json", {"corp_code": corp_code})
    if major and major.get("list"):
        signals["available"] = True
        recent = [x for x in major["list"] if x.get("rcept_dt", "0") >= bgn_de]
        if recent:
            new_entries = sum(1 for x in recent if "신규" in str(x.get("report_tp", "")))
            purpose_mgmt = sum(1 for x in recent if "경영" in str(x.get("stkrt_int", "")))
            if new_entries > 0:
                signals["score_delta"] += 10
                signals["reasons"].append(f"5%보고: 신규 진입 {new_entries}건 (최근 90일)")
            if purpose_mgmt > 0:
                signals["score_delta"] += 8
                signals["reasons"].append(f"5%보고: 경영참여 목적 {purpose_mgmt}건 (activist 후보)")
            elif recent:
                signals["reasons"].append(f"5%보고: 최근 90일 {len(recent)}건")

    # 2) 임원·주요주주 소유주식 (매매 클러스터)
    elestock = _dart_get("/elestock.json", {"corp_code": corp_code})
    if elestock and elestock.get("list"):
        signals["available"] = True
        recent = [x for x in elestock["list"] if x.get("rcept_dt", "0") >= bgn_de]
        buys = sum(1 for x in recent if "취득" in str(x.get("sp_stock_lmp_cnt_tp", "")))
        sells = sum(1 for x in recent if "처분" in str(x.get("sp_stock_lmp_cnt_tp", "")))
        # 대안 필드: isu_dcrs_srtdt / isu_dcrs_ostk_cnt 부호로 판정
        for x in recent:
            delta = x.get("isu_dcrs_ostk_cnt", "0")
            try:
                d = int(str(delta).replace(",", "").replace("-", "-").strip() or "0")
                if d > 0:
                    buys += 1
                elif d < 0:
                    sells += 1
            except Exception:
                pass
        net = buys - sells
        if buys >= 3 and net > 0:
            signals["score_delta"] += 10
            signals["reasons"].append(f"임원매매: 매수 클러스터 (매수{buys}/매도{sells}, 90일)")
        elif net < -3:
            signals["score_delta"] -= 8
            signals["reasons"].append(f"임원매매: 매도 우세 (매수{buys}/매도{sells})")

    # 3) 자기주식 취득 (긍정 시그널)
    treasury = _dart_get("/tsstkAqDecsn.json",
                        {"corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de})
    if treasury and treasury.get("list"):
        signals["available"] = True
        signals["score_delta"] += 8
        signals["reasons"].append(f"자기주식 취득 결정 {len(treasury['list'])}건 (오버행 해소)")

    # 4) 유상증자 (부정 시그널 - 희석)
    piic = _dart_get("/piicDecsn.json",
                    {"corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de})
    if piic and piic.get("list"):
        signals["available"] = True
        signals["score_delta"] -= 8
        signals["reasons"].append(f"유상증자 결정 {len(piic['list'])}건 (희석 리스크)")

    # 5) 전환사채(CB) 발행 (부정 시그널)
    cb = _dart_get("/cvbdIsDecsn.json",
                  {"corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de})
    if cb and cb.get("list"):
        signals["available"] = True
        signals["score_delta"] -= 5
        signals["reasons"].append(f"CB 발행 {len(cb['list'])}건 (희석 잠재)")

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


def fetch_us_universe(min_market_cap: float = 5e9, limit: int = 5000) -> list:
    """FMP stock-screener로 NYSE+NASDAQ 상장 종목 조회 (ETF 제외)"""
    cache_key = f"us_universe_{int(min_market_cap)}_{limit}"
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
            universe.append((symbol, name[:40], "US", sector_kr))

        _save_cache(cache_key, universe)
        print(f"    [OK] 미국 {len(universe)}종목 수집 완료")
        return universe
    except Exception as e:
        print(f"    [!] FMP 조회 실패: {e}")
        return []


# KOSPI 시총 상위 확장 리스트 (pykrx/KRX API 실패 시 폴백)
KOSPI_EXPANDED = [
    ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("373220", "LG에너지솔루션"),
    ("207940", "삼성바이오로직스"), ("005380", "현대차"), ("000270", "기아"),
    ("035420", "NAVER"), ("068270", "셀트리온"), ("105560", "KB금융"),
    ("005490", "POSCO홀딩스"), ("055550", "신한지주"), ("012330", "현대모비스"),
    ("035720", "카카오"), ("006400", "삼성SDI"), ("028260", "삼성물산"),
    ("015760", "한국전력"), ("010130", "고려아연"), ("034730", "SK"),
    ("003670", "포스코퓨처엠"), ("018260", "삼성에스디에스"), ("032830", "삼성생명"),
    ("138040", "메리츠금융지주"), ("011200", "HMM"), ("086790", "하나금융지주"),
    ("017670", "SK텔레콤"), ("011170", "롯데케미칼"), ("267260", "HD현대일렉트릭"),
    ("090430", "아모레퍼시픽"), ("267250", "HD현대중공업"), ("323410", "카카오뱅크"),
    ("000810", "삼성화재"), ("010950", "S-Oil"), ("011070", "LG이노텍"),
    ("010140", "삼성중공업"), ("329180", "HD현대미포"), ("024110", "기업은행"),
    ("009150", "삼성전기"), ("042660", "한화오션"), ("128940", "한미약품"),
    ("086520", "에코프로"), ("247540", "에코프로비엠"), ("251270", "넷마블"),
    ("036570", "엔씨소프트"), ("018880", "한온시스템"), ("000720", "현대건설"),
    ("139480", "이마트"), ("000100", "유한양행"), ("032640", "LG유플러스"),
    ("375500", "DL이앤씨"), ("096770", "SK이노베이션"), ("079550", "LIG넥스원"),
    ("006360", "GS건설"), ("078930", "GS"), ("036460", "한국가스공사"),
    ("016360", "삼성증권"), ("005940", "NH투자증권"), ("026960", "동서"),
    ("010060", "OCI홀딩스"), ("000210", "DL"),
    # 008560 메리츠증권: 2022 상장폐지(메리츠금융지주로 통합) — 제외
    ("006800", "미래에셋증권"), ("196170", "알테오젠"), ("064350", "현대로템"),
    ("005830", "DB손해보험"), ("002380", "KCC"), ("011790", "SKC"),
    ("006650", "대한유화"), ("000670", "영풍"), ("138930", "BNK금융지주"),
    ("006260", "LS"), ("008770", "호텔신라"), ("009830", "한화솔루션"),
    ("139130", "DGB금융지주"), ("011780", "금호석유"), ("078340", "컴투스"),
    ("079160", "CJ CGV"), ("033780", "KT&G"), ("005810", "풍산"),
    ("002960", "한국쉘석유"), ("000120", "CJ대한통운"),
    ("120110", "코오롱인더"),
    ("069620", "대웅제약"), ("185750", "종근당"),
    ("006840", "AK홀딩스"), ("008930", "한미사이언스"), ("002790", "아모레G"),
    ("069260", "휴켐스"), ("000080", "하이트진로"),
    # 아래 티커는 상장폐지/합병으로 yfinance 조회 불가 — 제외:
    # 002270 롯데푸드(2022 롯데제과에 흡수합병)
    # 010620 HD현대미포조선(329180 HD현대미포로 대체)
    # 003410 쌍용C&E(2024 상장폐지)
    # 042670 두산인프라코어(HD현대인프라코어로 개편, 티커 변경)
    # 036490 SK바이오사이언스(yfinance 데이터 이슈)
    ("030200", "KT"), ("012450", "한화에어로스페이스"), ("047810", "한국항공우주"),
    ("051910", "LG화학"), ("051900", "LG생활건강"), ("066570", "LG전자"),
    ("003550", "LG"), ("000150", "두산"), ("034020", "두산에너빌리티"),
    ("241560", "두산밥캣"), ("035250", "강원랜드"), ("114090", "GKL"),
    ("020150", "롯데에너지머티리얼즈"), ("004020", "현대제철"),
    # 003600 SK디스커버리: yfinance 데이터 이슈로 제외
    ("069960", "현대백화점"), ("023530", "롯데쇼핑"),
    # 011200 HMM 은 위(857행)에 이미 포함 — 중복 제거
    ("012750", "에스원"), ("035150", "백광산업"), ("018670", "삼성E&A"),
    ("003490", "대한항공"), ("020560", "아시아나항공"), ("047040", "대우건설"),
    ("088980", "맥쿼리인프라"), ("161390", "한국타이어앤테크놀로지"), ("161890", "한국콜마"),
]


def fetch_kr_universe(min_market_cap: float = 3e11) -> list:
    """
    한국 KOSPI 유니버스 조회
    pykrx 우선 시도, 실패 시 KOSPI 시총 상위 확장 하드코딩 리스트 사용
    (시가총액 필터는 yfinance info로 스캔 중 적용 - main() 참조)
    """
    cache_key = f"kr_universe_{int(min_market_cap)}"
    cached = _load_cache(cache_key)
    if cached:
        print(f"    [cache] 한국 유니버스 {len(cached)}종목 (캐시)")
        return cached

    # pykrx 시도 (인코딩 이슈 있음). pykrx 내부의 print 오류 노이즈는 억제.
    try:
        from pykrx import stock as pykrx_stock
        print(f"    [*] pykrx KOSPI 유니버스 조회 시도...")
        cap_df = None
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for delta in range(1, 30):
                d = (datetime.now() - timedelta(days=delta)).strftime("%Y%m%d")
                try:
                    df = pykrx_stock.get_market_cap_by_ticker(d, market="KOSPI")
                    if df is not None and len(df) > 100:
                        cap_df = df
                        break
                except Exception:
                    continue

        if cap_df is not None and len(cap_df) > 100:
            # 시가총액 컬럼 접근 (한글 인코딩 이슈 우회)
            cap_col = None
            for c in cap_df.columns:
                if "시가총액" in str(c) or "cap" in str(c).lower() or "Mkt" in str(c):
                    cap_col = c
                    break
            if cap_col is None and len(cap_df.columns) >= 2:
                cap_col = cap_df.columns[1]  # 두 번째 컬럼이 대체로 시총

            if cap_col:
                filtered = cap_df[cap_df[cap_col] >= min_market_cap]
                universe = []
                for tk in filtered.index:
                    try:
                        name = pykrx_stock.get_market_ticker_name(tk)
                        universe.append((f"{tk}.KS", name, "KR", "미분류"))
                    except Exception:
                        universe.append((f"{tk}.KS", tk, "KR", "미분류"))
                if universe:
                    _save_cache(cache_key, universe)
                    print(f"    [OK] pykrx KOSPI {len(universe)}종목 수집")
                    return universe
        print("    [!] pykrx 조회 불가 → 확장 하드코딩 리스트 사용")
    except Exception as e:
        print(f"    [!] pykrx 예외: {str(e)[:60]} → 확장 하드코딩 리스트 사용")

    # 폴백: 확장 하드코딩 (약 120종목)
    universe = [(f"{code}.KS", name, "KR", "미분류") for code, name in KOSPI_EXPANDED]
    _save_cache(cache_key, universe)
    print(f"    [OK] KOSPI 확장 리스트 {len(universe)}종목 사용")
    return universe


def load_universe(market: str = "ALL",
                  min_us_cap: float = 5e9,
                  min_kr_cap: float = 3e11,
                  test_mode: bool = False) -> list:
    """
    유니버스 로드
    market: "US" | "KR" | "ALL"
    test_mode: True 시 폴백 34개 종목 사용
    """
    if test_mode:
        return FALLBACK_UNIVERSE

    universe = []
    if market in ("US", "ALL"):
        us = fetch_us_universe(min_market_cap=min_us_cap)
        if not us:
            us = [s for s in FALLBACK_UNIVERSE if s[2] == "US"]
            print(f"    [폴백] 미국 하드코딩 {len(us)}종목 사용")
        universe.extend(us)

    if market in ("KR", "ALL"):
        kr = fetch_kr_universe(min_market_cap=min_kr_cap)
        if not kr:
            kr = [s for s in FALLBACK_UNIVERSE if s[2] == "KR"]
            print(f"    [폴백] 한국 하드코딩 {len(kr)}종목 사용")
        universe.extend(kr)

    return universe


# ─── 공시 스코어 (API 우선, 실패 시 프록시) ───────────────────
def calc_filing_score(info: dict, hist_df, ticker: str, market: str) -> tuple[int, list[str]]:
    """API 데이터가 있으면 우선 사용, 없으면 yfinance 프록시로 대체"""
    score = 55
    reasons = []

    # 실제 API 시그널 우선 시도
    api_signals = {}
    if market == "US" and FMP_KEY:
        api_signals = fetch_fmp_filing_signals(ticker)
    elif market == "KR" and DART_KEY:
        api_signals = fetch_dart_filing_signals(ticker)

    if api_signals.get("available"):
        score += api_signals["score_delta"]
        reasons.extend(api_signals["reasons"])
        source_tag = "FMP" if market == "US" else "DART"
        reasons.append(f"* {source_tag} 실시간 공시 반영")
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

        if market == "US":
            if not FMP_KEY:
                reasons.append("* FMP API 키 미설정 · 프록시 사용")
            else:
                reasons.append("* FMP 최근 공시 없음 · 프록시 보조")
        elif market == "KR":
            if not DART_KEY:
                reasons.append("* DART API 키 미설정 · 프록시 사용")
            elif ticker not in CORP_CODE_MAP:
                reasons.append("* DART corp_code 매핑 없음 · 프록시 사용")
            else:
                reasons.append("* DART 최근 공시 없음 · 프록시 보조")

    if not reasons:
        reasons.append("공시 데이터 부족")

    return int(np.clip(score, 0, 100)), reasons[:5]


# ─── 종합 판정 ────────────────────────────────────────────────
def calc_consensus(tech, macro, filing, value):
    return sum(1 for v in (tech, macro, filing, value) if v >= 70)


def calc_total(tech, macro, filing, value):
    return int(round(tech * 0.35 + macro * 0.20 + filing * 0.30 + value * 0.15))


def calc_signal(total, cons):
    if total >= 80 and cons >= 3:
        return "STRONG_BUY"
    if total >= 70 and cons >= 3:
        return "BUY"
    if total >= 60 and cons >= 2:
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
  dart_active: {str(bool(DART_KEY)).lower()},
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
    p.add_argument("--market", choices=["US", "KR", "ALL"], default="ALL",
                   help="스캔 대상 시장 (기본: ALL)")
    p.add_argument("--min-us-cap", type=float, default=5e9,
                   help="미국 최소 시가총액 USD (기본: 5e9 = $5B)\n"
                        "  1e9 = $1B (~2500종목, 2시간+)\n"
                        "  5e9 = $5B (~800종목, 30분)\n"
                        "  1e10 = $10B (~500종목, 20분)")
    p.add_argument("--min-kr-cap", type=float, default=3e11,
                   help="한국 최소 시가총액 KRW (기본: 3e11 = 3000억)\n"
                        "  1e11 = 1000억 (~500종목)\n"
                        "  3e11 = 3000억 (~250종목)\n"
                        "  1e12 = 1조 (~150종목)")
    p.add_argument("--limit", type=int, default=0,
                   help="시장별 상위 N개로 제한 (0=제한없음)")
    p.add_argument("--test", action="store_true",
                   help="테스트 모드 (기존 34개 폴백 종목)")
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

    # --all 옵션 시 시총 필터 제거
    if args.all:
        args.min_us_cap = 0
        args.min_kr_cap = 0

    print("=" * 65)
    print("  AI 3-Month Stock Finder v5 · 전체 유가증권 스캔")
    print("=" * 65)

    # API 키 상태 표시
    print(f"[env] FMP API : {'✓ 활성' if FMP_KEY else '✗ 미설정 (프록시 사용)'}")
    print(f"[env] DART API: {'✓ 활성' if DART_KEY else '✗ 미설정 (프록시 사용)'}")
    print(f"[env] FRED API: {'✓ 활성 (매크로 정밀화)' if FRED_KEY else '✗ 미설정 (yfinance 사용)'}")
    if not FMP_KEY and not DART_KEY and not FRED_KEY:
        print("      → .env 파일에 API 키를 추가하면 실제 시그널이 반영됩니다")
    print("-" * 65)

    # DART 회사코드 매핑 확장 (전체 상장사 corp_code 자동 로드)
    if DART_KEY:
        print("[*] DART 회사코드 매핑 로드...")
        dynamic_map = load_dart_corpcode()
        if dynamic_map:
            CORP_CODE_MAP.update(dynamic_map)
            print(f"    총 corp_code 매핑: {len({v for v in CORP_CODE_MAP.values()})}개 상장사")
        else:
            print("    [!] 동적 매핑 실패 — 하드코딩 11종목만 사용")
        print("-" * 65)

    # 유니버스 로드
    print("[*] 종목 유니버스 로드...")
    universe = load_universe(
        market=args.market,
        min_us_cap=args.min_us_cap,
        min_kr_cap=args.min_kr_cap,
        test_mode=args.test,
    )
    if args.limit > 0:
        us_only = [s for s in universe if s[2] == "US"][:args.limit]
        kr_only = [s for s in universe if s[2] == "KR"][:args.limit]
        universe = us_only + kr_only
        print(f"    [limit] 시장별 상위 {args.limit}개로 제한 → 총 {len(universe)}종목")

    if not universe:
        print("[!] 유니버스가 비었습니다. --test 옵션으로 폴백 모드 시도")
        sys.exit(1)

    n_us = sum(1 for s in universe if s[2] == "US")
    n_kr = sum(1 for s in universe if s[2] == "KR")
    est_sec = len(universe) * (2.5 + args.sleep) / max(1, args.workers)
    est_min = est_sec / 60
    print(f"    총 {len(universe)}종목 (US: {n_us}, KR: {n_kr})")
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

    def _scan_one(ticker: str, name: str, market: str, sector: str):
        """종목 1개 스코어링. 실패 시 None 반환. (워커 스레드에서 실행)"""
        try:
            data = fetch_stock(ticker)
            if not data:
                return None

            hist = data["hist"]
            info = data["info"]

            tech, tech_r, r3m = calc_tech_score(hist)
            macro, macro_r, regime = calc_macro_score(vix, dxy, us10y, sector, fred_data)
            value, value_r = calc_value_score(info, sector)
            filing, filing_r = calc_filing_score(info, hist, ticker, market)

            total = calc_total(tech, macro, filing, value)
            cons = calc_consensus(tech, macro, filing, value)
            signal = calc_signal(total, cons)
            hitl = calc_hitl(signal, total, tech)
            ev, target = calc_ev_and_target(tech, macro, filing, value, r3m)

            dash_row = {
                "t": ticker, "n": name, "m": market, "sec": sector,
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
            pool.submit(_scan_one, ticker, name, market, sector): (i, ticker, name)
            for i, (ticker, name, market, sector) in enumerate(universe, 1)
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

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_data.js")
    fred_json = json.dumps(fred_data, ensure_ascii=False)
    js_content = f"""// AI 3-Month Stock Finder - Live Data
// Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
// Macro: VIX={vix:.2f}, DXY={dxy:.2f}, US10Y={us10y:.2f}%
// FMP: {'active' if FMP_KEY else 'off'} · DART: {'active' if DART_KEY else 'off'} · FRED: {'active' if FRED_KEY else 'off'}
window.LIVE_MACRO = {{
  vix: {vix:.2f},
  dxy: {dxy:.2f},
  us10y: {us10y:.2f},
  generated_at: "{datetime.now().isoformat()}",
  fmp_active: {str(bool(FMP_KEY)).lower()},
  dart_active: {str(bool(DART_KEY)).lower()},
  fred_active: {str(bool(FRED_KEY)).lower()},
  fred: {fred_json}
}};
window.LIVE_STOCKS = {json.dumps(results, ensure_ascii=False, indent=2)};
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(js_content)

    print()
    print("=" * 65)
    print(f"  [완료] {len(results)}개 종목 · {os.path.basename(output_path)}")
    print(f"  STRONG_BUY: {sum(1 for r in results if r['signal']=='STRONG_BUY')}개")
    print(f"  BUY:        {sum(1 for r in results if r['signal']=='BUY')}개")
    print(f"  WATCH:      {sum(1 for r in results if r['signal']=='WATCH')}개")
    print(f"  HITL 필요:  {sum(1 for r in results if r['hitl'])}개")
    print("=" * 65)
    print("  브라우저에서 stock_finder_dashboard.html 을 새로고침하세요")
    print("=" * 65)


if __name__ == "__main__":
    main()
