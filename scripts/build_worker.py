"""Worker 번들 준비: kic_data, sql, web, main.py 를 cloudflare/src/ 로 복사합니다.

수집기(kic_data/collectors), __pycache__, 테스트는 제외합니다 (조회 전용 Worker 에 필요 없고, requests·yfinance 등을 끌어오지 않게).
사용: python scripts/build_worker.py [출력 폴더]   (기본: cloudflare/src)
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = ROOT / "cloudflare" / "src"
IGNORE = shutil.ignore_patterns("collectors", "__pycache__", "*.pyc", "tests")


def build(dest: Path = DEFAULT_DEST, root: Path = ROOT) -> Path:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("kic_data", "sql", "web"):
        target = dest / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(root / name, target, ignore=IGNORE)
    shutil.copy2(root / "main.py", dest / "main.py")
    return dest


if __name__ == "__main__":
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DEST)
    print(f"Worker 번들 준비 완료: {out}")
