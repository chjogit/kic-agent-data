"""관세청 품목별 수출입실적 (공공데이터포털). 반도체 HS 코드의 월별 수출·수입 금액(USD)을 수집합니다.

공공데이터포털 '관세청_품목별 수출입실적(GW)' 활용신청이 필요합니다 (인증키는 같은 일반 인증키, 2026-10-26 승인).
  - 한 번에 1년 이내 기간만 조회됩니다 → month_windows 로 12개월씩 나눠 부릅니다.
  - 월 단위 데이터이며 매월 중순 전월 실적이 갱신됩니다.
  - 6자리 HS 로 부르면 10자리 세부 품목별 행이 여러 개 올 수 있어 월별로 합칩니다
    (요청한 코드와 같은 hsCd 행이 있으면 그 행만 씁니다: 합계 행과 세부 행을 이중으로 더하지 않도록).
HS 코드: 854232 = 메모리 집적회로, 854231 = 프로세서·컨트롤러 집적회로.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date

from ..config import data_go_key
from ..http_util import RateLimiter, get
from ..util import to_decimal

URL = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"
_limiter = RateLimiter(0.3)

HS_NAMES = {"854232": "반도체 메모리(HS 854232)", "854231": "반도체 프로세서·컨트롤러(HS 854231)"}


def parse_trade_xml(xml_text: str, hs: str | None = None) -> list[tuple[date, object, object]]:
    """응답 XML → [(월 첫날, 수출금액USD, 수입금액USD)] (월 순). '총계' 행 등 월 형식이 아닌 행은 제외."""
    root = ET.fromstring(xml_text)
    code = (root.findtext(".//resultCode") or "").strip()
    if code not in ("", "00", "0"):
        raise RuntimeError(f"[customs] 오류 {code}: {(root.findtext('.//resultMsg') or '').strip()}")
    rows = []  # (월, hsCd, 수출, 수입)
    for item in root.iter("item"):
        ym = (item.findtext("year") or "").strip()
        m = re.fullmatch(r"(\d{4})\.(\d{2})", ym)
        if not m:
            continue
        hs_cd = (item.findtext("hsCd") or "").strip()
        rows.append((date(int(m.group(1)), int(m.group(2)), 1), hs_cd, to_decimal(item.findtext("expDlr")), to_decimal(item.findtext("impDlr"))))
    if hs and any(r[1] == hs for r in rows):
        rows = [r for r in rows if r[1] == hs]
    sums: dict[date, list] = {}
    for d, _, exp, imp in rows:
        acc = sums.setdefault(d, [None, None])
        for i, v in ((0, exp), (1, imp)):
            if v is not None:
                acc[i] = v if acc[i] is None else acc[i] + v
    return [(d, exp, imp) for d, (exp, imp) in sorted(sums.items())]


def month_windows(start: date, end: date, months: int = 12) -> list[tuple[str, str]]:
    """[start, end] 의 달을 months 개월 이하 구간으로 나눠 ('YYYYMM', 'YYYYMM') 목록으로."""
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        idx = y * 12 + m - 1 + months - 1
        ey, em = min((idx // 12, idx % 12 + 1), (end.year, end.month))
        out.append((f"{y}{m:02d}", f"{ey}{em:02d}"))
        nxt = ey * 12 + em
        y, m = nxt // 12, nxt % 12 + 1
    return out


def fetch_trade(session, hs: str, strt_yymm: str, end_yymm: str) -> list[tuple[date, object, object]]:
    params = {"serviceKey": data_go_key(), "strtYymm": strt_yymm, "endYymm": end_yymm, "hsSgn": hs}
    response = get(session, "customs", URL, params=params, limiter=_limiter)
    return parse_trade_xml(response.text, hs)
