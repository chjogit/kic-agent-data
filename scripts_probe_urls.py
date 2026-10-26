import requests, datetime
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
d = (datetime.date.today() - datetime.timedelta(days=3)).strftime("%Y%m%d")
cands = [
    ("GET", "https://finance.naver.com/sise/entryJongmok.naver?type=KPI200&page=1", None),
    ("GET", "https://finance.naver.com/sise/entryJongmok.nhn?type=KPI200&page=1", None),
    ("GET", "https://finance.naver.com/sise/entryJongmok.naver?page=1", None),
    ("GET", "https://m.stock.naver.com/api/index/KPI200/enrollStocks?page=1&pageSize=100", None),
    ("GET", "https://m.stock.naver.com/api/index/KPI200/integration", None),
    ("GET", "https://m.stock.naver.com/api/stock/069500/etfAnalysis", None),
    ("GET", "https://m.stock.naver.com/api/stocks/marketValue/KOSPI?page=1&pageSize=5", None),
    ("POST", "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
     {"bld": "dbms/MDC/STAT/standard/MDCSTAT00601", "indIdx": "1", "indIdx2": "028", "trdDd": d}),
    ("POST", "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
     {"bld": "dbms/MDC/STAT/standard/MDCSTAT03901", "mktId": "STK", "trdDd": d}),
]
for m, u, data in cands:
    try:
        h = dict(UA, Referer="https://data.krx.co.kr/" if "krx" in u else "https://finance.naver.com/")
        r = requests.request(m, u, data=data, headers=h, timeout=20)
        body = r.content[:400]
        try:
            body = r.content.decode("euc-kr" if "finance.naver" in u else "utf-8", errors="replace")[:400]
        except Exception:
            pass
        print("==", m, u.split("?")[0][-60:], data and data.get("bld"), r.status_code, len(r.content))
        print(repr(body.replace("\n", " ")[:400]))
    except Exception as e:
        print("==", u, "ERR", type(e).__name__)
