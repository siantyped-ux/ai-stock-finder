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
    },
    "etf": {
        "label": "ETF",
        "history": "history_etf",
        "dashboard": "dashboard_data_etf.js",
        "suffix": "_ETF",
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
