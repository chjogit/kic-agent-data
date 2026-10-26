"""시장 지수 일봉 (yfinance: 비공식, 개발·연구용). 당일 종가까지 받을 수 있어 시황 보고서에 사용합니다.
금융위 지수시세정보 API 는 기준일 다음 영업일 오후 갱신이라 당일 시황용으로는 부적합합니다."""
from __future__ import annotations

from ..util import to_decimal, to_int

# (yfinance 심볼, 이름)
INDEX_SYMBOLS = [
    ("^KS11", "코스피"),
    ("^KQ11", "코스닥"),
    ("^GSPC", "S&P 500"),
    ("^IXIC", "나스닥 종합"),
    ("^SOX", "필라델피아 반도체지수"),
    ("^VIX", "VIX 변동성지수"),
]


def rows_from_history(df) -> list[dict]:
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
            }
        )
    return [r for r in rows if r["close"] is not None]


def fetch_index(symbol: str, start: str) -> list[dict]:
    import yfinance as yf

    return rows_from_history(yf.Ticker(symbol).history(start=start, auto_adjust=False))
