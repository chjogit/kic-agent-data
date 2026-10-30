"""RAG 적재: 문서를 청킹하고 임베딩해서 rag.chunk 에 넣습니다.

- 임베딩 모델은 load_embedder() 한 곳에서 고릅니다. upstage(Solar Embedding 2, API) 또는 bge-m3(로컬). 둘 다 1024차원.
  테이블 열 차원(sql/003 → 026 에서 halfvec(1024))과 다르면 오류를 냅니다 (모델을 바꾸면 테이블 차원도 바꿔야 함).
- 모델 라이브러리는 함수 안에서 불러오므로 청킹·테스트는 설치 없이 동작합니다.
- 용량: Neon 무료 1GB 중 800MB 를 넘기지 않도록 적재 전에 estimate_chunk_bytes() 로 추정합니다.
"""
from __future__ import annotations

import re
import time

from .config import env

EMBED_DIM = 1024

# 임베딩 대상 (10-30 사용자 결정). 블로그·카페·유튜브 댓글은 키워드 검색만 유지.
TARGET_SOURCES = ("naver_news", "naver_news_backfill", "finnhub_news", "dart_report")
DB_LIMIT_MB = 800  # 적재 후 DB 전체가 이 크기를 넘을 것으로 추정되면 쓰지 않음

# Upstage Solar Embedding 2 (10-30 공식 문서 확인): 1024차원, 요청당 100개·204,800토큰, 100 RPM.
# 문서 적재는 passage 모델, 검색 질문은 query 모델 (짝을 맞춰야 함).
UPSTAGE_URL = "https://api.upstage.ai/v1/embeddings"
UPSTAGE_PASSAGE_MODEL = "solar-embedding-2-passage"
UPSTAGE_QUERY_MODEL = "solar-embedding-2-query"
UPSTAGE_MAX_BATCH = 100


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    """긴 글을 size 글자 단위로 자르되 overlap 만큼 겹칩니다. 짧은 글은 그대로 1개."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    step = max(1, size - overlap)
    return [text[i : i + size] for i in range(0, len(text), step) if text[i : i + size].strip()]


def build_chunks(title: str | None, summary: str | None) -> list[str]:
    """뉴스처럼 제목·요약만 있는 문서: 제목을 모든 청크 앞에 붙여 검색 문맥을 유지."""
    title = (title or "").strip()
    pieces = chunk_text(summary or "") or [""]
    out = [f"{title}\n{p}".strip() for p in pieces]
    return [c for c in out if c]


def vec_literal(vector) -> str:
    """pgvector 입력 문자열 '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{float(x):.6f}" for x in vector) + "]"


def parse_vector_type(type_text: str | None) -> tuple[str, int | None]:
    """DB 열 형식 'halfvec(1024)' → ('halfvec', 1024). 차원 없는 'vector' 는 (vector, None)."""
    m = re.fullmatch(r"\s*(vector|halfvec)\s*(?:\((\d+)\))?\s*", type_text or "")
    if not m:
        raise RuntimeError(f"rag.chunk.embedding 열 형식을 알 수 없습니다: {type_text}")
    return m.group(1), (int(m.group(2)) if m.group(2) else None)


def check_dim(column_dim: int | None, embed_dim: int) -> None:
    """임베딩 차원과 테이블 열 차원이 다르면 쓰기 전에 멈춥니다."""
    if column_dim is not None and column_dim != embed_dim:
        raise RuntimeError(f"임베딩 차원 {embed_dim} != 테이블 열 차원 {column_dim} "
                           "(모델을 바꿨다면 sql/ 에 새 번호 파일로 열 형식을 바꾸세요)")


def estimate_chunk_bytes(n_chunks: int, avg_content_bytes: float, vec_type: str = "halfvec",
                         dim: int = EMBED_DIM, hnsw: bool = False) -> int:
    """rag.chunk 에 n_chunks 행을 넣을 때 늘어날 DB 크기 추정 (바이트, 넉넉하게).

    행 = 임베딩(halfvec 2바이트·vector 4바이트 × 차원 + 8) + 본문 + 행 머리·열(약 80) → 페이지·TOAST 여유 1.3배.
    btree 2개(PK, (doc_id, chunk_idx)) 행당 약 50. HNSW 를 두면 임베딩 크기 + 이웃 목록(약 300) 이 더 붙음.
    """
    vec_bytes = (2 if vec_type == "halfvec" else 4) * dim + 8
    per_row = (vec_bytes + avg_content_bytes + 80) * 1.3 + 50
    if hnsw:
        per_row += (vec_bytes + 300) * 1.1
    return int(n_chunks * per_row)


class UpstageEmbedder:
    """Upstage 임베딩 API (OpenAI 호환 POST /v1/embeddings). 키는 UPSTAGE_API_KEY, 로그에 남기지 않음.

    키가 없으면 만들 때가 아니라 encode() 에서 오류를 내므로, 호출 쪽이 실패 문서를 기록하고 계속할 수 있습니다.
    """

    dim = EMBED_DIM

    def __init__(self, model: str = UPSTAGE_PASSAGE_MODEL, session=None, min_interval: float = 0.7,
                 retries: int = 3, sleep=time.sleep):
        from .http_util import RateLimiter

        self.model = model
        self.session = session
        self.limiter = RateLimiter(min_interval)  # 100 RPM 한도 → 0.7초 간격
        self.retries = retries
        self.sleep = sleep
        self.tokens = 0  # usage.total_tokens 누계 (비용 확인용)

    def encode(self, texts: list[str]) -> list[list[float]]:
        key = env("UPSTAGE_API_KEY")
        if not key:
            raise RuntimeError("UPSTAGE_API_KEY 가 비어 있습니다")
        if len(texts) > UPSTAGE_MAX_BATCH:
            raise RuntimeError(f"Upstage 임베딩은 요청당 {UPSTAGE_MAX_BATCH}개까지입니다 ({len(texts)}개)")
        if self.session is None:
            from .http_util import make_session

            self.session = make_session()
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            resp = self.session.post(UPSTAGE_URL, json={"model": self.model, "input": texts},
                                     headers={"Authorization": f"Bearer {key}"}, timeout=60)
            # 429·5xx 는 잠시 뒤 다시 (POST 라 make_session 의 자동 재시도 대상이 아님)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                self.sleep(2 ** attempt * 2)
                continue
            break
        if resp.status_code != 200:
            raise RuntimeError(f"Upstage 임베딩 HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        rows = [list(map(float, d["embedding"])) for d in sorted(body["data"], key=lambda d: d["index"])]
        if len(rows) != len(texts):
            raise RuntimeError(f"Upstage 임베딩 개수 {len(rows)} != 요청 {len(texts)}")
        for r in rows:
            if len(r) != EMBED_DIM:
                raise RuntimeError(f"임베딩 차원 {len(r)} != 테이블 차원 {EMBED_DIM}")
        self.tokens += int((body.get("usage") or {}).get("total_tokens") or 0)
        return rows


def load_embedder(name: str = "upstage"):
    """encode(list[str]) -> list[list[float]] 와 dim 을 가진 객체를 돌려줍니다."""
    if name == "upstage":
        return UpstageEmbedder()
    if name != "bge-m3":
        raise RuntimeError(f"지원하지 않는 임베딩 모델: {name} (rag.py 의 load_embedder 에 추가하세요)")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise RuntimeError("sentence-transformers 가 필요합니다: pip install -r requirements-rag.txt") from None
    model = SentenceTransformer("BAAI/bge-m3")

    class _Embedder:
        dim = EMBED_DIM

        def encode(self, texts: list[str]) -> list[list[float]]:
            vectors = model.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
            rows = [list(map(float, v)) for v in vectors]
            if rows and len(rows[0]) != EMBED_DIM:
                raise RuntimeError(f"임베딩 차원 {len(rows[0])} != 테이블 차원 {EMBED_DIM}")
            return rows

    return _Embedder()


def ingest(conn, embedder, sources=TARGET_SOURCES, limit: int | None = None, since: str | None = None,
           until: str | None = None, batch: int = 64, dry_run: bool = False, limit_mb: int = DB_LIMIT_MB) -> dict:
    """청크가 없는 문서를 청킹·임베딩해 rag.chunk 에 적재 (멱등·증분).

    쓰기 전에 대상 건수·예상 용량을 계산하고, 적재 후 DB 가 limit_mb 를 넘을 것으로 추정되면 쓰지 않고 멈춥니다
    (result['stopped']). 임베딩 호출이 실패한 배치는 result['failed'] 에 문서를 남기고 나머지는 계속합니다.
    """
    from . import db

    bad = [s for s in sources if s not in TARGET_SOURCES]
    if bad:
        raise RuntimeError(f"임베딩 대상이 아닌 소스: {', '.join(bad)} (허용: {', '.join(TARGET_SOURCES)})")
    vec_type, column_dim = parse_vector_type(db.chunk_embedding_type(conn))
    check_dim(column_dim, embedder.dim)

    docs = db.docs_without_chunks(conn, list(sources), limit, since, until)
    per_doc = [(d, rag_chunks) for d in docs if (rag_chunks := build_chunks(d[1], d[2]))]
    n_chunks = sum(len(c) for _, c in per_doc)
    avg_bytes = sum(len(t.encode()) for _, c in per_doc for t in c) / n_chunks if n_chunks else 0.0
    estimate = estimate_chunk_bytes(n_chunks, avg_bytes, vec_type, column_dim or EMBED_DIM, hnsw=True)
    size = db.database_size_bytes(conn)
    result = {"docs": len(docs), "chunks": n_chunks, "estimate_bytes": estimate, "db_bytes": size,
              "limit_bytes": limit_mb * 1024 * 1024, "written": 0, "failed": [], "stopped": False}
    if size + estimate > result["limit_bytes"]:
        result["stopped"] = True
        return result
    if dry_run:
        return result

    for i in range(0, len(per_doc), batch):
        part = per_doc[i : i + batch]
        items = [(d[0], idx, text) for d, chunks in part for idx, text in enumerate(chunks)]
        try:
            texts = [t for _, _, t in items]
            vectors = [v for j in range(0, len(texts), UPSTAGE_MAX_BATCH)
                       for v in embedder.encode(texts[j : j + UPSTAGE_MAX_BATCH])]
            if len(vectors) != len(items):
                raise RuntimeError(f"임베딩 개수 {len(vectors)} != 요청 {len(items)}")
        except Exception as exc:
            result["failed"] += [(d[0], d[4], f"{type(exc).__name__}: {exc}"[:200]) for d, _ in part]
            continue
        rows = [(doc_id, idx, text, vec_literal(v)) for (doc_id, idx, text), v in zip(items, vectors)]
        result["written"] += db.insert_chunks(conn, rows, vec_type)
    return result
