import pathlib

import yaml

WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(doc):
    # YAML 1.1 에서 따옴표 없는 on: 은 키가 아니라 불린 True 로 파싱된다.
    return doc.get("on", doc.get(True)) or {}


def _steps(doc):
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            yield step


def test_every_scheduled_workflow_tells_someone_when_it_fails():
    # 2026-08-27 점검에서 verify-quotes 만 판정을 job summary 에 남기고 끝났다.
    # 무인으로 도는 워크플로가 조용히 실패하면 며칠씩 모른 채 지나간다.
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = _load(path)
        if "schedule" not in _triggers(doc):
            continue
        assert "notify-failure" in (doc.get("jobs") or {}), \
            f"{path.name}: 스케줄로 도는데 실패를 알리는 잡이 없다"


def test_a_piped_step_keeps_the_real_exit_code():
    # 기본 셸은 `bash -e {0}` 라 pipefail 이 없다. 파이프라인의 종료 코드가
    # 마지막 명령의 것이 되어, tee 로 로그를 남기는 순간 앞선 python 이
    # 죽어도 스텝은 초록으로 끝난다.
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for step in _steps(_load(path)):
            run = step.get("run") or ""
            if "|" not in run or "||" == run.strip():
                continue
            piped = [ln for ln in run.splitlines()
                     if "| tee" in ln or "| head" in ln or "| grep" in ln]
            if not piped:
                continue
            assert step.get("shell") == "bash", (
                f"{path.name} / {step.get('name')}: 파이프를 쓰는데 shell: bash "
                f"가 없어 종료 코드가 묻힌다")


def test_a_failed_etf_track_does_not_commit_its_dashboard():
    # _save_intermediate 가 실제 dashboard_data_etf.js 를 쓰므로, 완결성
    # 가드에 걸려 죽은 뒤에도 잘린 파일이 남는다. ETF 스텝은
    # continue-on-error 라 커밋 스텝이 그대로 돈다.
    doc = _load(WORKFLOWS / "scan.yml")
    commit = next(s for s in _steps(doc)
                  if (s.get("name") or "").startswith("Commit and push"))
    assert "steps.etf_scan.outcome" in yaml.dump(commit.get("env") or {}), \
        "커밋 스텝이 ETF 트랙 결과를 보지 않는다"
    assert "dashboard_data_etf.js" in commit["run"] and "checkout" in commit["run"], \
        "ETF 실패 시 부분 대시보드를 되돌리지 않는다"
