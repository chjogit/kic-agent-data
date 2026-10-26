"""환경변수 로딩. 키와 비밀번호는 .env 에서만 읽고 코드나 로그에 남기지 않습니다."""
from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv 가 없어도 시스템 환경변수는 읽을 수 있음
    pass


def env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if value is not None:
        value = value.strip().strip('"').strip("'")
    if required and not value:
        raise RuntimeError(f"환경변수 {name} 가 비어 있습니다. .env 파일을 확인하세요.")
    return value


def data_go_key() -> str:
    """공공데이터포털 인증키. 서비스마다 따로 쓰려면 DATA_GO_KR_KEY, 없으면 FSC_API_KEY (같은 일반 인증키)."""
    return env("DATA_GO_KR_KEY") or env("FSC_API_KEY", required=True)
