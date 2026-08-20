import smtplib

import pytest

import mailer


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


def test_creds_from_env_falls_back_to_the_process_environment(monkeypatch):
    # 워크플로는 인자 없이 부른다. 실제로 쓰이는 경로가 이쪽이다.
    monkeypatch.setenv("SMTP_USER", "tx@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("MAIL_TO", "rx@example.com")

    assert mailer.creds_from_env() == {
        "user": "tx@example.com", "password": "pw", "to": "rx@example.com"}


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
