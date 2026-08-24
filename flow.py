"""수급(flow) 축. 일봉 OHLCV 만으로 매수세와 매도세를 잰다.

이 축을 만든 이유는 두 가지다.

1) ETF 점수 왜곡. ETF 는 개별기업 재무·공시가 없어 filing/value 를 계산할 수
   없고, 남은 tech/macro 두 축을 재정규화하면 tech 가중치가 0.35 에서 0.636
   으로 뛴다. 그런데 tech 는 분산된 ETF 에 구조적으로 유리하다 (2026-08-22
   실측 tech 평균 ETF 63.5 vs 주식 55.4). 유리한 축에 1.8배 가중치가 얹혀
   그날 BUY 90건 중 88건이 ETF 가 되었다. flow 는 ETF 도 계산할 수 있는
   축이라 결측 축 수를 줄여 이 증폭을 완화한다.

2) 호가잔량 요구. "매수잔량·매도잔량을 매매에 반영" 이라는 요구는 3개월
   보유 전략에서는 호가창이 아니라 여기서 답해야 한다. 최우선호가 잔량의
   예측력은 수 초~수 분 단위이고, 무엇보다 과거 호가 데이터가 없어
   백테스트로 검증할 수 없다. 검증할 수 없는 신호는 점수에 넣지 않는다.
   일봉 수급 지표는 같은 질문("사는 쪽이 강한가")을 3개월 시간축에서 묻고,
   과거 OHLCV 가 있으므로 검증도 된다.

파일도 네트워크도 건드리지 않는다. 가격은 호출자가 넘긴다.

축 구성 (기준 50 에서 가감 후 0~100 clip)
  · 매집/분산   CMF(20)                       ±15
  · 자금 방향   OBV 60일 기울기 vs 가격 기울기  ±15
  · 수급 강도   U/D Volume Ratio(50)          ±12
  · 유동성      거래대금 20일 평균 + 추세      ±8
"""
from __future__ import annotations

import numpy as np

# 최소 봉 수. calc_tech_score 와 같은 60 으로 맞춘다 - 한쪽만 통과하고
# 다른 쪽이 데이터 부족으로 빠지면 축 사이 비교가 깨진다.
MIN_BARS = 60

# 유동성 절대 수준 기준(달러). 거래대금 20일 평균이다.
# 하한 2e6 은 임의값이 아니다 - 3 ATR 손절폭에 해당하는 포지션을 하루
# 거래대금의 1% 안에서 세우려면 이 정도는 있어야 한다.
DOLLAR_VOL_HIGH = 50e6
DOLLAR_VOL_MID = 10e6
DOLLAR_VOL_LOW = 2e6


def cmf(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        vol: np.ndarray, period: int = 20) -> float:
    """Chaikin Money Flow. -1(매도우위) ~ +1(매수우위).

    종가가 그날 고저 범위의 어디에서 끝났는지에 거래량을 가중한다. 종가가
    상단에서 끝나면 그날 산 쪽이 이긴 것으로 본다.

    고가와 저가가 같은 봉(거래정지·상한가)은 승자를 가릴 수 없으므로 0 으로
    둔다. 0 으로 나누면 inf 가 섞여 합계 전체가 오염된다.
    """
    h, l, c, v = high[-period:], low[-period:], close[-period:], vol[-period:]
    rng = h - l
    mult = np.where(rng > 0, ((c - l) - (h - c)) / np.where(rng > 0, rng, 1), 0.0)
    vsum = float(np.sum(v))
    if vsum <= 0:
        return 0.0
    return float(np.sum(mult * v) / vsum)


def obv(close: np.ndarray, vol: np.ndarray) -> np.ndarray:
    """On-Balance Volume 누적 시계열.

    종가가 오른 날의 거래량은 더하고 내린 날은 뺀다. 보합은 0 이다.
    """
    direction = np.sign(np.diff(close))
    return np.concatenate([[0.0], np.cumsum(direction * vol[1:])])


def _std_slope(series: np.ndarray) -> float:
    """표준화한 시계열의 최소제곱 기울기 (일당 표준편차).

    표준화하는 것이 핵심이다. OBV 는 주(株) 단위이고 가격은 달러 단위라
    원 단위로는 기울기를 비교할 수 없다. 각자 자기 표준편차로 나누면 둘 다
    "하루에 몇 표준편차씩 움직였나" 가 되어 같은 자로 잴 수 있다.

    변동이 없는 구간(표준편차 0)은 기울기 0 으로 본다.
    """
    n = len(series)
    if n < 2:
        return 0.0
    sd = float(np.std(series))
    if sd <= 0:
        return 0.0
    y = (series - float(np.mean(series))) / sd
    x = np.arange(n, dtype=float)
    return float(np.polyfit(x, y, 1)[0])


def ud_volume_ratio(close: np.ndarray, vol: np.ndarray,
                    period: int = 50) -> float:
    """상승일 거래량 합 / 하락일 거래량 합.

    1 보다 크면 오르는 날에 거래가 몰린 것이다. 하락일 거래량이 0 이면
    비율이 무한대가 되므로 상한을 씌운다.
    """
    c, v = close[-(period + 1):], vol[-(period + 1):]
    if len(c) < 2:
        return 1.0
    d = np.diff(c)
    up = float(np.sum(v[1:][d > 0]))
    down = float(np.sum(v[1:][d < 0]))
    if down <= 0:
        return 10.0 if up > 0 else 1.0
    return min(up / down, 10.0)


def dollar_volume(close: np.ndarray, vol: np.ndarray,
                  period: int = 20) -> float:
    """최근 period 일 평균 거래대금(달러)."""
    return float(np.mean(close[-period:] * vol[-period:]))


def calc_flow_score(hist_df) -> tuple[int, list[str]]:
    """수급 점수와 근거. calc_tech_score 와 같은 계약이다.

    ETF 와 주식에 같은 함수를 쓴다 - 일봉 OHLCV 만 있으면 되므로 자산군을
    가릴 이유가 없다. 이것이 이 축을 만든 목적이기도 하다.
    """
    close = hist_df["Close"].values.astype(float)
    high = hist_df["High"].values.astype(float)
    low = hist_df["Low"].values.astype(float)
    vol = hist_df["Volume"].values.astype(float)

    if len(close) < MIN_BARS:
        return 40, [f"데이터 부족 ({MIN_BARS}봉 미만)"]

    # 유동성은 감점이 아니라 자격 요건이다. 이 축의 네 컴포넌트가 전부
    # 거래량 기반이라, 거래량이 없으면 감점할 지표가 아니라 믿을 수 없는
    # 지표가 된다. 2026-08-24 실측에서 TMH(거래대금 $0.0M)가 tech 71 · flow
    # 71 로 BUY 를 통과했다 - -8 감점으로는 노이즈로 부푼 나머지 컴포넌트를
    # 막지 못한다. 데이터 부족과 같은 취급으로 되돌린다.
    dv = dollar_volume(close, vol)
    if dv < DOLLAR_VOL_LOW:
        return 40, [f"거래대금 ${dv/1e6:.2f}M · 수급 판정 불가"]

    score = 50.0
    reasons = []

    # ── 매집/분산: CMF(20) ±15 ──
    c = cmf(high, low, close, vol)
    if c > 0.15:
        score += 15
        reasons.append(f"CMF {c:+.2f} · 강한 매집")
    elif c > 0.05:
        score += 8
        reasons.append(f"CMF {c:+.2f} · 매수 우위")
    elif c < -0.15:
        score -= 15
        reasons.append(f"CMF {c:+.2f} · 강한 분산")
    elif c < -0.05:
        score -= 8
        reasons.append(f"CMF {c:+.2f} · 매도 우위")

    # ── 자금 방향: OBV vs 가격 다이버전스 ±15 ──
    # 가격과 거래량이 같은 말을 하는지 본다. 가격만 오르고 OBV 가 빠지면
    # 오르는 동안 물량이 나가고 있다는 뜻이라 상승을 신뢰하지 않는다.
    win = min(60, len(close))
    p_slope = _std_slope(close[-win:])
    o_slope = _std_slope(obv(close, vol)[-win:])
    if p_slope > 0 and o_slope < 0:
        score -= 15
        reasons.append(f"가격↑ OBV↓ 다이버전스 · 상승 중 분산")
    elif p_slope < 0 and o_slope > 0:
        score += 15
        reasons.append(f"가격↓ OBV↑ 다이버전스 · 하락 중 매집")
    elif p_slope > 0 and o_slope > 0:
        score += 8
        reasons.append("가격·OBV 동반 상승 · 수급 확인")
    elif p_slope < 0 and o_slope < 0:
        score -= 8
        reasons.append("가격·OBV 동반 하락 · 수급 이탈")

    # ── 수급 강도: U/D Volume Ratio(50) ±12 ──
    ud = ud_volume_ratio(close, vol)
    if ud > 1.5:
        score += 12
        reasons.append(f"상승일 거래량 {ud:.2f}배 · 매수 집중")
    elif ud > 1.15:
        score += 6
        reasons.append(f"상승일 거래량 {ud:.2f}배")
    elif ud < 0.65:
        score -= 12
        reasons.append(f"하락일 거래량 우위 ({ud:.2f}배) · 매도 집중")
    elif ud < 0.85:
        score -= 6
        reasons.append(f"하락일 거래량 우위 ({ud:.2f}배)")

    # ── 유동성: 절대 수준 +5 · 추세 ±3 ──
    # 이 항목이 호가 스프레드 가드의 일봉 대용이다. 실시간 호가를 못 쓰는
    # 상황에서도 "실제로 살 수 있는 종목인가" 는 걸러야 한다. 하한 미달은
    # 위에서 이미 판정 불가로 걸렀으므로 여기서는 가점만 준다.
    if dv >= DOLLAR_VOL_HIGH:
        score += 5
        reasons.append(f"거래대금 ${dv/1e6:.0f}M · 유동성 충분")
    elif dv >= DOLLAR_VOL_MID:
        score += 2

    if len(close) >= 80:
        recent = dollar_volume(close, vol, 20)
        prev = float(np.mean(close[-80:-20] * vol[-80:-20]))
        if prev > 0:
            r = recent / prev
            if r > 1.2:
                score += 3
                reasons.append(f"거래대금 증가 {r:.1f}배 · 관심 유입")
            elif r < 0.7:
                score -= 3
                reasons.append(f"거래대금 감소 {r:.1f}배 · 관심 이탈")

    if not reasons:
        reasons.append("수급 특이사항 없음 · 중립")
    return int(np.clip(score, 0, 100)), reasons[:5]
