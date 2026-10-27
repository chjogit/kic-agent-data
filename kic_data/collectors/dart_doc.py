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
UNIT_RE = re.compile(r"단위\s*[:：]\s*([가-힣A-Za-z$,\s]+?)\s*[)\]]")


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


def tables_from_xml(text: str, file: str = "") -> list[Table]:
    out = []
    last = 0
    for i, m in enumerate(TABLE_RE.finditer(text)):
        before = clean(text[max(last, m.start() - 4000):m.start()])[-400:]
        last = m.end()
        rows = []
        for rm in ROW_RE.finditer(m.group(1)):
            row = []
            for cm in CELL_RE.finditer(rm.group(1)):
                val = clean(cm.group(3))
                span = SPAN_RE.search(cm.group(2) or "")
                row.extend([val] * (int(span.group(1)) if span else 1))
            if any(row):
                rows.append(row)
        if not rows:
            continue
        head = before + " " + " ".join(rows[0])
        um = UNIT_RE.search(head) or UNIT_RE.search(" ".join(" ".join(r) for r in rows[:2]))
        unit = re.sub(r"\s+", "", um.group(1)) if um else None
        title_m = re.findall(r"(?:\(\d+\)|\d+\)|[가-하]\.|\d+\.)\s*[^.()]{2,40}", before)
        out.append(Table(rows=rows, context=before, unit=unit, file=file, index=i,
                         title=title_m[-1].strip() if title_m else ""))
    return out


def parse_zip(zip_bytes: bytes) -> dict[str, str]:
    """zip → {파일명: 본문 문자열}. 본문(접수번호.xml)이 먼저 오도록 정렬."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = sorted(zf.namelist(), key=lambda n: ("_" in n, n))
        return {n: decode(zf.read(n)) for n in names if n.lower().endswith((".xml", ".htm", ".html"))}


def fetch_document(session, rcept_no: str) -> dict[str, str]:
    response = get(session, "dart", f"{BASE}/document.xml",
                   params={"crtfc_key": env("DART_API_KEY", required=True), "rcept_no": rcept_no},
                   timeout=90, limiter=_limiter)
    content = response.content
    if not content.startswith(b"PK"):  # 오류는 XML/JSON 으로 옴
        msg = decode(content[:300])
        status = re.search(r"<status>(\d+)</status>|\"status\"\s*:\s*\"(\d+)\"", msg)
        raise RuntimeError(f"[dart] 원문 없음 {status.group(1) or status.group(2) if status else ''}".strip())
    return parse_zip(content)
