"""값 변환 유틸."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation


def to_decimal(value) -> Decimal | None:
    """'1,234' 같은 문자열을 Decimal 로. 비어 있거나 '-', '.' 이면 None."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "."):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None  # 'nan', 'inf' 는 값 없음으로 취급


def to_int(value) -> int | None:
    number = to_decimal(value)
    return int(number) if number is not None else None


def parse_ymd(text: str) -> date:
    """'20240102' 또는 '2024-01-02' 를 date 로."""
    text = str(text).strip()
    fmt = "%Y%m%d" if "-" not in text else "%Y-%m-%d"
    return datetime.strptime(text, fmt).date()


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def date_windows(start: date, end: date, days: int) -> list[tuple[date, date]]:
    """[start, end] 를 days 일 이하 구간으로 나눕니다 (API 1회 한도 대응)."""
    out, cur = [], start
    while cur <= end:
        nxt = min(cur + timedelta(days=days - 1), end)
        out.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return out


def drop_incomplete_today(rows: list[dict], market: str, now: datetime | None = None) -> list[dict]:
    """장이 끝나기 전의 '오늘' 일봉(프리마켓·장중 값)은 버립니다.
    KR: 15:40 KST 이후, US: 16:30 ET 이후에만 오늘 행을 인정합니다."""
    from zoneinfo import ZoneInfo  # Windows 는 tzdata 패키지가 필요 (requirements.txt)

    tz, cutoff = (ZoneInfo("Asia/Seoul"), time(15, 40)) if market == "KR" else (ZoneInfo("America/New_York"), time(16, 30))
    local = now.astimezone(tz) if now else datetime.now(tz)
    if local.time() >= cutoff:
        return rows
    return [r for r in rows if r["trade_date"] < local.date()]
