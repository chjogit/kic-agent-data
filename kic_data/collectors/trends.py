"""네이버 데이터랩 검색어 트렌드 (공식 API): 종목·주제에 대한 일별 검색 관심도.

- 2026-07-31 부터 개발자센터에서는 데이터랩 신규 신청이 막히고 네이버 클라우드의 NAVER API HUB 로 이전됐습니다.
  → .env 에 NCP_APIHUB_KEY_ID / NCP_APIHUB_KEY (HUB 'Search Trend' 이용 신청 후 받은 키) 가 있으면 HUB 를,
    없으면 예전 개발자센터 엔드포인트(NAVER_CLIENT_ID/SECRET, 기존 발급분만)를 씁니다. 요청·응답 형식은 같습니다.
- ratio 는 한 번의 요청 안에서 최댓값을 100으로 둔 상대값입니다. 요청이 달라지면 비교할 수 없어서
  모든 요청에 기준 키워드(ANCHOR)를 포함하고 anchor_ratio 를 같이 저장합니다.
  → 요청·기간이 달라도 ratio / anchor_ratio (= 기준 대비 상대 관심도)는 서로 비교할 수 있습니다.
- 종목 검색어는 '삼성전자 주가', '삼성전자 주식' 처럼 투자 의도가 있는 검색만 셉니다
  (그냥 '네이버'·'애플' 은 서비스/제품 검색이 대부분이라 종목 관심도로 쓸 수 없음).
- 데이터랩은 전일까지 일 단위로 제공됩니다 (당일·실시간 값 없음).
"""
from __future__ import annotations

from datetime import date

import requests

from ..config import env
from ..http_util import RateLimiter

URL = "https://openapi.naver.com/v1/datalab/search"  # 개발자센터 (기존 발급 키만, 2027-06-30 종료 예정)
HUB_URL = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"  # NAVER API HUB (네이버 클라우드)
_limiter = RateLimiter(0.2)

ANCHOR = ("ANCHOR", "주식")  # (그룹명, 키워드). 종목이 아닌 일반어를 기준으로 둬야 모든 종목을 같은 잣대로 잽니다
MAX_GROUPS = 5  # 요청당 키워드 그룹 최대 5개 (기준 1 + 대상 4)

# 시장 전반 관심도를 보기 위한 주제 키워드
TOPICS = ["반도체", "HBM", "AI 반도체", "코스피", "나스닥"]
STOCK_SUFFIXES = ("주가", "주식")


def stock_keywords(term: str) -> list[str]:
    """종목 검색어 → 투자 의도 검색어 묶음 (데이터랩은 묶음 안 키워드의 합으로 집계)."""
    return [f"{term} {suf}" for suf in STOCK_SUFFIXES]


def build_body(start: date, end: date, groups: list[tuple[str, list[str]]]) -> dict:
    return {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "timeUnit": "date",
        "keywordGroups": [{"groupName": name, "keywords": kws} for name, kws in groups],
    }


def parse_trend(payload: dict) -> dict[str, list[tuple[date, float]]]:
    """응답 → {그룹명: [(날짜, ratio)]}"""
    if "errorCode" in payload:
        raise RuntimeError(f"[naver-datalab] 오류 {payload.get('errorCode')}: {payload.get('errorMessage')}")
    out = {}
    for res in payload.get("results", []):
        out[res["title"]] = [(date.fromisoformat(d["period"]), float(d["ratio"])) for d in res.get("data", [])]
    return out


def endpoint() -> tuple[str, dict]:
    if env("NCP_APIHUB_KEY_ID") and env("NCP_APIHUB_KEY"):
        return HUB_URL, {"X-NCP-APIGW-API-KEY-ID": env("NCP_APIHUB_KEY_ID"), "X-NCP-APIGW-API-KEY": env("NCP_APIHUB_KEY"),
                         "Content-Type": "application/json"}
    return URL, {"X-Naver-Client-Id": env("NAVER_CLIENT_ID", required=True),
                 "X-Naver-Client-Secret": env("NAVER_CLIENT_SECRET", required=True), "Content-Type": "application/json"}


def fetch_trend(session, start: date, end: date, groups: list[tuple[str, list[str]]]) -> dict[str, list[tuple[date, float]]]:
    url, headers = endpoint()
    _limiter.wait()
    try:
        res = session.post(url, headers=headers, json=build_body(start, end, groups), timeout=30)
    except requests.RequestException as exc:
        raise RuntimeError(f"[naver-datalab] 요청 실패: {type(exc).__name__}") from None
    if res.status_code != 200:
        try:
            err = res.json()
            detail = f"{err.get('errorCode', '')} {err.get('errorMessage', '')}".strip()
        except ValueError:
            detail = res.text[:120]
        hint = {401: "개발자센터 키면 데이터랩 신규 신청 불가 → .env 에 NCP_APIHUB_KEY_ID/KEY (NAVER API HUB) 설정. "
                     "HUB 키면 콘솔에서 Search Trend 이용 신청·키 확인",
                403: "HUB 콘솔에서 Search Trend 상품 이용 신청 여부 확인",
                400: "요청 본문(날짜·키워드) 확인", 429: "호출 한도 초과 (HUB 월 5만 건 / 초당 50회)"}.get(res.status_code, "")
        raise RuntimeError(f"[naver-datalab] HTTP {res.status_code}: {detail} ({hint})")
    return parse_trend(res.json())


def batches(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def relative(rows: list[tuple]) -> list[tuple[date, float]]:
    """[(날짜, ratio, anchor_ratio)] → [(날짜, ratio/anchor_ratio)]. 기준값이 0/없음인 날은 제외."""
    return [(d, float(r) / float(a)) for d, r, a in rows if r is not None and a]
