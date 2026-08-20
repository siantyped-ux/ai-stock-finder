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
