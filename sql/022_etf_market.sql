-- 022: ETF 확장 (10-28). 해외 상장 ETF(SPY·QQQ 등)를 같은 표에 넣고, ETF 분류표를 둡니다.
-- 종목코드 형식이 달라(국내 6자리 숫자, 해외 영문) 기본키 (ticker, trade_date) 는 그대로 둡니다.

ALTER TABLE core.etf_price_daily ADD COLUMN IF NOT EXISTS market   text NOT NULL DEFAULT 'KR';   -- KR / US
ALTER TABLE core.etf_price_daily ADD COLUMN IF NOT EXISTS currency text NOT NULL DEFAULT 'KRW';  -- KRW / USD

-- ETF 분류 (universe.py 의 ETF_CATEGORY 를 수집 때마다 반영). 같은 지수를 따르는 ETF 를 묶어 볼 때 씁니다.
CREATE TABLE IF NOT EXISTS core.etf_info (
    ticker      text PRIMARY KEY,
    name        text,                 -- 원천 API 의 최근 종목명 (없으면 universe.py 이름)
    market      text NOT NULL,        -- KR / US
    category    text,                 -- kospi200 / sp500 / nasdaq100 / kr_high_dividend / semiconductor
    base_index  text,                 -- 원천 API 가 준 기초지수명 (국내만)
    name_check  text,                 -- ok / mismatch (기대한 말이 종목명에 없음) / unknown
    updated_at  timestamptz NOT NULL DEFAULT now()
);
