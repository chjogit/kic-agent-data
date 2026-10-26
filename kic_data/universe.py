"""수집 대상 종목과 시범 Peer Group. 시작용 예시이므로 팀에서 자유롭게 바꾸세요.

종목은 업종 순위 목록의 대표 종목을 근거로 한 수동 선정입니다 (편입 전 시총·유동성 확인 필요).
"""

# (티커, 이름)
KR_UNIVERSE = [
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("042700", "한미반도체"),
    ("000990", "DB하이텍"),
    ("067310", "하나마이크론"),
    ("036930", "주성엔지니어링"),
    ("035420", "NAVER"),
    # 10-25 추가: 반도체 비교군·학습 표본 확대 (장비·소재·부품·팹리스)
    ("058470", "리노공업"),
    ("039030", "이오테크닉스"),
    ("240810", "원익IPS"),
    ("357780", "솔브레인"),
    ("005290", "동진쎄미켐"),
    ("095340", "ISC"),
    ("108320", "LX세미콘"),
]

US_UNIVERSE = [
    ("NVDA", "NVIDIA"),
    ("MU", "Micron Technology"),
    ("AVGO", "Broadcom"),
    ("AMD", "Advanced Micro Devices"),
    ("INTC", "Intel"),
    ("AMAT", "Applied Materials"),
    ("ASML", "ASML Holding (ADR)"),
    ("TSM", "TSMC (ADR)"),
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    # 10-25 추가: 모두 10-K 제출 기업이라 분기 재무(TTM)까지 채워집니다
    ("QCOM", "Qualcomm"),
    ("TXN", "Texas Instruments"),
    ("LRCX", "Lam Research"),
    ("KLAC", "KLA"),
    ("ADI", "Analog Devices"),
    ("MRVL", "Marvell Technology"),
    ("ON", "ON Semiconductor"),
    ("NXPI", "NXP Semiconductors"),
    ("MCHP", "Microchip Technology"),
    ("TER", "Teradyne"),
]

# 국내 ETF (금융위원회 증권상품시세정보). 시장·업종 대표 ETF 로 시작합니다.
ETF_UNIVERSE = [
    ("069500", "KODEX 200"),
    ("091160", "KODEX 반도체"),
    ("091230", "TIGER 반도체"),
    ("381180", "TIGER 미국필라델피아반도체나스닥"),
    ("396500", "TIGER 반도체TOP10"),
]

# 시범 업종: 기준 종목마다 같은 업종의 나머지 종목을 비교군으로 묶습니다.
PEER_THEMES = {
    "반도체": {
        "bases": [("KR", "005930"), ("KR", "000660"), ("US", "NVDA")],
        "members": [
            ("KR", "005930"), ("KR", "000660"), ("KR", "042700"), ("KR", "000990"),
            ("KR", "067310"), ("KR", "036930"),
            ("US", "NVDA"), ("US", "MU"), ("US", "AVGO"), ("US", "AMD"),
            ("US", "INTC"), ("US", "AMAT"), ("US", "ASML"), ("US", "TSM"),
            ("KR", "058470"), ("KR", "039030"), ("KR", "240810"), ("KR", "357780"),
            ("KR", "005290"), ("KR", "095340"), ("KR", "108320"),
            ("US", "QCOM"), ("US", "TXN"), ("US", "LRCX"), ("US", "KLAC"), ("US", "ADI"),
            ("US", "MRVL"), ("US", "ON"), ("US", "NXPI"), ("US", "MCHP"), ("US", "TER"),
        ],
    },
    # 반도체 밖 종목(NAVER·Apple·Microsoft)도 비교군이 비지 않도록 묶은 보조 그룹 (표본이 작아 참고용)
    "빅테크·플랫폼": {
        "bases": [("KR", "035420"), ("US", "AAPL"), ("US", "MSFT")],
        "members": [("KR", "035420"), ("US", "AAPL"), ("US", "MSFT")],
    },
}
