# 리포트 메일 발송 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 매영업일 성과 리포트를 요약 본문 + XLSX 첨부로 메일 발송하고, 스캔·리포트 실패 알림을 GitHub 알림 설정과 무관하게 메일로 도착시킨다.

**Architecture:** 발송만 담당하는 `mailer.py` 를 표준 라이브러리 `smtplib` 로 새로 만든다. 리포트를 모르는 모듈이라 실패 알림과 리포트 발송이 같은 함수를 공유한다. 본문 생성은 요약 딕셔너리를 이미 들고 있는 `perf_report.summary_text()` 가 맡고, 콘솔 출력도 같은 함수를 쓰게 해 두 벌이 되지 않게 한다.

**Tech Stack:** Python 3.11 · smtplib/email (표준 라이브러리) · pytest · GitHub Actions · Gmail SMTP

**설계서:** `docs/superpowers/specs/2026-08-20-report-mail-design.md`

---

## 파일 구조

| 파일 | 책임 | 상태 |
|---|---|---|
| `mailer.py` | SMTP 발송 · 자격증명 읽기 · CLI 진입점 | 신규 |
| `tests/test_mailer.py` | 위 전부의 테스트 (SMTP 는 대역으로 갈아끼움) | 신규 |
| `perf_report.py` | `summary_text()` 추가, `--mail` 플래그, `main()` 출력 일원화 | 수정 |
| `tests/test_perf_report.py` | `summary_text()` 테스트 | 수정 |
| `.github/workflows/report.yml` | 빌드 스텝에 `--mail`, `notify-failure` 에 메일 | 수정 |
| `.github/workflows/scan.yml` | `notify-failure` 를 본문 파일 방식으로 바꾸고 메일 추가 | 수정 |

`mailer.py` 는 표준 라이브러리만 쓴다. `notify-failure` 잡이 `pip install` 없이 돌아야 하기 때문이다 — 스캔이 의존성 설치에서 죽었을 때도 알림은 나가야 한다. `requirements.txt` 는 건드리지 않는다.

**시크릿은 이미 등록돼 있다** (`SMTP_USER` / `SMTP_PASSWORD` / `MAIL_TO`, 2026-08-20 등록). 이 계획에는 시크릿 등록 작업이 없다.

---

### Task 1: 자격증명을 환경변수에서 읽는다

빠진 키를 이름으로 말해 주지 않으면, 워크플로에서 인증 실패가 났을 때 셋 중 뭐가 비었는지 알 수 없다. 시크릿 값은 로그에 안 찍히므로 이름이 유일한 단서다.

**Files:**
- Create: `mailer.py`
- Test: `tests/test_mailer.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_mailer.py` 를 새로 만든다:

```python
import pytest

import mailer


def test_creds_from_env_returns_send_kwargs():
    env = {"SMTP_USER": "tx@example.com", "SMTP_PASSWORD": "pw",
           "MAIL_TO": "rx@example.com"}
    assert mailer.creds_from_env(env) == {
        "user": "tx@example.com", "password": "pw", "to": "rx@example.com"}


@pytest.mark.parametrize("missing", ["SMTP_USER", "SMTP_PASSWORD", "MAIL_TO"])
def test_creds_from_env_names_the_missing_key(missing):
    env = {"SMTP_USER": "tx@example.com", "SMTP_PASSWORD": "pw",
           "MAIL_TO": "rx@example.com"}
    del env[missing]

    # 값은 로그에 안 찍힌다. 어느 키가 빈 건지는 말해 줘야 한다.
    with pytest.raises(RuntimeError) as e:
        mailer.creds_from_env(env)
    assert missing in str(e.value)


def test_creds_from_env_treats_blank_as_missing():
    # 시크릿을 빈 값으로 등록하면 키는 있고 값만 없다.
    env = {"SMTP_USER": "tx@example.com", "SMTP_PASSWORD": "   ",
           "MAIL_TO": "rx@example.com"}
    with pytest.raises(RuntimeError) as e:
        mailer.creds_from_env(env)
    assert "SMTP_PASSWORD" in str(e.value)
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_mailer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mailer'`

- [ ] **Step 3: 최소 구현**

`mailer.py` 를 새로 만든다:

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_mailer.py -v`
Expected: PASS (5 passed — parametrize 3개 포함)

- [ ] **Step 5: 커밋**

```bash
git add mailer.py tests/test_mailer.py
git commit -m "Read the SMTP credentials from the environment"
```

---

### Task 2: 메일 한 통을 보낸다

**Files:**
- Modify: `mailer.py`
- Test: `tests/test_mailer.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_mailer.py` 의 `import mailer` 아래에 대역과 픽스처를 넣고, 파일 끝에 테스트를 추가한다:

```python
import smtplib


class FakeSMTP:
    """smtplib.SMTP_SSL 대역. 실제 접속은 하지 않는다."""

    last = None

    def __init__(self, host, port, context=None):
        self.host, self.port, self.context = host, port, context
        self.credentials = None
        self.messages = []
        FakeSMTP.last = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.credentials = (user, password)

    def send_message(self, msg):
        self.messages.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.last = None
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


CREDS = dict(to="rx@example.com", user="tx@example.com", password="pw")
```

이어서 파일 끝에:

```python
def test_send_fills_the_headers_and_a_utf8_body(fake_smtp):
    mailer.send("제목", "본문 한글", **CREDS)

    msg = fake_smtp.last.messages[0]
    assert msg["Subject"] == "제목"
    assert msg["From"] == "tx@example.com"
    assert msg["To"] == "rx@example.com"
    assert msg.get_content().strip() == "본문 한글"


def test_send_logs_in_to_gmail_over_ssl(fake_smtp):
    mailer.send("제목", "본문", **CREDS)

    assert (fake_smtp.last.host, fake_smtp.last.port) == ("smtp.gmail.com", 465)
    assert fake_smtp.last.credentials == ("tx@example.com", "pw")


def test_send_without_attachments_is_a_single_part(fake_smtp):
    mailer.send("제목", "본문", **CREDS)
    assert not fake_smtp.last.messages[0].is_multipart()


def test_send_attaches_the_xlsx_under_its_own_name(tmp_path, fake_smtp):
    xlsx = tmp_path / "perf_2026-08-20.xlsx"
    xlsx.write_bytes(b"PK\x03\x04fake")

    mailer.send("제목", "본문", [xlsx], **CREDS)

    parts = list(fake_smtp.last.messages[0].iter_attachments())
    assert len(parts) == 1
    assert parts[0].get_filename() == "perf_2026-08-20.xlsx"
    assert parts[0].get_content_type() == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert parts[0].get_payload(decode=True) == b"PK\x03\x04fake"


def test_send_lets_smtp_errors_through(fake_smtp, monkeypatch):
    def boom(self, user, password):
        raise smtplib.SMTPAuthenticationError(535, b"bad password")

    monkeypatch.setattr(FakeSMTP, "login", boom)

    # 삼키면 메일이 안 갔는데 잡은 성공한다. 그게 지금 고치려는 상태다.
    with pytest.raises(smtplib.SMTPAuthenticationError):
        mailer.send("제목", "본문", **CREDS)
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_mailer.py -v`
Expected: FAIL — `AttributeError: module 'mailer' has no attribute 'smtplib'` (픽스처 수집 단계)

- [ ] **Step 3: 최소 구현**

`mailer.py` 에서 `import os` 한 줄을 아래 블록으로 교체한다. `ENV_KEYS` 와
`creds_from_env()` 는 그대로 둔다:

```python
import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Sequence

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
```

이어서 파일 끝에 추가:

```python
def build_message(subject: str, body: str,
                  attachments: Sequence = (), *,
                  to: str, user: str) -> EmailMessage:
    """평문 UTF-8 본문 + 첨부. 발송과 분리해 두면 테스트가 쉽다."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body, subtype="plain", charset="utf-8")

    for path in attachments:
        path = Path(path)
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(path.read_bytes(), maintype=maintype,
                           subtype=subtype, filename=path.name)
    return msg


def send(subject: str, body: str, attachments: Sequence = (), *,
         to: str, user: str, password: str,
         host: str = SMTP_HOST, port: int = SMTP_PORT) -> None:
    """한 통 보낸다. 실패하면 예외를 올린다 - 삼키지 않는다."""
    msg = build_message(subject, body, attachments, to=to, user=user)
    with smtplib.SMTP_SSL(host, port,
                          context=ssl.create_default_context()) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_mailer.py -v`
Expected: PASS (10 passed)

`mimetypes.guess_type("perf_2026-08-20.xlsx")` 가 xlsx MIME 을 못 찾아
`test_send_attaches_the_xlsx_under_its_own_name` 이 실패하면, `ctype` 폴백
대신 확장자 매핑을 명시한다 — `mailer.py` 의 상수 블록에 추가:

```python
# 일부 환경의 mimetypes 레지스트리에 xlsx 가 없다. 명시해 둔다.
mimetypes.add_type(
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsx")
```

- [ ] **Step 5: 커밋**

```bash
git add mailer.py tests/test_mailer.py
git commit -m "Send a plain-text mail with the report attached"
```

---

### Task 3: 본문 파일을 받는 CLI 진입점

실패 알림 잡이 이슈 본문 파일을 그대로 메일 본문으로 재사용한다. 같은 텍스트를 두 벌 쓰지 않기 위한 진입점이다.

**Files:**
- Modify: `mailer.py`
- Test: `tests/test_mailer.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_mailer.py` 끝에 추가:

```python
def test_cli_sends_the_body_file_as_the_body(tmp_path, fake_smtp, monkeypatch):
    body = tmp_path / "alert_body.txt"
    body.write_text("스캔이 실패했습니다\n로그: http://example/run/1",
                    encoding="utf-8")
    monkeypatch.setenv("SMTP_USER", "tx@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("MAIL_TO", "rx@example.com")
    monkeypatch.setattr("sys.argv", [
        "mailer.py", "--subject", "[스캔실패] 2026-08-20 (KST)",
        "--body-file", str(body),
    ])

    mailer.main()

    msg = fake_smtp.last.messages[0]
    assert msg["Subject"] == "[스캔실패] 2026-08-20 (KST)"
    assert "스캔이 실패했습니다" in msg.get_content()
    assert not msg.is_multipart()


def test_cli_attaches_every_attach_flag(tmp_path, fake_smtp, monkeypatch):
    body = tmp_path / "body.txt"
    body.write_text("본문", encoding="utf-8")
    xlsx = tmp_path / "perf_2026-08-20.xlsx"
    xlsx.write_bytes(b"PK\x03\x04fake")
    monkeypatch.setenv("SMTP_USER", "tx@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("MAIL_TO", "rx@example.com")
    monkeypatch.setattr("sys.argv", [
        "mailer.py", "--subject", "제목", "--body-file", str(body),
        "--attach", str(xlsx),
    ])

    mailer.main()

    parts = list(fake_smtp.last.messages[0].iter_attachments())
    assert [p.get_filename() for p in parts] == ["perf_2026-08-20.xlsx"]
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_mailer.py -k cli -v`
Expected: FAIL — `AttributeError: module 'mailer' has no attribute 'main'`

- [ ] **Step 3: 최소 구현**

`mailer.py` 의 `import mimetypes` 위에 `import argparse` 를, `from typing import
Sequence` 아래에 빈 줄과 `import console` 을 넣는다. 결과는 이렇게 된다:

```python
import argparse
import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Sequence

import console
```

`console` 은 표준 라이브러리만 쓰므로 pip install 없이 임포트된다.

파일 끝에 추가:

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_mailer.py -v`
Expected: PASS (12 passed)

- [ ] **Step 5: 커밋**

```bash
git add mailer.py tests/test_mailer.py
git commit -m "Add a CLI entry point that mails a body file"
```

---

### Task 4: 요약 딕셔너리를 본문 텍스트로

콘솔 출력과 메일 본문이 거의 같은 내용이다. 두 벌로 두면 갈라진다.

**Files:**
- Modify: `perf_report.py` (`_write_summary` 바로 위에 추가)
- Test: `tests/test_perf_report.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_perf_report.py` 끝에 추가:

```python
SUMMARY = {
    "generated": "2026-08-20 10:00 KST",
    "archive_from": "2026-08-01", "archive_to": "2026-08-19",
    "live_rows": 300, "backfill_rows": 800, "backfill_pct": 72.7,
    "mark_date": "2026-08-19", "failed": [],
    "closed_n": 12, "win_rate": 58.333,
    "gross_krw": 1_500_000, "net_krw": 1_180_000, "avg_net_pct": 2.1,
    "open_n": 8, "open_net_krw": 2_240_000, "capital": 10_000_000,
}


def test_summary_text_totals_realised_and_unrealised():
    text = pr.summary_text(SUMMARY)

    # 총 손익 = 실현 + 미실현
    assert "+3,420,000원" in text
    assert "+1,180,000원" in text
    assert "+2,240,000원" in text


def test_summary_text_carries_the_closed_stats():
    text = pr.summary_text(SUMMARY)

    assert "청산 12건" in text
    assert "58.3%" in text
    assert "+2.10%" in text
    assert "보유 8종목" in text


def test_summary_text_survives_zero_closed_trades():
    s = dict(SUMMARY, closed_n=0, win_rate=None, avg_net_pct=None,
             net_krw=0)

    # None 을 포맷하면 터진다. 청산 표본이 없는 지금이 바로 그 상태다.
    text = pr.summary_text(s)
    assert "청산 0건" in text
    assert "None" not in text


def test_summary_text_lists_the_failed_tickers():
    assert "없음" in pr.summary_text(SUMMARY)
    assert "AAA, BBB" in pr.summary_text(dict(SUMMARY, failed=["AAA", "BBB"]))


def test_summary_text_leads_with_the_warning():
    text = pr.summary_text(SUMMARY)

    # 메일은 첨부를 안 열고 본문만 훑게 만든다. 경고가 첨부 안에만
    # 있으면 없는 것과 같다.
    assert text.startswith("!!")
    assert "파이프라인 검증용" in text
    assert "73% 가 backfill" in text
    assert "60거래일" in text
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_perf_report.py -k summary_text -v`
Expected: FAIL — `AttributeError: module 'perf_report' has no attribute 'summary_text'`

- [ ] **Step 3: 최소 구현**

`perf_report.py` 의 `def _write_summary(ws, s: dict) -> None:` 바로 위에 추가:

```python
def summary_text(s: dict) -> str:
    """요약 딕셔너리를 콘솔·메일 공용 본문으로 만든다.

    win_rate 와 avg_net_pct 는 청산완료가 0건이면 None 이다. 포맷하면
    터지므로 건수만 적는다.

    총 수익률(%) 은 싣지 않는다. capital 은 종목당 투자금이라 총 투입금
    대비 수익률을 내려면 청산 자금을 재투자하지 않는다는 가정을 몰래
    들여오게 된다.
    """
    if s["closed_n"]:
        closed_note = (f"청산 {s['closed_n']}건, 승률 {s['win_rate']:.1f}%, "
                       f"평균 순수익률 {s['avg_net_pct']:+.2f}%")
    else:
        closed_note = "청산 0건"

    failed = ", ".join(s["failed"]) if s["failed"] else "없음"
    total = s["net_krw"] + s["open_net_krw"]

    return "\n".join([
        "!! 이 리포트는 파이프라인 검증용이다. 시그널 성능의 근거가 아니다.",
        f"   아카이브의 {s['backfill_pct']:.0f}% 가 backfill 이라 스코어가 "
        "미확정 봉 결함에 오염돼 있다.",
        "   보유 상한 60거래일을 채운 표본이 나오기 전까지 승률·평균은 "
        "무의미하다.",
        "",
        f"총 손익      {total:+,.0f}원",
        f"  └ 실현     {s['net_krw']:+,.0f}원  ({closed_note})",
        f"  └ 미실현   {s['open_net_krw']:+,.0f}원  (보유 {s['open_n']}종목)",
        "",
        f"평가기준일   {s['mark_date']}",
        f"시세 조회 실패: {failed}",
    ])
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_perf_report.py -k summary_text -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add perf_report.py tests/test_perf_report.py
git commit -m "Turn the summary into one body of text"
```

---

### Task 5: --mail 플래그

**Files:**
- Modify: `perf_report.py` (import 블록, `main()` 끝부분)

- [ ] **Step 1: import 추가**

`perf_report.py` 의 `import history` 아래 줄에 추가 (알파벳 순서 유지):

```python
import mailer
```

- [ ] **Step 2: main() 에 플래그와 발송을 넣는다**

`perf_report.py:301` 부근 `main()` 에서 아래 두 곳을 바꾼다.

인자 정의 — `p.add_argument("--capital", ...)` 다음 줄에 추가:

```python
    p.add_argument("--mail", action="store_true",
                   help="리포트를 메일로 보낸다 (SMTP_* 환경변수 필요)")
```

`main()` 의 마지막 4줄을 통째로 교체한다. 기존:

```python
    s = built["summary"]
    print(f"{path} 작성 완료")
    print(f"  청산완료 {s['closed_n']}건 · 누적 순수익 {s['net_krw']:+,.0f}원")
    print(f"  미결 {s['open_n']}건 · 평가 순손익 {s['open_net_krw']:+,.0f}원")
```

교체 후:

```python
    body = summary_text(built["summary"])
    print(f"{path} 작성 완료")
    print(body)

    if args.mail:
        # 발송 실패를 삼키지 않는다. 조용히 안 보내는 것보다 잡이 실패하는
        # 편이 낫다 - fetch_fx 가 고정환율로 대체하지 않는 것과 같다.
        subject = f"[성과리포트] {stamp} (KST)"
        mailer.send(subject, f"{body}\n\n상세는 첨부된 {path.name} 참고",
                    [path], **mailer.creds_from_env())
        print(f"메일 발송 완료: {subject}")
```

- [ ] **Step 3: 플래그 없이 기존 동작이 그대로인지 확인**

Run: `python perf_report.py`
Expected: `reports/perf_2026-08-20.xlsx 작성 완료` 뒤에 경고 3줄과 손익 요약이
출력된다. 메일은 나가지 않는다.

- [ ] **Step 4: 전체 테스트 확인**

Run: `python -m pytest -q`
Expected: 전부 PASS (기존 테스트가 깨지지 않았는지 본다)

- [ ] **Step 5: 커밋**

```bash
git add perf_report.py
git commit -m "Mail the report when --mail is given"
```

---

### Task 6: report.yml 에 발송을 붙인다

**Files:**
- Modify: `.github/workflows/report.yml`

- [ ] **Step 1: 빌드 스텝에 --mail 과 시크릿을 넣는다**

`Build the performance report` 스텝을 교체한다. 기존:

```yaml
      - name: Build the performance report
        id: build
        run: |
          python perf_report.py
          # KST 는 서머타임이 없어 UTC+9 고정. TZ=Asia/Seoul 은 tzdata 가 없는
          # 환경에서 조용히 UTC 를 KST 라고 찍으므로 오프셋으로 계산한다.
          echo "kst_date=$(date -u -d '+9 hours' +'%Y-%m-%d')" >> "$GITHUB_OUTPUT"
```

교체 후:

```yaml
      - name: Build and mail the performance report
        id: build
        env:
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
          MAIL_TO: ${{ secrets.MAIL_TO }}
        run: |
          python perf_report.py --mail
          # KST 는 서머타임이 없어 UTC+9 고정. TZ=Asia/Seoul 은 tzdata 가 없는
          # 환경에서 조용히 UTC 를 KST 라고 찍으므로 오프셋으로 계산한다.
          echo "kst_date=$(date -u -d '+9 hours' +'%Y-%m-%d')" >> "$GITHUB_OUTPUT"
```

artifact 업로드 스텝은 건드리지 않는다. 메일이 유실돼도 90일간 원본이 남고,
`if-no-files-found: error` 가 XLSX 생성 자체를 계속 검증한다.

- [ ] **Step 2: notify-failure 잡을 통째로 교체한다**

`.github/workflows/report.yml` 의 `notify-failure:` 부터 파일 끝까지를 아래로 바꾼다:

```yaml
  # 리포트가 실패하면 그날 성과 스냅샷이 비는데, 스캔과 달리 원천 데이터는
  # 남아 있으므로 재실행으로 복구된다. 그래도 조용히 넘어가면 며칠씩 비는
  # 것을 눈치채지 못하므로 이슈를 열고 메일을 보낸다.
  #
  # 이슈는 기록이고 메일이 도착 보장이다. 이슈만으로는 알림이 가지 않는다 -
  # 저장소 Watch 인원이 0이면 봇이 연 이슈는 아무에게도 닿지 않는다.
  notify-failure:
    needs: report
    if: always() && needs.report.result == 'failure'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v7

      - name: Setup Python 3.11
        uses: actions/setup-python@v6
        with:
          python-version: '3.11'

      # mailer.py 는 표준 라이브러리만 쓴다. pip install 을 하지 않는 것은
      # 의도다 - 의존성 설치가 죽어서 실패한 경우에도 알림은 나가야 한다.

      - name: Write the alert body
        id: body
        env:
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          # KST 는 서머타임이 없어 UTC+9 고정
          KST_DATE=$(date -u -d '+9 hours' +'%Y-%m-%d')
          echo "kst_date=$KST_DATE" >> "$GITHUB_OUTPUT"
          cat > alert_body.txt <<EOB
          ${KST_DATE} (KST) 성과 리포트 생성이 실패했습니다.

          스캔과 달리 원천 데이터(history/*.csv)는 남아 있으므로
          **재실행으로 복구됩니다.** 워크플로를 수동 실행하세요.

          실행 로그: ${RUN_URL}

          ## 확인할 것

          - **환율 조회 실패** — \`USDKRW=X 환율 조회 실패\` 가 보이면 yfinance
            rate limit 이거나 티커 응답이 빈 경우입니다. 고정환율로 대체하지
            않고 일부러 실패시킵니다 - 조용히 틀린 금액을 커밋하지 않으려는 것입니다.
          - **환율 소급 실패** — \`... 이전의 환율이 없다\` 는 조회 시작일이
            첫 진입일보다 충분히 앞서지 않은 경우입니다.
          - **시세 조회 실패** — 요약 시트의 \`시세 조회 실패\` 항목을 보세요.
            일부 종목 실패는 리포트를 막지 않습니다.
          - **메일 발송 실패** — \`SMTP 환경변수가 비어 있다\` 면 시크릿이
            빠진 것이고, 인증 오류면 앱 비밀번호가 폐기된 것입니다.
          - **artifact 업로드 실패** — \`if-no-files-found: error\` 이므로
            \`reports/\` 가 비면 여기서 멈춥니다. 앞선 빌드 스텝의 로그를 보세요.
          EOB

      - name: Open an issue for the missing report
        id: issue
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          REPO: ${{ github.repository }}
          KST_DATE: ${{ steps.body.outputs.kst_date }}
        run: |
          TITLE="성과 리포트 실패 ${KST_DATE} (KST)"
          echo "title=$TITLE" >> "$GITHUB_OUTPUT"

          # 같은 날 재실행으로 이슈가 중복되지 않게 한다. 메일도 같이 건너뛴다 -
          # 재실행마다 같은 메일이 쌓일 이유가 없다.
          EXISTING=$(gh issue list --repo "$REPO" --state open \
            --search "\"$TITLE\" in:title" --json number --jq 'length')
          if [ "$EXISTING" != "0" ]; then
            echo "이미 열린 이슈가 있다. 건너뛴다."
            echo "notify=no" >> "$GITHUB_OUTPUT"
            exit 0
          fi

          echo "notify=yes" >> "$GITHUB_OUTPUT"
          gh issue create --repo "$REPO" --title "$TITLE" \
            --assignee siantyped-ux --body-file alert_body.txt

      - name: Send the alert mail
        if: steps.issue.outputs.notify == 'yes'
        env:
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
          MAIL_TO: ${{ secrets.MAIL_TO }}
        run: |
          python mailer.py \
            --subject "[리포트실패] ${{ steps.body.outputs.kst_date }} (KST)" \
            --body-file alert_body.txt
```

- [ ] **Step 3: YAML 문법 확인**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/report.yml',encoding='utf-8')); print('OK')"`
Expected: `OK`

(`yaml` 이 없으면 `pip install pyyaml` 후 실행한다. 개발 편의용이라
`requirements.txt` 에는 넣지 않는다.)

- [ ] **Step 4: 커밋**

```bash
git add .github/workflows/report.yml
git commit -m "Mail the daily report and its failures"
```

---

### Task 7: scan.yml 실패 알림에 메일을 붙인다

**Files:**
- Modify: `.github/workflows/scan.yml`

- [ ] **Step 1: notify-failure 잡을 통째로 교체한다**

`.github/workflows/scan.yml` 의 `notify-failure:` 부터 파일 끝까지를 아래로 바꾼다.
앞의 주석 블록(`# 스캔이 실패하면 그날 스코어는 영구히...`)은 그대로 둔다:

```yaml
  notify-failure:
    needs: scan
    if: always() && needs.scan.result == 'failure'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v7

      - name: Setup Python 3.11
        uses: actions/setup-python@v7
        with:
          python-version: '3.11'

      # mailer.py 는 표준 라이브러리만 쓴다. pip install 을 하지 않는 것은
      # 의도다 - 스캔이 의존성 설치에서 죽어도 알림은 나가야 한다.

      - name: Write the alert body
        id: body
        env:
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          # KST 는 서머타임이 없어 UTC+9 고정
          KST_DATE=$(date -u -d '+9 hours' +'%Y-%m-%d')
          echo "kst_date=$KST_DATE" >> "$GITHUB_OUTPUT"
          cat > alert_body.txt <<EOB
          ${KST_DATE} (KST) 자동 스캔이 실패했습니다.

          **이 날짜의 스코어는 복원할 수 없습니다.** 스코어는 시점 데이터라
          나중에 다시 계산할 수 없고, 소급 적재는 \`dashboard_data.js\` 커밋
          스냅샷에서 복원하는 방식인데 스캔이 실패하면 그 스냅샷도 없습니다.

          실행 로그: ${RUN_URL}

          ## 확인할 것

          - **수집률 미달** — 로그에 \`수집률 ... 기준\` 이 있으면 완결성 가드가
            부분 데이터를 막은 것입니다. yfinance rate limit(429)이 흔한 원인이며,
            \`--workers\` 를 낮추면 완화됩니다.
          - **API 키 만료** — FMP / DART / FRED 중 하나가 죽으면 스캔이 멈춥니다.
          - **timeout** — 120분 상한에 걸렸는지 확인하세요.
          - **push 실패** — rebase 재시도 3회를 모두 소진했는지 확인하세요.

          복구되면 이 이슈를 닫아 주세요. 하루치 공백은 남지만, 며칠씩 조용히
          비는 것을 막는 것이 이 알림의 목적입니다.
          EOB

      - name: Open an issue for the lost day
        id: issue
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          REPO: ${{ github.repository }}
          KST_DATE: ${{ steps.body.outputs.kst_date }}
        run: |
          TITLE="스캔 실패 ${KST_DATE} (KST) - 그날 스코어 유실"
          echo "title=$TITLE" >> "$GITHUB_OUTPUT"

          # 같은 날 재실행으로 이슈가 중복되지 않게 한다. 메일도 같이 건너뛴다 -
          # 재실행마다 같은 메일이 쌓일 이유가 없다.
          EXISTING=$(gh issue list --repo "$REPO" --state open \
            --search "\"$TITLE\" in:title" --json number --jq 'length')
          if [ "$EXISTING" != "0" ]; then
            echo "이미 열린 이슈가 있다. 건너뛴다."
            echo "notify=no" >> "$GITHUB_OUTPUT"
            exit 0
          fi

          echo "notify=yes" >> "$GITHUB_OUTPUT"
          gh issue create --repo "$REPO" --title "$TITLE" \
            --assignee siantyped-ux --body-file alert_body.txt

      - name: Send the alert mail
        if: steps.issue.outputs.notify == 'yes'
        env:
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
          MAIL_TO: ${{ secrets.MAIL_TO }}
        run: |
          python mailer.py \
            --subject "[스캔실패] ${{ steps.body.outputs.kst_date }} (KST) - 그날 스코어 유실" \
            --body-file alert_body.txt
```

- [ ] **Step 2: YAML 문법 확인**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/scan.yml',encoding='utf-8')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: 커밋**

```bash
git add .github/workflows/scan.yml
git commit -m "Mail the scan failure alongside the issue"
```

---

### Task 8: 실제 발송 검증

여기까지는 전부 대역이었다. Gmail 이 앱 비밀번호를 받아 주는지, 첨부가 열리는지는 실제로 한 번 보내 봐야 안다.

**Files:** 없음 (실행만)

- [ ] **Step 1: 브랜치를 push 한다**

```bash
git push -u origin feat/report-mail
```

- [ ] **Step 2: 브랜치에서 리포트 워크플로를 수동 실행한다**

```bash
gh workflow run report.yml --ref feat/report-mail
```

`workflow_dispatch` 트리거가 이미 정의돼 있으므로 별도 설정이 필요 없다.

- [ ] **Step 3: 실행 결과를 본다**

```bash
gh run list --workflow=report.yml --limit 1
gh run view --log | grep -E "메일 발송 완료|SMTP|Traceback"
```

Expected: `메일 발송 완료: [성과리포트] YYYY-MM-DD (KST)`

실패 시 진단:
- `SMTP 환경변수가 비어 있다: ...` → 해당 시크릿 이름이 틀렸다. `gh secret list` 로 확인한다.
- `SMTPAuthenticationError (535)` → 앱 비밀번호가 틀렸거나 폐기됐다. 공백 없이 16자로 다시 등록한다.
- `SMTPSenderRefused` → `SMTP_USER` 가 앱 비밀번호를 발급한 계정과 다르다.

- [ ] **Step 4: 받은 메일을 확인한다**

`siantyped@gmail.com` 받은편지함에서 확인할 것:
- 제목 `[성과리포트] YYYY-MM-DD (KST)`
- 본문 첫 줄이 `!!` 경고로 시작하는지
- 한글이 깨지지 않는지
- `perf_YYYY-MM-DD.xlsx` 첨부가 Excel 에서 열리는지 (3시트)

- [ ] **Step 5: 실패 알림 경로를 검증한다**

리포트를 일부러 실패시켜 `notify-failure` 를 태운다. `perf_report.py` 의
`fetch_fx` 티커를 잠깐 존재하지 않는 값으로 바꿔 커밋·push 한 뒤 실행한다:

```bash
sed -i 's/USDKRW=X/USDKRW-BROKEN/' perf_report.py
git commit -am "TEMP: break the fx lookup to test the alert path"
git push
gh workflow run report.yml --ref feat/report-mail
```

확인할 것: 이슈가 열리고 assignee 가 `siantyped-ux` 인지, `[리포트실패] ...`
메일이 도착하는지.

검증이 끝나면 되돌린다:

```bash
git revert --no-edit HEAD
git push
```

열린 테스트 이슈는 닫는다:

```bash
gh issue list --state open
gh issue close <번호> --comment "알림 경로 검증용 이슈. 닫는다."
```

- [ ] **Step 6: main 에 머지한다**

REQUIRED SUB-SKILL: `superpowers:finishing-a-development-branch` 로 마무리한다.

---

## 검증 체크리스트

구현이 끝났다고 말하기 전에 전부 확인한다:

- [ ] `python -m pytest -q` 전부 통과
- [ ] `python perf_report.py` (플래그 없이) 가 예전과 같이 동작하고 메일을 안 보낸다
- [ ] 실제 메일이 도착했고 첨부 XLSX 가 열린다
- [ ] 실패 경로에서 이슈 + 메일이 둘 다 나갔다
- [ ] `git grep -n "xluq\|SMTP_PASSWORD *=" -- ':!docs'` 가 앱 비밀번호를 찾지 못한다 (public 저장소다)
