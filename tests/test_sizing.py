"""포지션 사이징 테스트.

산수만 담는 순수 모듈이라 표 하나로 고정할 수 있다. 날짜도 네트워크도
들어오지 않는다.

설계: docs/superpowers/specs/2026-08-25-position-sizing-design.md
"""
import sizing


ACC = sizing.Account(capital=10_000)


def test_default_account_risks_one_percent():
    # 자본이 작으면 리스크 비율이 사실상 종목 수를 정한다. 1% 는 약 10종목.
    assert ACC.risk_budget == 100.0


def test_default_account_caps_a_position_at_twenty_percent():
    # 손절폭이 좁은 종목이 자본을 독식하는 것을 막는다(실측 PHG 42%).
    assert ACC.max_position == 2_000.0


def test_quantity_comes_from_the_risk_budget():
    # 1R 이 $6 이면 $100 을 잃도록 16주. 16 x 6 = 96 <= 100.
    assert sizing.shares(10_000, ACC, entry_price=100.0, r_unit=6.0) == 16


def test_the_position_cap_can_bind_before_the_risk_budget():
    # 1R 이 $1 이면 리스크로는 100주지만 100 x $50 = $5,000 로 상한 초과.
    # 상한 $2,000 에서 40주로 잘린다.
    assert sizing.shares(10_000, ACC, entry_price=50.0, r_unit=1.0) == 40


def test_cash_can_bind_before_both():
    # 현금이 $500 뿐이면 $100 짜리를 5주까지만 산다.
    assert sizing.shares(500, ACC, entry_price=100.0, r_unit=6.0) == 5


def test_a_share_too_expensive_for_the_budget_is_zero():
    # 1주 값이 상한을 넘으면 0주다. 목표 리스크를 못 맞추는 포지션은 열지 않는다.
    assert sizing.shares(10_000, ACC, entry_price=2_500.0, r_unit=6.0) == 0


def test_a_risk_unit_wider_than_the_budget_is_zero():
    # 1R 이 $150 이면 1주만 사도 $150 을 걸게 된다. 예산이 $100 이므로 사지 않는다.
    assert sizing.shares(10_000, ACC, entry_price=100.0, r_unit=150.0) == 0


def test_no_cash_means_no_shares():
    assert sizing.shares(0, ACC, entry_price=100.0, r_unit=6.0) == 0


def test_a_non_positive_risk_unit_is_refused():
    # r_unit 이 0 이면 나눗셈이 터지고, 음수면 수량이 음수가 된다.
    assert sizing.shares(10_000, ACC, entry_price=100.0, r_unit=0.0) == 0
    assert sizing.shares(10_000, ACC, entry_price=100.0, r_unit=-6.0) == 0


def test_a_non_positive_price_is_refused():
    assert sizing.shares(10_000, ACC, entry_price=0.0, r_unit=6.0) == 0


def test_a_custom_account_scales_both_budgets():
    acc = sizing.Account(capital=50_000, risk_pct=2.0, max_weight_pct=10.0)
    assert acc.risk_budget == 1_000.0
    assert acc.max_position == 5_000.0
