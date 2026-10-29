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
    assert res == {"metrics": [], "risk_text": "", "price_text": [], "hedge_text": "", "found": []}


def test_sensitivity_prefers_consolidated_notes_section():   # 지주사: 앞쪽 관계회사 표보다 연결재무제표 주석의 표
    other = _t([["구분", "10% 상승시", "10% 하락시"], ["USD", "(964,900)", "964,900"]],
               before="LG화학의 환율이 10% 변동시 법인세비용차감전순이익에 미치는 영향", unit_table="(단위: 백만원)")
    own = _t([["구분", "10% 상승시", "10% 하락시"], ["USD", "400", "(400)"]],
             before="연결실체의 환율이 10% 변동시 법인세비용차감전순이익에 미치는 영향", unit_table="(단위: 백만원)")
    x = ("<TITLE>II. 사업의 내용</TITLE>" + other + "<TITLE>3. 연결재무제표 주석</TITLE>" + own
         + "<TITLE>5. 재무제표 주석</TITLE>" + other)
    (s,) = [k for k in ep.extract({"a.xml": x})["metrics"] if k.metric == "fx_sens"]
    assert s.value == 400e6
    assert dart_doc.tables_from_xml(x)[0].extra["section"] == "II. 사업의 내용"


def test_ir_sensitivity_bp_and_up_column():
    x = _t([["구 분", "100bp 상승시", "100bp 하락시"], ["법인세비용차감전순이익", "(12,345)", "12,345"]],
           before="변동금리부 차입금에 대하여 이자율이 100bp 변동시 법인세비용차감전순이익에 미치는 영향",
           unit_table="(단위: 백만원)")
    res = ep.extract({"a.xml": x})
    (s,) = [k for k in res["metrics"] if k.metric == "ir_sens"]
    assert s.value == -12_345e6 and s.change_pct == 1 and s.basis == "세전이익" and "ir_sens" in res["found"]
    assert not [k for k in res["metrics"] if k.metric == "fx_sens"]


def test_ir_sensitivity_rows_are_up_down():
    x = _t([["구분", "당기", "전기"], ["1%p 상승시 이자비용 증가", "3,000", "2,500"], ["1%p 하락시 이자비용 감소", "(3,000)", "(2,500)"]],
           before="이자율위험: 이자율 변동시 이자비용에 미치는 영향", unit_table="(단위: 천원)")
    (s,) = [k for k in ep.extract({"a.xml": x})["metrics"] if k.metric == "ir_sens"]
    assert s.value == 3_000e3 and s.change_pct == 1


def test_price_text_excerpt_without_numbers_from_tables():
    body = ("<TITLE>II. 사업의 내용</TITLE><P>나. 주요 제품 등의 가격변동추이</P>"
            "<TABLE><TR><TD>DRAM</TD><TD>3.5</TD></TR></TABLE>"
            "<P>메모리 가격은 수요 회복과 공급사의 감산으로 상승하였으며, 고객사와의 분기별 가격 협상을 통해 원가 변동을 "
            "판매가격에 반영하고 있습니다.</P><P>다. 주요 원재료</P>")
    res = ep.extract({"a.xml": body})
    assert "price_text" in res["found"] and "가격 협상" in res["price_text"][0] and "3.5" not in res["price_text"][0]


def test_sensitivity_risk_section_beats_mdna():   # 한화: 경영진 분석(3. 재무상태 및 영업실적)의 1% 표보다 위험관리 절
    mdna = _t([["구분", "1% 상승시", "1% 하락시"], ["USD", "12", "(12)"]],
              before="연결실체의 환율이 1% 변동시 법인세비용차감전순이익에 미치는 영향", unit_table="(단위: 백만원)")
    risk = _t([["구분", "10% 상승시", "10% 하락시"], ["USD", "(92,568)", "92,568"]],
              before="연결실체의 환율이 10% 변동시 법인세비용차감전순이익에 미치는 영향", unit_table="(단위: 백만원)")
    x = "<TITLE>3. 재무상태 및 영업실적</TITLE>" + mdna + "<TITLE>5. 위험관리 및 파생거래</TITLE>" + risk
    (s,) = [k for k in ep.extract({"a.xml": x})["metrics"] if k.metric == "fx_sens"]
    assert s.value == -92_568e6


def test_ir_liability_rows_samsung_lgchem_hyundai():   # 10-29 원문 점검(probe-hedge)에서 본 세 가지 모양
    sam = _t([["구 분", "제57기말", "제57기말", "제56기말", "제56기말"],
              ["구 분", "이자율 상승시", "이자율 하락시", "이자율 상승시", "이자율 하락시"],
              ["금융자산", "76,243", "△76,243", "141,671", "△141,671"],
              ["금융부채", "△33,597", "33,597", "△5,702", "5,702"],
              ["순효과", "42,646", "△42,646", "135,969", "△135,969"]],
             before="변동금리부 금융자산 및 금융부채에 대하여 이자율이 1%p 변동할 경우 당기손익(법인세효과 반영 전)에 미치는 영향",
             unit_table="(단위: 백만원)")
    lg = _t([["구분", "2025.12.31", "2025.12.31", "2024.12.31", "2024.12.31"],
             ["구분", "1%p 상승시", "1%p 하락시", "1%p 상승시", "1%p 하락시"],
             ["이자수익", "26,810", "(26,810)", "27,347", "(27,347)"], ["이자비용", "(92,814)", "92,814", "(51,899)", "51,899"]],
            before="이자율이 1%(100bp) 변동하는 경우 예금 및 변동금리부 차입금으로부터 발생하는 이자수익과 이자비용의 영향",
            unit_table="(단위: 백만원)")
    hy = _t([["구분", "1% 상승시", "1% 하락시"], ["현금및현금성자산", "36,425", "(36,425)"],
             ["차입금 및 사채", "(173,638)", "173,638"]],
            before="이자율이 1% 변동시 이자율변동이 법인세비용차감전순이익에 미치는 영향", unit_table="(단위: 백만원)")
    for x, want in ((sam, -33_597e6), (lg, -92_814e6), (hy, -173_638e6)):
        (m,) = [k for k in ep.extract({"a.xml": x})["metrics"] if k.metric == "ir_sens_liab"]
        assert m.value == want and m.change_pct == 1


def test_ir_liability_absent_when_only_net_row():   # SK하이닉스 모양: 순효과 한 줄 → 부채 쪽 값 없음
    x = _t([["구분", "100bp 상승시", "100bp 하락시"], ["법인세비용차감전순이익", "(41,875)", "41,875"]],
           before="이자율이 100bp 하락 또는 상승시 변동금리부 차입금 및 금융자산으로부터의 순이자비용", unit_table="(단위: 백만원)")
    res = ep.extract({"a.xml": x})
    assert "ir_sens" in res["found"] and "ir_sens_liab" not in res["found"]


def test_hedge_accounting_flag_and_text_without_numbers_parsed():
    x = _t([["위험회피대상", "계약종류", "계약기간"],
            ["고정금리외화사채(액면금액 USD 750,000)", "통화스왑계약", "2023.01.17 ~ 2026.01.17"]],
           before="현금흐름위험회피회계를 적용한 통화스왑 및 이자율스왑 거래내역은 다음과 같습니다. 환율변동위험",
           unit_table="(단위: 백만원)")
    res = ep.extract({"a.xml": x})
    assert "fx_hedge_acct" in res["found"] and "통화스왑" in res["hedge_text"]
    assert not [m for m in res["metrics"] if m.metric not in ("fx_hedge_acct", "fx_derivative")]   # 계약 규모는 숫자로 저장 안 함


def test_hedge_accounting_negation_is_not_flagged():
    x = _t([["구 분", "자산", "부채"], ["통화선도", "1,000", "2,000"]],
           before="당사는 환율변동위험을 줄이기 위해 통화선도를 이용하나 위험회피회계를 적용하지 않습니다.",
           unit_table="(단위: 백만원)")
    res = ep.extract({"a.xml": x})
    assert "fx_derivative" in res["found"] and "fx_hedge_acct" not in res["found"]


# ---- v3 (10-29): 원문 절을 못 찾던 회사들의 표 모양 (한전KPS·강원랜드·삼성SDS 2025 사업보고서)

def test_v3_net_income_row_with_increase_decrease_label():   # 한전KPS '5. 위험관리 및 파생거래'
    x = _t([["구 분", "당 기", "당 기", "전 기", "전 기"], ["구 분", "10% 증가", "10% 감소", "10% 증가", "10% 감소"],
            ["순이익 증가(감소)", "5,574", "-5,574", "4,230", "-4,230"], ["자본의 증가(감소)", "5,574", "-5,574", "4,230", "-4,230"]],
           before="보고기간말 현재 각 외화에 대한 원화환율이 10% 변동 시 환율변동이 순이익 및 자본에 미치는 영향은 다음과 같습니다.",
           unit_table="(단위: 백만원)")
    m = [k for k in ep.extract({"a.xml": x})["metrics"] if k.metric == "fx_sens"]
    assert len(m) == 1 and m[0].currency == "ALL" and m[0].value == 5_574e6 and m[0].change_pct == 10
    assert m[0].basis == "당기손익"


def test_v3_xbrl_rate_row_and_two_row_unit_table():   # 한전KPS 연결 주석 '34. 위험관리' (표준 주석)
    unit = "<TABLE><TR><TD>외화환율변동의 민감도분석에 대한 공시</TD></TR><TR><TD>당기</TD><TD>(단위 : 원)</TD></TR></TABLE>"
    x = unit + _t([["", "위험"], ["", "시장위험"], ["", "환위험"],
                   ["외화환율변동의 합리적인 범위 내에서의 발생가능한 0.1 증가율", "0.1"],
                   ["법인세비용차감전순손익 증가(감소)", "5,574,487,337"],
                   ["외화환율증가에 따른 자본의 증가(감소)", "5,574,487,337"]])
    t = dart_doc.tables_from_xml(x)
    assert len(t) == 1 and t[0].unit == "원"
    m = [k for k in ep.extract({"a.xml": x})["metrics"] if k.metric == "fx_sens"]
    assert len(m) == 1 and m[0].value == 5_574_487_337 and m[0].change_pct == 10 and m[0].basis == "세전이익"


def test_v3_ir_table_mentioning_other_variables_like_fx():   # 강원랜드 연결감사보고서 주석
    x = _t([["구 분", "당기", "당기", "전기", "전기"], ["구 분", "100bp 상승", "100bp 하락", "100bp 상승", "100bp 하락"],
            ["법인세차감전순손익", "(65,000,000)", "65,000,000", "(65,000,000)", "65,000,000"]],
           before="② 이자율위험 관리 연결기업은 변동이자율로 자금을 차입하고 있습니다. 당기말 현재 이자율이 100베이시스포인트 "
                  "변동한다면, 손익은 증가 또는 감소하였을 것입니다. 이 분석은 환율과 같은 다른 변수는 변동하지 않는다고 가정하였으며",
           unit_table="(단위: 원)")
    m = [k for k in ep.extract({"a.xml": x})["metrics"] if k.metric == "ir_sens"]
    assert len(m) == 1 and m[0].value == -65_000_000 and m[0].change_pct == 1.0


def test_v3_fx_risk_narrative_from_xbrl_description_table():   # 삼성SDS '32. 재무위험관리'
    desc = "연결실체는 글로벌 영업활동을 수행함에 따라 외화 수입, 지출이 발생하고 있으며 이로 인해 환포지션이 발생합니다. 주된 통화는 USD, EUR 입니다."
    x = ("<TITLE>32. 재무위험관리 (연결)</TITLE>"
         + _t([["", "위험", "위험", "위험"], ["", "시장위험", "시장위험", "신용위험"], ["", "환율변동위험", "이자율위험", "신용위험"],
               ["위험에 대한 노출정도에 대한 기술", desc, "변동금리 차입금은 없습니다. 이자율 위험은 제한적입니다 추가 설명입니다", "신용위험 설명"]]))
    res = ep.extract({"a.xml": x})
    assert res["risk_text"] == desc and "risk_text" in res["found"]


def test_v3_fx_risk_body_fallback_and_title_only_skipped():
    body = ("<P>손상검사에서 환 위험은 사용가치에 영향을 미칠 수 있습니다. 이 문장은 위험관리 절 밖이라 잡지 않아야 합니다. "
            "기후 관련 가정과 배출 비용 증가, 수요 변화 등을 함께 고려하였습니다.</P>"
            "<P>5. 위험관리 및 파생거래</P><P>나. 환위험 관리</P><P>환위험은 외화로 표시된 거래에서 발생합니다. 회사는 국내 영업 비중이 높아 외화 거래가 적고, "
            "외화 예금과 외화 매출채권 규모가 크지 않아 환율 변동의 영향은 제한적입니다. 필요시 통화선도 계약을 검토합니다.</P>")
    res = ep.extract({"a.xml": body})
    assert res["risk_text"].startswith("환위험 관리") and "제한적" in res["risk_text"]
    assert ep.extract({"a.xml": "<P>환위험 관리</P>"})["risk_text"] == ""


def test_v3_relaxed_rules_do_not_change_tables_v2_already_found():
    """v3 넓힌 규칙은 v2 규칙으로 한 표도 못 찾을 때만: 사업의 내용 표(v2)와 연결 주석 XBRL 표(v3 전용)가 함께 있으면 v2 표."""
    v2 = _t([["구 분", "10% 상승시", "10% 하락시"], ["USD", "100", "(100)"]],
            before="환율이 10% 변동시 법인세비용차감전순이익에 미치는 영향", unit_table="(단위: 백만원)")
    unit = "<TABLE><TR><TD>외화환율변동의 민감도분석에 대한 공시</TD></TR><TR><TD>당기</TD><TD>(단위 : 원)</TD></TR></TABLE>"
    xbrl = "<TITLE>34. 위험관리 (연결)</TITLE>" + unit + _t(
        [["", "환위험"], ["외화환율변동의 합리적인 범위 내에서의 발생가능한 0.1 증가율", "0.1"],
         ["법인세비용차감전순손익 증가(감소)", "999,000,000"]])
    m = [k for k in ep.extract({"a.xml": "<TITLE>위험관리 및 파생거래</TITLE>" + v2 + xbrl})["metrics"] if k.metric == "fx_sens"]
    assert [(k.currency, k.value) for k in m] == [("USD", 100e6)]


def test_v4_hedge_accounting_needs_currency_in_same_sentence():   # 삼성전자·한전KPS 오탐 (10-29 표본 대조)
    inv = _t([["구분", "당기"], ["통화선도 평가손익", "10"]],
             before="라. 파생상품 연결회사는 재고자산의 가격 변동 위험 등을 회피하기 위한 현금흐름 위험회피회계를 적용하고 있습니다. "
                    "외화 환율 변동 위험은 선물환으로 관리합니다.")
    assert "fx_hedge_acct" not in ep.extract({"a.xml": inv})["found"]
    policy = "<TITLE>1. 개요 (연결)</TITLE>" + _t([["구분", "내용"], ["외화환산", "환율"]],
                                                  before="연결실체는 통화선도에 대해 위험회피회계를 적용할 수 있습니다.")
    assert "fx_hedge_acct" not in ep.extract({"a.xml": policy})["found"]
    ok = "<TITLE>36. 위험관리 (연결)</TITLE>" + _t([["구분", "당기"], ["통화스왑", "1"]],
                                                   before="연결실체는 외화차입금의 현금흐름 변동위험을 회피하기 위하여 통화스왑을 이용한 현금흐름위험회피회계를 적용하고 있습니다.")
    res = ep.extract({"a.xml": ok})
    m = next(x for x in res["metrics"] if x.metric == "fx_hedge_acct")
    assert "통화스왑" in m.evidence and m.evidence.endswith("있습니다")
