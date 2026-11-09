# 감성 집계 내보내기 (`sentiment/latest/`)

종목 × 출처 × 기간 창의 감성 집계를 Object Storage(archive-dart 와 같은 저장소·`NEON_STORAGE_*`)의 `sentiment/latest/` 에 **덮어씁니다**.
명세: ai-office `specs/sentiment-export.md`. 코드: `kic_data/sentiment_export.py`, 명령 `python -m kic_data.cli export-sentiment`.

## 위치·파일
| 경로 | 내용 |
|---|---|
| `sentiment/latest/window=14d.json` · `.csv` | 최근 14일(기준일 포함, KST 달력일) |
| `…/window=7d.*`, `window=3d.*`, `window=1d.*` | 7·3·1일 |
| `sentiment/latest/_meta.json` | 기준 시각·생성 시각·`complete`·규칙 설명·출처별 마지막 수집 시각·파일별 크기/sha256. **가장 마지막에 올림** — 이 파일이 새 것이면 나머지 8개도 모두 올라간 것 |

`sentiment/latest/` **밖은 읽지도 쓰지도 지우지도 않습니다**(코드에서 경로 검사). 같은 이름은 덮어쓰고, 새 파일이 모두 올라간 뒤에만 이 접두사 아래의 옛 파일을 지웁니다. 하나라도 올리기에 실패하면 아무것도 지우지 않고 실패로 끝납니다.

## 열 (행 = 종목 × (출처별 + `합계`) × 창)
`ticker, name, market, source, window_days, as_of_date, n_docs, n_labeled, n_unlabeled, positive, neutral, negative, none, *_ratio, avg_score, n_scored, prev_avg_score, change, change_status, last_doc_date, last_collected_at`
- 문서 0건인 종목·출처도 행을 남깁니다(`n_docs=0`, 비율·평균은 null).
- 비율 = 라벨 건수 / `n_labeled`. 평균 점수 = `n_scored` 문서 평균(−1~+1). `none` = 감성 표현이 없는 문서.
- `change` = 이번 평균 − 직전 같은 길이 기간 평균. 직전 기간 점수가 없으면 null + `change_status` 에 사유(`비교 불가(...)`). 문서가 14일만 보관돼 14일 창은 직전 기간 자료가 없어 항상 비교 불가.
- `합계` 행은 메인 DB 출처와 kic-opinion(블로그·카페) 출처를 모두 합친 값.
- 원문·제목·주소·작성자는 없습니다. 방법: 키워드 사전(lexicon) 기준선, 사람 라벨 정확도 56.6% (`_meta.json` 에도 적음).

## 실행·알림
- `.github/workflows/export-sentiment.yml`: UTC `0 0,5,9,12 * * *`(= 09·14·18·21시 KST) + 수동. 저장소 변수 `SENTIMENT_EXPORT_UPLOAD=true` 가 아니면 예약 실행은 dry-run. 정시가 필요하면 cron-job.org 에서 같은 시각에 `workflow_dispatch` 호출.
- 실행 순서: 감성 점수 매기기(`cmd_sentiment`) → 집계 → 업로드. 점수 매기기나 집계가 실패하면 업로드하지 않고 실패(종료 코드 1).
- `/quality` 신호등 `sentiment-export`: 실패가 1번이어도 error, 마지막 성공이 13시간보다 오래돼도 error → 기존 `alerts` 가 GitHub 이슈를 엽니다.
- 점검: `python -m kic_data.cli export-sentiment --dry-run --out-dir <폴더>` 로 업로드 없이 파일 확인.
