"""제목·요약 텍스트의 긍정/부정 점수 (-1 ~ +1).

기본은 사전(키워드) 방식이라 설치·GPU 없이 바로 돕니다. 정확도는 모델보다 낮으니
리포트에는 '키워드 기반 톤' 으로 표기하세요. 모델을 쓰려면 requirements-sentiment.txt 설치 후
SENTIMENT_METHOD=finbert (snunlp/KR-FinBert-SC: 금융 뉴스로 학습. 커뮤니티 은어에는 약함).
원문은 점수 계산에만 쓰고 별도 저장하지 않습니다.
"""
from __future__ import annotations

import re

# 표현은 팀에서 계속 보완하세요. 강함 1.0 / 일반 0.5
POSITIVE = {
    # 강한 긍정
    "상한가": 1.0, "불기둥": 1.0, "떡상": 1.0, "어닝서프라이즈": 1.0, "어닝 서프라이즈": 1.0, "깜짝 실적": 1.0,
    "신고가": 1.0, "양매수": 1.0, "사상 최대": 1.0, "급등": 1.0,
    # 일반 긍정
    "상승": 0.5, "반등": 0.5, "매수": 0.5, "순매수": 0.5, "호재": 0.5, "기대": 0.5, "우상향": 0.5, "저점": 0.5,
    "돌파": 0.5, "강세": 0.5, "수혜": 0.5, "상향": 0.5, "흑자": 0.5, "호실적": 0.5, "회복": 0.5, "최대 실적": 0.5,
    "저평가": 0.5, "줍줍": 0.5, "가즈아": 0.5,
}
NEGATIVE = {
    # 강한 부정
    "하한가": 1.0, "떡락": 1.0, "설거지": 1.0, "폭락": 1.0, "상폐": 1.0, "상장폐지": 1.0, "유증": 1.0, "유상증자": 1.0,
    "개잡주": 1.0, "손절": 1.0, "급락": 1.0, "어닝쇼크": 1.0, "어닝 쇼크": 1.0, "신저가": 1.0, "지하실": 1.0,
    # 일반 부정
    "하락": 0.5, "조정": 0.5, "매도": 0.5, "순매도": 0.5, "악재": 0.5, "우려": 0.5, "약세": 0.5, "손실": 0.5,
    "물림": 0.5, "물렸": 0.5, "하향": 0.5, "적자": 0.5, "부진": 0.5, "리스크": 0.5, "고평가": 0.5, "공매도": 0.5,
    "둔화": 0.5, "쇼크": 0.5,
}
# 키워드 뒤 1~2어절 안에 이런 서술이 오면 부호 반전 ("상승 기대하기 힘들다", "호재가 아니다", "돌파 실패")
NEGATORS = ("않", "아니", "못하", "힘들", "어렵", "없", "실패", "글쎄", "끝", "멈", "그쳤", "제한적", "이하", "못미")
NEG_TOKENS = ("안", "못")  # 단독 어절일 때만 ('안정', '안전' 오판 방지)
_WINDOW = re.compile(r"^[^\s.,!?…·]*(?:\s+[^\s.,!?…·]+){0,2}")  # 남은 글자 + 다음 2어절, 문장부호에서 멈춤
_TERMS = sorted([(w, s) for w, s in POSITIVE.items()] + [(w, -s) for w, s in NEGATIVE.items()], key=lambda x: -len(x[0]))


def negated(after: str) -> bool:
    window = _WINDOW.match(after).group(0)
    if any(n in window for n in NEGATORS):
        return True
    return any(tok in NEG_TOKENS for tok in window.split())


def lexicon_score(text: str) -> tuple[float | None, int]:
    """(점수, 매칭 수). 점수 = 매칭된 가중치 합을 -1~+1 로 자름. 매칭이 없으면 None ('중립' 과 '판단 불가' 구분)."""
    text = text or ""
    used = [False] * len(text)
    total, hits = 0.0, 0
    for word, w in _TERMS:  # 긴 표현 먼저 ('순매도' 를 '매도' 로 또 세지 않음)
        start = 0
        while (i := text.find(word, start)) != -1:
            start = i + len(word)
            if any(used[i:start]):
                continue
            for k in range(i, start):
                used[k] = True
            hits += 1
            total += -w if negated(text[start:]) else w
    if hits == 0:
        return None, 0
    return max(-1.0, min(1.0, total)), hits


def label_of(score: float | None) -> str:
    if score is None:
        return "none"
    return "positive" if score > 0.2 else "negative" if score < -0.2 else "neutral"


class FinBertScorer:
    """선택: 한국어 금융 감성 모델. 처음 실행 시 모델을 내려받습니다(수백 MB)."""

    def __init__(self, model: str = "snunlp/KR-FinBert-SC"):
        from transformers import pipeline  # requirements-sentiment.txt

        self.pipe = pipeline("sentiment-analysis", model=model, tokenizer=model, truncation=True)

    def score_many(self, texts: list[str]) -> list[float]:
        out = []
        for p in self.pipe([t[:300] for t in texts], batch_size=32):
            lab, conf = p["label"].lower(), float(p["score"])
            out.append(conf if lab == "positive" else -conf if lab == "negative" else 0.0)
        return out


def score_texts(texts: list[str], method: str = "lexicon") -> list[float | None]:
    if method == "finbert":
        return FinBertScorer().score_many(texts)
    return [lexicon_score(t)[0] for t in texts]
