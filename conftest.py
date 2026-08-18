"""pytest가 프로젝트 루트를 임포트 경로에 넣도록 한다.

테스트는 루트의 history.py / stock_finder.py 를 임포트한다.
이 파일이 없으면 `pytest` 실행 시 ModuleNotFoundError가 난다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
