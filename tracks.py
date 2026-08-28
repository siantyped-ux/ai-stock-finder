"""스캔 트랙 정의.

주식과 ETF 는 점수 척도가 달라 한 목록에서 순위를 비교할 수 없다. 2026-08-25
실측에서 총점 상위 20 이 전부 ETF 였고 주식은 23위에서야 나왔다 - ETF 총점
표준편차가 15.1 로 주식(10.3)의 1.5배라 꼬리가 두껍기 때문이다(축이 tech·flow
둘뿐이라 둘이 같이 높으면 극단값이 나온다). 평균은 오히려 ETF 가 낮다
(53.9 대 56.3). 그래서 임계를 손보는 것으로는 풀리지 않고, 유니버스부터 갈라
각자의 순위를 낸다.

산출물 경로가 한 곳에 모여 있어야 하는 이유는 단순하다. 스캐너와 리포트가
각자 정의를 들고 있으면 한쪽만 고쳐졌을 때 두 트랙이 같은 파일을 써서 서로를
덮어쓴다.

## max_correlation 이 트랙마다 다른 이유

ETF 는 같은 베팅의 복제본이 흔하다. 2026-08-22 스캔에서 BUY 90건이 나왔는데
그중 XLV·VHT·IYH·FHLC·IXJ 는 상관이 0.97~0.999 라 신호 다섯 개가 아니라 신호
하나를 다섯 번 센 것이었다. 주식에는 이 정도로 완전한 복제본이 없다.

0.90 은 추정이 아니라 실측이다 (2026-08-24, portfolio.Limits 참조). 같은
베팅으로 알려진 쌍은 0.974~0.999 에 몰리고 무관한 쌍은 -0.31~+0.33 에 있어,
그 사이 어디를 잘라도 결과가 같다. BUY ETF 39종목의 741개 쌍 중 0.90 을
넘는 것은 4.6% 뿐이라 진짜 복제본만 걸린다.

주식이 1.0(끔)인 것은 근거가 없어서다. 상관 상한이 주식에 이로운지 해로운지
재 본 적이 없고, 없는 근거로 켜면 그건 튜닝이 아니라 추측이다.
"""
from __future__ import annotations

TRACKS = {
    "stocks": {
        "label": "미국 주식",
        "history": "history",
        "dashboard": "dashboard_data.js",
        # 대시보드 전역 변수 접미사. 한 페이지가 두 파일을 함께 읽으므로
        # 이름이 같으면 나중에 로드된 쪽이 앞의 것을 덮어쓴다.
        "suffix": "",
        # 1.0 은 검사를 끈다는 뜻이다. 머리말 참조.
        "max_correlation": 1.0,
        # calc_signal 의 BUY 문턱과 같은 값이라 오늘 아카이브에서는 아무것도
        # 거르지 않는다. 구조적으로 무해한 것이 아니라 데이터가 그럴 뿐이다 -
        # total 이 빈 BUY 행이 한 건이라도 생기면 그때부터 거른다.
        "min_total": 70,
        "exit_total": 60,
        "stop_atr_mult": 3.0,
        "trail_atr_mult": 3.0,
    },
    "etf": {
        "label": "ETF",
        "history": "history_etf",
        "dashboard": "dashboard_data_etf.js",
        "suffix": "_ETF",
        "max_correlation": 0.90,
        "min_total": 75,
        "exit_total": 45,
        "stop_atr_mult": 3.0,
        "trail_atr_mult": 3.0,
    },
}


def paths(track: str) -> dict:
    """트랙의 산출물 경로. 모르는 트랙은 거부한다.

    오타를 조용히 기본값으로 흘리면 ETF 스캔이 주식 아카이브를 덮어쓴다.
    """
    try:
        return TRACKS[track]
    except KeyError:
        raise ValueError(
            f"알 수 없는 트랙: {track!r} ({'|'.join(TRACKS)} 중 하나여야 함)")


def history_glob(track: str) -> str:
    """그 트랙 아카이브의 glob 패턴."""
    return f"{paths(track)['history']}/*.csv"


def max_correlation(track: str) -> float:
    """그 트랙의 상관 상한. 1.0 이면 검사를 끈다.

    portfolio.Limits 를 여기서 만들지 않는 것은 의도다. tracks 는 아무것도
    임포트하지 않는 정의 모듈이고, portfolio 를 끌어오면 스캐너가 백테스트
    계층 전체를 함께 임포트하게 된다.
    """
    return paths(track)["max_correlation"]


def trade_params(track: str) -> dict:
    """그 트랙의 매매 파라미터 4종.

    키 이름은 전부 소비자의 파라미터 이름과 1:1 이다 - min_total 은
    backtest.filter_rows(min_total=), 나머지 셋은 exit_rules.Params 의 같은
    이름 필드로 그대로 들어간다. 번역 단계를 두지 않는 것은 의도다.

    특히 이것을 entry_total 이라 부르지 않는다. backtest.filter_rows 에 이미
    entry_total 이 있는데 그쪽은 문턱을 **내리는**(BUY 로 승격) 비교용
    손잡이라 방향이 정반대다. 같은 이름 둘이 한 함수 안에서 만나면 조용히
    뒤집힌 필터가 나온다.

    exit_rules.Params 를 여기서 만들지 않는 것은 max_correlation 이
    portfolio.Limits 를 만들지 않는 것과 같은 이유다 - tracks 는 아무것도
    임포트하지 않는 정의 모듈이고, 스캐너가 이것을 읽는다. 여기서 매매
    계층을 끌어오면 스캔이 백테스트 전체를 함께 임포트하게 된다.

    min_total 과 exit_total 은 히스테리시스 밴드의 양끝이다. 들어갈 때는
    까다롭게, 한 번 들어가면 웬만해선 안 흔들리게. ETF 가 75/45 로 주식
    70/60 보다 넓다.

    ## 45 의 근거는 약하다

    2026-08-28 실측이 말해 주는 것은 "exit_total 이 실제로 발동하는 유일한
    청산이고 ATR 손절은 7일간 한 번도 안 걸렸다" 까지다. 45 라는 값 자체는
    측정된 것이 아니라 calc_signal 의 AVOID 경계에서 고른 것이다 - exit_rules
    가 total < exit_total 로 판정하므로 45 는 "스캔이 회피로 볼 때 나간다" 와
    정확히 같아진다. 표본은 닫힌 트레이드 1건(SDOG)뿐이다. 아카이브가 30일을
    넘기면 45 와 60 을 나란히 재 볼 것.

    ## 두 ATR 배수는 같이 움직여야 한다

    고점이 진입가+1R 에 닿는 순간 트레일 손절선이 정확히 진입가가 되어
    "1R 도달 시 본전이동" 이 파라미터를 늘리지 않고 나온다. 한쪽만 바꾸면
    이 성질이 깨진다. 3.0 을 유지하는 것은 발동한 적이 없어 튜닝할 근거가
    0이기 때문이다 - 지금 올리면 효과가 포지션 축소로만 나타난다.
    """
    p = paths(track)
    return {k: p[k] for k in ("min_total", "exit_total",
                              "stop_atr_mult", "trail_atr_mult")}
