"""거시지표: 한국은행 ECOS, 미국 FRED.

ECOS 통계표/항목 코드는 ECOS '통계검색'에서 직접 확인하세요 (아래는 시작용 예시이며 미검증).
"""
from __future__ import annotations

from datetime import date

from ..config import env
from ..http_util import RateLimiter, get_json
from ..util import to_decimal

ECOS_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
_ecos_limiter = RateLimiter(0.2)
_fred_limiter = RateLimiter(0.6)

# (series_id, 이름, 통계표코드, 주기, 항목코드)
ECOS_SERIES = [
    ("ECOS_BASE_RATE", "한국은행 기준금리", "722Y001", "M", "0101000"),
    ("ECOS_USDKRW", "원/달러 환율(종가)", "731Y001", "D", "0000001"),
]
# (series_id, 이름, 주기)
FRED_SERIES = [
    ("DGS10", "미국 10년 국채금리", "D"),
    ("FEDFUNDS", "미국 기준금리(실효)", "M"),
    ("CPIAUCSL", "미국 CPI", "M"),
    # 환율: 20-F 제출 외국 기업(ASML=EUR, TSMC=TWD) 재무를 USD 로 환산해 PER/PBR 을 계산할 때 사용
    ("DEXUSEU", "달러/유로 환율 (1유로당 달러)", "D"),
    ("DEXTAUS", "대만달러/달러 환율 (1달러당 대만달러)", "D"),
    # 반도체 업황 (10-25 추가): 시황 보고서·거시 관점에서 업종 사이클을 설명할 때 사용
    ("IPG3344S", "미국 반도체·전자부품 산업생산지수", "M"),
    ("PCU33443344", "미국 반도체·전자부품 생산자물가지수", "M"),
]


def parse_ecos_time(text: str, cycle: str) -> date:
    text = str(text)
    if cycle == "D":
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    if cycle == "M":
        return date(int(text[:4]), int(text[4:6]), 1)
    if cycle == "A":
        return date(int(text[:4]), 1, 1)
    raise ValueError(f"지원하지 않는 주기: {cycle}")


def parse_ecos(payload: dict, cycle: str) -> list[tuple]:
    if "RESULT" in payload:
        code = payload["RESULT"].get("CODE")
        if code == "INFO-200":
            return []
        raise RuntimeError(f"[ecos] {code} {payload['RESULT'].get('MESSAGE')}")
    rows = payload.get("StatisticSearch", {}).get("row", [])
    return [(parse_ecos_time(r["TIME"], cycle), to_decimal(r.get("DATA_VALUE"))) for r in rows]


def fetch_ecos(session, stat_code: str, cycle: str, start: str, end: str, item: str) -> list[tuple]:
    key = env("ECOS_API_KEY", required=True)
    url = f"{ECOS_BASE}/{key}/json/kr/1/10000/{stat_code}/{cycle}/{start}/{end}/{item}"
    payload = get_json(session, "ecos", url, limiter=_ecos_limiter)
    return parse_ecos(payload, cycle)


def parse_fred(payload: dict) -> list[tuple]:
    if "error_message" in payload:
        raise RuntimeError(f"[fred] {payload.get('error_code')} {payload.get('error_message')}")
    out = []
    for o in payload.get("observations", []):
        y, m, d = o["date"].split("-")
        out.append((date(int(y), int(m), int(d)), to_decimal(o.get("value"))))
    return out


def fetch_fred(session, series_id: str, start: str) -> list[tuple]:
    params = {
        "series_id": series_id,
        "api_key": env("FRED_API_KEY", required=True),
        "file_type": "json",
        "observation_start": start,
    }
    payload = get_json(session, "fred", FRED_URL, params=params, limiter=_fred_limiter)
    return parse_fred(payload)
