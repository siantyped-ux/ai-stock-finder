"""메일 발송. 리포트도 스캔도 모른다.

성과 리포트와 실패 알림이 같은 SMTP 경로를 쓴다. 발송이 실패하면 예외를
그대로 올린다 - 조용히 안 보내는 것보다 잡이 실패하는 편이 낫다.

표준 라이브러리만 쓴다. notify-failure 잡이 pip install 없이 돌아야
하기 때문이다. 스캔이 의존성 설치에서 죽어도 알림은 나가야 한다.

설계: docs/superpowers/specs/2026-08-20-report-mail-design.md
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Sequence

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

# 첨부 MIME 을 직접 정한다. mimetypes.guess_type 은 OS 에 깔린 프로그램에
# 좌우된다 - 한컴오피스가 있는 머신은 .xlsx 를 application/haansoftxlsx 로
# 준다. 전역 레지스트리를 고쳐 쓰면 mailer 를 임포트한 다른 모듈까지 영향을
# 받으므로, 아는 확장자만 여기서 정하고 나머지는 octet-stream 으로 보낸다.
CONTENT_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
DEFAULT_CONTENT_TYPE = "application/octet-stream"

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


def build_message(subject: str, body: str,
                  attachments: Sequence[Path] = (), *,
                  to: str, user: str) -> EmailMessage:
    """평문 UTF-8 본문 + 첨부. 발송과 분리해 두면 테스트가 쉽다."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body, subtype="plain", charset="utf-8")

    for path in attachments:
        path = Path(path)
        ctype = CONTENT_TYPES.get(path.suffix.lower(), DEFAULT_CONTENT_TYPE)
        maintype, _, subtype = ctype.partition("/")
        msg.add_attachment(path.read_bytes(), maintype=maintype,
                           subtype=subtype, filename=path.name)
    return msg


def send(subject: str, body: str, attachments: Sequence[Path] = (), *,
         to: str, user: str, password: str,
         host: str = SMTP_HOST, port: int = SMTP_PORT) -> None:
    """한 통 보낸다. 실패하면 예외를 올린다 - 삼키지 않는다."""
    msg = build_message(subject, body, attachments, to=to, user=user)
    with smtplib.SMTP_SSL(host, port,
                          context=ssl.create_default_context()) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
