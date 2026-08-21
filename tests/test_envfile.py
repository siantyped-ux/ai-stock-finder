import envfile


def write(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_reads_key_value_pairs(tmp_path):
    path = write(tmp_path, "SMTP_USER=tx@example.com\nMAIL_TO=rx@example.com\n")

    assert envfile.load(path) == {"SMTP_USER": "tx@example.com",
                                  "MAIL_TO": "rx@example.com"}


def test_load_skips_blank_lines_and_comments(tmp_path):
    path = write(tmp_path, "# 주석\n\nSMTP_USER=tx@example.com\n   \n")

    assert envfile.load(path) == {"SMTP_USER": "tx@example.com"}


def test_load_strips_whitespace_around_key_and_value(tmp_path):
    path = write(tmp_path, "  SMTP_USER  =  tx@example.com  \n")

    assert envfile.load(path) == {"SMTP_USER": "tx@example.com"}


def test_load_strips_surrounding_quotes(tmp_path):
    # .env.example 은 따옴표 없이 쓰라고 하지만 붙여 두는 사람이 있다.
    path = write(tmp_path, 'SMTP_USER="tx@example.com"\nMAIL_TO=\'rx@example.com\'\n')

    assert envfile.load(path) == {"SMTP_USER": "tx@example.com",
                                  "MAIL_TO": "rx@example.com"}


def test_load_keeps_an_equals_sign_inside_the_value(tmp_path):
    # 앱 비밀번호에 = 가 들어갈 수 있다. 첫 = 에서만 자른다.
    path = write(tmp_path, "SMTP_PASSWORD=ab=cd=ef\n")

    assert envfile.load(path) == {"SMTP_PASSWORD": "ab=cd=ef"}


def test_load_keeps_a_hash_inside_the_value(tmp_path):
    # 값 안의 # 는 주석이 아니다. 비밀번호를 조용히 잘라 먹으면 인증만 깨진다.
    path = write(tmp_path, "SMTP_PASSWORD=pw#1234\n")

    assert envfile.load(path) == {"SMTP_PASSWORD": "pw#1234"}


def test_load_ignores_a_line_without_an_equals_sign(tmp_path):
    path = write(tmp_path, "쓰레기줄\nSMTP_USER=tx@example.com\n")

    assert envfile.load(path) == {"SMTP_USER": "tx@example.com"}


def test_load_returns_empty_when_the_file_is_missing(tmp_path):
    # CI 에는 .env 가 없다. 없는 것이 정상이므로 예외를 올리지 않는다.
    assert envfile.load(tmp_path / "없는파일.env") == {}
