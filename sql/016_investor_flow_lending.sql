-- 투자자별 일별 순매수 (네이버 증권 모바일 공개 API, 로그인 불필요). 수량 기준(주), 금액 아님
CREATE TABLE IF NOT EXISTS core.investor_flow_daily (
    company_id         bigint NOT NULL REFERENCES core.company (company_id),
    trade_date         date   NOT NULL,
    foreign_net_qty    bigint,          -- 외국인 순매수 수량
    institution_net_qty bigint,         -- 기관 순매수 수량
    individual_net_qty bigint,          -- 개인 순매수 수량
    foreign_hold_pct   numeric(7, 4),   -- 외국인 보유율 (%)
    volume             bigint,          -- 거래량 (순매수 비율 계산용)
    source             text NOT NULL,
    collected_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, trade_date)
);

-- 종목별 일별 주식 대차거래 (금융위원회_주식대차거래정보 GetCMStckLnbInfoService, 공공누리 4유형).
-- 공매도는 먼저 주식을 빌려야 해서 대차잔고를 공매도 압력의 대용 지표로 씁니다 (KRX 공매도 통계는 로그인 필요). 2021-04 이후만 제공
CREATE TABLE IF NOT EXISTS core.stock_lending_daily (
    company_id   bigint NOT NULL REFERENCES core.company (company_id),
    trade_date   date   NOT NULL,
    lent_qty     bigint,          -- 체결주식수
    repaid_qty   bigint,          -- 상환주식수
    balance_qty  bigint,          -- 잔고주식수
    balance_amt  numeric(20, 0),  -- 잔고금액(원) = 잔고주수 × 해당일 종가
    source       text NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, trade_date)
);
