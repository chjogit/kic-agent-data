"""RAG 적재: 문서를 청킹하고 임베딩해서 rag.chunk 에 넣습니다.

- 임베딩 모델은 load_embedder() 한 곳에서 고릅니다. 기본 bge-m3 (1024차원, 한·영 지원).
  sql/003 의 vector(1024) 와 차원이 다르면 오류를 냅니다 (모델을 바꾸면 테이블 차원도 바꿔야 함).
- 모델 라이브러리는 함수 안에서 불러오므로 청킹·테스트는 설치 없이 동작합니다.
"""
from __future__ import annotations

EMBED_DIM = 1024


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


def load_embedder(name: str = "bge-m3"):
    """encode(list[str]) -> list[list[float]] 를 가진 객체를 돌려줍니다."""
    if name != "bge-m3":
        raise RuntimeError(f"지원하지 않는 임베딩 모델: {name} (rag.py 의 load_embedder 에 추가하세요)")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise RuntimeError("sentence-transformers 가 필요합니다: pip install -r requirements-rag.txt") from None
    model = SentenceTransformer("BAAI/bge-m3")

    class _Embedder:
        def encode(self, texts: list[str]) -> list[list[float]]:
            vectors = model.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
            rows = [list(map(float, v)) for v in vectors]
            if rows and len(rows[0]) != EMBED_DIM:
                raise RuntimeError(f"임베딩 차원 {len(rows[0])} != 테이블 차원 {EMBED_DIM}")
            return rows

    return _Embedder()
