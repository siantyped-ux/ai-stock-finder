"""시그널 아카이브를 트레이드로 재현하는 순수 시뮬레이터.

성과는 R 배수로 집계한다. R = 진입가 - 초기 손절가 이므로 자본도 환율도
필요 없고, 한국·미국 종목을 같은 잣대로 비교할 수 있다. 포지션 사이징은
3단계 범위이며 여기서는 다루지 않는다.

파일도 네트워크도 건드리지 않는다. 가격은 호출자가 넘긴다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

import exit_rules as er


@dataclass(frozen=True)
class Costs:
    """편도 비용률(%). 전부 진입가 대비 백분율이다.

    kr_tax_pct 는 확정된 값이 아니다. 국내 증권거래세는 단계적으로 인하돼
    왔고 2026년 시행 세율을 확인하지 못했다. 현재 한국 종목 진입이 0건이라
    결과에 영향이 없으나, 한국 트레이드가 생기기 전에 실제 세율로 교체할 것.
    """
    us_buy_pct: float = 0.10
    us_sell_pct: float = 0.10
    kr_buy_pct: float = 0.02
    kr_sell_pct: float = 0.02
    kr_tax_pct: float = 0.15
    slippage_pct: float = 0.05


def cost_r(entry_price: float, r_unit: float, market: str,
           costs: Costs) -> float:
    """왕복 거래비용을 R 배수로 환산한다.

    매도 비용도 진입가 기준으로 잡는 근사다. 청산가 기준이 정확하지만
    그러면 미결 포지션의 비용이 확정되지 않아 닫힌 트레이드와 비교가
    어려워진다. 손절폭이 진입가의 10% 안팎이라 오차는 0.01R 미만이다.

    r_unit 이 클수록(변동성이 큰 종목일수록) 비용 부담이 자동으로 작아진다.
    """
    if market == "US":
        pct = costs.us_buy_pct + costs.us_sell_pct
    elif market == "KR":
        pct = costs.kr_buy_pct + costs.kr_sell_pct + costs.kr_tax_pct
    else:
        raise ValueError(f"알 수 없는 시장: {market!r} (US 또는 KR 이어야 함)")

    pct += costs.slippage_pct * 2
    return (entry_price * pct / 100.0) / r_unit
