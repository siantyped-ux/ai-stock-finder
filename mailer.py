"""메일 발송. 리포트도 스캔도 모른다.

성과 리포트와 실패 알림이 같은 SMTP 경로를 쓴다. 발송이 실패하면 예외를
그대로 올린다 - 조용히 안 보내는 것보다 잡이 실패하는 편이 낫다.

표준 라이브러리만 쓴다. notify-failure 잡이 pip install 없이 돌아야
하기 때문이다. 스캔이 의존성 설치에서 죽어도 알림은 나가야 한다.

설계: docs/superpowers/specs/2026-08-20-report-mail-design.md
"""
from __future__ import annotations

import os

# 환경변수 이름 -> send() 의 키워드 인자 이름
ENV_KEYS = {"SMTP_USER": "user", "SMTP_PASSWORD": "password",
            "MAIL_TO": "to"}


def creds_from_env(env=None) -> dict:
    """SMTP 자격증명을 읽어 send() 에 그대로 펼칠 수 있는 dict 로 낸다.

    하나라도 비면 어느 키가 없는지 밝히고 멈춘다. 시크릿 값은 로그에
    마스킹되므로 키 이름이 유일한 단서다.
    """
    env = os.environ if env is None else env

    creds, missing = {}, []
    for key, name in ENV_KEYS.items():
        value = (env.get(key) or "").strip()
        if not value:
            missing.append(key)
        creds[name] = value

    if missing:
        raise RuntimeError("SMTP 환경변수가 비어 있다: " + ", ".join(missing))
    return creds
