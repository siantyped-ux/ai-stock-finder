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


BUY_SIGNALS = ("BUY", "STRONG_BUY")


@dataclass(frozen=True)
class Trade:
    ticker: str
    market: str
    source: str
    entry_date: str
    entry_price: float
    r_unit: float
    exit_date: Optional[str]
    exit_price: Optional[float]
    exit_reason: Optional[str]
    bars_held: int
    is_open: bool
    gross_r: float
    cost_r: float
    net_r: float


@dataclass(frozen=True)
class EntryState:
    """진입 판정에 필요한 상태. 봉 유무와 무관하게 날짜마다 갱신한다."""
    was_buy: bool = False
    pending: bool = False


@dataclass(frozen=True)
class EntryStep:
    state: EntryState
    should_enter: bool


def step_entry(state: EntryState, signal: str) -> EntryStep:
    """아카이브 하루치를 소화해 진입 대기 여부를 갱신한다.

    BUY 로 전환되는 순간에만 pending 이 선다. 봉이 없는 날(주말·휴장)에도
    호출해야 한다 - 그러지 않으면 토요일에 전환된 종목이 월요일에는 이미
    전환이 아니어서 영영 진입하지 못한다.
    """
    is_buy = signal in BUY_SIGNALS

    if not is_buy:
        pending = False
    elif not state.was_buy:
        pending = True           # 전환
    else:
        pending = state.pending  # 계속 BUY - 기존 pending 유지

    return EntryStep(EntryState(was_buy=is_buy, pending=pending),
                     should_enter=pending)


def consume(state: EntryState) -> EntryState:
    """진입이 일어났으니 대기를 해제한다."""
    return replace(state, pending=False)


def _make_trade(pos: er.Position, market: str, source: str,
                exit_price: float, exit_date: Optional[str],
                exit_reason: Optional[str], costs: Costs) -> Trade:
    gross = (exit_price - pos.entry_price) / pos.r_unit
    cost = cost_r(pos.entry_price, pos.r_unit, market, costs)
    return Trade(
        ticker=pos.ticker,
        market=market,
        source=source,
        entry_date=pos.entry_date,
        entry_price=pos.entry_price,
        r_unit=pos.r_unit,
        exit_date=exit_date,
        exit_price=exit_price if exit_date else None,
        exit_reason=exit_reason,
        bars_held=pos.bars_held,
        is_open=exit_date is None,
        gross_r=gross,
        cost_r=cost,
        net_r=gross - cost,
    )


def simulate_ticker(ticker: str, market: str, rows: list, bars: dict,
                    params: er.Params, costs: Costs) -> list:
    """티커 하나의 트레이드를 재현한다.

    rows 는 아카이브 행(date·signal·total·source)을 날짜 오름차순으로,
    bars 는 날짜 -> exit_rules.Bar 매핑이다. 봉이 없는 날은 세션이 없었다는
    뜻이므로 보유 일수를 세지 않는다.

    exit_rules 의 계약대로 evaluate 를 먼저 하고 advance 를 나중에 한다.
    진입한 봉도 advance 로 접어 넣는다 - 그래야 그날 고가가 다음 봉의
    트레일 계산에 반영되고 bars_held 가 실제 보유 봉 수와 맞는다. 다만
    진입 봉에서는 evaluate 를 돌리지 않는다. 그 봉의 시가에 막 들어갔고,
    같은 봉에서 청산까지 판정하려면 봉 안의 시간 순서를 알아야 한다.
    """
    trades = []
    state = EntryState()
    pos = None
    last_close = None
    open_source = ""

    for row in rows:
        bar = bars.get(row["date"])
        if bar is not None and row.get("total") is not None:
            # 그날 스코어를 봉에 실어 SIGNAL 판정이 가능하게 한다.
            bar = er.Bar(bar.date, bar.open, bar.high, bar.low, bar.close,
                         bar.atr14, row["total"])

        if pos is not None and bar is not None:
            decision = er.evaluate(pos, bar, params)
            if decision is not None:
                trades.append(_make_trade(pos, market, open_source,
                                          decision.price, decision.date,
                                          decision.reason, costs))
                pos = None
            else:
                pos = er.advance(pos, bar, params)
                last_close = bar.close

        step = step_entry(state, row["signal"])
        entered = False
        if step.should_enter and pos is None and bar is not None:
            if bar.atr14:
                pos = er.open_position(ticker, row["date"], bar.open,
                                       bar.atr14, params)
                pos = er.advance(pos, bar, params)
                open_source = row["source"]
                last_close = bar.close
            # ATR 이 없어 못 들어갔어도 이 전환은 소진한다. 그러지 않으면
            # 같은 전환으로 다음 봉에 뒤늦게 진입해 버린다.
            entered = True

        state = consume(step.state) if entered else step.state

    if pos is not None and last_close is not None:
        trades.append(_make_trade(pos, market, open_source, last_close,
                                  None, None, costs))

    return trades
