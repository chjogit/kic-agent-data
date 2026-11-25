"""DB 적재 전 검사 (가드). 검사에 걸리면 GuardError 로 멈추고, DB 에는 아무것도 쓰지 않습니다.

목적: 잘못된 행이 ON CONFLICT DO UPDATE 로 정상 값을 조용히 덮어쓰는 일을 막습니다.
흐름과 깨질 수 있는 지점은 docs/PRICE_PIPELINE_FLOW.md 참고.
"""
from __future__ import annotations

from datetime import date

# 한 종목 한 번 적재의 상한. 5년 백필이 약 1,250 거래일이라 넉넉히 잡았습니다.
# 이보다 많으면 종목 구분 없이 전 종목 행이 한 종목에 들어가는 실수일 가능성이 큽니다.
MAX_PRICE_ROWS = 4000


class GuardError(ValueError):
    """적재 전 검사 실패."""


def check_price_rows(rows: list[dict], who: str = "", max_rows: int = MAX_PRICE_ROWS) -> None:
    """core.price_daily 에 넣을 한 종목의 행 목록을 검사합니다.

    - 행 수 상한 (max_rows)
    - trade_date 가 date 이고 비어 있지 않음
    - 같은 trade_date 중복 없음 (PK 가 (company_id, trade_date) 라 중복이면 마지막 값이 앞 값을 덮음)
    - close 가 비어 있지 않음 (비면 기존 정상 종가를 NULL 로 덮음)
    """
    tag = f"[guard] 시세 {who}".rstrip()
    if len(rows) > max_rows:
        raise GuardError(f"{tag}: 행 수 {len(rows)} 이 상한 {max_rows} 초과 (종목 구분 누락 의심)")
    seen: dict[date, int] = {}
    for i, r in enumerate(rows):
        d = r.get("trade_date")
        if not isinstance(d, date):
            raise GuardError(f"{tag}: {i}번째 행 trade_date 가 날짜가 아님 ({d!r})")
        if d in seen:
            raise GuardError(f"{tag}: trade_date {d} 중복 ({seen[d]}번째, {i}번째 행)")
        seen[d] = i
        if r.get("close") is None:
            raise GuardError(f"{tag}: trade_date {d} 종가(close) 없음")
