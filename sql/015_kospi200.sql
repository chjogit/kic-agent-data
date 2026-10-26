-- KOSPI 200 유니버스: 지수 편입 표시와 KRX 업종(KSIC 기반 매핑)
ALTER TABLE core.company ADD COLUMN IF NOT EXISTS krx_sector  text;
ALTER TABLE core.company ADD COLUMN IF NOT EXISTS index_tags  text[] NOT NULL DEFAULT '{}';
ALTER TABLE core.company ADD COLUMN IF NOT EXISTS index_asof  date;
CREATE INDEX IF NOT EXISTS company_index_tags_idx ON core.company USING gin (index_tags);
