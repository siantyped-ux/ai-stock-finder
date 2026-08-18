import pandas as pd

import backtest as bt


def _flat_frame(n=20):
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame(
        dict(Open=[100.0] * n, High=[101.0] * n, Low=[99.0] * n,
             Close=[100.0] * n),
        index=idx,
    )


def test_atr_excludes_the_bar_it_is_attached_to():
    # 이 봉의 고저가 자기 ATR 에 들어가면, 개장 전에 정해져야 할 손절선이
    # 그날 장중 정보를 쓰게 된다. 계획 초안이 tr[:i] 로 잘라 실제로 샜다.
    calm = _flat_frame()
    spike = calm.copy()
    spike.iloc[-1, spike.columns.get_loc("High")] = 500.0
    spike.iloc[-1, spike.columns.get_loc("Low")] = 1.0

    last = sorted(bt.atr_series(calm))[-1]

    assert bt.atr_series(calm)[last] == bt.atr_series(spike)[last]


def test_atr_reflects_the_previous_bar():
    # 직전 봉의 변동은 반영돼야 한다. 아예 한 칸 더 잘라내면 그것도 사라진다.
    calm = _flat_frame()
    spike = calm.copy()
    spike.iloc[-2, spike.columns.get_loc("High")] = 500.0
    spike.iloc[-2, spike.columns.get_loc("Low")] = 1.0

    last = sorted(bt.atr_series(calm))[-1]

    assert bt.atr_series(spike)[last] > bt.atr_series(calm)[last]


def test_atr_needs_enough_history():
    # 15봉이면 TR 이 14개지만 그중 마지막은 당일 것이라 쓸 수 없다.
    short = _flat_frame(n=15)
    assert sorted(short.index)[-1].strftime("%Y-%m-%d") not in bt.atr_series(short)
