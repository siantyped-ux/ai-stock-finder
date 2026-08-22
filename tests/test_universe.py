"""유니버스 필터 순수 함수 테스트.

설계: docs/superpowers/specs/2026-08-22-us-etf-universe-design.md
"""
import pytest

import stock_finder as sf


@pytest.mark.parametrize("name", [
    "Direxion Daily Semiconductor Bull 3X Shares",
    "ProShares Ultra QQQ",
    "ProShares UltraPro Short QQQ",
    "ProShares Short S&P500",
    "Direxion Daily Financial Bear 3X Shares",
    "ProShares UltraShort Bloomberg Crude Oil",
    "Simplify Inverse Treasury ETF",
    "Amplify 2X Covered Call ETF",
])
def test_rejects_leveraged_and_inverse(name):
    assert sf.is_leveraged_or_inverse(name) is True


@pytest.mark.parametrize("name", [
    "SPDR S&P 500 ETF Trust",
    "Invesco QQQ Trust",
    "iShares Russell 2000 ETF",
    "Vanguard Total Stock Market ETF",
    "Schwab U.S. Dividend Equity ETF",
    "SPDR Gold Shares",
])
def test_accepts_plain_etfs(name):
    assert sf.is_leveraged_or_inverse(name) is False


@pytest.mark.parametrize("name", [
    "iShares Short Treasury Bond ETF",
    "Vanguard Short-Term Bond ETF",
    "SPDR Portfolio Short Term Corporate Bond ETF",
    "iShares 1-3 Year Treasury Bond ETF",
])
def test_keeps_short_duration_bond_etfs(name):
    """'Short' 가 만기를 뜻하는 채권 ETF 는 인버스가 아니다.

    이 구분이 없으면 단기채 ETF 수십 개가 통째로 빠진다.
    """
    assert sf.is_leveraged_or_inverse(name) is False


def test_matching_is_case_insensitive():
    assert sf.is_leveraged_or_inverse("direxion daily 3x bull") is True


def test_does_not_match_inside_a_word():
    """'Shortline' 같은 단어 안의 우연한 일치를 걸러내면 안 된다."""
    assert sf.is_leveraged_or_inverse("Shortline Logistics ETF") is False


def test_handles_empty_name():
    assert sf.is_leveraged_or_inverse("") is False


# ─── ETF 유니버스 파싱 (네트워크 없이 응답만 넣는다) ──────────
ETF_RESPONSE = [
    # SPY 는 NYSE Arca 상장인데 FMP 가 AMEX 로 준다. 이 케이스를 놓치면
    # 주요 ETF 가 통째로 빠진다.
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "marketCap": 6.2e11,
     "exchangeShortName": "AMEX"},
    {"symbol": "QQQ", "name": "Invesco QQQ Trust", "marketCap": 3.5e11,
     "exchangeShortName": "NASDAQ"},
    {"symbol": "TQQQ", "name": "ProShares UltraPro QQQ", "marketCap": 2.6e10,
     "exchangeShortName": "NASDAQ"},
    {"symbol": "TINY", "name": "Tiny Niche ETF", "marketCap": 4.0e8,
     "exchangeShortName": "AMEX"},
    {"symbol": "", "name": "No Symbol ETF", "marketCap": 9.9e10,
     "exchangeShortName": "AMEX"},
]


def test_parse_etf_rows_keeps_plain_large_etfs():
    rows = sf.parse_etf_rows(ETF_RESPONSE, min_aum=1e9)
    tickers = [r[0] for r in rows]
    assert tickers == ["SPY", "QQQ"]


def test_parse_etf_rows_drops_leveraged():
    rows = sf.parse_etf_rows(ETF_RESPONSE, min_aum=1e9)
    assert "TQQQ" not in [r[0] for r in rows]


def test_parse_etf_rows_drops_below_min_aum():
    rows = sf.parse_etf_rows(ETF_RESPONSE, min_aum=1e9)
    assert "TINY" not in [r[0] for r in rows]


def test_parse_etf_rows_drops_missing_symbol():
    rows = sf.parse_etf_rows(ETF_RESPONSE, min_aum=1e9)
    assert all(r[0] for r in rows)


def test_parse_etf_rows_shape_carries_the_exchange():
    """튜플 끝의 거래소는 대시보드 티커 라벨((NASDAQ)/(NYSE)/(ETF))에 쓴다."""
    rows = sf.parse_etf_rows(ETF_RESPONSE, min_aum=1e9)
    assert rows[0] == ("SPY", "SPDR S&P 500 ETF Trust", "US", "미분류", "ETF", "AMEX")


def test_parse_etf_rows_handles_missing_market_cap():
    rows = sf.parse_etf_rows(
        [{"symbol": "AAA", "name": "A ETF", "exchangeShortName": "AMEX"}],
        min_aum=1e9)
    assert rows == []


# ─── 미국 외 상장 제외 (2026-08-22 실제 스캔에서 148건 유입) ──
def test_parse_etf_rows_drops_foreign_listings():
    """FMP ETF 스크리너는 거래소를 안 걸면 TSX 를 155건 함께 준다.

    2026-08-22 첫 실전 스캔에서 캐나다 ETF 148건이 market=US 로 아카이브에
    들어갔다. 미국 유니버스라는 전제가 깨진다.
    """
    data = [
        {"symbol": "VFV.TO", "name": "Vanguard S&P 500 Index ETF",
         "marketCap": 1.5e10, "exchangeShortName": "TSX"},
        {"symbol": "XIC.TO", "name": "iShares Core S&P TSX Capped Composite",
         "marketCap": 1.2e10, "exchangeShortName": "TSX"},
    ]
    assert sf.parse_etf_rows(data, min_aum=1e9) == []


def test_parse_etf_rows_keeps_all_three_us_exchanges():
    data = [
        {"symbol": "AAA", "name": "A ETF", "marketCap": 2e9,
         "exchangeShortName": "AMEX"},
        {"symbol": "BBB", "name": "B ETF", "marketCap": 2e9,
         "exchangeShortName": "NASDAQ"},
        {"symbol": "CCC", "name": "C ETF", "marketCap": 2e9,
         "exchangeShortName": "NYSE"},
    ]
    assert [r[0] for r in sf.parse_etf_rows(data, min_aum=1e9)] == ["AAA", "BBB", "CCC"]


def test_parse_etf_rows_drops_suffixed_symbols_even_on_a_us_exchange():
    """거래소 값이 잘못 와도 접미사 티커는 막는다. 주식 경로와 같은 규칙이다."""
    data = [{"symbol": "ZZZ.TO", "name": "Z ETF", "marketCap": 2e9,
             "exchangeShortName": "AMEX"}]
    assert sf.parse_etf_rows(data, min_aum=1e9) == []


def test_parse_etf_rows_drops_unknown_exchange():
    data = [{"symbol": "AAA", "name": "A ETF", "marketCap": 2e9,
             "exchangeShortName": "LSE"}]
    assert sf.parse_etf_rows(data, min_aum=1e9) == []


def test_parse_etf_rows_drops_missing_exchange():
    """거래소를 알 수 없으면 미국이라고 가정하지 않는다."""
    data = [{"symbol": "AAA", "name": "A ETF", "marketCap": 2e9}]
    assert sf.parse_etf_rows(data, min_aum=1e9) == []


# ─── 한국 제거 ─────────────────────────────────────────────
def test_fallback_universe_has_no_korean_tickers():
    assert all(not t.endswith(".KS") for t, *_ in sf.FALLBACK_UNIVERSE)


def test_fallback_universe_is_all_us():
    assert all(row[2] == "US" for row in sf.FALLBACK_UNIVERSE)


def test_fallback_universe_rows_are_six_tuples():
    assert all(len(row) == 6 for row in sf.FALLBACK_UNIVERSE)


def test_fallback_universe_carries_a_us_exchange():
    assert all(row[5] in {"NASDAQ", "NYSE", "AMEX"} for row in sf.FALLBACK_UNIVERSE)


def test_kr_universe_function_is_gone():
    assert not hasattr(sf, "fetch_kr_universe")
    assert not hasattr(sf, "KOSPI_EXPANDED")


# ─── ETF 지배 섹터 ─────────────────────────────────────────
# 스크리너의 sector 필드는 운용사 업종이라 874건 전부 Financial Services 다.
# 실제 노출은 etf/sector-weightings 로 따로 받는다.
def test_dominant_sector_takes_a_concentrated_holding():
    # XLB 실측: Basic Materials 83.8% / Consumer Cyclical 16.2%
    w = [{"sector": "Basic Materials", "weightPercentage": 83.81},
         {"sector": "Consumer Cyclical", "weightPercentage": 16.19}]
    assert sf.dominant_sector(w) == "소재"


def test_dominant_sector_rejects_a_broad_index():
    """VOO 실측 38.6%. 광범위 지수를 'IT ETF' 로 부르면 로테이션 가점을
    부당하게 받는다."""
    w = [{"sector": "Technology", "weightPercentage": 38.6},
         {"sector": "Financial Services", "weightPercentage": 13.2}]
    assert sf.dominant_sector(w) == "미분류"


def test_dominant_sector_accepts_qqq_at_its_measured_weight():
    # QQQ 실측 60.3% — 임계 60 을 막 넘는다
    w = [{"sector": "Technology", "weightPercentage": 60.3}]
    assert sf.dominant_sector(w) == "IT"


def test_dominant_sector_rejects_bond_and_cash_funds():
    """BND 실측: Cash & Others 100%. 섹터 로테이션은 주식 섹터 개념이다."""
    w = [{"sector": "Cash & Others", "weightPercentage": 100.0}]
    assert sf.dominant_sector(w) == "미분류"


def test_dominant_sector_handles_an_empty_response():
    assert sf.dominant_sector([]) == "미분류"


def test_dominant_sector_handles_a_missing_weight():
    assert sf.dominant_sector([{"sector": "Technology"}]) == "미분류"


def test_dominant_sector_threshold_is_inclusive():
    w = [{"sector": "Technology", "weightPercentage": 60.0}]
    assert sf.dominant_sector(w) == "IT"
    w = [{"sector": "Technology", "weightPercentage": 59.9}]
    assert sf.dominant_sector(w) == "미분류"


@pytest.mark.parametrize("name", [
    "CORP_CODE_MAP", "DART_KEY", "DART_BASE",
    "load_dart_corpcode", "_dart_get", "fetch_dart_filing_signals",
])
def test_dart_symbols_are_gone(name):
    """한국 공시(DART) 경로는 한국 종목과 함께 사라졌다.

    스캔 대상이 미국 주식·ETF 뿐이라 조회할 곳이 없다. 남겨 두면 도달하지
    않는 코드가 계속 유지보수 대상으로 남는다.
    """
    assert not hasattr(sf, name)
