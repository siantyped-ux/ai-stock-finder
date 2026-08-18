# 청산 규칙 설계 (1단계)

작성일: 2026-08-18
상태: 승인됨

## 배경

`stock_finder.py`의 `calc_signal`은 STRONG_BUY / BUY / WATCH / HOLD / AVOID 다섯 등급을
내지만 전부 매수 방향이다. AVOID는 "사지 않는다"는 뜻이지 "판다"는 뜻이 아니므로,
진입한 포지션을 어떻게 빠져나오는지에 대한 규칙이 통째로 비어 있다.

자동매매에서 진입보다 청산이 성과를 크게 좌우한다. 손절이 없으면 한 종목의 손실이
전체 수익을 지울 수 있고, 청산 규칙이 없으면 백테스트 자체가 정의되지 않는다.
2단계 백테스트 하네스는 이 규칙 없이는 만들 수 없다.

v5 설계서(`AI_주식투자_에이전트_프롬프트_v5.xlsx`, Strategy Engineer 시트)가 이미
Entry/Exit/SL/TP/Trail/Size 스키마를 정의하고 있으므로 그것을 기준으로 삼는다.
다만 v5 스키마는 MNQ 선물 단일 전략을 상정하므로, 1,100종목 3개월 주식 스크리너에
맞게 조정한다.

## 목표

1. 진입한 포지션이 언제 어떤 가격에 청산되는지를 모호함 없이 정의한다.
2. 백테스트와 실거래가 같은 코드를 쓰도록 규칙을 한 모듈에 모은다.
3. v5의 과최적화 금지 원칙을 지킨다 — 파라미터 4개, 튜닝하지 않은 기본값.

## 비목표 (의도적 제외)

- **포지션 사이징·Kelly** — 3단계. `ev`가 캘리브레이션되기 전에는 사이징 근거가 없다.
- **포트폴리오 노출 한도·Kill Switch** — 4단계.
- **수수료·세금·슬리피지** — 2단계 백테스트 하네스의 책임.
- **공매도** — 스캐너가 매수 시그널만 낸다.
- **상장폐지·거래정지 처리** — 2단계.

## 설계 결정

| 항목 | 결정 | 근거 |
|---|---|---|
| 포지션 구조 | 개별 트레이드 | BUY가 하루 0~5건으로 희소해 바구니를 구성할 만큼 나오지 않는다. v5의 SL/TP/Trail 스키마와도 일치하고, 청산 사유(Stop/Trail/Time/Signal)가 트레이드마다 기록돼 무엇이 성과를 냈는지 분해할 수 있다 |
| 진입 조건 | `signal in (BUY, STRONG_BUY)` | 관측된 23개 스냅샷에서 STRONG_BUY는 0건이라 그것만으로는 거래가 발생하지 않는다. WATCH는 하루 90종목으로 너무 많다 |
| 파라미터 수 | 4개 | v5가 5개 초과를 금지한다. 고정 익절가를 빼서 4개를 맞췄다 |
| 손절 판정 | 증권사 스탑주문(장중 체결) | 하루 한 번 도는 시스템이지만 손절은 예약주문으로 위임할 수 있다. 종가 판정은 장중 급락 후 반등한 날 손절이 안 되고 실손실이 설계값을 넘는다 |

### 검토했으나 채택하지 않은 대안

- **포트폴리오 방식(상위 N종목 주기적 교체)**: 청산이 "바구니에서 빠지면 매도"로 암묵적이 되어
  개별 손절이 없다. v5의 "Trailing Stop 없는 전략 금지"에 정면 충돌한다.
- **혼합 방식(바구니 + 개별 손절)**: 파라미터가 6~8개로 늘어 v5 상한을 넘고,
  "순위 이탈"과 "손절"이 동시 발생할 때 우선순위 규칙이 또 필요해진다.
- **고정 익절가(R 배수 도달 시 부분청산)**: v5 스키마에 있으나 파라미터 상한에 걸린다.
  트레일링이 상방을 이미 처리하고, 고정 목표가는 추세가 길게 나올 때 수익을 잘라먹는다.
  3개월 추세 전략과 상극이다.
- **종가 판정 손절**: 아카이브만으로 백테스트가 가능해 단순하지만, 손실 제한이
  설계대로 작동하지 않는다. 가격은 언제든 재조회 가능하므로 OHLC 의존은 비용이 아니다.

## 모듈 구조

```
exit_rules.py               신규 · 청산 판정 (순수 함수, I/O 없음)
tests/test_exit_rules.py    신규
```

2단계 백테스트 하네스와 4단계 실행 엔진이 같은 모듈을 쓴다. 규칙이 한 곳에만
존재해야 백테스트와 실거래가 갈라지지 않는다.

`exit_rules.py`는 가격을 스스로 조회하지 않는다. 호출자가 `Bar`를 넘긴다.
`history/*.csv`에는 종가만 있고 고가·저가가 없으므로, 2단계 하네스가 OHLC를
별도로 재조회해 넘긴다. 가격은 시점이 지나도 언제든 복원 가능하므로 문제되지 않는다
(반면 스코어는 복원 불가라 아카이브가 필요했다).

## 데이터 모델

```python
@dataclass(frozen=True)
class Params:
    stop_atr_mult: float = 3.0
    trail_atr_mult: float = 3.0
    max_hold_days: int = 60
    exit_total: int = 60


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    atr14: Optional[float]   # 트레일링용 현재 ATR. 없으면 트레일링 미적용
    total: Optional[int]     # 그날 스코어. 없으면 SIGNAL 판정 생략


@dataclass(frozen=True)
class Position:
    ticker: str
    entry_date: str
    entry_price: float
    initial_stop: float
    r_unit: float            # entry_price - initial_stop
    high_since_entry: float
    stop: float              # 현재 유효 손절선 (래칫 - 내려가지 않음)
    bars_held: int


@dataclass(frozen=True)
class ExitDecision:
    reason: str              # "TIME" | "SIGNAL" | "STOP" | "TRAIL"
    price: float
    date: str
```

## 파라미터와 기본값

| 파라미터 | 기본값 | 근거 |
|---|---|---|
| `stop_atr_mult` | 3.0 | 3개월 보유에 v5 예시의 1.5×ATR(선물 단타 기준)은 며칠 만에 털린다. NVDA 실측 ATR14=6.84 / 종가 225 = 일간 3.0%이므로 3×ATR ≈ 9% |
| `trail_atr_mult` | 3.0 | Chandelier 표준값 |
| `max_hold_days` | 60 | 3개월 ≈ 63거래일 |
| `exit_total` | 60 | 진입 70(BUY 하한) / 청산 60(WATCH 하한). 넷 중 근거가 가장 약하다 |

두 ATR 배수를 같은 값으로 둔 것은 우연이 아니다. 고점이 진입가+1R에 닿는 순간
트레일 손절선이 정확히 진입가가 되어, v5의 "1R 도달 시 본전이동"이 파라미터를
추가하지 않고 자동으로 나온다.

**모든 기본값은 튜닝되지 않았다.** 백테스트가 없어 맞출 수 없고, v5의 과최적화
금지 원칙상 지금 맞춰서도 안 된다. 3단계에서 확정한다.

## 체결 시점

스캔은 KST 07:00에 돈다. KR 개장은 09:00 KST, US 개장은 22:30 KST(서머타임 기준)
이므로 **날짜 D의 스코어는 D 세션이 열리기 전에 이미 확정돼 있다.**

따라서 날짜 D 행의 시그널로 판단한 진입·청산은 모두 **D 세션 시가**에 체결된다.
D 스코어는 `bar_date` D-1 이하의 봉으로 산출되므로 미래 정보가 섞이지 않는다.

## 평가 순서

한 봉 안에서 여러 트리거가 동시에 발동할 수 있다. 시간 순서대로 판정한다.

```
1. TIME    bars_held >= max_hold_days      -> 시가 체결
2. SIGNAL  total < exit_total              -> 시가 체결
3. STOP    저가 <= 손절선                   -> 손절가 체결 (시가가 더 낮으면 시가)
```

TIME과 SIGNAL이 STOP보다 앞서는 이유는 **둘 다 개장 전에 결정되기 때문**이다.
`bars_held`는 결정론적이고 `total`은 07:00 스캔에서 이미 나와 있으므로, 둘 다
시가 시장가주문으로 나간다. 손절은 장중에 걸린 예약주문이라 시가 이후에야 체결된다.

STOP과 TRAIL은 같은 트리거이며 손절선이 초기값인지 트레일링값인지로 사유만 구분한다.

## 트레일링 손절

`advance` 가 봉마다 손절선을 갱신하며, **한 방향으로만 움직인다**.

```
트레일 활성: high_since_entry >= entry_price + r_unit
후보       : high_since_entry - trail_atr_mult * bar.atr14   (활성이고 atr14 있을 때)
손절선     : max(직전 손절선, 후보)
```

초기 손절은 **진입 시점 ATR** 로 고정한다 - R 정의가 흔들리면 안 된다.
트레일링은 **현재 ATR** 을 쓴다(Chandelier 표준). 3개월간 변동성이 크게 바뀌므로
트레일링까지 진입 시점 값에 묶으면 뒤로 갈수록 부정확해진다.

### 초기 손절선이 아니라 직전 손절선에 클램프하는 이유

초기값에만 클램프하면 손절선이 뒤로 물러선다. 고점 140 · ATR 2.0 에서 손절선이
134 였다가, 고점이 그대로인 채 ATR 이 10 으로 확대되면 110 으로 24포인트 후퇴한다.
확정된 이익 보호가 풀리는 것이다. 직전 손절선에 클램프하면 134 가 유지된다.

`bar.atr14` 가 없으면 후보를 계산하지 않고 **직전 손절선을 그대로 유지**한다.
초기 손절로 되돌리면 하루의 데이터 결측만으로 그동안 쌓인 보호가 전부 풀린다.

### "1R 도달 시 본전이동" 의 정확한 범위

두 ATR 배수가 같을 때 트레일 후보가 정확히 진입가가 되려면
`stop_atr_mult * atr_at_entry == trail_atr_mult * atr_now` 여야 하므로,
**진입 이후 ATR 이 변하지 않은 경우에만 정확히 성립한다.** 60일 보유 동안
변동성이 움직이면 근사치가 된다. 파라미터를 추가하지 않고 얻는 성질이라
유지할 가치가 있지만 보장으로 취급해서는 안 된다.


### 상태 갱신 순서 (룩어헤드 차단)

한 봉을 처리할 때 **평가를 먼저 하고 상태를 나중에 갱신한다.**

```python
decision = evaluate(position, bar, params)   # 어제까지의 high_since_entry 사용
if decision is None:
    position = advance(position, bar, params)  # 고점·손절선·bars_held 갱신
```

순서를 바꾸면 오늘 고가로 계산한 손절선이 오늘 장중에 체결되는 셈이 되어
룩어헤드가 된다. 실제로는 오늘 아침 시점에 알 수 있는 정보로만 손절선이 정해진다.

## 공개 함수

```python
def open_position(ticker, date, entry_price, atr_at_entry, params) -> Position
def current_stop(position, params, atr) -> float
def evaluate(position, bar, params) -> Optional[ExitDecision]
def advance(position, bar, params) -> Position
```

전부 순수 함수다. 파일 접근도 네트워크 접근도 하지 않는다.

## 결측·예외 처리

| 상황 | 처리 |
|---|---|
| 특정일 OHLC 결측 | 그날 평가와 갱신을 모두 건너뛴다. `bars_held`는 증가하지 않는다 |
| 진입일 시가 없음 | 진입 취소, 포지션 미생성 |
| `bar.atr14` 없음 | 트레일 후보 미계산, **직전 손절선 유지** |
| `bar.total` 없음 | SIGNAL 판정 생략, 나머지 트리거는 정상 평가 |
| `atr_at_entry`가 0 또는 None | `ValueError`. 손절폭 0은 R이 0이 되어 이후 모든 계산이 무의미해진다 |

`max_hold_days`는 달력일이 아니라 **데이터가 있는 세션 수**로 센다. `advance`가
`Bar`를 요구하므로 봉이 없는 날은 셀 방법이 없고, 별도 증가 경로를 두면 호출자마다
다르게 구현될 여지가 생긴다. 결측은 드물어 실질 영향이 작다.


## 테스트

1. **STOP** — 저가가 정확히 손절선일 때 체결 / 갭하락 시 시가 체결
2. **TRAIL** — 고점이 진입가+1R일 때 손절선이 정확히 진입가가 되는지
3. **TRAIL** — 손절선이 직전 값 아래로 내려가지 않는지 (고점 고정 · ATR 확대)
3-b. **TRAIL** — `atr14` 결측 봉에서 직전 손절선이 유지되는지
4. **TIME** — `bars_held == max_hold_days`에서 시가 체결
5. **SIGNAL** — `total < exit_total`에서 시가 체결
6. **히스테리시스** — `total`이 65일 때 청산되지 않는지 (진입 70 / 청산 60 사이)
7. **우선순위** — 같은 봉에서 TIME과 STOP이 동시 발동하면 TIME
8. **우선순위** — 같은 봉에서 SIGNAL과 STOP이 동시 발동하면 SIGNAL
9. **룩어헤드** — 오늘 고가가 트레일을 활성화시켜도 오늘 손절선에는 반영되지 않는지
10. **결측** — `atr14` 없을 때 초기 손절 유지, `total` 없을 때 SIGNAL 생략
11. **입력 검증** — `atr_at_entry=0`이면 `ValueError`

## 완료 기준

- `exit_rules.py`의 네 함수가 모두 순수 함수로 구현되고 위 테스트 11건이 통과한다.
- 파라미터는 정확히 4개이며 기본값이 코드에 명시돼 있다.
- 모듈이 `history.py`·`stock_finder.py`를 임포트하지 않는다 (순환 의존 없음).
