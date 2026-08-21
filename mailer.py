"""메일 발송. 리포트도 스캔도 모른다.

성과 리포트와 실패 알림이 같은 SMTP 경로를 쓴다. 발송이 실패하면 예외를
그대로 올린다 - 조용히 안 보내는 것보다 잡이 실패하는 편이 낫다.

표준 라이브러리만 쓴다. notify-failure 잡이 pip install 없이 돌아야
하기 때문이다. 스캔이 의존성 설치에서 죽어도 알림은 나가야 한다.
.env 파서(envfile)도 python-dotenv 대신 직접 둔 것이 같은 이유다.

자격증명은 프로세스 환경이 우선이고 빈 자리를 .env 가 채운다. CI 는
워크플로 시크릿을, 로컬은 .env 를 쓰게 된다.

설계: docs/superpowers/specs/2026-08-20-report-mail-design.md
"""
from __future__ import annotations

import argparse
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Sequence

import console
import envfile

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

# cwd 가 아니라 이 파일 옆에서 찾는다. 워크플로는 저장소 루트에서 부르지만
# 로컬에서 다른 디렉터리에 있을 때도 같은 .env 를 봐야 한다.
ENV_FILE = Path(__file__).resolve().parent / ".env"


def creds_from_env(env=None, *, env_file=ENV_FILE) -> dict:
    """SMTP 자격증명을 읽어 send() 에 그대로 펼칠 수 있는 dict 로 낸다.

    하나라도 비면 어느 키가 없는지 밝히고 멈춘다. 시크릿 값은 로그에
    마스킹되므로 키 이름이 유일한 단서다.

    env 를 직접 주면 그게 전부다 - .env 는 보지 않는다. 인자 없이 부르면
    프로세스 환경이 우선이고 빈 자리를 .env 가 채운다. CI 에는 .env 가
    없으므로 워크플로 시크릿만 쓰이고, 로컬에서는 셸에 아무것도 없어도
    .env 만으로 보낼 수 있다.
    """
    if env is None:
        env = _merge_env(env_file)

    creds, missing = {}, []
    for key, name in ENV_KEYS.items():
        value = (env.get(key) or "").strip()
        if not value:
            missing.append(key)
        creds[name] = value

    if missing:
        raise RuntimeError("SMTP 환경변수가 비어 있다: " + ", ".join(missing))
    return creds


def _merge_env(env_file) -> dict:
    """.env 위에 프로세스 환경을 얹는다. 빈 값은 덮어쓰지 않는다.

    시크릿을 빈 값으로 등록하면 키는 있고 값만 없는데, 그 빈 값이 .env 를
    가려 버리면 값이 바로 옆에 있는데도 "환경변수가 비어 있다" 로 죽는다.
    """
    merged = envfile.load(env_file)
    for key in ENV_KEYS:
        value = (os.environ.get(key) or "").strip()
        if value:
            merged[key] = value
    return merged


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


def main() -> None:
    console.force_utf8()
    p = argparse.ArgumentParser(description="메일 한 통 발송")
    p.add_argument("--subject", required=True)
    p.add_argument("--body-file", required=True,
                   help="본문 텍스트 파일 (UTF-8)")
    p.add_argument("--attach", action="append", default=[],
                   help="첨부 파일. 여러 번 줄 수 있다")
    args = p.parse_args()

    body = Path(args.body_file).read_text(encoding="utf-8")
    send(args.subject, body, args.attach, **creds_from_env())
    print(f"메일 발송 완료: {args.subject}")


if __name__ == "__main__":
    main()
