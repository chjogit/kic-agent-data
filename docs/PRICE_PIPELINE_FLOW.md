# 국내 시세 경로 흐름표 (FSC JSON → core.price_daily)

데이터가 어디서 들어와 어디를 거쳐 DB 에 들어가는지, 각 단계에서 어디가 깨질 수 있는지 정리합니다.
검증 하네스: `tests/test_fsc_fixtures.py` + `tests/fixtures/fsc/*.json`. 적재 전 검사: `kic_data/guards.py`.

## 1. 단계

| 단계 | 코드 | 입력 → 출력 |
| --- | --- | --- |
| ① 호출 | `fsc.fetch_all_prices` (전 종목, 기본) / `fsc.fetch_prices` (종목별, 장기 백필) | 공공데이터포털 `getStockPriceInfo_V2` JSON, 쪽당 5,000행 |
| ② 파싱 | `fsc.parse_prices` | `response.body.items.item[]` → 행 dict 목록 |
| ③ 묶기 | `cli._kr_prices_bulk` / `cli.cmd_kr_prices` | 종목코드별 `{trade_date: 행}` dict, 오늘 장중 행 제외(`drop_incomplete_today`) |
| ④ 적재 전 검사 | `guards.check_price_rows` (`db.upsert_prices` 첫 줄) | 실패 시 그 종목은 쓰지 않음 |
| ⑤ 적재 | `db.upsert_prices` | `INSERT … ON CONFLICT (company_id, trade_date) DO UPDATE` |

## 2. 입력 스키마 (JSON 필드) → DB 컬럼

| JSON 필드 | 변환 | 행 키 | `core.price_daily` 컬럼 | 필수 |
| --- | --- | --- | --- | --- |
| `basDt` | `parse_ymd` ('20261117' → date) | `trade_date` | `trade_date date` (PK) | 예 |
| `clpr` | `to_decimal` (쉼표 제거, '-'·''·nan → None) | `close` | `close numeric` | 예 |
| `mkp` / `hipr` / `lopr` | `to_decimal` | `open` / `high` / `low` | `numeric` | |
| `trqu` | `to_int` | `volume` | `bigint` | |
| `trPrc` | `to_decimal` | `trade_value` | `numeric` | |
| `mrktTotAmt` | `to_decimal` | `market_cap` | `numeric` | |
| `srtnCd` | 공백·앞 'A' 제거 | `ticker` | (저장 안 함, ③에서 `company_id` 로 바뀜) | 전 종목 조회에서만 |
| `lstgStCnt` | `to_int` | `shares` | (저장 안 함, `core.company.shares` 갱신) | |
| — | 상수 | — | `source = 'fsc'`, `collected_at = now()` | |

## 3. 깨질 수 있는 지점

| # | 지점 | 증상 | 현재 처리 |
| --- | --- | --- | --- |
| 1 | `resultCode` ≠ '00' (키 오류·한도 초과) | 오류 응답 | ② `RuntimeError` → 명령 실패 |
| 2 | 결과 0건 (`items` 가 빈 문자열) | 빈 목록 | ② `[]`, ③ 종목 누락 10% 초과면 실패 코드 |
| 3 | `item` 이 1건이면 dict, 여러 건이면 list | 타입 다름 | ② list 로 맞춤 |
| 4 | **필드명 변경** (2026-09 V2 전환 같은 일) | 모든 행에서 `clpr`·`basDt` 없음 | ② **모든 행이 비면 `RuntimeError` "응답 형식 변경 의심"** (이번 추가) |
| 5 | 일부 행 종가가 '' 또는 '-' | 예전엔 `close=NULL` 로 저장돼 기존 정상 값을 덮음 | ② **그 행만 버리고 `[WARN]`** (이번 추가) |
| 6 | 같은 종목·같은 날짜 중복 | upsert 가 앞 값을 덮음 | ③ dict 가 '나중 값 우선'으로 조용히 합침 → **알려진 위험** (아래 4절). ④ 는 ③ 을 거치지 않은 중복을 막음 |
| 7 | 종목 구분 누락으로 전 종목 행이 한 종목에 몰림 | 수천 행이 한 `company_id` 로 | ④ **행 수 상한 4,000 초과 시 실패** (이번 추가) |
| 8 | 숫자로 못 읽는 값 ('1,2,3', 'N/A') | `to_decimal` 이 None 으로 삼킴 | 종가면 ② 에서 행 제외, 나머지 컬럼은 NULL 로 저장 (경고 없음) |
| 9 | 오늘 장중 값 | 미완성 일봉 | ③ `drop_incomplete_today` (15:40 KST 전 오늘 행 제외) |
| 10 | 상장폐지·거래정지 | 기간 내 행 없음 | ③ `[WARN] 기간 내 시세 없음`, 누락 10% 초과면 실패 코드 |

## 4. 실패 → 제약 기록 (연습 4)

반복되면 가드(`guards.py`)나 fixture(`tests/fixtures/`)에 추가합니다.

| 날짜 | 무엇이 깨졌나 | 원인 패턴 | 반영 |
| --- | --- | --- | --- |
| 2026-09 | FSC V2 전환으로 엔드포인트·필드 확인 필요 | JSON 필드명 변경에 취약 | 이번: `renamed_field.json` + ② 형식 변경 실패 |
| 2026-11-04 | 외국인 보유율이 `numeric(7,4)` 를 넘어 수급 백필 중단 (#50) | DB 타입 범위 미검사 | `investor_flow` 에서 0~100 밖이면 비움 (PR #66). 가드 일반화는 후보 |
| 2026-11-09 | 수급 재확인 기준일을 시세 최신일로 잡아 경보 오판 (#74) | 기준 날짜를 다른 테이블 상태에 의존 | 평일 달력 기준으로 변경 |
| 열림 | ③ 같은 날짜 중복을 조용히 합침 | 중복 키 무시 | `test_dup_date_is_collapsed_before_load` 로 현재 동작 고정. 실제 중복이 관찰되면 경고로 바꿀 것 |

## 5. 실행

Windows cmd (저장소 폴더에서, DB·키 불필요):

```
python -m pytest -q tests/test_fsc_fixtures.py
```

실패하면 exit code 1 과 함께 어떤 fixture·어떤 단계(파싱·가드·묶기)에서 걸렸는지 시험 이름으로 나옵니다.
