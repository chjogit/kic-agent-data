"""미국 일별 시세 (yfinance: 비공식, 개발·연구용). 상용 전환 시 정식 데이터 소스로 교체하세요."""
from __future__ import annotations

from ..util import to_decimal, to_int


def fetch_prices(ticker: str, start: str) -> list[dict]:
    import yfinance as yf

    df = yf.Ticker(ticker).history(start=start, auto_adjust=False)
    rows = []
    for ts, r in df.iterrows():
        rows.append(
            {
                "trade_date": ts.date(),
                "open": to_decimal(r.get("Open")),
                "high": to_decimal(r.get("High")),
                "low": to_decimal(r.get("Low")),
                "close": to_decimal(r.get("Close")),
                "volume": to_int(r.get("Volume")),
                "trade_value": None,
                "market_cap": None,
            }
        )
    return rows
