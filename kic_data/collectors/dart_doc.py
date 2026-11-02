"""OpenDART 공시 원문(document.xml): 사업보고서 원문에서 표를 꺼냅니다.

document.xml 은 접수번호(rcept_no)의 원문 파일들을 zip 으로 줍니다 (본문 {rcept_no}.xml, 첨부 감사보고서 등).
DART 원문 XML 은 표준 XML 이 아닐 때가 있어(이스케이프 안 된 & 등) 파서 대신 정규식으로 표를 나눕니다.
"""
from __future__ import annotations

import html
import io
import re
import zipfile
from dataclasses import dataclass, field

from ..config import env
from ..http_util import RateLimiter, get

BASE = "https://opendart.fss.or.kr/api"
_limiter = RateLimiter(0.5)

TABLE_RE = re.compile(r"<TABLE\b[^>]*>(.*?)</TABLE>", re.S | re.I)
ROW_RE = re.compile(r"<TR\b[^>]*>(.*?)</TR>", re.S | re.I)
CELL_RE = re.compile(r"<(TD|TH|TE|TU)\b([^>]*)>(.*?)</\1>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
SPAN_RE = re.compile(r'COLSPAN="?(\d+)"?', re.I)
ROWSPAN_RE = re.compile(r'ROWSPAN="?(\d+)"?', re.I)
KRW_UNIT_RE = re.compile(r"원화\s*단위\s*[:：]\s*([가-힣]*원)")
SECTION_RE = re.compile(r"<TITLE\b[^>]*>(.*?)</TITLE>", re.S | re.I)
UNIT_RE = re.compile(r"단위\s*[:：]\s*([가-힣A-Za-z$,\s]+?)\s*[)\],]")


@dataclass
class Table:
    rows: list[list[str]]
    context: str            # 표 앞 본문 (제목·설명, 최대 400자)
    unit: str | None        # '백만원', '천원', '원', 'USD' 등
    file: str
    index: int
    title: str = ""         # 문맥에서 가장 가까운 소제목
    extra: dict = field(default_factory=dict)

    def text(self, max_rows: int = 40) -> str:
        return "\n".join(" | ".join(r) for r in self.rows[:max_rows])


def clean(fragment: str) -> str:
    t = TAG_RE.sub(" ", fragment)
    t = html.unescape(t).replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


def decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _rows(body: str) -> list[list[str]]:
    """행 목록. COLSPAN 은 옆으로, ROWSPAN 은 아래 행으로 같은 값을 채웁니다."""
    rows: list[list[str]] = []
    carry: dict[int, list] = {}  # 열 위치 → [값, 남은 행 수]
    for rm in ROW_RE.finditer(body):
        row: list[str] = []
        col = 0
        cells = list(CELL_RE.finditer(rm.group(1)))
        ci = 0
        while ci < len(cells) or col in carry:
            if col in carry:
                val, left = carry[col]
                row.append(val)
                if left <= 1:
                    del carry[col]
                else:
                    carry[col] = [val, left - 1]
                col += 1
                continue
            cm = cells[ci]
            ci += 1
            val = clean(cm.group(3))
            attrs = cm.group(2) or ""
            span = SPAN_RE.search(attrs)
            rspan = ROWSPAN_RE.search(attrs)
            for _ in range(int(span.group(1)) if span else 1):
                if rspan and int(rspan.group(1)) > 1:
                    carry[col] = [val, int(rspan.group(1)) - 1]
                row.append(val)
                col += 1
        if any(row):
            rows.append(row)
    return rows


def _unit(text: str) -> str | None:
    m = KRW_UNIT_RE.search(text) or UNIT_RE.search(text)
    return re.sub(r"\s+", "", m.group(1)) if m else None


def tables_from_xml(text: str, file: str = "") -> list[Table]:
    """표 목록. '(단위: 백만원)'만 있는 표(1행, 또는 표준 주석의 '…공시 / 당기 (단위 : 원)' 2행)는 바로 다음 표에
    단위·문맥을 넘기고 뺍니다."""
    out: list[Table] = []
    last = 0
    sections = [(m.start(), clean(m.group(1))) for m in SECTION_RE.finditer(text)]
    si, section = 0, ""
    pending: tuple[str, str | None] | None = None  # 단위만 있던 표의 (문맥, 단위)
    for i, m in enumerate(TABLE_RE.finditer(text)):
        before = clean(text[max(last, m.start() - 4000):m.start()])[-400:]
        last = m.end()
        while si < len(sections) and sections[si][0] < m.start():
            section = sections[si][1]
            si += 1
        rows = _rows(m.group(1))
        if not rows:
            continue
        flat = " ".join(" ".join(r) for r in rows[:2])
        unit = _unit(before[-120:] + " " + flat)
        digits = re.sub(r"[^0-9]", "", re.sub(r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}", "", flat))
        xbrl_head = len(rows) == 2 and "공시" in " ".join(rows[0]) and "단위" in " ".join(rows[1])  # 표준 주석 '…공시' + '당기 (단위 : 원)'
        if (len(rows) == 1 or xbrl_head) and "단위" in flat and len(digits) < 6:
            pending = (before, unit)
            continue
        if pending:
            before = (pending[0] + " " + before).strip()[-400:]
            unit = unit or pending[1]
            pending = None
        title_m = re.findall(r"(?:\(\d+\)|\d+\)|[가-하]\.|\d+\.)\s*[^.()]{2,40}", before)
        out.append(Table(rows=rows, context=before, unit=unit, file=file, index=i,
                         title=title_m[-1].strip() if title_m else "", extra={"section": section}))
    return out


def parse_zip(zip_bytes: bytes) -> dict[str, str]:
    """zip → {파일명: 본문 문자열}. 본문(접수번호.xml)이 먼저 오도록 정렬."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = sorted(zf.namelist(), key=lambda n: ("_" in n, n))
        return {n: decode(zf.read(n)) for n in names if n.lower().endswith((".xml", ".htm", ".html"))}


def fetch_document(session, rcept_no: str) -> dict[str, str]:
    """원문 zip 을 받아 {파일명: 본문} 으로."""
    return parse_zip(fetch_document_raw(session, rcept_no))


def fetch_document_raw(session, rcept_no: str) -> bytes:
    """원문 zip 바이트 그대로 (보관용). 접속 재시도는 세션의 국내 어댑터(접속 4번, 지수 백오프+지터)에만 맡깁니다.
    여기서 다시 감싸 재시도하면 곱해져 DART 장애 때 원문 1건이 3분 넘게 걸리므로 하지 않습니다.
    접속에 실패하면 공용 get() 이 'dart' 를 10분 동안 건너뛰게 표시해, 같은 실행의 다음 원문은 기다리지 않고 바로 실패합니다."""
    response = get(session, "dart-doc", f"{BASE}/document.xml",
                   params={"crtfc_key": env("DART_API_KEY", required=True), "rcept_no": rcept_no},
                   timeout=90, limiter=_limiter)
    content = response.content
    if not content.startswith(b"PK"):  # 오류는 XML/JSON 으로 옴
        msg = decode(content[:300])
        status = re.search(r"<status>(\d+)</status>|\"status\"\s*:\s*\"(\d+)\"", msg)
        raise RuntimeError(f"[dart] 원문 없음 {status.group(1) or status.group(2) if status else ''}".strip())
    return content
