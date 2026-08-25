"""포지션 사이징.

거래당 잃을 금액을 고정하고 손절폭으로 수량을 역산한다. 정액으로 사면 손절폭이
종목마다 달라 실제 리스크가 제각각이 된다 - 2026-08-25 실측에서 손절폭이
4.7%~21.7% 로 4.6배 차이났고, 같은 $1,000 을 넣어도 잃는 돈은 $47 과 $217 였다.

리스크를 고정하면 1R 의 달러 가치가 종목마다 같아져 `R 합계 x 1R = 실현 손익`이
성립한다. 이 저장소가 성과를 R 배수로 재 온 것과 처음으로 맞물린다.

산수만 담는다. 날짜도 네트워크도 모른다 - exit_rules(청산 판정)와 flow(수급
점수)를 순수 모듈로 떼어 둔 것과 같은 결이다.

설계: docs/superpowers/specs/2026-08-25-position-sizing-design.md
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Account:
    """계좌. 얼마가 있고 한 번에 얼마를 걸지.

    portfolio.Limits 와 따로 두는 것은 성격이 다르기 때문이다. Limits 는
    "무엇을 막을까"(동시 보유 수, 상관)이고 이쪽은 "얼마가 있나" 다.

    risk_pct 는 초기 자본 대비다. 현재 평가자산이 아니다 - 표본이 0인 단계에서
    복리를 켜면 성과가 시그널 품질 때문인지 사이징 때문인지 갈라볼 수 없다.

    max_weight_pct 는 한 종목에 넣을 수 있는 상한이다. 리스크만으로 정하면
    손절폭이 좁은 종목이 자본을 독식한다(실측 PHG 42%).
    """
    capital: float
    risk_pct: float = 1.0
    max_weight_pct: float = 20.0

    @property
    def risk_budget(self) -> float:
        """거래당 잃을 금액."""
        return self.capital * self.risk_pct / 100.0

    @property
    def max_position(self) -> float:
        """한 종목에 넣을 수 있는 최대 금액."""
        return self.capital * self.max_weight_pct / 100.0


def shares(cash: float, account: Account,
           entry_price: float, r_unit: float) -> int:
    """살 주 수. 셋 중 가장 빡빡한 제약이 이긴다. 못 사면 0.

    0 을 돌려주면 호출자는 그 포지션을 열지 않는다. 목표 리스크를 못 맞추는
    포지션은 규칙을 흐리므로 여는 것보다 건너뛰는 편이 낫다.

    r_unit 이 예산보다 크면 1주만 사도 예산을 넘기므로 0 이 된다. 값이 0 이나
    음수면 나눗셈이 터지거나 수량이 음수가 되므로 같이 막는다.
    """
    if entry_price <= 0 or r_unit <= 0:
        return 0

    by_risk = int(account.risk_budget // r_unit)
    by_cap = int(account.max_position // entry_price)
    by_cash = int(cash // entry_price)
    return max(min(by_risk, by_cap, by_cash), 0)
