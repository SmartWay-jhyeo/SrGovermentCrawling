# 데이터 사전 (P0)

DB: SQLite, 기본 경로 `.local/real/bidloc.sqlite3`(Git 제외). 마이그레이션: `migrations/0001_p0_core.sql`.
연결 설정: 외래키 ON, WAL, busy_timeout 30초. 적용 이력은 `schema_migrations`(버전·SHA-256·적용시각)에 남고, 적용된 파일이 바뀌면 오류로 멈춘다.

P0에는 실행 기록·호출예산·원본 메타데이터·검증 관측 테이블만 있다. 공고·면허·지역·금액 정규화 테이블(`notice_revision`, `license_requirement`, `region_requirement`, `notice_amount`, `opening_round` 등)은 필드 의미를 실응답으로 확인한 뒤 P1 마이그레이션에서 만든다.

공통 규칙:
- 공고번호·차수·입찰분류번호·재입찰번호·사업자번호는 TEXT다. 0 채움이나 정수 변환을 하지 않는다.
- NULL은 "없음·미조회·빈값"이고 0이 아니다. 상태 컬럼에 이유를 함께 남긴다.
- 시각은 UTC ISO-8601 문자열(`*_utc`)이다. API 원문 시각은 `*_raw`로 원문 그대로 둔다.

## api_run — 실행 기록

| 컬럼 | 설명 |
|---|---|
| run_id | 실행 ID (`verify-YYYYMMDDTHHMMSS-xxxxxxxx`) |
| command | 실행 명령 (`verify-api`, `pytest-live`) |
| data_mode | `real` / `demo` |
| live | 실제 호출 여부(0/1). dry-run·BLOCKED는 0 |
| status | RUNNING / COMPLETED / PARTIAL / BLOCKED / SKIPPED / FAILED / ABORTED |
| max_calls_run | 이번 실행 호출 상한 |
| calls_attempted | 실제 HTTP 시도 수(재시도 포함) |
| stop_reason | 중단·차단 사유(마스킹) |
| resumed_from_run_id | `--resume`으로 이어받은 이전 실행 |
| code_version, catalog_sha256 | 코드 버전, 사용한 카탈로그 해시 |
| notes_json | 부가 정보(마스킹된 JSON) |

## request_budget_daily — 일일 내부 호출예산

| 컬럼 | 설명 |
|---|---|
| budget_day_kst | KST 날짜 `YYYY-MM-DD` |
| calls_reserved | 전송 전에 예약한 호출 수(실패·재시도 포함) |
| daily_limit_last_seen | 예약 당시 일일 한도 설정값 |

제공기관 쿼터가 아니라 내부 안전값이다. `BEGIN IMMEDIATE`로 예약해 동시 프로세스에서도 한도를 넘지 않는다.

## source_response — 원본 응답 메타데이터 (시도 1회당 1행)

| 컬럼 | 설명 |
|---|---|
| service_id, operation | 카탈로그 서비스 ID, 오퍼레이션명 |
| request_params_redacted_json | 요청 파라미터(인증키 제외·마스킹) |
| request_url_redacted | 인증키가 `***REDACTED***`로 바뀐 URL |
| attempt_no | 같은 요청의 시도 번호 |
| requested_at_utc, elapsed_ms | 요청 시각, 소요 시간 |
| http_status, content_type | HTTP 상태, 응답 헤더 |
| response_format | json / xml / text / empty / none(전송 실패) |
| envelope_shape | 관측한 응답 구조 (예: `json:response:items.item=list`) |
| result_code, result_msg | 업무 결과코드·메시지 원문(마스킹) |
| outcome, classification_basis | 분류 결과와 근거 (`src/bidloc/clients/errors.py`) |
| page_no, num_of_rows, total_count, item_count | 응답 페이지 정보. totalCount가 없거나 정수가 아니면 NULL |
| body_sha256, raw_path, body_bytes | 저장 본문(마스킹 후) 해시, `RAW_RESPONSE_DIR` 기준 상대경로, 크기 |
| parser_version | 파서 버전 (`p0-envelope-1`) |
| redaction_applied | 본문에서 비밀값을 치환했는지 |
| error_detail | 오류 설명(마스킹) |

원본 파일 경로: `RAW_RESPONSE_DIR/<KST 날짜>/<service_id>/<operation>/<HHMMSS>_a<시도>_<sha16>.<json|xml|txt>`.

## verify_step — verify-api 단계 상태

| 컬럼 | 설명 |
|---|---|
| step_key | 예: `industry:lookup`, `discovery:2025`, `sample:<공고번호>:license`, `sample:<공고번호>:roster:<차수>-<분류>-<재입찰>` |
| status | DONE / DONE_EMPTY / FAILED / BLOCKED / SKIPPED / NOT_RUN_BUDGET / NOT_RUN_QUOTA / NOT_RUN_SERVICE_BLOCKED |
| calls_used | 이 단계의 HTTP 시도 수 |
| summary_json | 관측 요약(필드 존재 여부, 키, 행 수, 비교 결과 등) |

`NOT_RUN_*`는 이어받기 지점이다. `--resume <run_id>`는 DONE·DONE_EMPTY 단계를 다시 호출하지 않고 재사용한다.

## verify_notice_revision — 공고 차수 관측

UNIQUE(run_id, bid_ntce_no, bid_ntce_ord). 공고번호 단독 키를 쓰지 않는다.

| 컬럼 | API 필드 |
|---|---|
| bid_ntce_no / bid_ntce_ord | bidNtceNo / bidNtceOrd |
| ntce_kind_nm, re_ntce_yn | ntceKindNm, reNtceYn |
| bef_bid_ntce_no | befBidBbancNo (참고자료 1.2에만 있는 필드, 없으면 NULL) |
| bid_ntce_dt_raw, rgst_dt_raw | bidNtceDt, rgstDt 원문 |

## verify_opening_unit — 개찰단위 관측

UNIQUE(run_id, bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no).

| 컬럼 | 설명 |
|---|---|
| bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no | 개찰결과 목록의 네 키 |
| progrs_div_cd_nm, openg_dt_raw | 진행구분(유찰/개찰완료/재입찰), 개찰일시 원문 |
| official_prtcpt_cnum | 공식 참가업체수(prtcptCnum). 빈값·없음이면 NULL |
| official_prtcpt_cnum_status | OBSERVED / BLANK_IN_RESPONSE / FIELD_ABSENT / INVALID / NOT_QUERIED / QUERY_FAILED |
| roster_row_count | 개찰완료 명부 행 수. 전체 페이지 수집·검사를 통과한 경우만 값이 있다 |
| roster_unique_bizno | 명부 투찰업체 사업자번호 고유 수 |
| roster_status | COMPLETE / INCOMPLETE / NO_DATA / NOT_QUERIED / QUERY_FAILED |
| count_comparison | MATCH / MISMATCH / NOT_COMPARABLE (NULL은 비교하지 않음) |
| linked_notice_revision_id, link_status | 같은 (공고번호, 차수) 공고 차수와의 연결. LINKED / SAME_NO_OTHER_ORD / FORMAT_MISMATCH / NOT_LINKED |
