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
    """청산 파라미터 4개.

    v5 설계서가 파라미터 5개 초과를 금지한다. 기본값은 전부 튜닝되지 않았다 —
    백테스트가 없어 맞출 수 없고, 과최적화 금지 원칙상 지금 맞춰서도 안 된다.

    두 ATR 배수가 같은 값인 것은 우연이 아니다. 고점이 진입가+1R 에 닿는 순간
    트레일 손절선이 정확히 진입가가 되어, "1R 도달 시 본전이동"이 파라미터를
    추가하지 않고 자동으로 나온다.
    """
    stop_atr_mult: float = 3.0
    trail_atr_mult: float = 3.0
    max_hold_days: int = 60
    exit_total: int = 60


@dataclass(frozen=True)
class Bar:
    """하루치 시세와 그날의 스코어.

    atr14 가 없으면 트레일링을 적용하지 않고, total 이 없으면 SIGNAL 판정을 건너뛴다.
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
    bars_held: int


@dataclass(frozen=True)
class ExitDecision:
    reason: str      # "TIME" | "SIGNAL" | "STOP" | "TRAIL"
    price: float
    date: str


def open_position(ticker: str, date: str, entry_price: float,
                  atr_at_entry: Optional[float], params: Params) -> Position:
    """진입 시점 ATR 로 초기 손절선과 R 을 확정한다.

    초기 손절은 진입 시점 ATR 로 고정한다 — R 정의가 도중에 흔들리면 손익을
    R 배수로 비교할 수 없게 된다.
    """
    if atr_at_entry is None or atr_at_entry <= 0:
        raise ValueError(
            f"{ticker}: atr_at_entry 가 {atr_at_entry} 입니다. "
            "손절폭이 0 이면 R 이 0 이 되어 이후 계산이 전부 무의미해집니다."
        )

    initial_stop = entry_price - params.stop_atr_mult * atr_at_entry
    return Position(
        ticker=ticker,
        entry_date=date,
        entry_price=entry_price,
        initial_stop=initial_stop,
        r_unit=entry_price - initial_stop,
        high_since_entry=entry_price,
        bars_held=0,
    )


def current_stop(position: Position, params: Params,
                 atr: Optional[float]) -> float:
    """현재 유효한 손절선.

    고점이 진입가+1R 에 닿으면 트레일링이 켜진다. 트레일링은 현재 ATR 을 쓴다
    (Chandelier 표준) — 3개월간 변동성이 크게 바뀌므로 진입 시점 값에 묶어두면
    뒤로 갈수록 부정확해진다. 손절선은 절대 내려가지 않는다.
    """
    if atr is None:
        return position.initial_stop

    trail_active = position.high_since_entry >= position.entry_price + position.r_unit
    if not trail_active:
        return position.initial_stop

    trailed = position.high_since_entry - params.trail_atr_mult * atr
    return max(position.initial_stop, trailed)


def advance(position: Position, bar: Bar) -> Position:
    """봉 하나를 소화하고 포지션 상태를 갱신한다.

    반드시 evaluate 다음에 호출할 것. 먼저 호출하면 오늘 고가로 계산한 손절선이
    오늘 장중에 체결되는 셈이 되어 룩어헤드가 된다.
    """
    return replace(
        position,
        high_since_entry=max(position.high_since_entry, bar.high),
        bars_held=position.bars_held + 1,
    )


def evaluate(position: Position, bar: Bar,
             params: Params) -> Optional[ExitDecision]:
    """이 봉에서 청산이 발생하는지 판정한다. 없으면 None.

    순서는 하루 안의 시간 순서다. TIME 과 SIGNAL 은 개장 전에 결정된다 —
    bars_held 는 결정론적이고 total 은 KST 07:00 스캔에서 이미 나와 있다.
    따라서 둘 다 시가 시장가로 나가고, 장중에 걸린 손절보다 먼저 체결된다.
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

    return None
