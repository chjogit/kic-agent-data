import requests
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
      "Referer": "https://m.stock.naver.com/"}
B = "https://m.stock.naver.com/api/index/KPI200/enrollStocks"
for q in ["", "?page=2", "?page=1&pageSize=100", "?page=2&pageSize=100", "?page=3&pageSize=100", "?pageSize=300", "?page=4"]:
    x = requests.get(B + q, headers=UA, timeout=20).json()
    codes = [i["itemCode"] for i in x] if isinstance(x, list) else x
    print(q or "(none)", len(codes) if isinstance(codes, list) else codes, codes[:3] if isinstance(codes, list) else "", codes[-2:] if isinstance(codes, list) else "")
