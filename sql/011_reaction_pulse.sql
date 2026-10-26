-- 011: 장중 여론 스냅샷 (pulse). 15분마다 또는 화면에서 종목을 누를 때 한 행씩 쌓여 '오늘의 여론 추이' 를 그립니다.
CREATE TABLE IF NOT EXISTS core.reaction_pulse (
    company_id      bigint NOT NULL REFERENCES core.company (company_id),
    captured_at     timestamptz NOT NULL DEFAULT now(),
    trade_date      date NOT NULL,         -- 한국 날짜
    n_today         int,                   -- 오늘 뉴스+블로그 수
    n_news          int,
    n_blog          int,
    n_positive      int,
    n_negative      int,
    sentiment       numeric,               -- 오늘 평균 톤 (-1~+1), 3건 미만이면 NULL
    mentions_vs_30d numeric,               -- 오늘 언급 수 / 최근 30일 일평균
    new_docs        int,                   -- 이번 호출로 새로 들어온 글
    method          text,
    PRIMARY KEY (company_id, captured_at)
);
CREATE INDEX IF NOT EXISTS ix_reaction_pulse_day ON core.reaction_pulse (company_id, trade_date);
