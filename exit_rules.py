"""포지션 청산 규칙.

진입한 포지션이 언제 어떤 가격에 청산되는지를 정의한다. 2단계 백테스트 하네스와
4단계 실행 엔진이 같은 모듈을 쓴다 — 규칙이 한 곳에만 있어야 백테스트와 실거래가
갈라지지 않는다.

전부 순수 함수다. 파일도 네트워크도 건드리지 않고, 가격은 호출자가 Bar 로 넘긴다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class Params:
    """청산 파라미터 5개.

    v5 설계서가 파라미터 5개 초과를 금지한다. 기본값은 전부 튜닝되지 않았다 —
    백테스트가 없어 맞출 수 없고, 과최적화 금지 원칙상 지금 맞춰서도 안 된다.

    두 ATR 배수가 같은 값인 것은 우연이 아니다. 고점이 진입가+1R 에 닿는 순간
    트레일 손절선이 정확히 진입가가 되어, "1R 도달 시 본전이동"이 파라미터를
    추가하지 않고 자동으로 나온다.

    use_target 은 기본이 꺼짐이다. 목표폭 대비 손절폭(위험보상비)이 1 근처라
    목표가 익절이 기대값을 올리는지 내리는지 아직 알 수 없다. 켠 결과와 끈
    결과를 나란히 비교할 수 있게만 해 두고, 기본값은 건드리지 않는다.
    """
    stop_atr_mult: float = 3.0
    trail_atr_mult: float = 3.0
    max_hold_days: int = 60
    exit_total: int = 60
    use_target: bool = False


@dataclass(frozen=True)
class Bar:
    """하루치 시세와 그날의 스코어.

    atr14 가 없으면 트레일링을 적용하지 않고, total 이 없으면 SIGNAL 판정을 건너뛴다.

    atr14 와 total 은 둘 다 이 봉이 열리기 전에 알 수 있는 값이어야 한다.
    atr14 는 전일 종가까지로 계산한 ATR 을 넣을 것 - 당일 고저를 포함해 계산하면
    개장 전에 정해져 있어야 할 손절선이 미래 정보를 쓰게 되어, 이 모듈이 순수해도
    호출자 쪽에서 룩어헤드가 다시 생긴다.
    """
    date: str
    open: float
    high: float
    low: float
    close: float
    atr14: Optional[float] = None
    total: Optional[int] = None


@dataclass(frozen=True)
class Position:
    ticker: str
    entry_date: str
    entry_price: float
    initial_stop: float
    r_unit: float
    high_since_entry: float
    stop: float
    bars_held: int
    # 진입일 스코어의 목표 상승률로 확정한 익절가. 목표를 알 수 없거나
    # 목표가가 진입가 이하면 None 이고, 그 포지션에는 TARGET 규칙이 없다.
    # initial_stop 과 같이 진입 시점에 고정한다 - 도중에 움직이면 "목표
    # 달성" 의 정의가 흔들려 달성률을 비교할 수 없다.
    target_price: Optional[float]


@dataclass(frozen=True)
class ExitDecision:
    reason: str      # "TIME" | "SIGNAL" | "STOP" | "TRAIL" | "TARGET"
    price: float
    date: str


def open_position(ticker: str, date: str, entry_price: float,
                  atr_at_entry: Optional[float], params: Params,
                  target_pct: Optional[float] = None) -> Position:
    """진입 시점 ATR 로 초기 손절선과 R 을, 진입일 스코어로 목표가를 확정한다.

    초기 손절은 진입 시점 ATR 로 고정한다 — R 정의가 도중에 흔들리면 손익을
    R 배수로 비교할 수 없게 된다. 목표가도 같은 이유로 진입 시점에 고정한다.

    target_pct 는 아카이브의 target 컬럼 값(3개월 기대 상승률 %)이다.
    없거나 0 이하면 목표가를 두지 않는다 - 목표가가 진입가 이하이면 익절이
    곧 손실 확정이 되어 규칙이 뒤집힌다.
    """
    if atr_at_entry is None or atr_at_entry <= 0:
        raise ValueError(
            f"{ticker}: atr_at_entry 가 {atr_at_entry} 입니다. "
            "손절폭이 0 이면 R 이 0 이 되어 이후 계산이 전부 무의미해집니다."
        )

    initial_stop = entry_price - params.stop_atr_mult * atr_at_entry
    target_price = (entry_price * (1 + target_pct / 100.0)
                    if target_pct is not None and target_pct > 0 else None)
    return Position(
        ticker=ticker,
        entry_date=date,
        entry_price=entry_price,
        initial_stop=initial_stop,
        r_unit=entry_price - initial_stop,
        high_since_entry=entry_price,
        stop=initial_stop,
        bars_held=0,
        target_price=target_price,
    )


def current_stop(position: Position, params: Params,
                 atr: Optional[float]) -> float:
    """현재 유효한 손절선.

    고점이 진입가+1R 에 닿으면 트레일링이 켜진다. 트레일링은 현재 ATR 을 쓴다
    (Chandelier 표준) — 3개월간 변동성이 크게 바뀌므로 진입 시점 값에 묶어두면
    뒤로 갈수록 부정확해진다.

    손절선은 한 방향으로만 움직이는 래칫이다. 하한은 초기 손절선이 아니라
    Position 에 저장된 직전 손절선이다 — 고점이 그대로인 채 ATR 이 확대돼도
    이미 확보한 손절선은 절대 후퇴하지 않는다. ATR 이 없거나 트레일링이 아직
    켜지지 않았으면 저장된 손절선을 그대로 돌려준다.
    """
    if atr is None:
        return position.stop

    trail_active = position.high_since_entry >= position.entry_price + position.r_unit
    if not trail_active:
        return position.stop

    trailed = position.high_since_entry - params.trail_atr_mult * atr
    return max(position.stop, trailed)


def advance(position: Position, bar: Bar, params: Params) -> Position:
    """봉 하나를 소화하고 포지션 상태를 갱신한다.

    반드시 evaluate 다음에 호출할 것. 먼저 호출하면 오늘 고가로 계산한 손절선이
    오늘 장중에 체결되는 셈이 되어 룩어헤드가 된다.

    고점을 먼저 갱신한 다음 그 새 고점으로 손절선을 다시 래칫한다 - 순서를
    바꾸면 오늘 고가가 오늘 손절선에 반영되어 버린다.

    params 를 반드시 받는다. 여기서 Params() 기본값을 쓰면 호출자가 다른
    trail_atr_mult 를 줬을 때 저장되는 손절선과 evaluate 가 판정에 쓰는
    손절선이 조용히 어긋난다.
    """
    new_high = max(position.high_since_entry, bar.high)
    with_new_high = replace(position, high_since_entry=new_high)
    return replace(
        position,
        high_since_entry=new_high,
        stop=current_stop(with_new_high, params, bar.atr14),
        bars_held=position.bars_held + 1,
    )


def evaluate(position: Position, bar: Bar,
             params: Params) -> Optional[ExitDecision]:
    """이 봉에서 청산이 발생하는지 판정한다. 없으면 None.

    순서는 하루 안의 시간 순서다. TIME 과 SIGNAL 은 개장 전에 결정된다 —
    bars_held 는 결정론적이고 total 은 KST 07:00 스캔에서 이미 나와 있다.
    따라서 둘 다 시가 시장가로 나가고, 장중에 걸린 손절보다 먼저 체결된다.

    TARGET 은 맨 뒤다. 고가가 목표를, 저가가 손절선을 같은 봉에서 건드리면
    일봉만으로는 어느 쪽이 먼저였는지 알 수 없다. 백테스트가 실제보다 좋게
    나오는 것보다 나쁘게 나오는 편이 안전하므로 손절을 먼저 잡는다.
    """
    if position.bars_held >= params.max_hold_days:
        return ExitDecision("TIME", bar.open, bar.date)

    if bar.total is not None and bar.total < params.exit_total:
        return ExitDecision("SIGNAL", bar.open, bar.date)

    stop = current_stop(position, params, bar.atr14)
    if bar.low <= stop:
        trailing = stop > position.initial_stop
        # 갭하락으로 시가가 이미 손절선 아래면 그 가격에 체결된다.
        fill = min(bar.open, stop)
        return ExitDecision("TRAIL" if trailing else "STOP", fill, bar.date)

    if (params.use_target and position.target_price is not None
            and bar.high >= position.target_price):
        # 갭상승으로 시가가 이미 목표 위면 그 가격에 체결된다.
        fill = max(bar.open, position.target_price)
        return ExitDecision("TARGET", fill, bar.date)

    return None
