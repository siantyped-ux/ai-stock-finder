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
