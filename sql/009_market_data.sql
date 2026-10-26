-- 009: 시장 지수, 공시 분류 컬럼, 검색 관심도

CREATE TABLE IF NOT EXISTS core.market_index_daily (
    symbol       text NOT NULL,              -- ^KS11 등 (yfinance 심볼)
    trade_date   date NOT NULL,
    name         text,
    open         numeric, high numeric, low numeric, close numeric,
    volume       bigint,
    source       text NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, trade_date)
);

ALTER TABLE core.disclosure ADD COLUMN IF NOT EXISTS category   text;   -- DART pblntf_ty(A/B/I...) 또는 SEC form
ALTER TABLE core.disclosure ADD COLUMN IF NOT EXISTS event_type text;   -- rights_issue, buyback, earnings 등 (키워드 분류)
ALTER TABLE core.disclosure ADD COLUMN IF NOT EXISTS url        text;

-- 검색 관심도(네이버 데이터랩). ratio 는 '요청 한 번 안에서' 최댓값을 100으로 한 상대값이라
-- 요청이 다르면 직접 비교할 수 없습니다 → 매 요청에 기준 키워드(anchor)를 넣고 anchor_ratio 도 함께 저장합니다.
CREATE TABLE IF NOT EXISTS core.search_trend (
    subject      text NOT NULL,              -- 'KR:005930', 'topic:HBM' 등
    keyword      text,
    trade_date   date NOT NULL,
    ratio        numeric,
    anchor_ratio numeric,
    source       text NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (subject, trade_date, source)
);
