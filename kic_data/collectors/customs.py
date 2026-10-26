"""관세청 품목별 수출입실적 (공공데이터포털). 반도체 HS 코드의 월별 수출·수입 금액(USD)을 수집합니다.

※ 엔드포인트·파라미터·응답 필드는 '활용신청 > 상세기능' 명세로 반드시 확인하세요 (아래는 알려진 형태 기준, 미검증).
  - 별도 활용신청이 필요합니다 (인증키는 같은 일반 인증키 사용 가능).
  - 월 단위 데이터이며 매월 중순 전월 실적이 갱신됩니다.
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


def parse_trade_xml(xml_text: str) -> list[tuple[date, object, object]]:
    """응답 XML → [(월 첫날, 수출금액USD, 수입금액USD)]. '총계' 행 등 월 형식이 아닌 행은 제외."""
    root = ET.fromstring(xml_text)
    code = (root.findtext(".//resultCode") or "").strip()
    if code not in ("", "00", "0"):
        raise RuntimeError(f"[customs] 오류 {code}: {(root.findtext('.//resultMsg') or '').strip()}")
    out = []
    for item in root.iter("item"):
        ym = (item.findtext("year") or "").strip()
        m = re.fullmatch(r"(\d{4})\.(\d{2})", ym)
        if not m:
            continue
        out.append((date(int(m.group(1)), int(m.group(2)), 1), to_decimal(item.findtext("expDlr")), to_decimal(item.findtext("impDlr"))))
    return out


def fetch_trade(session, hs: str, strt_yymm: str, end_yymm: str) -> list[tuple[date, object, object]]:
    params = {"serviceKey": data_go_key(), "strtYymm": strt_yymm, "endYymm": end_yymm, "hsSgn": hs}
    response = get(session, "customs", URL, params=params, limiter=_limiter)
    return parse_trade_xml(response.text)
