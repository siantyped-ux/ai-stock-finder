"""보유 중인 포지션이 지금 어디서 잘리는지 보여준다.

이 전략에는 고정 익절가가 없다 - 이익 실현은 트레일링 스톱이 맡는다. 그래서
"얼마에 팔리나" 를 물으면 답할 값이 손절선 하나뿐이고, 그 손절선은 고점과
ATR 에 따라 매일 움직인다. 그 현재값을 읽는 것이 이 모듈의 전부다.

규칙은 전부 exit_rules 에 있고, 손절 상태는 trade_sim 이 Trade 에 실어 보낸다.
여기서 시뮬레이션을 다시 돌리지 않는다 - 돌리면 규칙이 두 벌이 되어 갈라진다.

설계: docs/superpowers/specs/2026-08-18-exit-rules-design.md
"""
from __future__ import annotations

import argparse

import backtest
import console
import exit_rules as er


def stop_view(trade, params: er.Params) -> dict:
    """미결 포지션 하나의 손절 상태를 읽기 쉬운 값으로 편다.

    trail_trigger 는 트레일링이 켜지는 가격이다. 손절 배수와 트레일 배수가
    같아서, 고점이 이 가격에 닿는 순간 손절선이 정확히 진입가가 된다
    (본전 이동). to_trigger_pct 는 최고가 기준으로 거기까지 남은 거리이며,
    이미 켜졌으면 None 이다.
    """
    if not trade.is_open:
        raise ValueError(
            f"{trade.ticker}: 청산된 트레이드다. 손절선은 보유 중일 때만 의미가 있다."
        )

    trigger = trade.entry_price + trade.r_unit
    active = trade.high_since_entry >= trigger

    return {
        "ticker": trade.ticker,
        "entry_date": trade.entry_date,
        "entry_price": trade.entry_price,
        "r_unit": trade.r_unit,
        "initial_stop": trade.initial_stop,
        "high_since_entry": trade.high_since_entry,
        "stop": trade.stop,
        "price": trade.mark_price,
        "trail_active": active,
        "trail_trigger": trigger,
        "to_trigger_pct": (None if active else
                           (trigger - trade.high_since_entry)
                           / trade.high_since_entry * 100.0),
        # 진입가 대비 1R. ATR 비례라 종목마다 다르다.
        "risk_pct": trade.r_unit / trade.entry_price * 100.0,
        # 현재가에서 손절선까지 남은 하락 여유.
        "room_pct": (trade.mark_price - trade.stop) / trade.mark_price * 100.0,
        "bars_held": trade.bars_held,
        "bars_left": params.max_hold_days - trade.bars_held,
    }


def build_views(trades: list, params: er.Params) -> list:
    """미결 포지션만 골라 손절선이 가까운 순으로 정렬한다.

    가장 위험한 포지션이 맨 위에 와야 눈에 띈다.
    """
    views = [stop_view(t, params) for t in trades if t.is_open]
    views.sort(key=lambda v: v["room_pct"])
    return views


def report(views: list, params: er.Params) -> None:
    if not views:
        print("보유 중인 포지션이 없다.")
        return

    print("=" * 78)
    print(f"[보유 포지션 손절선] {len(views)}건 · 손절선이 가까운 순")
    print(f"  손절 = 진입가 - ATR14 x {params.stop_atr_mult}"
          f" · 트레일 = 최고가 - ATR14 x {params.trail_atr_mult}"
          f" (진입가 +1R 도달 시 발동)")
    print(f"  고정 익절가는 없다. 보유 상한 {params.max_hold_days}거래일.")
    print()
    print(f"{'티커':<6}{'진입일':<12}{'진입가':>9}{'현재가':>9}"
          f"{'손절가':>9}{'여유':>8}{'1R':>8}{'트레일':>7}{'잔여봉':>7}")
    print("-" * 78)

    for v in views:
        print(f"{v['ticker']:<6}{v['entry_date']:<12}{v['entry_price']:>9.2f}"
              f"{v['price']:>9.2f}{v['stop']:>9.2f}{-v['room_pct']:>7.1f}%"
              f"{-v['risk_pct']:>7.1f}%"
              f"{'ON' if v['trail_active'] else 'off':>7}"
              f"{v['bars_left']:>7}")

    off = [v for v in views if not v["trail_active"]]
    if off:
        print()
        print("트레일 미발동 - 켜지려면 최고가가 여기까지 올라야 한다:")
        for v in sorted(off, key=lambda v: v["to_trigger_pct"]):
            print(f"  {v['ticker']:<6} {v['trail_trigger']:>9.2f}"
                  f"  (최고 {v['high_since_entry']:.2f} 에서 "
                  f"+{v['to_trigger_pct']:.1f}%)"
                  f"  → 켜지면 손절선이 {v['entry_price']:.2f} 로 점프")
    print("=" * 78)


def main():
    console.force_utf8()
    p = argparse.ArgumentParser(description="보유 포지션 손절선 조회")
    p.add_argument("--history", default="history/*.csv")
    p.add_argument("--stop-atr-mult", type=float, default=3.0)
    p.add_argument("--trail-atr-mult", type=float, default=3.0)
    p.add_argument("--max-hold-days", type=int, default=60)
    p.add_argument("--exit-total", type=int, default=60)
    args = p.parse_args()

    params = er.Params(
        stop_atr_mult=args.stop_atr_mult,
        trail_atr_mult=args.trail_atr_mult,
        max_hold_days=args.max_hold_days,
        exit_total=args.exit_total,
    )
    result = backtest.run(args.history, params)
    report(build_views(result["trades"], params), params)


if __name__ == "__main__":
    main()
