"""사업보고서 원문 노출 추출 (kic_data/exposure_parse.py): 실제 원문(2025 사업보고서)에서 본 표 모양을 재현합니다."""
from kic_data import exposure_parse as ep
from kic_data.collectors import dart_doc


def _t(cells_rows, before="", unit_table=None):
    rows = "".join("<TR>" + "".join(f"<TD>{c}</TD>" for c in r) + "</TR>" for r in cells_rows)
    pre = f"<TABLE><TR><TD>{unit_table}</TD></TR></TABLE>" if unit_table else ""
    return f"<P>{before}</P>{pre}<TABLE>{rows}</TABLE>"


def test_rowspan_colspan_and_unit_table_merge():
    x = ('<P>환율이 10% 변동시 법인세비용차감전순이익에 미치는 영향</P><TABLE><TR><TD>(단위: 백만원)</TD></TR></TABLE>'
         '<TABLE><TR><TH ROWSPAN="2">구분</TH><TH COLSPAN="2">당기</TH></TR><TR><TH>상승</TH><TH>하락</TH></TR>'
         '<TR><TD>USD</TD><TD>1,234</TD><TD>(1,234)</TD></TR></TABLE>')
    t = dart_doc.tables_from_xml(x)
    assert len(t) == 1 and t[0].unit == "백만원" and "10% 변동시" in t[0].context
    assert t[0].rows == [["구분", "당기", "당기"], ["구분", "상승", "하락"], ["USD", "1,234", "(1,234)"]]


def test_numbers():
    assert ep.num("1,234") == 1234 and ep.num("(1,234)") == -1234 and ep.num("△435,059") == -435059
    assert ep.num("-") is None and ep.num("USD 500") is None and ep.num("12.5") == 12.5


def test_sensitivity_layout_rows_are_currencies():   # SK하이닉스·삼성전자 형태
    x = _t([["구 분", "10% 상승시", "10% 하락시"], ["USD", "1,210,371", "(1,210,371)"], ["JPY", "(105,656)", "105,656"]],
           before="각 외화에 대한 기능통화의 환율이 10% 변동시, 동 환율변동이 법인세비용차감전순이익에 미치는 영향",
           unit_table="(단위: 백만원)")
    res = ep.extract({"a.xml": x})
    m = {(k.metric, k.currency): k for k in res["metrics"]}
    usd = m[("fx_sens", "USD")]
    assert usd.value == 1_210_371e6 and usd.change_pct == 10 and usd.basis == "세전이익"
    assert m[("fx_sens", "JPY")].value == -105_656e6


def test_sensitivity_layout_xbrl_rows_are_up_down():  # 표준 주석(XBRL) 형태
    x = _t([["", "USD", "JPY"],
            ["시장위험의 각 유형별 민감도분석 기술", "외화에 대한 기능통화의 환율이 10% 변동시 법인세비용차감전순이익", "같음"],
            ["시장변수 상승 시 당기손익 증가", "1,210,371", ""],
            ["시장변수 상승 시 당기손익 감소", "", "(105,656)"],
            ["시장변수 하락 시 당기손익 증가", "", "105,656"]],
           before="기능통화 외 환율 변동 위험", unit_table="(단위 : 백만원)")
    m = {(k.metric, k.currency): k for k in ep.extract({"a.xml": x})["metrics"]}
    assert m[("fx_sens", "USD")].value == 1_210_371e6 and m[("fx_sens", "JPY")].value == -105_656e6


def test_sensitivity_layout_period_then_currency():   # 한화 형태: '당 기 | USD | …', 전기 행은 무시
    x = _t([["구 분", "구 분", "10% 상승시", "10% 상승시", "10% 하락시", "10% 하락시"],
            ["구 분", "구 분", "손 익", "순자산", "손 익", "순자산"],
            ["당 기", "USD", "(92,568)", "(92,568)", "92,568", "92,568"],
            ["당 기", "EUR", "2,094", "2,094", "(2,094)", "(2,094)"],
            ["전 기", "USD", "(50,000)", "(50,000)", "50,000", "50,000"]],
           before="보고기간말 현재 각 외화에 대한 원화 환율 10% 변동시 손익 및 순자산에 미치는 영향",
           unit_table="(단위: 백만원)")
    m = {(k.metric, k.currency): k for k in ep.extract({"a.xml": x})["metrics"]}
    assert m[("fx_sens", "USD")].value == -92_568e6 and m[("fx_sens", "EUR")].value == 2_094e6
    assert m[("fx_sens", "USD")].change_pct == 10


def test_sensitivity_layout_single_total_row():       # DL·롯데정밀화학 형태: 통화 구분 없는 합계 한 행
    x = _t([["구분", "당기", "당기", "전기", "전기"],
            ["구분", "10% 환율 인상시", "10% 환율 인하시", "10% 환율 인상시", "10% 환율 인하시"],
            ["세전순이익", "(6,410)", "6,410", "46,423", "(46,423)"]],
           before="각 외화에 대한 기능통화의 환율이 10% 변동시 연결회사의 세전순이익에 미치는 영향",
           unit_table="(단위: 백만원)")
    (s,) = [k for k in ep.extract({"a.xml": x})["metrics"] if k.metric == "fx_sens"]
    assert s.currency == "ALL" and s.value == -6_410e6 and s.basis == "세전이익" and s.change_pct == 10


def test_sensitivity_down_column_first():
    x = _t([["구분", "10% 하락시", "10% 상승시"], ["USD", "500", "(500)"]],
           before="환율이 10% 변동시 법인세비용차감전순이익에 미치는 영향", unit_table="(단위: 백만원)")
    (s,) = [k for k in ep.extract({"a.xml": x})["metrics"] if k.metric == "fx_sens"]
    assert s.value == -500e6


def test_fx_position_uses_krw_columns():
    x = ('<P>화폐성 외화자산 및 외화부채의 내역</P><TABLE><TR><TD>(원화단위: 백만원, 외화단위: 외화 백만단위)</TD></TR></TABLE>'
         '<TABLE><TR><TD ROWSPAN="2">구 분</TD><TD COLSPAN="2">자 산</TD><TD COLSPAN="2">부 채</TD></TR>'
         '<TR><TD>현지통화</TD><TD>원화환산</TD><TD>현지통화</TD><TD>원화환산</TD></TR>'
         '<TR><TD>USD</TD><TD>23,074</TD><TD>33,108,951</TD><TD>15,528</TD><TD>22,280,863</TD></TR></TABLE>')
    m = {(k.metric, k.currency): k for k in ep.extract({"a.xml": x})["metrics"]}
    assert m[("fx_asset", "USD")].value == 33_108_951e6 and m[("fx_liab", "USD")].value == 22_280_863e6


SAMSUNG_EXPORT = [["구 분", "구 분", "제57기", "제56기"], ["내수", "국내", "216,560", "202,978"],
                  ["수출", "미주", "678,942", "613,533"], ["수출", "유럽", "312,307", "290,967"],
                  ["수출", "아시아ㆍ아프리카", "456,870", "333,769"], ["수출", "중국", "715,751", "649,275"],
                  ["계", "계", "2,380,430", "2,090,522"]]


def test_export_share_checks_total_and_company_size():
    x = _t(SAMSUNG_EXPORT)
    m = {(k.metric, k.currency): k for k in ep.extract({"a.xml": x}, revenue_krw=333.6e12)["metrics"]}
    assert round(m[("export_share", "ALL")].value, 1) == 90.9          # 2,163,870 / 2,380,430
    # 같은 표인데 회사 매출 규모와 안 맞으면(종속회사·일부 부문 표) 버림
    assert not ep.extract({"a.xml": x}, revenue_krw=3_000e12)["metrics"]


def test_export_share_rejects_segment_table_and_bad_total():
    rotem = [["사업구분", "품 목", "품 목", "2025년"], ["디펜스", "방산물자", "내수", "8,861"], ["디펜스", "방산물자", "수출", "23,292"],
             ["디펜스", "방산물자", "합계", "32,153"], ["연결기준합계", "연결기준합계", "연결기준합계", "32,153"]]
    assert not ep.extract({"a.xml": _t(rotem)}, revenue_krw=186e12)["metrics"]   # 현대차 원문 속 현대로템 표
    bad = [r[:] for r in SAMSUNG_EXPORT]
    bad[-1] = ["계", "계", "9,999,999", "1"]
    assert not ep.extract({"a.xml": _t(bad)}, revenue_krw=333.6e12)["metrics"]


def test_fx_derivative_and_risk_text():
    x = _t([["구 분", "자산", "부채"], ["통화선도", "93,248", "107,811"]],
           before="외화 포지션에 대하여 통화선도(Currency Forward) 상품을 매매하여 헷지하고 있습니다.",
           unit_table="(단위 : 백만원)")
    res = ep.extract({"a.xml": x})
    assert "fx_derivative" in res["found"] and "통화선도" in res["risk_text"]


def test_nothing_found_returns_empty():
    res = ep.extract({"a.xml": _t([["구분", "금액"], ["토지", "1,000"]])})
    assert res == {"metrics": [], "risk_text": "", "found": []}
