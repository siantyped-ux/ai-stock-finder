""".env 파서. 표준 라이브러리만 쓴다.

python-dotenv 를 쓰지 않는 것은 의도다. mailer.py 가 이 모듈을 임포트하는데,
notify-failure 잡은 pip install 없이 돌아야 한다 - 스캔이 의존성 설치에서
죽어도 알림은 나가야 하기 때문이다.

os.environ 을 건드리지 않고 dict 만 낸다. 부르는 쪽이 무엇을 우선할지
정한다 (mailer.creds_from_env 참조).
"""
from __future__ import annotations

import os
from pathlib import Path


def load(path: str | os.PathLike) -> dict[str, str]:
    """KEY=VALUE 줄을 읽어 dict 로 낸다. 파일이 없으면 빈 dict.

    CI 에는 .env 가 없다. 없는 것이 정상이므로 예외를 올리지 않는다.
    """
    path = Path(path)
    if not path.exists():
        return {}

    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # 값 안의 # 는 주석으로 보지 않는다. 비밀번호를 조용히 잘라 먹으면
        # 인증 오류만 남고 원인이 안 보인다.
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env
