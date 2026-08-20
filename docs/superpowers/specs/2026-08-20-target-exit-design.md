# 목표가 익절(TARGET)과 목표구간 리포트 설계

작성일: 2026-08-20
상태: 검토 대기

## 배경

대시보드에는 종목마다 `목표가 상승률`이 표시된다. `stock_finder.py`의
`calc_ev_and_target()`이 내는 값으로, `target = round(ev * 12)`을 `[-15, 30]`으로
클립한 3개월 기대 상승률(%)이다. 이 값은 아카이브 CSV의 `target` 컬럼에 매일
저장돼 있다.

그런데 이 숫자는 지금 아무 데도 쓰이지 않는다.

- `exit_rules.py`의 청산 조건은 ATR 손절 / 트레일 / 최대보유 60일 / 시그널소멸
  넷뿐이라, 목표가에 닿아도 이익을 실현하지 않는다.
- `perf_report.py`의 미결포지션 시트는 손절선만 보여준다. "어디서 잘리는가"는
  답하지만 "어디까지 가면 되는가"는 답하지 않는다.

2026-08-19 기준으로 보유 5종목 중 DVN은 목표가 48.83에 1.33% 남은 상태(달성률
84.1%)인데, 현재 규칙으로는 목표가를 그냥 통과한다. 청산된 2건(YPF·FISV)은
목표 근처에도 못 가보고 SIGNAL 소멸로 끝났다.

## 목표

1. 목표가 도달 시 청산하는 `TARGET` 사유를 `exit_rules.py`에 추가한다.
   기본값은 꺼짐이고, 파라미터로 켠다.
2. `perf_report.py` 미결포지션 시트에 목표%·목표가·달성률·위험보상 네 컬럼을
   추가한다. 익절 규칙의 on/off와 무관하게 항상 표시한다.

## 비목표 (의도적 제외)

- **부분 익절** — 절반 익절 후 잔량 트레일링 같은 규칙. 파라미터가 늘고
  포지션 상태가 분기한다. 표본이 없는 지금 도입할 근거가 없다.
- **목표가 트레일링** — 목표가를 진입 후에 재계산하는 것. 아래 "설계 결정"
  참조.
- **목표% 튜닝** — `ev * 12`의 계수 12를 건드리지 않는다. 과최적화 금지 원칙.
- **청산완료 시트 컬럼 확장** — `청산사유`에 `TARGET`이 새로 등장하는 것으로
  충분하다.
- **`use_target` 기본값 변경** — 아래 "지금 알 수 없는 것" 참조.

## 지금 이 기능으로 알 수 있는 것과 없는 것

3단계 리포트의 경고가 그대로 유효하다. 아카이브의 90%가 `backfill`이라
스코어가 미확정 봉 결함에 오염돼 있고, 보유 상한 60거래일을 채운 표본이 아직
하나도 없다.

여기에 이 기능 고유의 미확정 요소가 하나 더 붙는다. 2026-08-19 기준 7개
포지션의 **위험보상비(목표폭 ÷ 손절폭)는 0.86~1.45**다.

| 종목 | 위험보상 |
|---|---|
| C | 1.45 |
| YPF | 1.21 |
| EXE | 1.11 |
| BWA | 1.04 |
| DVN | 0.95 |
| FISV | 0.90 |
| CNC | 0.86 |

셋은 1 미만이다. 목표를 다 채워도 손절 한 번 맞는 것보다 덜 번다는 뜻이다.
`target = ev * 12`는 3개월 기대치인데 ATR 3.0 손절폭은 그보다 짧은 호흡이라,
두 축의 시간 지평이 애초에 맞지 않는다.

**따라서 TARGET 익절이 기대값을 올리는지 내리는지 지금은 알 수 없다.**
기본값을 끈 채로 두고, `--use-target`으로 켠 백테스트와 나란히 비교할 수 있게
만드는 것이 이 설계의 입장이다.

## 설계 결정

| 항목 | 결정 | 근거 |
|---|---|---|
| 목표% 시점 | 진입일 스캔의 `target`으로 고정 | `initial_stop`을 진입 시점 ATR로 고정하는 것과 같은 논리다. 목표가가 도중에 움직이면 "목표 달성"의 정의가 흔들리고 달성률을 비교할 수 없다. 백테스트 재현성도 깨진다 |
| 목표가 보관 위치 | `Position.target_price` → `Trade.target_price` | 규칙은 `exit_rules`, 상태는 `Trade`, 뷰는 재시뮬 없이 읽기만. `stops.py`가 이미 따르는 원칙이다 |
| 기본 동작 | `Params.use_target = False` | 익절이 돈이 되는지 모르는 상태에서 기본값을 바꾸지 않는다. 기존 리포트·테스트 130개가 그대로 통과한다 |
| 파라미터 개수 | 4개 → 5개 | v5 설계서의 상한이 5개다. 준수 |
| 동시 도달 판정 | 손절 우선 (TIME → SIGNAL → STOP/TRAIL → TARGET) | 일봉만으로는 고가와 저가 중 어느 쪽이 먼저였는지 알 수 없다. 백테스트가 실제보다 좋게 나오는 것보다 나쁘게 나오는 편이 안전하다. 기존 `evaluate`가 갭하락 시 시가 체결을 잡는 비관적 가정과 같은 방향이다 |
| 체결가 | `max(bar.open, target_price)` | 갭상승으로 시가가 이미 목표 위면 그 시가에 체결된다. 갭하락 처리 `min(bar.open, stop)`의 대칭 |
| 목표% ≤ 0 방어 | `target_price = None`, 규칙 비활성 | 목표가가 진입가 이하면 "익절"이 즉시 손실 확정이 된다. 현재 아카이브의 BUY 행 37건은 전부 `target` 9~13%라 걸리지 않지만, `ev`가 낮은 BUY가 앞으로 나올 수 있다 |
| 리포트 표시 조건 | `use_target`과 무관하게 항상 표시 | 컬럼의 목적은 "목표까지 얼마나 왔나"이지 익절 예고가 아니다. 대신 요약 시트에 규칙 on/off를 한 줄 적어 혼동을 막는다 |

## 데이터 흐름

```
history/*.csv  (target 컬럼, 이미 존재)
  └─ backtest.run
       prepared 행에 "target" 추가
       └─ trade_sim.simulate_ticker
            진입하는 봉의 target 만 사용 (이후 스캔의 target 은 무시)
            └─ exit_rules.open_position(target_pct=...)
                 └─ Position.target_price 확정
                      ├─ exit_rules.evaluate → ExitDecision("TARGET", ...)
                      └─ trade_sim._make_trade → Trade.target_price
                           └─ perf_report.build_rows  (읽기만, 재시뮬 없음)
```

## 컴포넌트별 변경

### `exit_rules.py`

- `Params`에 `use_target: bool = False` 추가.
- `Position`에 `target_price: Optional[float]` 추가.
- `open_position(ticker, date, entry_price, atr_at_entry, params, target_pct)`:
  `target_pct`가 `None`이거나 `<= 0`이면 `target_price = None`,
  아니면 `entry_price * (1 + target_pct / 100)`.
- `evaluate` 판정 순서에 TARGET을 맨 뒤로 추가:

  ```
  TIME    bars_held >= max_hold_days        → bar.open
  SIGNAL  total < exit_total                → bar.open
  STOP    bar.low <= stop, 트레일 미발동     → min(bar.open, stop)
  TRAIL   bar.low <= stop, 트레일 발동       → min(bar.open, stop)
  TARGET  use_target and bar.high >= target  → max(bar.open, target_price)
  ```

- `ExitDecision.reason` 주석에 `"TARGET"` 추가.
- `current_stop`·`advance`는 변경 없음.

### `trade_sim.py`

- `Trade`에 `target_price: Optional[float]` 추가. 기본값을 두지 않는다 —
  `mark_price`와 같은 규약으로, 값을 빠뜨린 생성이 조용히 통과하면 안 된다.
- `_make_trade`가 `pos.target_price`를 그대로 전달.
- `simulate_ticker`가 진입 시 `row["target"]`을 `open_position`에 넘김.

### `backtest.py`

- `prepared` dict에 `"target"` 추가 (`int(r["target"]) if r["target"] else None`).
- `main()`에 `--use-target` 플래그 추가 → `Params(use_target=True)`.

### `stops.py`

- 변경 없음. 손절선 조회 전용 모듈이고, 목표가는 리포트 쪽 관심사다.
- 단 `main()`의 `Params` 생성은 `use_target` 기본값(False)을 그대로 쓴다.

### `perf_report.py`

`OPEN_COLS` 끝에 네 컬럼 추가:

| 헤더 | 행 키 | 서식 | 계산 |
|---|---|---|---|
| 목표(%) | `target_pct` | `PCT_FMT` | `(목표가 / 진입가 - 1) * 100` |
| 목표가 | `target_price` | `PRICE_FMT` | `Trade.target_price` |
| 달성률(%) | `target_progress_pct` | `PCT_FMT` | `(현재가 - 진입가) / (목표가 - 진입가) * 100` |
| 위험보상 | `reward_risk` | `RATE_FMT` | `(목표가 - 진입가) / r_unit` |

`목표(%)`는 `Trade.target_price`에서 되계산한다. 아카이브의 `target` 정수값을
`Position`·`Trade`에 따로 싣지 않는다 — 같은 사실을 두 필드에 보관하면 둘이
어긋날 수 있고, 목표가가 유일한 진실이어야 한다. 부동소수 오차로 `9`가
`9.000000000000002`이 되지만 `PCT_FMT`가 소수 두 자리로 자른다.

- `target_price`가 `None`이면 네 값 모두 `None`(빈칸).
- 달성률은 음수가 나올 수 있다. 진입가 아래라는 뜻이고, `PCT_FMT`가 `-`를 붙인다.
- 요약 시트 `[가정]`에 한 줄 추가:
  `목표가 익절 | 사용 안 함 (--use-target 으로 켬)` / 켜져 있으면
  `사용함 (목표가 도달 시 청산)`.
- `build_rows`가 `params.use_target`을 읽어 그 문장을 고른다.

## 에러 처리

| 상황 | 동작 |
|---|---|
| 아카이브에 `target` 컬럼 없음 | `None`으로 읽고 규칙 비활성. 예전 백필 파일이 섞여도 죽지 않는다 |
| `target` 값이 빈 문자열 | 위와 같음 |
| `target <= 0` | `target_price = None`, 규칙 비활성, 리포트 빈칸 |
| `r_unit`이 0 | `open_position`이 이미 `ValueError`로 막는다. 변경 없음 |
| 청산된 트레이드의 달성률 | 미결포지션 시트에만 컬럼이 있으므로 해당 없음 |

## 테스트

### `test_exit_rules.py`

- `use_target=True`, `bar.high >= target_price` → `TARGET`, 체결가 `target_price`
- 갭상승(`bar.open > target_price`) → 체결가 `bar.open`
- 같은 봉에서 `bar.high >= target` **and** `bar.low <= stop` → `STOP`/`TRAIL` 우선
- `use_target=False` → 목표가를 뚫어도 `None`
- `target_pct`가 `None` / `0` / 음수 → `target_price is None`, 규칙 비활성
- TIME·SIGNAL이 TARGET보다 먼저 잡히는지

### `test_trade_sim.py`

- `target_price`가 `Trade`까지 전달되는지
- 진입 후 아카이브의 `target`이 바뀌어도 목표가가 고정인지
- `use_target=True`인 시뮬레이션에서 `TARGET` 청산이 실제로 나오는지

### `test_perf_report.py`

- 네 컬럼의 값 (달성률 음수 포함)
- `target_price`가 `None`일 때 네 칸 모두 빈칸
- 요약 `[가정]`의 익절 on/off 문장 두 가지

### 회귀

기존 130개는 `use_target=False`가 기본이므로 전부 그대로 통과해야 한다.
하나라도 깨지면 기본값이 새 나간 것이다.

## 검증 방법

구현 후 같은 아카이브로 두 번 돌려 비교한다.

```
python backtest.py                  # 기존 결과와 완전히 동일해야 함
python backtest.py --use-target     # TARGET 청산이 섞인 결과
```

첫 번째가 기존과 한 글자라도 다르면 기본값이 샌 것이다.
