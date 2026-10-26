import requests, json
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
      "Referer": "https://m.stock.naver.com/"}
r = requests.get("https://m.stock.naver.com/api/stock/069500/etfAnalysis", headers=UA, timeout=20).json()
for k, v in r.items():
    print("etfAnalysis.", k, type(v).__name__, (len(v) if isinstance(v, (list, dict, str)) else v) if not isinstance(v, str) or len(v) > 60 else v)
    if isinstance(v, list) and v:
        print("   first:", json.dumps(v[0], ensure_ascii=False)[:300])
for u in ["https://m.stock.naver.com/api/index/KPI200/enrollStocks",
          "https://m.stock.naver.com/api/index/KPI200/enrollStocks?pageSize=20&page=1",
          "https://m.stock.naver.com/api/index/KPI200/enrollStocks?page=1&pageSize=20&sortType=MARKET_VALUE",
          "https://m.stock.naver.com/api/index/KPI200/includeStocks?page=1&pageSize=50",
          "https://m.stock.naver.com/api/index/KPI200/stocks?page=1&pageSize=50",
          "https://m.stock.naver.com/api/stock/069500/etfComponent?page=1&pageSize=300",
          "https://m.stock.naver.com/api/etf/069500/components",
          "https://finance.naver.com/api/sise/etfItemList.nhn",
          "https://navercomp.wisereport.co.kr/v2/ETF/Index.aspx?cn=&cmp_cd=069500&menuType=block"]:
    try:
        x = requests.get(u, headers=UA, timeout=20)
        print("==", u[28:], x.status_code, len(x.content), repr(x.text[:250]))
    except Exception as e:
        print("==", u, "ERR", type(e).__name__)
