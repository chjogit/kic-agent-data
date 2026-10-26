"""유튜브 공개 데이터 (YouTube Data API v3, API 키만 사용: 로그인·OAuth 사용자 데이터 없음).

종목명으로 최근 영상을 찾아 제목·설명·조회수·좋아요·댓글 수와 공개 댓글 본문을 받습니다.
댓글 작성자 이름·채널은 저장하지 않습니다 (본문·작성일·좋아요 수만).
할당량: 하루 10,000 단위. search.list 1회 100, videos.list·commentThreads.list 1회 1
→ 34종목 하루 1회 검색 ≈ 3,400 + 댓글 수백 단위.
검색어는 한국어(종목명 + '주가')라 댓글도 한국어 위주 → 기존 감성 사전으로 채점합니다.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..config import env
from ..http_util import RateLimiter, get_json

BASE = "https://www.googleapis.com/youtube/v3"
_limiter = RateLimiter(0.2)


def _date(ts: str | None) -> date | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).date()


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _check(payload: dict) -> dict:
    if isinstance(payload, dict) and payload.get("error"):
        err = payload["error"]
        reason = ((err.get("errors") or [{}])[0]).get("reason", "")
        raise RuntimeError(f"[youtube] {err.get('code')} {reason}".strip())
    return payload


def parse_search(payload: dict) -> list[dict]:
    """search.list → [{video_id, title, summary, published_at, channel}]"""
    out = []
    for it in _check(payload).get("items", []):
        vid = (it.get("id") or {}).get("videoId")
        sn = it.get("snippet") or {}
        if not vid:
            continue
        out.append({"video_id": vid, "title": (sn.get("title") or "").strip(),
                    "summary": (sn.get("description") or "").strip()[:500],
                    "published_at": _date(sn.get("publishedAt")), "channel": (sn.get("channelTitle") or "").strip()})
    return out


def parse_stats(payload: dict) -> dict[str, dict]:
    """videos.list(part=statistics) → {video_id: {view_count, like_count, comment_count}}"""
    out = {}
    for it in _check(payload).get("items", []):
        st = it.get("statistics") or {}
        out[it.get("id")] = {"view_count": _int(st.get("viewCount")), "like_count": _int(st.get("likeCount")),
                             "comment_count": _int(st.get("commentCount"))}
    return out


def parse_comments(payload: dict, video_id: str) -> list[dict]:
    """commentThreads.list → rag.document 용 행 (최상위 댓글만, 작성자 정보 제외)."""
    rows = []
    for it in _check(payload).get("items", []):
        top = ((it.get("snippet") or {}).get("topLevelComment") or {})
        sn = top.get("snippet") or {}
        text = (sn.get("textOriginal") or sn.get("textDisplay") or "").strip()
        if not top.get("id") or not text:
            continue
        rows.append({"title": "", "url": f"https://www.youtube.com/watch?v={video_id}&lc={top['id']}",
                     "published_at": _date(sn.get("publishedAt")), "summary": text[:500]})
    return rows


def search_videos(session, query: str, since: date, max_results: int = 5) -> list[dict]:
    params = {"part": "snippet", "q": query, "type": "video", "order": "date", "maxResults": max_results,
              "publishedAfter": f"{since.isoformat()}T00:00:00Z", "regionCode": "KR", "relevanceLanguage": "ko",
              "key": env("YOUTUBE_API_KEY", required=True)}
    return parse_search(get_json(session, "youtube", f"{BASE}/search", params=params, limiter=_limiter))


def video_stats(session, video_ids: list[str]) -> dict[str, dict]:
    if not video_ids:
        return {}
    params = {"part": "statistics", "id": ",".join(video_ids[:50]), "key": env("YOUTUBE_API_KEY", required=True)}
    return parse_stats(get_json(session, "youtube", f"{BASE}/videos", params=params, limiter=_limiter))


def video_comments(session, video_id: str, max_results: int = 100) -> list[dict]:
    """댓글을 막은 영상(403)은 빈 목록."""
    params = {"part": "snippet", "videoId": video_id, "maxResults": max_results, "order": "relevance",
              "textFormat": "plainText", "key": env("YOUTUBE_API_KEY", required=True)}
    try:
        payload = get_json(session, "youtube", f"{BASE}/commentThreads", params=params, limiter=_limiter)
    except RuntimeError as exc:
        if "HTTP 403" in str(exc):
            return []
        raise
    return parse_comments(payload, video_id)
