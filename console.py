"""콘솔 출력 인코딩 설정.

진입점 스크립트(stock_finder / backtest / backfill_history)가 전부 한글을
출력하는데 Windows 콘솔 기본 코드페이지는 cp949다. 그대로 두면 출력이
깨지거나 UnicodeEncodeError 로 죽는다. 같은 블록을 세 곳에 복사하는 대신
여기 하나만 둔다.
"""
from __future__ import annotations

import sys


def force_utf8() -> None:
    """Windows 에서 stdout/stderr 을 UTF-8 로 재설정한다.

    다른 OS 는 이미 UTF-8 이므로 아무것도 하지 않는다. 재설정에 실패해도
    (스트림이 TextIOWrapper 가 아닌 경우 등) 출력이 깨질 뿐 실행을 막을
    이유는 없으므로 삼킨다.
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
