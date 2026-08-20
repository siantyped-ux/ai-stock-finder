# 리포트 메일 발송 · 실패 알림 경로 설계

작성일: 2026-08-20
상태: 검토 대기

## 배경

3단계에서 성과 리포트가 매영업일 KST 10:00에 자동 생성되고, 실패하면
`notify-failure` 잡이 이슈를 연다. 두 경로 모두 **도착하지 않는다.**

리포트는 Actions artifact 로만 올라간다. 받으려면 GitHub에 로그인해
Actions 탭에서 실행을 찾아 zip 을 내려받아야 한다. 매일 할 일이 아니다.

실패 알림은 더 나쁘다. 2026-08-18 스캔 실패 때 `notify-failure` 잡은
정상 동작했고 [issue #1] 이 실제로 만들어졌다. 그런데 알림 대상이 없다:

| 항목 | 값 |
|---|---|
| `subscribers_count` (Watch 인원) | 0 |
| 이슈 assignee | 없음 |
| 이슈 본문 @멘션 | 없음 |

`github-actions[bot]` 이 연 이슈는 Watch 중이거나 담당자·멘션된 사람에게만
알림이 간다. 셋 다 비어 있으니 GitHub 이 보낼 대상이 없고, 메일도 나가지
않았다. 저장소에 메일을 보내는 코드는 애초에 한 줄도 없다 — 알림 전체가
GitHub 개인 설정에 의존하고 있었고, 그 설정은 저장소에서 확인할 수 없다.

[issue #1]: https://github.com/siantyped-ux/ai-stock-finder/issues/1

## 목표

1. 매영업일 리포트를 요약 본문 + XLSX 첨부로 메일 발송한다.
2. 스캔·리포트 실패를 GitHub 알림 설정과 무관하게 메일로 도착시킨다.
3. 발송이 실패하면 조용히 넘어가지 않는다.

## 비목표 (의도적 제외)

- **HTML 메일·차트 이미지** — 본문은 텍스트다. 폰에서 읽히는 것이 목적이고,
  상세는 첨부가 담당한다.
- **수신자 다중화·구독 관리** — 수신자는 한 명이다. 리스트가 필요해지면 그때.
- **메일 발송 재시도** — Gmail SMTP 일시 장애는 다음 영업일 실행으로 복구된다.
  원천 데이터가 남아 있어 수동 재실행도 된다. 재시도 루프는 실패를 흐린다.
- **발송 이력 저장** — 저장소가 public 이다. 손익 금액이 담긴 메일 본문을
  커밋할 이유가 없다. artifact 와 `history/*.csv` 로 충분하다.
- **스캔 결과(대시보드) 메일 발송** — 대시보드는 GitHub Pages 로 이미 공개된다.
  메일이 필요한 것은 손익 리포트와 실패 알림뿐이다.

## 설계 결정

| 항목 | 결정 | 근거 |
|---|---|---|
| 발송 경로 | Gmail SMTP + 앱 비밀번호 | 비용 0원, 일 500통 한도(평시 1일 1통), 첨부 가능. 외부 서비스 가입·도메인 인증이 없다 |
| 구현 위치 | `mailer.py` 신설 (표준 라이브러리 `smtplib`) | `requirements.txt` 변경이 없고, 앱 비밀번호가 서드파티 액션 코드로 넘어가지 않는다. 공개 저장소라 공급망 노출을 줄인다 |
| 본문 생성 | `perf_report.py` 의 `summary_text()` | 요약 딕셔너리를 이미 들고 있는 곳이다. `mailer.py` 는 리포트를 모른다 |
| 실패 알림 | 이슈 + SMTP 메일 + assignee | 이슈는 기록, 메일은 도착 보장. assignee 는 한 줄이라 같이 넣는다 |
| 발송 실패 시 | 잡을 실패시킨다 | `fetch_fx` 가 고정환율로 대체하지 않고 실패하는 것과 같은 원칙이다. 조용히 안 보내는 것보다 실패가 낫다 |
| 자격증명 | GitHub Secrets 3개 | `.env` 는 커밋되지 않고 Actions 에 없다. Secrets 는 로그에서 자동 마스킹된다 |
| 총 수익률(%) | 싣지 않는다 | 아래 참조 |

### 총 수익률 %를 싣지 않는 이유

요약 딕셔너리의 `capital` 은 **종목당** 투자금(1,000만원)이다. 총 투입금 대비
수익률을 내려면 `capital × (closed_n + open_n)` 을 분모로 써야 하는데, 이는
청산된 자금을 회수해 재투자하지 않는다는 가정을 몰래 들여온다. 실제 운용과
다르고 표본이 늘수록 분모만 커져 수익률이 희석된다.

본문에는 실재하는 값만 쓴다: 금액(`net_krw`, `open_net_krw`)과 청산완료만으로
계산된 `win_rate`·`avg_net_pct`.

### 경고문을 본문에 싣는 이유

요약 시트 최상단 경고 3줄(파이프라인 검증용 · backfill 오염 · 표본 부족)을
메일 본문에도 그대로 싣는다.

메일은 첨부를 열지 않고 본문만 훑게 만든다. 손익 금액만 매일 눈에 들어오면
3단계 설계서가 요약 시트 맨 위에 경고를 고정 배치해 막으려던 오독이 그대로
재현된다. 경고가 첨부 안에만 있으면 없는 것과 같다.

## 아키텍처

```
report.yml ─▶ perf_report.py --mail
               ├─ build_rows()          ──▶ summary dict
               ├─ write_xlsx()          ──▶ reports/perf_YYYY-MM-DD.xlsx
               ├─ summary_text(summary) ──▶ 본문 문자열
               └─ mailer.send(본문, 첨부=[xlsx], **mailer.creds_from_env())

scan.yml ─▶ (실패 시) notify-failure
               ├─ 이슈 본문을 파일로 쓴다
               ├─ gh issue create --assignee siantyped-ux --body-file ...
               └─ python mailer.py --subject "..." --body-file ...
```

### mailer.py 의 단위

리포트를 모른다. 제목·본문·첨부를 받아 보내는 것이 전부다.

```python
def creds_from_env() -> dict
    """SMTP_USER / SMTP_PASSWORD / MAIL_TO 를 읽는다.

    하나라도 비면 어느 키가 없는지 밝히고 RuntimeError 를 올린다.
    """

def send(subject: str, body: str, attachments: Sequence[Path] = (), *,
         to: str, user: str, password: str,
         host: str = "smtp.gmail.com", port: int = 465) -> None
    """SMTP_SSL 로 한 통 보낸다. 실패하면 예외를 올린다."""

def main() -> None
    """CLI: --subject S --body-file F [--attach P ...]

    실패 알림 잡이 이슈 본문 파일을 그대로 재사용하기 위한 진입점이다.
    """
```

`EmailMessage` 를 쓴다. 본문은 UTF-8 평문, 첨부는
`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`.

### perf_report.py 수정 2건

**1. `summary_text(s: dict) -> str` 추가**

`_write_summary` 가 쓰는 것과 같은 딕셔너리를 받아 본문 문자열을 만든다.
`main()` 의 기존 콘솔 출력 3줄과 중복되므로, `main()` 도 이 함수를 쓰도록
바꾼다 — 콘솔과 메일이 갈라지지 않게 한다.

`win_rate` 와 `avg_net_pct` 는 청산완료가 0건이면 `None` 이다. 이때는 해당
줄을 `(청산 0건)` 으로 대체한다. `None` 을 포맷하면 터진다.

**2. `--mail` 플래그 추가**

없으면 지금과 완전히 같이 동작한다. 있으면 XLSX 를 쓴 뒤 발송한다.
발송 예외는 잡지 않는다 — 위로 올라가 잡을 실패시킨다.

### 본문 형식

```
제목: [성과리포트] 2026-08-20 (KST)

!! 이 리포트는 파이프라인 검증용이다. 시그널 성능의 근거가 아니다.
   아카이브의 73% 가 backfill 이라 스코어가 미확정 봉 결함에 오염돼 있다.
   보유 상한 60거래일을 채운 표본이 나오기 전까지 승률·평균은 무의미하다.

총 손익      +3,420,000원
  └ 실현     +1,180,000원  (청산 12건, 승률 58.3%, 평균 순수익률 +2.10%)
  └ 미실현   +2,240,000원  (보유 8종목)

평가기준일   2026-08-19
시세 조회 실패: 없음

상세는 첨부된 perf_2026-08-20.xlsx 참고
```

제목의 날짜는 `history.kst_now()` 기준이다. XLSX 파일명과 같은 값을 쓴다.

## 워크플로 수정

### report.yml

빌드 스텝을 `python perf_report.py --mail` 로 바꾸고 SMTP 시크릿 3개를 `env`
로 넣는다. artifact 업로드는 그대로 둔다 — 메일이 유실돼도 90일간 원본이 남고,
`if-no-files-found: error` 가 XLSX 생성 자체를 검증하는 역할을 계속한다.

### scan.yml

`notify-failure` 잡에서:

1. 이슈 본문을 히어독으로 파일에 쓴다 (지금은 `gh issue create --body "$(cat <<EOB ...)"` 로 인라인이다). 같은 텍스트를 이슈와 메일 양쪽에 쓰기 위한 변경이다.
2. `gh issue create --body-file` + `--assignee siantyped-ux` 로 바꾼다.
3. `python mailer.py --subject "[스캔실패] ..." --body-file ...` 스텝을 잇는다.

메일 스텝은 이슈 생성 뒤에 둔다. 순서가 반대면 메일 발송이 실패했을 때 기록이
남지 않는다. 중복 이슈로 건너뛰는 경로(`EXISTING != 0`)에서는 메일도 보내지
않는다 — 같은 날 재실행마다 메일이 쌓일 이유가 없다.

`report.yml` 의 `notify-failure` 도 같은 방식으로 바꾼다.

두 워크플로의 `notify-failure` 잡은 파이썬을 쓰게 되므로 `actions/checkout` 과
`actions/setup-python` 을 붙인다. `mailer.py` 는 표준 라이브러리만 쓰므로
`pip install` 은 필요 없다.

## 필요한 GitHub Secrets

| 이름 | 값 |
|---|---|
| `SMTP_USER` | `siantyped@gmail.com` |
| `SMTP_PASSWORD` | Google 앱 비밀번호 16자리 |
| `MAIL_TO` | `siantyped@gmail.com` |

앱 비밀번호는 2단계 인증을 켠 뒤 https://myaccount.google.com/apppasswords
에서 발급한다. 계정 비밀번호가 아니다 — 유출돼도 앱 비밀번호만 폐기하면 된다.

**저장소가 public 이므로** 이 값들은 반드시 Secrets 로만 넣는다. 워크플로
YAML·코드·`.env.example` 어디에도 실제 값을 쓰지 않는다.

## 테스트

`tests/test_mailer.py` (신규) — `smtplib.SMTP_SSL` 을 monkeypatch 로 갈아끼운다.
실제 발송은 하지 않는다.

| 케이스 | 검증 |
|---|---|
| 기본 발송 | `Subject`·`To`·`From` 헤더, 본문 UTF-8 디코딩, `login()` 인자 |
| 첨부 있음 | 파트 2개, 파일명과 MIME 타입 |
| 첨부 없음 | 단일 파트 평문 |
| 환경변수 누락 | 빠진 키 이름이 예외 메시지에 있는지 (3개 각각) |
| SMTP 예외 | `send()` 가 삼키지 않고 그대로 올리는지 |

`tests/test_perf_report.py` (수정) — `summary_text()` 케이스 추가.

| 케이스 | 검증 |
|---|---|
| 정상 | 총 손익 = `net_krw + open_net_krw`, 승률·평균 포맷, 부호 |
| 청산 0건 | `win_rate`·`avg_net_pct` 가 `None` 일 때 터지지 않고 `(청산 0건)` |
| 조회 실패 있음 | 실패 티커가 본문에 나열되는지 |
| 경고문 | 3줄이 본문 상단에 있는지 |

## 신규 의존성

없다. `smtplib`·`email`·`ssl` 은 파이썬 표준 라이브러리다.
