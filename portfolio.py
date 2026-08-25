"""포트폴리오 계층 시뮬레이션. 동시 진입 상한과 상관 중복을 여기서 건다.

trade_sim.simulate_ticker 는 종목 하나를 독립적으로 재현한다. 그래서 "지금
몇 개가 열려 있는가" 와 "이미 같은 베팅을 들고 있는가" 를 알 수 없다. 그
두 질문은 종목을 가로질러야 답할 수 있으므로, 이 모듈은 종목이 아니라
날짜를 바깥 루프로 돈다.

왜 필요한가 - 2026-08-22 스캔에서 BUY 가 90건 나왔다. 상한이 없으면 90개
포지션을 동시에 여는 것이고, 그중 XLV·VHT·IYH·FHLC·IXJ 는 상관이 0.97~0.999
라 사실상 한 포지션을 다섯 배로 든 것이다. 신호 다섯 개가 아니라 신호 하나가
다섯 번 세어진 것이며, 이것이 ETF 가 매매로 이어지지 않던 실질적인 이유다.

규칙은 전부 exit_rules 와 trade_sim 에 있고 여기서는 진입 자격만 정한다 -
규칙이 두 벌이 되면 백테스트와 실거래가 갈라진다.

파일도 네트워크도 건드리지 않는다. 가격과 상관은 호출자가 넘긴다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import exit_rules as er
import trade_sim as ts


@dataclass(frozen=True)
class Limits:
    """포트폴리오 제약.

    max_positions 0 은 무제한이다. 기본을 무제한으로 두는 것은 의도다 -
    상한을 기본값으로 켜면 예전 백테스트 결과의 의미가 조용히 바뀐다.
    호출자가 명시적으로 켜야 한다.

    max_correlation 은 이미 보유한 포지션과의 일간수익률 상관 상한이다.
    0.90 은 실측으로 정했다 (2026-08-24): 같은 베팅으로 알려진 쌍은
    0.974~0.999 에 몰리고(VHT-FHLC 0.999 · EEM-IEMG 0.999 · XLE-FENY 0.996),
    무관한 쌍은 -0.31~+0.33 에 있다. BUY ETF 39종목의 741개 쌍 중 0.90 을
    넘는 것은 4.6% 뿐이라, 진짜 복제본만 걸리고 단순히 상관 높은 종목은
    통과한다. 1.0 이면 검사를 끈다.
    """
    max_positions: int = 0
    max_correlation: float = 1.0


def daily_returns(closes: list) -> list:
    """종가 목록에서 일간 수익률. 0 이하 가격은 구간을 끊는다."""
    out = []
    for prev, cur in zip(closes, closes[1:]):
        out.append((cur / prev - 1) if prev > 0 else 0.0)
    return out


def correlation(a: list, b: list) -> Optional[float]:
    """두 수익률 목록의 피어슨 상관. 길이가 다르면 뒤에서 맞춘다.

    표본이 30 미만이거나 한쪽이 무변동이면 None 이다 - 상관을 못 재는
    것과 상관이 0 인 것은 다르다. 못 재면 검사를 통과시킨다(아래 참조).
    """
    n = min(len(a), len(b))
    if n < 30:
        return None
    x, y = a[-n:], b[-n:]
    mx, my = sum(x) / n, sum(y) / n
    dx = [v - mx for v in x]
    dy = [v - my for v in y]
    sx = sum(v * v for v in dx) ** 0.5
    sy = sum(v * v for v in dy) ** 0.5
    if sx <= 0 or sy <= 0:
        return None
    return sum(p * q for p, q in zip(dx, dy)) / (sx * sy)


def build_correlator(closes_by_ticker: dict, lookback: int = 120) -> Callable:
    """티커 쌍 -> 상관 함수. 수익률을 미리 계산하고 쌍을 캐시한다.

    상관을 못 재면 None 을 낸다. 호출자가 그것을 '통과' 로 볼지 '차단' 으로
    볼지 정한다.
    """
    rets = {t: daily_returns(c[-(lookback + 1):])
            for t, c in closes_by_ticker.items() if len(c) >= 2}
    cache: dict = {}

    def get(a: str, b: str) -> Optional[float]:
        key = (a, b) if a <= b else (b, a)
        if key not in cache:
            ra, rb = rets.get(a), rets.get(b)
            cache[key] = None if ra is None or rb is None else correlation(ra, rb)
        return cache[key]

    return get


def _too_correlated(ticker: str, held: list, limits: Limits,
                    correlator: Optional[Callable]) -> Optional[tuple]:
    """보유 종목 중 상관이 상한을 넘는 것. 없으면 None.

    상관을 못 재면 통과시킨다. 데이터가 없다는 이유로 진입을 막으면 이력이
    짧은 신규 종목이 영구히 배제된다 - 중복 방지라는 목적과 무관한 부작용이다.
    """
    if correlator is None or limits.max_correlation >= 1.0:
        return None
    for other in held:
        rho = correlator(ticker, other)
        if rho is not None and rho > limits.max_correlation:
            return other, rho
    return None


def simulate(rows_by_ticker: dict, bars_by_ticker: dict, markets: dict,
             params: er.Params = None, costs: ts.Costs = None,
             limits: Limits = None,
             correlator: Optional[Callable] = None,
             universe_exits: dict = None) -> dict:
    """날짜 순으로 포트폴리오를 재현한다.

    rows_by_ticker  티커 -> 아카이브 행 목록(date·signal·total·target·source)
    bars_by_ticker  티커 -> {날짜: exit_rules.Bar}
    markets         티커 -> 'US' | 'KR' (비용 계산용)
    universe_exits  market -> 그 시장이 빠진 것을 알아차린 첫 스캔일

    반환에는 trades 와 함께 rejected 가 들어간다. 무엇을 왜 막았는지 세지
    않으면 상한이 조용히 기회를 죽여도 알 수 없다.
    """
    params = params or er.Params()
    costs = costs or ts.Costs()
    limits = limits or Limits()

    dates = sorted({r["date"] for rows in rows_by_ticker.values() for r in rows})
    rows_by_date = {}
    for ticker, rows in rows_by_ticker.items():
        for r in rows:
            rows_by_date.setdefault(r["date"], []).append((ticker, r))

    state = {t: ts.EntryState() for t in rows_by_ticker}
    positions: dict = {}       # ticker -> er.Position
    sources: dict = {}         # ticker -> 진입일 source
    last_close: dict = {}
    trades = []
    rejected = {"capacity": 0, "correlation": 0}
    rejected_pairs = []

    def bar_for(ticker: str, date: str, total=None):
        bar = bars_by_ticker.get(ticker, {}).get(date)
        if bar is not None and total is not None:
            # 그날 스코어를 봉에 실어 SIGNAL 판정이 가능하게 한다.
            bar = er.Bar(bar.date, bar.open, bar.high, bar.low, bar.close,
                         bar.atr14, total)
        return bar

    for date in dates:
        today = rows_by_date.get(date, [])
        totals = {t: r.get("total") for t, r in today}

        # ── 1. 청산 먼저 ──
        # exit_rules 의 계약대로 evaluate 를 advance 보다 먼저 한다.
        closed_today = set()
        for ticker in list(positions):
            bar = bar_for(ticker, date, totals.get(ticker))
            if bar is None:
                continue
            decision = er.evaluate(positions[ticker], bar, params)
            if decision is not None:
                trades.append(ts.make_trade(positions[ticker], markets.get(ticker, "US"),
                                            sources.get(ticker, ""), decision.price,
                                            decision.date, decision.reason, costs))
                del positions[ticker]
                closed_today.add(ticker)
            else:
                positions[ticker] = er.advance(positions[ticker], bar, params)
                last_close[ticker] = bar.close

        # ── 2. 진입 전환 판정 ──
        # 봉이 없는 날에도 step_entry 를 돌려야 한다. 그러지 않으면 휴장일에
        # 전환된 종목이 다음 세션에는 이미 전환이 아니어서 영영 진입하지 못한다.
        wants = []
        for ticker, row in today:
            step = ts.step_entry(state[ticker], row["signal"])
            state[ticker] = step.state
            bar = bar_for(ticker, date, row.get("total"))
            if step.should_enter and bar is not None:
                # 진입 가능 여부와 무관하게 이 전환은 이 봉에서 소진한다.
                # 그러지 않으면 상한에 막힌 전환이 살아남아 훗날 엉뚱한 봉에서
                # 뒤늦게 포지션을 연다 (trade_sim.simulate_ticker 와 같은 규칙).
                state[ticker] = ts.consume(state[ticker])
                if (ticker not in positions and ticker not in closed_today
                        and bar.atr14):
                    wants.append((ticker, row, bar))

        # ── 3. 자격 심사 ──
        # 총점 높은 순으로 자리를 준다. 동점은 티커 순으로 갈라 결과를 결정적으로
        # 만든다 - 그러지 않으면 dict 순서에 따라 백테스트가 흔들린다.
        wants.sort(key=lambda w: (-(w[1].get("total") or 0), w[0]))
        for ticker, row, bar in wants:
            if limits.max_positions and len(positions) >= limits.max_positions:
                rejected["capacity"] += 1
                continue
            clash = _too_correlated(ticker, list(positions), limits, correlator)
            if clash is not None:
                rejected["correlation"] += 1
                rejected_pairs.append((date, ticker, clash[0], round(clash[1], 3)))
                continue
            pos = er.open_position(ticker, date, bar.open, bar.atr14, params,
                                   row.get("target"))
            positions[ticker] = er.advance(pos, bar, params)
            sources[ticker] = row["source"]
            last_close[ticker] = bar.close

    # ── 유니버스에서 빠진 시장의 포지션을 청산 ──
    # 위 날짜 루프는 아카이브에 행이 있는 날만 돈다. 이 처리가 없으면 스캔
    # 대상에서 빠진 종목이 판정이 멈춘 채 남는다.
    for ticker in list(positions):
        decision = ts.universe_exit(
            positions[ticker], bars_by_ticker.get(ticker, {}),
            (universe_exits or {}).get(markets.get(ticker, "US")), params)
        if decision is not None:
            trades.append(ts.make_trade(positions[ticker],
                                        markets.get(ticker, "US"),
                                        sources.get(ticker, ""), decision.price,
                                        decision.date, decision.reason, costs))
            del positions[ticker]

    # ── 미결 포지션을 마지막 종가로 평가 ──
    for ticker, pos in positions.items():
        close = last_close.get(ticker)
        if close is not None:
            trades.append(ts.make_trade(pos, markets.get(ticker, "US"),
                                        sources.get(ticker, ""), close,
                                        None, None, costs))

    return {
        "trades": trades,
        "summary": ts.summarize(trades),
        "rejected": rejected,
        "rejected_pairs": rejected_pairs,
    }
