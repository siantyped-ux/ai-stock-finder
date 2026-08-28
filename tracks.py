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
        "entry_total": 70,
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
        "entry_total": 75,
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

    exit_rules.Params 를 여기서 만들지 않는 것은 max_correlation 이
    portfolio.Limits 를 만들지 않는 것과 같은 이유다 - tracks 는 아무것도
    임포트하지 않는 정의 모듈이고, 스캐너가 이것을 읽는다. 여기서 매매
    계층을 끌어오면 스캔이 백테스트 전체를 함께 임포트하게 된다.

    entry_total 과 exit_total 은 히스테리시스 밴드의 양끝이다. 들어갈 때는
    까다롭게(75), 한 번 들어가면 웬만해선 안 흔들리게(45). ETF 의 밴드가
    주식보다 넓은 것은 2026-08-28 실측 때문이다 - 7일간 ATR 손절은 한 번도
    걸리지 않았고 유일한 청산이 exit_total 이었다.

    45 는 calc_signal 의 AVOID 경계다. 50 은 어느 등급 경계도 아니어서
    "왜 50인가" 에 답할 근거가 없다.
    """
    p = paths(track)
    return {k: p[k] for k in ("entry_total", "exit_total",
                              "stop_atr_mult", "trail_atr_mult")}
