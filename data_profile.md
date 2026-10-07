# 저장 조달 공사 데이터 구조 진단

진단일: 2026-10-07 KST. 목적: 시공노트 유지보수 사업 탐색과 향후 서비스 이용료 검토에 앞선 데이터 구조 확인. **시장규모·금액 합계·매출·가격안·대시보드는 산출하지 않았으며 X%는 미정이다.**

> **진단 기준은 2026-10-07 23:20:50 KST에 시작한 검사의 고정 DB 사본(시작 시 원본과 SHA-256 일치) 및 당시 원본 JSON 9,802파일이다.** 진단 중 원본 DB 해시가 바뀌고 JSON 26파일이 추가되어, 종료 무렵 파일 집합은 9,828개가 되었다. 아래 본문 건수·기간은 고정 사본 기준이며 새 26파일을 포함한 종료 시점 최신 집계가 아니다. 진단 코드는 추가수집/원본 쓰기를 실행하지 않았으며 동시 변경 주체는 미확인이다.

## 완료

핵심 결과: 공고 차수 **502,346행**, 개찰 단위 **420,788행**이다. 공고 고유번호는 **443,909개**이며 중복 제거된 사업 수가 아니다. 현재 대량 공사목록은 업종 제한 없이 수집되었지만, 계약 데이터는 미수집이다. 최종낙찰·투찰명부 정규화 테이블은 각각 0행·0행이다.

### 1. 범위와 보존 방법

`.local/real/bidloc.sqlite3`와 `.local/real/raw/` 전체를 검사했다. 집계는 표본 추정이 아닌 SQL `COUNT(*)`, 날짜 최솟값·최댓값, 전체 JSON 순회 결과다. 수집기·마이그레이션·자동수집 예약 작업은 실행하거나 변경하지 않았다. 원본을 읽기만 했으며 추가 API 호출은 0회다.

DB는 임시 사본의 SHA-256이 시작 시 원본과 일치함을 확인한 뒤 `mode=ro&immutable=1`, `PRAGMA query_only=ON`으로 열었다. 원본 WAL은 시작 시 0바이트였다. 종료 시 원본 DB 해시·파일 집합 보존 검사는 동시 변경을 감지하여 실패했다. 다만 검사한 기존 JSON 9,802파일의 경로·본문 해시를 묶은 manifest가 고정 사본 source_response의 경로·본문 해시 manifest와 완전히 일치한다. 따라서 아래 DB/원본 집계는 동일한 고정 범위로 대조할 수 있다. 원본 JSON은 프로그램 내부에서 읽었으며 원문 행·업체정보·요청 URL·인증키를 보고서에 복사하지 않았다.

| 항목 | 결과 |
| --- | --- |
| Python / SQLite | 3.11.9 / 3.45.1 |
| 진단 시작 / 종료 (UTC) | 2026-10-07T14:20:50.944241+00:00 / 2026-10-07T14:37:42.574923+00:00 |
| DB 원본·사본 SHA-256 일치 | True |
| DB 진단 전후 SHA-256 일치 | False |
| 원본 파일 크기·수정시각 동일 | False |
| 원본 JSON 파일 집합 동일 | False |
| DB SHA-256 | 53eead1522ab0a7b1be8352bca2f4d60c0c8a55e41fbdf95236ac88410906fb9 |
| 검사한 9,802 원본 manifest와 사본 메타데이터 일치 | True |
| 기존 원본 경로 중 진단 시작 후 수정시각 변경 / 누락 | 0 / 0 |
| 종료 무렵 관측한 원본 DB 바이트 | 4706439168 |
| 종료 시 원본 DB SHA-256 | d21c430fe270e58bced06ca5e88941738b54af9a5af7a2b77e1dd0a396d5822f |

원본 DB가 계속 동일하다는 보존 검사는 FAIL이며 이를 PASS로 바꾸지 않는다. 이번 진단 코드는 원본을 읽기만 했지만, 전체 실행 동안 다른 작업까지 포함한 원본 불변 상태를 보장할 수는 없었다. 추가된 응답 파일 수는 공고 5·면허 8·지역 8·개찰 5개이며 수집 주체/목적은 미확인이다. 기존 원본의 수정시각 변경·경로 누락은 0건이다.

### 2. 파일 형식과 용량

실데이터 DB는 4.38 GiB, JSON 원본은 4.16 GiB다. 아래 바이트 수가 정확한 파일 용량이다.

| 저장 위치 | 형식 | 파일 수 | 바이트 | 비고 |
| --- | --- | --- | --- | --- |
| .local/real/bidloc.sqlite3 | SQLite 3 | 1 | 4705980416 | 실데이터·수집상태·검증이력 혼재 |
| .local/real/raw/ | JSON 응답 | 9802 | 4471903386 | 응답 페이지·재조회 중복 포함 |
| .local/real/bidloc.sqlite3-wal | WAL | 1 | 0 | 진단 시작 시 |
| .local/real/bidloc.sqlite3-shm | SQLite 공유메모리 | 1 | 32768 | 원본 보존 |
| .local/p0_revalidation/synthetic/bidloc.sqlite3 | SQLite 3 | 1 | 286720 | 합성 검증용: 분석 분모 제외 |
| .local/official_specs/2026-09-16/ | DOCX / HTML / PDF | 9 | 2873254 | 저장된 공식 명세·포털 사본: 사업 행 아님 |

프로젝트 탐색에서 공사 실데이터 CSV·XLSX·Parquet는 발견되지 않았다. `.local/real/reports/`의 JSON·Markdown·HTML·로그는 파생 보고/실행기록으로 원본 행 수에 더하지 않았다. 공고 첨부 원문 PDF/HWP/HWPX 저장소는 발견되지 않았다(공식 API 안내 PDF와 구분).

### 3. 테이블별 정확한 행 수

| 테이블 | 행 수 | 기본키 (순서대로) |
| --- | --- | --- |
| api_daily_usage | 46 | budget_day_kst, service_id, operation |
| api_run | 44 | run_id |
| bf_allowed_region | 1097631 | bid_ntce_no, bid_ntce_ord, lmt_sno |
| bf_award | 0 | bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no |
| bf_job | 2 | job_id |
| bf_license_fetch | 1709 | bid_ntce_no, bid_ntce_ord |
| bf_license_limit | 1734415 | bid_ntce_no, bid_ntce_ord, lmt_grp_no, lmt_sno |
| bf_notice_revision | 502346 | bid_ntce_no, bid_ntce_ord |
| bf_notice_state | 444101 | bid_ntce_no |
| bf_opening_unit | 420788 | bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no |
| bf_partition | 314 | job_id, window_begin |
| bf_record_conflict | 30 | id |
| bf_roster_row | 0 | bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no, row_key |
| bf_task | 27959 | job_id, task_type, bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no |
| rc_hit | 2042 | study_id, query_kind, window_begin, bid_ntce_no, bid_ntce_ord |
| rc_study | 2 | study_id |
| rc_truth | 1679 | study_id, bid_ntce_no |
| rc_window_query | 252 | study_id, window_begin, query_kind |
| request_budget_daily | 1 | budget_day_kst |
| schema_migrations | 6 | version |
| source_response | 9825 | id |
| sw_job | 1 | job_id |
| sw_partition | 4452 | job_id, stage, window_begin |
| verify_notice_revision | 13 | id |
| verify_opening_unit | 11 | id |
| verify_step | 166 | id |

사용자 테이블 26개의 저장 레코드 합은 **4,247,835행**이다. 이는 DB 저장 레코드 총수일 뿐 사업 수가 아니다. 하나의 공고에 여러 차수·면허·지역·개찰회차가 있고 검증/수집 메타데이터도 포함되어 있으므로 총사업 수로 합산하지 않는다.

### 4. 실제 기간과 수집 범위

수집 작업 ID에 적힌 날짜, 설정의 “3년”, 과거 STATUS 숫자를 실제 기간으로 재사용하지 않았다. 아래는 현재 DB의 날짜 필드·작업 상태를 직접 집계한 결과다. 날짜 범위는 저장 문자열의 MIN/MAX이며 빈 문자열은 제외했다. 업무 날짜는 원문 KST 표현, `_utc`는 수집/적재 시각이다.

| 구분 | 필드 | 실제 최솟값 | 실제 최댓값 |
| --- | --- | --- | --- |
| 공고게시일 | bid_ntce_dt | 2023-09-16 17:23:20 | 2026-10-06 23:06:38 |
| 개찰일 | openg_dt | 2023-09-20 10:00:00 | 2026-10-06 18:00:00 |
| API 요청/실제 수집시각 (UTC) | requested_at_utc | 2026-09-16T13:59:19+00:00 | 2026-10-06T15:12:44+00:00 |

**원본과 정규화 DB의 기간도 다르다.** 원본 공사목록 두 오퍼레이션의 공고게시일 전수 범위는 2023-05-09 16:31:08~2026-10-06 23:06:38이다. 2023년 5월의 소량 과거 검증 원본이 별도로 남아 있기 때문이다. 따라서 “프로젝트의 모든 원본이 2023-09-16 이후”라고 볼 수 없으며, 2023년 5월부터 전수수집됐다는 뜻도 아니다. 원본 오퍼레이션별 정확한 날짜 범위는 부록 C에 있다.

**날짜 품질 이상:** 정규화 공고의 예정 개찰일 `bf_notice_revision.openg_dt` 최댓값이 2424-03-27 11:00:00으로 저장되어 있다. 이를 실제 수집기간이나 실제 개찰결과 기간으로 사용하지 않는다. 정규화 면허의 `rgst_dt`에는 날짜 대신 “공사” 같은 값이 있어 무필터 MAX가 “공사”가 된다. 원본 면허 `rgstDt`의 비어 있지 않은 수신값 1,738,392개 중 ISO 날짜 접두 형식으로 확인된 것은 1,737,317개이며 1,075개는 그 형식에 맞지 않는다(재수신 포함 수, 고유 공고 수 아님). 원본 수정 없이 이상값을 격리해 파싱/기간 필터에서 UNKNOWN으로 처리할 것을 제안한다.

공고게시일 연도별:

| year | n | first | last |
| --- | --- | --- | --- |
| 2023 | 58908 | 2023-09-16 17:23:20 | 2023-12-31 23:38:19 |
| 2024 | 169177 | 2024-01-01 08:59:51 | 2024-12-31 22:11:37 |
| 2025 | 162918 | 2025-01-05 16:59:12 | 2025-12-31 23:21:11 |
| 2026 | 111343 | 2026-01-01 09:52:05 | 2026-10-06 23:06:38 |

정규화 DB 공고일 최솟값은 2023-09-16이지만, 대량 스윕 시작일은 2023-09-20이다. 그 이전에 저장된 과거 검증/필터 수집분과 별도 2023년 5월 원본을 해당 기간 전수수집으로 해석하지 않는다. 2023년·2026년은 부분연도이며 2023~2025의 완결된 3개 연도 자료도 아니다.

스윕 작업의 현재 범위:

| job_id | range_begin_kst | range_end_kst | status |
| --- | --- | --- | --- |
| sweep-3y:2023-09-20:2026-09-19 | 2023-09-20 | 2026-10-06 | COMPLETED |

스윕 job_id와 config_json의 초기 범위에는 2026-09-19가 남아 있지만, 현재 range_end_kst와 실제 파티션은 2026-10-06까지 확장되어 있다. 작업 이름/초기 설정만으로 실제 기간을 판단하면 안 된다. source_response의 data_mode는 별도 전수 GROUP BY에서 real 9,825행으로 확인했다.

기존 업종필터 작업의 범위:

| job_id | range_begin_kst | range_end_kst | status |
| --- | --- | --- | --- |
| dojang-seupsik-3y:2023-09-16:2026-09-15 | 2023-09-16 | 2026-09-15 | ACTIVE |
| dojang-seupsik-3y-cd4992:2023-09-17:2026-09-16 | 2023-09-17 | 2026-09-16 | ACTIVE |

단계별 파티션 집계 (`received`는 수신 행 누계이며 고유 사업 수가 아님):

| job_id | stage | status | n | begin | end | received | count_mismatch | missing_total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sweep-3y:2023-09-20:2026-09-19 | LICENSE | DONE | 1113 | 202309200000 | 202610070000 | 1734247 | 0 | 0 |
| sweep-3y:2023-09-20:2026-09-19 | LIST | DONE | 1113 | 202309200000 | 202610070000 | 502261 | 0 | 0 |
| sweep-3y:2023-09-20:2026-09-19 | OPENING | DONE | 1113 | 202309200000 | 202610070000 | 420798 | 0 | 0 |
| sweep-3y:2023-09-20:2026-09-19 | REGION | DONE | 1113 | 202309200000 | 202610070000 | 1103104 | 0 | 0 |

LIST는 공고게시일(`inqryDiv=1`), LICENSE/REGION은 등록일(`inqryDiv=1`), OPENING은 개찰일(`inqryDiv=3`)이다. DONE 및 수신행/totalCount 일치는 저장된 요청 범위의 처리 상태이지 전국 실제 공사 전체의 완전성 증거가 아니다. 서로 다른 날짜 기준, 기간 밖 늦은 개찰, 과거 검증·필터 수집분의 혼재를 고려해야 한다.

**수집 대상 판정:** 현재 대량 스윕의 공고목록은 공사 오퍼레이션을 업종 필터 없이 조회한다. 따라서 4992 업종만 저장된 DB가 아니다. 이전 `indstrytyNm=도장`, `indstrytyCd=4992` 필터 수집과 검증표본도 남아 있다. `bf_notice_state.relevance`는 기존 도장·습식·방수·석공사업 목적의 관련성 상태이므로 시공노트 대상 유지보수 분모로 그대로 사용하면 안 된다. 면허·지역 API는 업무 공통이어서 공사 외 행이 섞일 수 있으므로 공사목록의 (공고번호, 차수)와 연결해 제한해야 한다.

저장된 요청조건 확인(보안상 인가된 비민감 필터만 투영):

| operation | 업종/제목/지역/조회구분 | 응답 수 |
| --- | --- | --- |
| getBidPblancListEvaluationIndstrytyMfrcInfo | {"inqryDiv": "2"} | 8 |
| getBidPblancListInfoChgHstryCnstwk | {"inqryDiv": "2"} | 2 |
| getBidPblancListInfoCnstwk | {"inqryDiv": "2"} | 9 |
| getBidPblancListInfoCnstwkBsisAmount | {"inqryDiv": "2"} | 9 |
| getBidPblancListInfoCnstwkPPSSrch | {"indstrytyCd": "4992", "inqryDiv": "1"} | 472 |
| getBidPblancListInfoCnstwkPPSSrch | {"indstrytyNm": "도장", "inqryDiv": "1"} | 94 |
| getBidPblancListInfoCnstwkPPSSrch | {"inqryDiv": "1"} | 1254 |
| getBidPblancListInfoLicenseLimit | {"inqryDiv": "1"} | 2476 |
| getBidPblancListInfoLicenseLimit | {"inqryDiv": "2"} | 1724 |
| getBidPblancListInfoPrtcptPsblRgn | {"inqryDiv": "1"} | 1863 |
| getBidPblancListInfoPrtcptPsblRgn | {"inqryDiv": "2"} | 383 |
| getIndstrytyBaseLawrgltInfoList | {} | 2 |
| getOpengResultListInfoCnstwk | {"inqryDiv": "3"} | 1134 |
| getOpengResultListInfoCnstwk | {"inqryDiv": "4"} | 380 |
| getOpengResultListInfoOpengCompt | {} | 7 |
| getScsbidListSttusCnstwk | {"inqryDiv": "4"} | 8 |

면허 업무구분과 코드 다양성:

| bsns_div_nm | n |
| --- | --- |
| 미확인 | 1062 |
| 공사 | 624989 |
| 물품 | 209516 |
| 용역 | 898848 |

| n |
| --- |
| 1465 |

기존 관련성 상태(시공노트 분류가 아님):

| relevance | n |
| --- | --- |
| CANCELLED | 28145 |
| NOT_RELEVANT | 346714 |
| RELEVANT | 34593 |
| UNKNOWN | 34649 |

응답 메타데이터의 오퍼레이션·처리상태별 건수와 실제 수집시각(UTC):

| service_id | operation | outcome | responses | received | first_utc | last_utc |
| --- | --- | --- | --- | --- | --- | --- |
| bid_award | getOpengResultListInfoCnstwk | SUCCESS | 1155 | 421939 | 2026-09-16T13:59:26+00:00 | 2026-10-06T15:12:21+00:00 |
| bid_award | getOpengResultListInfoCnstwk | SUCCESS_EMPTY | 359 | 미확인 | 2026-09-16T14:01:17+00:00 | 2026-10-06T13:36:53+00:00 |
| bid_award | getOpengResultListInfoOpengCompt | SUCCESS | 7 | 508 | 2026-09-16T13:59:53+00:00 | 2026-09-16T14:04:00+00:00 |
| bid_award | getScsbidListSttusCnstwk | SUCCESS | 6 | 6 | 2026-09-16T13:59:54+00:00 | 2026-09-16T14:04:01+00:00 |
| bid_award | getScsbidListSttusCnstwk | SUCCESS_EMPTY | 2 | 미확인 | 2026-09-16T14:01:18+00:00 | 2026-09-16T14:01:27+00:00 |
| bid_notice | getBidPblancListEvaluationIndstrytyMfrcInfo | SUCCESS | 2 | 3 | 2026-09-16T14:01:15+00:00 | 2026-09-16T14:03:33+00:00 |
| bid_notice | getBidPblancListEvaluationIndstrytyMfrcInfo | SUCCESS_EMPTY | 6 | 0 | 2026-09-16T13:59:25+00:00 | 2026-09-16T14:03:57+00:00 |
| bid_notice | getBidPblancListInfoChgHstryCnstwk | SUCCESS_EMPTY | 2 | 0 | 2026-09-16T14:01:16+00:00 | 2026-09-16T14:01:25+00:00 |
| bid_notice | getBidPblancListInfoCnstwk | SUCCESS | 9 | 13 | 2026-09-16T13:59:21+00:00 | 2026-09-16T14:03:53+00:00 |
| bid_notice | getBidPblancListInfoCnstwkBsisAmount | SUCCESS | 6 | 6 | 2026-09-16T13:59:24+00:00 | 2026-09-16T14:03:56+00:00 |
| bid_notice | getBidPblancListInfoCnstwkBsisAmount | SUCCESS_EMPTY | 3 | 0 | 2026-09-16T14:01:14+00:00 | 2026-09-16T14:01:32+00:00 |
| bid_notice | getBidPblancListInfoCnstwkPPSSrch | SUCCESS | 1606 | 537227 | 2026-09-16T14:01:01+00:00 | 2026-10-06T15:12:15+00:00 |
| bid_notice | getBidPblancListInfoCnstwkPPSSrch | SUCCESS_EMPTY | 197 | 0 | 2026-09-16T23:53:57+00:00 | 2026-09-27T09:17:45+00:00 |
| bid_notice | getBidPblancListInfoCnstwkPPSSrch | TIMEOUT | 17 | 미확인 | 2026-09-18T15:22:05+00:00 | 2026-09-19T15:25:34+00:00 |
| bid_notice | getBidPblancListInfoLicenseLimit | SUCCESS | 4061 | 1738392 | 2026-09-16T13:59:22+00:00 | 2026-10-06T15:12:33+00:00 |
| bid_notice | getBidPblancListInfoLicenseLimit | SUCCESS_EMPTY | 133 | 미확인 | 2026-09-16T23:55:54+00:00 | 2026-09-23T15:21:20+00:00 |
| bid_notice | getBidPblancListInfoLicenseLimit | TIMEOUT | 6 | 미확인 | 2026-09-21T15:21:45+00:00 | 2026-09-21T15:30:57+00:00 |
| bid_notice | getBidPblancListInfoPrtcptPsblRgn | SUCCESS | 2212 | 1105168 | 2026-09-16T13:59:23+00:00 | 2026-10-06T15:12:44+00:00 |
| bid_notice | getBidPblancListInfoPrtcptPsblRgn | SUCCESS_EMPTY | 34 | 미확인 | 2026-09-18T15:14:48+00:00 | 2026-10-06T13:37:46+00:00 |
| industry_law | getIndstrytyBaseLawrgltInfoList | SUCCESS | 2 | 111 | 2026-09-16T13:59:19+00:00 | 2026-09-16T13:59:20+00:00 |

과거 수집 TIMEOUT 기록은 성공한 무자료 응답과 구별한다. 원본 파일이 없는 요청 실패도 source_response에는 남을 수 있다. 원본 파일 수와 요청 기록 수를 동일시하지 않는다.

별도 메타데이터 대조: source_response 9,825행 중 raw_path 존재/고유 경로는 9,802개이고 body_bytes 합계는 4,471,903,386바이트로 실제 파일과 일치한다. 처리상태는 SUCCESS 9,066건, SUCCESS_EMPTY 736건, TIMEOUT 23건이다. 이 23건은 과거 수집의 실패 요청 기록이며 이번 진단에서 새 API를 호출한 것이 아니다.

### 5. 공고·개찰·최종낙찰·계약의 구분과 연결

| 구분 | 저장소 | 연결키 / 주의 |
| --- | --- | --- |
| 입찰공고 | bf_notice_revision | (bid_ntce_no, bid_ntce_ord). 차수별 공고이며 고유 사업과 다름 |
| 면허제한 | bf_license_limit | 공고키 + lmt_grp_no + lmt_sno. 다대일 연결 |
| 참가가능지역 | bf_allowed_region | 공고키 + lmt_sno. 복수지역 집합 보존 |
| 개찰결과 | bf_opening_unit | 공고키 + bid_clsfc_no + rbid_no. 분류/집행단위와 재입찰회차 구분 |
| 최종낙찰 | bf_award / 원본 getScsbidListSttusCnstwk | 개찰 4개 키. bf_award가 비어 있어도 과거 검증 원본은 별도 존재할 수 있음 |
| 투찰명부 | bf_roster_row / 원본 getOpengResultListInfoOpengCompt | 개찰 4개 키 + row_key. 투찰금액은 계약금액이 아님 |
| 기초금액 | 원본 getBidPblancListInfoCnstwkBsisAmount | 공고키 + bidClsfcNo. 대량 정규화 테이블 없음 |
| 계약·계약변경 | 미수집 | 계약번호·통합계약번호·계약차수·변경차수의 실제 연결키 미확인 |

공고 및 개찰 키 실측:

| revisions | notice_numbers | previous_link | quality_flags |
| --- | --- | --- | --- |
| 502346 | 443909 | 6355 | 2 |

| n | nonzero_class | nonzero_rebid |
| --- | --- | --- |
| 420788 | 0 | 8739 |

공고의 `cntrctCnclsMthdNm`(계약체결방법명)은 경쟁/수의 등 공고 조건이다. 이 필드가 있다는 이유로 계약 체결·계약금액이 수집됐다고 볼 수 없다. 계약 테이블·계약정보 오퍼레이션 원본은 이번 파일/스키마 전수 탐색에서 발견되지 않았다.

연결 건수(정확히 공고번호+차수로 대조; 서로 다른 분모를 혼합하지 않음):

| n | opening | license | region |
| --- | --- | --- | --- |
| 502346 | 409959 | 459718 | 441615 |

| n | matched |
| --- | --- |
| 420788 | 418658 |

| n | matched |
| --- | --- |
| 1734415 | 626051 |

| n | matched |
| --- | --- |
| 1097631 | 518740 |

위 연결은 현재 로컬 DB 사이의 키 일치다. 미연결을 “공사 없음” 또는 “입찰 0개”로 바꾸지 않는다. 개찰→공고의 `matched/n`, 공고→개찰의 `opening/n`은 서로 다른 지표이며, 아직 결과를 기대할 수 없는 공고를 제외한 성과 지표가 아니다. 분류번호·재입찰번호의 비영 값이 저장되어 있어도 실제 사업 단위 분할·이력 의미까지 검증된 것은 아니다.

각 미연결의 원인(기간 밖 공고/개찰, 조회기준 차이, 미수집, 키/원본 품질)은 이번에 개별 판정하지 않았다. 면허/지역 행이 없는 공고도 자동으로 무면허제한·전국입찰이라고 확정하지 않는다.

개찰 상태:

| progrs_div_cd_nm | n |
| --- | --- |
| 개찰완료 | 407273 |
| 유찰 | 4612 |
| 재시담 | 594 |
| 재입찰 | 8309 |

### 6. 금액 필드 의미와 사용 가능성

필드 의미는 저장된 공식 참고자료 `guide_bid.docx`, `guide_award.docx`, `docs/API_CONTRACT.md`와 `src/bidloc/backfill/store.py`의 매핑을 대조했다. 이는 로컬 명세 확인(DOCUMENTED) 및 저장 응답 관측이며 이번에 API를 새로 LIVE_VERIFIED 한 것은 아니다.

| 정규화 / 원문 필드 | 확인한 의미 | 범위·한계 |
| --- | --- | --- |
| bdgt_amt / bdgtAmt | 예산금액(원) | 기초금액·예정가격·계약금액으로 대체 금지. VAT 포함 여부·총액/단가 구분은 이 필드만으로 미확인 |
| presmpt_prce / presmptPrce | 추정가격(원) | 명세상 부가가치세·조달수수료 제외. 계약금액 아님 |
| vat / VAT | 부가가치세 | 추정가격과 별도 저장. 0·누락 구분 유지 |
| 원본 bssamt | 기초금액(원) | 소수 검증 응답만 존재. 전체 공고의 기초금액으로 확대 불가 |
| sucsfbid_amt / sucsfbidAmt | 최종낙찰금액(원) | 소수 검증 원본과 정규화 테이블 상태 구분. 계약·정산금액 아님 |
| bidprc_amt / bidprcAmt, opengCorpInfo 내 금액 | 투찰금액 | 순위 1위 금액을 최종낙찰·계약금액으로 확정하지 않음 |
| govsplyAmt | 관급금액(원) | 공고 item_json에 보존. 서비스 과금대상 포함 여부 미정 |
| mainCnsttyCnstwkPrearngAmt | 주공종공사예정금액 | 전체 공사의 예정가격과 동일 개념으로 단정하지 않음 |
| 계약금액 / 변경계약금액 / 최종정산액 | 미수집·미확인 | 현재 저장 자료로 실제 계약액 기반 요율 검토를 확정할 수 없음 |

금액 필드의 비NULL·0 저장 건수(업무상 금액 정확성을 보증하는 수가 아니며 합계금액은 계산하지 않음):

| n | budget_nonnull | estimated_nonnull | vat_nonnull | budget_zero | estimated_zero |
| --- | --- | --- | --- | --- | --- |
| 502346 | 500782 | 498275 | 494954 | 1952 | 1338 |

| 소량 검증 원본 | 파일 수 | 수신 행 수 | 고유키 후보 수 |
| --- | --- | --- | --- |
| getBidPblancListInfoCnstwkBsisAmount | 9 | 6 | 6 |
| getScsbidListSttusCnstwk | 8 | 6 | 6 |
| getOpengResultListInfoOpengCompt | 7 | 508 | 508 |
| getBidPblancListInfoChgHstryCnstwk | 2 | 0 | 0 |

소량 원본의 현재 정규화 DB 연결도 별도로 확인했다. 최종낙찰 고유키 6개 중 공고번호+차수 일치 5개, 개찰 4개 키 일치 5개다. 기초금액 고유키 6개 중 공고번호+차수 일치는 5개다. 각각 미연결 1개를 누락/0원으로 대체하지 않으며, 과거 검증 이력의 연결 성공 수를 현재 백필 테이블의 연결률로 재사용하지 않는다. 근거: supplement.json의 small_raw_links.

단가·총액·연간단가 한도·장기계속 총액/차수액·관급자재 포함·VAT 기준은 개별 사업별로 미확인이다. 원본 금액이 정수범위를 넘는 경우 정규화 NULL과 quality_flag가 있어 누락을 0으로 바꾸면 안 된다. 이용료 X%를 검토하기 전 과금 기준금액의 종류와 변경계약 반영 기준을 별도로 정해야 하며, 이번 진단에서는 X에 숫자를 부여하지 않았다.

### 7. 변경·재공고·취소·계약변경 중복 처리 제안

현재 관측 상태:

| ntce_kind_nm | n |
| --- | --- |
| 등록공고 | 440944 |
| 변경공고 | 30395 |
| 재공고 | 2860 |
| 취소공고 | 28147 |

| re_ntce_yn | n |
| --- | --- |
| N | 495991 |
| Y | 6355 |

| table_name | n |
| --- | --- |
| bf_notice_revision | 30 |

추가 복합키 집계: 49,582개 공고번호에 2개 이상 공고차수가 있다. `bf_notice_state` 중 공사목록에 공고번호 자체가 없는 상태행도 192개다. 따라서 상태 테이블을 공사 기회 수의 독립 분모로 사용하지 않는다. 근거 쿼리는 `.local/data_profile/supplement.json`과 별도 실행 기록에 보존했다.

현 구현은 공고 복합키로 UPSERT하며 동일키 본문 변경은 `bf_record_conflict`에 이전 JSON을 보존한다. 개찰도 충돌이력을 남기지만 면허·지역·최종낙찰·명부의 `INSERT OR REPLACE`는 같은 방식의 정규화 이력을 모두 남기지 않는다. 원본 source_response를 보존해야 한다. `bf_notice_state`는 공고번호 단독 상태이며 어느 차수에서든 취소가 관측되면 CANCELLED가 유지되는 코드가 있어, 이후 유효한 공고가 존재하는지 별도 판정해야 한다.

| 사건 | 제안하는 별도 분석층 정책 | 미확인/검토 조건 |
| --- | --- | --- |
| 동일 응답 재수신 | 오퍼레이션+검증된 행 복합키+본문 해시로 동일 관측을 제거하고 모든 source_response 근거 유지 | 응답 파일/행 누계는 사업 수가 아님 |
| 변경공고·같은 차수 본문 변경 | 공고번호+차수 이력 및 관측시각 보존. 기준일 당시 유효 버전을 선택해 금액은 1회만 반영 | 단순 MAX(차수)·마지막 수집시각만으로 법적 최신 상태를 확정하지 않음 |
| 재공고 | befBidBbancNo와 원문 근거로 전후 연결 테이블을 만들고 검증된 동일 기회만 묶음 | 이전 공고번호는 차수가 없음. 연결 공고 부재·애매한 차수·순환은 UNKNOWN; 제목 유사도만으로 병합 금지 |
| 취소 | 차수별 취소 사건과 시각 보존. 기준일 최종 유효 상태로 기회/금액 포함 여부 결정 | 과거 취소가 한 번 있었다는 이유로 모든 후속 공고를 영구 제외하지 않음 |
| 분할·재입찰 | bid_clsfc_no별 집행/분할단위와 rbid_no별 회차를 별도 보존. 동일 기회 반복금액 합산 방지 | 분류번호 의미·분할 금액 배분 확인 전 공고금액을 개찰행마다 복제 합산하지 않음 |
| 계약변경 | 계약 식별키+계약/변경차수+효력일을 확보한 뒤 원계약과 변경 이력 분리. 기준일 최종액 또는 검증된 증감액 중 하나로 계산 | 현재 미수집. 총액과 증감액, 장기계속 총계약·차수계약을 임의 합산하지 않음 |

위 정책은 제안이며 이번에 원본·DB에 중복 제거/갱신을 적용하지 않았다. `procurement_opportunity`, 계약·금액 이력 분석층은 현재 실제 테이블 목록에 없다.

### 8. 수도권 유지보수 분류에 필요한 정보

수도권 범위는 서울·인천·경기로 두되 현장지역 / 참가허용지역 / 발주·수요기관을 분리해야 한다. 저장된 `cnstrtsiteRgnNm`은 명세상 공사현장 지역이며, 기관명에 “서울”이 있거나 참가허용지역이 경기라는 이유만으로 수도권 현장으로 확정하면 안 된다. 행정구역 명칭·복수현장·전국 표기는 과거 유효시점 기준 검토가 필요하다.

| 공고 JSON 필드 | 값이 있는 행 수 | 공고 분모 |
| --- | --- | --- |
| bidNtceNm | 502346 | 502346 |
| cnstrtsiteRgnNm | 502341 | 502346 |
| mainCnsttyNm | 147193 | 502346 |
| ntceInsttNm | 502346 | 502346 |
| dminsttNm | 502346 | 502346 |
| chgNtceRsn | 57450 | 502346 |
| befBidBbancNo | 6355 | 502346 |
| bidNtceDtlUrl | 502346 | 502346 |
| ciblAplYn | 502346 | 502346 |
| mtltyAdvcPsblYn | 502346 | 502346 |
| untyNtceNo | 502346 | 502346 |

| 추가 점검 | 행 수 |
| --- | --- |
| attachment_url_any | 454900 |
| site_capital_text | 131598 |

공고명은 전 행에 있으나 주공종명은 147,193/502,346행에만 있어 공종명만으로 전체 분류를 완성할 수 없다. 현장지역은 502,341행, 첨부 링크는 454,900행에 존재한다. 링크 존재와 문서 내용 확인은 별개다.

`site_capital_text`는 현장지역 원문에 서울/경기/인천 문자열이 있는 행 수일 뿐, 행정구역 정규화나 유지보수 대상 선정을 완료한 수가 아니다. `attachment_url_any`는 첨부 링크가 있는 공고 수이며 첨부 문서의 로컬 수집·내용검증을 뜻하지 않는다.

보조 구조화 정보도 소량 있다. `getBidPblancListEvaluationIndstrytyMfrcInfo` 원본 8파일·3행 중 `cnstrtWkrarDivCd`가 건060002(전문) 2행, 건060003(유지보수) 1행이다. 코드 의미는 로컬 API 계약/공식 명세에 DOCUMENTED되어 있다. 그러나 이는 평가대상 주력분야 응답의 공사구분으로, 전체 공고에 수집된 교통시설·차선도색·공원녹지 유지보수 라벨이 아니다. 참가조건과 낙찰심사 구분을 유지하고 해당 1행을 전체 유지보수 규모로 확대하지 않는다.

| 대상 | 현재 가능한 판단 | 추가로 필요한 확인 |
| --- | --- | --- |
| 교통시설 유지보수 | 제목·공종·기관·현장지역으로 후보 탐색 가능 | 도로/교량/터널/신호/안전시설 등 시설 종류와 유지보수 업무를 함께 확인. 신설·확장과 구별 |
| 차선도색 | 제목·공종·면허/주력분야로 후보 탐색 가능 | 차선/노면표시와 도색·재도색·제거·보수의 작업범위 확인. 제목/도장면허만으로 완전 분류 불가 |
| 공원녹지 유지보수 | 제목·현장지역·공종·면허정보로 공사 후보 탐색 가능 | 공원/녹지/가로수 및 예초·전정·수목관리·시설보수의 실제 범위 확인. 용역 발주분은 현재 공사목록의 분모 밖 |
| 유지보수 공통 | 자유텍스트 근거를 이용한 후보 분류 가능 | 확정 라벨·대상시설 표준코드·실제 작업내용·성과지표·서비스 도입가능성은 현재 미확인 |

제안 분류 결과는 시설유형(복수 허용), 작업유형(유지보수/신설/혼합/미확인), 현장지역, 판단상태, 근거 필드·source_response_id, 규칙 버전을 분리한다. 기존 4992 RELEVANT 여부를 시공노트 대상 여부로 재사용하지 않는다. 이번에는 후보 검색식이나 라벨을 적용해 사업 수/금액을 산출하지 않았다.

## 실패

**원본 전체 불변 검증 FAIL:** 23:20:50~23:37:42 KST 진단 중 원본 DB 해시와 원본 파일 집합이 바뀌어 `profile_data.py`의 마지막 assertion이 exit 1로 끝났다. 집계 결과는 assertion 이전에 저장되어 있으며, 시작 시 원본과 일치한 고정 사본 및 그 메타데이터와 해시가 일치하는 기존 JSON 9,802개 기준으로 보고한다. 현재 원본까지 불변이었다거나 종료 시점 최신 전수 집계라고 표시하지 않는다.

그 밖의 중간 명령 실패·복구 기록:


- `git status --short`: Git 저장소가 아니어서 실패. Git 초기화·커밋을 하지 않았다.
- 최초 SQLite URI 연결 2회는 `uri=True` 누락으로 `unable to open database file` 발생. 올바른 읽기 전용 연결로 수정했다.
- 존재하지 않는 `config/sweep.yaml` 읽기는 실패. 스윕 설정은 실제 `config/backfill.yaml`의 `sweep` 절에서 확인했다.
- 성능 확인을 위한 Win32_OperatingSystem CIM 조회는 접근 거부. 운영체제 설정은 변경하지 않았으며 데이터 진단 결과에는 영향 없음.
- 원본 manifest 대조의 최초 경로 해석은 프로젝트 루트를 기준으로 삼아 불일치했다. raw_store.py에서 raw_path가 RAW_RESPONSE_DIR 기준임을 확인하여 재대조했고, 9,802파일 전체 manifest 일치를 확인했다.
- 원본 DB 직접 전체집계 2회와 초기 진단 실행은 느린 조회로 중단했다. 임시 사본 해시를 대조한 후 최종 진단을 다시 실행했다. 첫 DOCX 터미널 출력의 한글 인코딩도 UTF-8로 재실행해 확인했다.

## 미검증

- 진단 도중 추가된 원본 JSON 26파일과 변경된 종료 시점 DB의 최신 전체 행 수·기간은 본 고정 사본 집계에 포함하지 않았다. 동시 변경 주체 및 해당 수집의 인가 여부는 미확인. 다른 프로세스/예약 작업은 임의 중단하거나 변경하지 않음.
- 전국 실제 공사 전수 완전성, 공식 외부 모집단 대비 누락률, 날짜 경계 포함/제외, API의 과거 변경 이력 완전성: 미검증. 이번 실연동/추가수집은 SKIPPED(사용자 지시).
- 분할·재입찰·재공고의 실제 사업 동일성, 재공고 전후 차수 연결, 기준일별 유효공고 확정: 미검증. 관측 복합키만으로 자동 병합하지 않음.
- 계약·계약변경·정산 데이터: 미수집. 공고와 계약 사이 실제 식별키 및 계약금액 기준의 과금가능액: 미확인.
- 서비스 대상 유지보수 확정 라벨, 공고문/설계서·내역서 내용, 작업별 금액, 관리·성과분석 서비스 적용가능성: 미확인.
- API 공식 명세 최신성 재확인은 SKIPPED. 로컬 2026-09-16 저장 명세만 사용했으며 DOCUMENTED와 과거 표본 LIVE_VERIFIED를 구분함.
- 기존 애플리케이션 전체 단위테스트는 SKIPPED(제품코드 변경 없음). 이번 진단의 전수 건수·원본보존·집계일관성 검사는 별도로 실행 결과를 남김.

## 다음 조치

다음 분석에서는 먼저 동시 쓰기 작업과 분석 기준시점을 정리해 고정 데이터 범위를 확정해야 한다. 이후 사용할 기간·공고/계약 기준·수도권 현장 기준과 유지보수 포함/제외 기준을 정하고 별도 분석층에서 근거를 보존하며 라벨·공고 이력을 검토한다. 계약액 기반 이용료가 필요하면 계약 미수집 상태를 해소하는 별도 수집 범위를 검토해야 한다. 이번 진단에서는 추가수집을 수행하지 않았다. **X%는 계속 미정으로 둔다.**

## 재현 명령·근거

진단 코드와 집계만 `.local/data_profile/`에 저장했다(기존 `.gitignore` 적용). 최종 문서는 프로젝트 루트 `data_profile.md`다. 원본 레코드는 결과 파일에 내보내지 않았다.

```powershell
.\.venv\Scripts\python.exe -u .local/data_profile/profile_data.py
.\.venv\Scripts\python.exe .local/data_profile/render_profile.py
.\.venv\Scripts\python.exe .local/data_profile/validate_profile.py
```

진단 스크립트는 `.local/data_profile/snapshot_path.txt`가 가리키는 임시 읽기 전용 사본을 사용하며 실행 시 원본과 해시를 대조한다. 재현 시에는 현재 원본에서 사본을 새로 만든 뒤 실행해야 한다. SQL 전문, 현재 테이블 스키마, 전체 필드 충족도, raw 응답별 집계와 날짜는 `aggregates.json`에 있다.

이번 사본은 원본 동시 변경 때문에 진단 근거로 임시 경로에 보존했다(약 4.38 GiB, 실제 위치는 snapshot_path.txt). 현재 원본에서 새로 만든 사본의 결과는 이번 고정 사본과 달라질 수 있다. 기존 사본에 대한 SQL 재실행과, 새 기준시점으로 전체 진단을 다시 하는 작업을 구분해야 한다.

근거: `AGENTS.md`, `PROJECT_SPEC.md`, `docs/SOURCES.md`, `docs/STATUS.md`, `docs/VALIDATION_PLAN.md`, `docs/VALIDATION_REPORT.md`, `docs/API_CONTRACT.md`, 저장 공식 DOCX, `migrations/0001~0006`, `config/backfill.yaml`, `src/bidloc/backfill/sweep.py`, `store.py`, 현재 실데이터 DB 및 JSON. 과거 보고서 수치를 현재 건수의 정답으로 쓰지 않았다.

## 부록 A. 전체 테이블·컬럼

### `api_daily_usage` — 46행

`budget_day_kst TEXT PK1`, `service_id TEXT PK2`, `operation TEXT PK3`, `calls_reserved INTEGER`, `seeded_calls INTEGER`, `quota_exhausted_at_utc TEXT`, `updated_at_utc TEXT`

### `api_run` — 44행

`run_id TEXT PK1`, `command TEXT`, `data_mode TEXT`, `live INTEGER`, `status TEXT`, `started_at_utc TEXT`, `finished_at_utc TEXT`, `max_calls_run INTEGER`, `calls_attempted INTEGER`, `stop_reason TEXT`, `resumed_from_run_id TEXT`, `code_version TEXT`, `catalog_sha256 TEXT`, `notes_json TEXT`

### `bf_allowed_region` — 1,097,631행

`bid_ntce_no TEXT PK1`, `bid_ntce_ord TEXT PK2`, `lmt_sno TEXT PK3`, `prtcpt_psbl_rgn_nm TEXT`, `item_json TEXT`, `response_id INTEGER`, `updated_at_utc TEXT`

### `bf_award` — 0행

`bid_ntce_no TEXT PK1`, `bid_ntce_ord TEXT PK2`, `bid_clsfc_no TEXT PK3`, `rbid_no TEXT PK4`, `bidwinnr_bizno TEXT`, `sucsfbid_amt INTEGER`, `sucsfbid_rate TEXT`, `rl_openg_dt TEXT`, `fnl_sucsf_date TEXT`, `prtcpt_cnum INTEGER`, `item_json TEXT`, `response_id INTEGER`, `updated_at_utc TEXT`

### `bf_job` — 2행

`job_id TEXT PK1`, `job_name TEXT`, `range_begin_kst TEXT`, `range_end_kst TEXT`, `config_json TEXT`, `config_sha256 TEXT`, `status TEXT`, `created_at_utc TEXT`, `updated_at_utc TEXT`

### `bf_license_fetch` — 1,709행

`bid_ntce_no TEXT PK1`, `bid_ntce_ord TEXT PK2`, `rows_received INTEGER`, `response_id INTEGER`, `fetched_at_utc TEXT`

### `bf_license_limit` — 1,734,415행

`bid_ntce_no TEXT PK1`, `bid_ntce_ord TEXT PK2`, `lmt_grp_no TEXT PK3`, `lmt_sno TEXT PK4`, `lcns_lmt_nm TEXT`, `license_code TEXT`, `permsn_indstryty_list TEXT`, `indstryty_mfrc_fld_list TEXT`, `rgst_dt TEXT`, `bsns_div_nm TEXT`, `quality_flag TEXT`, `item_json TEXT`, `response_id INTEGER`, `updated_at_utc TEXT`

### `bf_notice_revision` — 502,346행

`bid_ntce_no TEXT PK1`, `bid_ntce_ord TEXT PK2`, `ntce_kind_nm TEXT`, `re_ntce_yn TEXT`, `bef_bid_ntce_no TEXT`, `bid_ntce_nm TEXT`, `bid_ntce_dt TEXT`, `rgst_dt TEXT`, `openg_dt TEXT`, `cntrct_cncls_mthd_nm TEXT`, `sucsfbid_mthd_nm TEXT`, `ntce_instt_cd TEXT`, `ntce_instt_nm TEXT`, `dminstt_cd TEXT`, `dminstt_nm TEXT`, `cnstrtsite_rgn_nm TEXT`, `main_cnstty_nm TEXT`, `indstryty_lmt_yn TEXT`, `bdgt_amt INTEGER`, `presmpt_prce INTEGER`, `vat INTEGER`, `item_json TEXT`, `item_sha256 TEXT`, `first_response_id INTEGER`, `last_response_id INTEGER`, `first_seen_utc TEXT`, `last_seen_utc TEXT`, `quality_flag TEXT`

### `bf_notice_state` — 444,101행

`bid_ntce_no TEXT PK1`, `relevance TEXT`, `relevance_basis TEXT`, `license_ord TEXT`, `region_ord TEXT`, `updated_at_utc TEXT`

### `bf_opening_unit` — 420,788행

`bid_ntce_no TEXT PK1`, `bid_ntce_ord TEXT PK2`, `bid_clsfc_no TEXT PK3`, `rbid_no TEXT PK4`, `progrs_div_cd_nm TEXT`, `openg_dt TEXT`, `prtcpt_cnum INTEGER`, `prtcpt_cnum_raw TEXT`, `openg_corp_info TEXT`, `item_json TEXT`, `response_id INTEGER`, `updated_at_utc TEXT`

### `bf_partition` — 314행

`job_id TEXT PK1`, `window_begin TEXT PK2`, `window_end TEXT`, `status TEXT`, `next_page INTEGER`, `total_count INTEGER`, `rows_received INTEGER`, `restarts INTEGER`, `attempts INTEGER`, `last_outcome TEXT`, `last_error TEXT`, `updated_at_utc TEXT`

### `bf_record_conflict` — 30행

`id INTEGER PK1`, `table_name TEXT`, `record_key TEXT`, `old_sha256 TEXT`, `new_sha256 TEXT`, `old_item_json TEXT`, `response_id INTEGER`, `detected_at_utc TEXT`

### `bf_roster_row` — 0행

`bid_ntce_no TEXT PK1`, `bid_ntce_ord TEXT PK2`, `bid_clsfc_no TEXT PK3`, `rbid_no TEXT PK4`, `row_key TEXT PK5`, `prcbdr_bizno TEXT`, `openg_rank TEXT`, `bidprc_amt INTEGER`, `rmrk TEXT`, `item_json TEXT`, `response_id INTEGER`, `updated_at_utc TEXT`

### `bf_task` — 27,959행

`job_id TEXT PK1`, `task_type TEXT PK2`, `bid_ntce_no TEXT PK3`, `bid_ntce_ord TEXT PK4`, `bid_clsfc_no TEXT PK5`, `rbid_no TEXT PK6`, `status TEXT`, `attempts INTEGER`, `not_before_kst TEXT`, `total_count INTEGER`, `rows_received INTEGER`, `last_outcome TEXT`, `last_error TEXT`, `reason TEXT`, `created_at_utc TEXT`, `updated_at_utc TEXT`

### `rc_hit` — 2,042행

`study_id TEXT PK1`, `query_kind TEXT PK2`, `window_begin TEXT PK3`, `bid_ntce_no TEXT PK4`, `bid_ntce_ord TEXT PK5`

### `rc_study` — 2행

`study_id TEXT PK1`, `seed INTEGER`, `range_begin_kst TEXT`, `range_end_kst TEXT`, `window_minutes INTEGER`, `config_json TEXT`, `created_at_utc TEXT`

### `rc_truth` — 1,679행

`study_id TEXT PK1`, `bid_ntce_no TEXT PK2`, `bid_ntce_ord TEXT`, `status TEXT`, `attempts INTEGER`, `license_rows INTEGER`, `has_target_lcns INTEGER`, `has_target_any INTEGER`, `flagged_rows INTEGER`, `reused INTEGER`, `last_error TEXT`, `updated_at_utc TEXT`

### `rc_window_query` — 252행

`study_id TEXT PK1`, `window_begin TEXT PK2`, `window_end TEXT`, `stratum TEXT`, `query_kind TEXT PK3`, `status TEXT`, `next_page INTEGER`, `total_count INTEGER`, `rows_received INTEGER`, `attempts INTEGER`, `last_error TEXT`, `updated_at_utc TEXT`

### `request_budget_daily` — 1행

`budget_day_kst TEXT PK1`, `calls_reserved INTEGER`, `daily_limit_last_seen INTEGER`, `updated_at_utc TEXT`

### `schema_migrations` — 6행

`version TEXT PK1`, `checksum_sha256 TEXT`, `applied_at_utc TEXT`

### `source_response` — 9,825행

`id INTEGER PK1`, `run_id TEXT`, `service_id TEXT`, `operation TEXT`, `request_params_redacted_json TEXT`, `request_url_redacted TEXT`, `attempt_no INTEGER`, `requested_at_utc TEXT`, `elapsed_ms INTEGER`, `http_status INTEGER`, `content_type TEXT`, `response_format TEXT`, `envelope_shape TEXT`, `result_code TEXT`, `result_msg TEXT`, `outcome TEXT`, `classification_basis TEXT`, `page_no INTEGER`, `num_of_rows INTEGER`, `total_count INTEGER`, `item_count INTEGER`, `body_sha256 TEXT`, `raw_path TEXT`, `body_bytes INTEGER`, `parser_version TEXT`, `redaction_applied INTEGER`, `data_mode TEXT`, `error_detail TEXT`

### `sw_job` — 1행

`job_id TEXT PK1`, `job_name TEXT`, `range_begin_kst TEXT`, `range_end_kst TEXT`, `config_json TEXT`, `config_sha256 TEXT`, `status TEXT`, `created_at_utc TEXT`, `updated_at_utc TEXT`

### `sw_partition` — 4,452행

`job_id TEXT PK1`, `stage TEXT PK2`, `window_begin TEXT PK3`, `window_end TEXT`, `status TEXT`, `next_page INTEGER`, `total_count INTEGER`, `rows_received INTEGER`, `restarts INTEGER`, `attempts INTEGER`, `last_outcome TEXT`, `last_error TEXT`, `updated_at_utc TEXT`

### `verify_notice_revision` — 13행

`id INTEGER PK1`, `run_id TEXT`, `bid_ntce_no TEXT`, `bid_ntce_ord TEXT`, `ntce_kind_nm TEXT`, `re_ntce_yn TEXT`, `bef_bid_ntce_no TEXT`, `bid_ntce_dt_raw TEXT`, `rgst_dt_raw TEXT`, `source_response_id INTEGER`

### `verify_opening_unit` — 11행

`id INTEGER PK1`, `run_id TEXT`, `bid_ntce_no TEXT`, `bid_ntce_ord TEXT`, `bid_clsfc_no TEXT`, `rbid_no TEXT`, `progrs_div_cd_nm TEXT`, `openg_dt_raw TEXT`, `official_prtcpt_cnum INTEGER`, `official_prtcpt_cnum_status TEXT`, `roster_row_count INTEGER`, `roster_unique_bizno INTEGER`, `roster_status TEXT`, `count_comparison TEXT`, `linked_notice_revision_id INTEGER`, `link_status TEXT`, `source_response_id INTEGER`

### `verify_step` — 166행

`id INTEGER PK1`, `run_id TEXT`, `step_key TEXT`, `sample_key TEXT`, `service_id TEXT`, `operation TEXT`, `status TEXT`, `calls_used INTEGER`, `summary_json TEXT`, `started_at_utc TEXT`, `finished_at_utc TEXT`

## 부록 B. 업무·수집 날짜 필드의 실제 범위

아래는 필터 전 저장 문자열의 MIN/MAX다. 날짜 유효성 검증 결과와 다르며 “공사” 등 비날짜 값과 2424년 이상값도 감추지 않고 표시했다. 실제 수집·분석 기간은 본문 4절의 날짜 기준과 함께 해석해야 한다.

| 테이블 | 필드 | 값 존재 행 수 | 최솟값 | 최댓값 |
| --- | --- | --- | --- | --- |
| api_daily_usage | quota_exhausted_at_utc | 0 | 미확인 | 미확인 |
| api_daily_usage | updated_at_utc | 46 | 2026-09-16T14:20:52+00:00 | 2026-10-06T15:12:44+00:00 |
| api_run | started_at_utc | 44 | 2026-09-16T13:36:19+00:00 | 2026-10-06T15:12:10+00:00 |
| api_run | finished_at_utc | 41 | 2026-09-16T13:36:19+00:00 | 2026-10-06T15:12:45+00:00 |
| bf_allowed_region | updated_at_utc | 1097631 | 2026-09-18T15:12:17+00:00 | 2026-10-06T15:12:44+00:00 |
| bf_award | rl_openg_dt | 0 | 미확인 | 미확인 |
| bf_award | fnl_sucsf_date | 0 | 미확인 | 미확인 |
| bf_award | updated_at_utc | 0 | 미확인 | 미확인 |
| bf_job | created_at_utc | 2 | 2026-09-16T14:19:52+00:00 | 2026-09-17T00:14:29+00:00 |
| bf_job | updated_at_utc | 2 | 2026-09-17 00:15:00 | 2026-09-20 11:22:21 |
| bf_license_fetch | fetched_at_utc | 1709 | 2026-09-16T23:55:48+00:00 | 2026-09-20 11:22:20 |
| bf_license_limit | rgst_dt | 1734415 | 2023-09-16 20:12:50 | 공사 |
| bf_license_limit | updated_at_utc | 1734415 | 2026-09-16T23:55:48+00:00 | 2026-10-06T15:12:36+00:00 |
| bf_notice_revision | bid_ntce_dt | 502346 | 2023-09-16 17:23:20 | 2026-10-06 23:06:38 |
| bf_notice_revision | rgst_dt | 502346 | 2023-09-16 17:23:20 | 2026-10-06 23:06:38 |
| bf_notice_revision | openg_dt | 502345 | 2023-03-31 11:00:00 | 2424-03-27 11:00:00 |
| bf_notice_revision | first_seen_utc | 502346 | 2026-09-16T14:20:13+00:00 | 2026-10-06T15:12:20+00:00 |
| bf_notice_revision | last_seen_utc | 502346 | 2026-09-16T14:20:13+00:00 | 2026-10-06T15:12:20+00:00 |
| bf_notice_state | updated_at_utc | 444101 | 2026-09-16T14:20:13+00:00 | 2026-10-06T15:12:46+00:00 |
| bf_opening_unit | openg_dt | 420788 | 2023-09-20 10:00:00 | 2026-10-06 18:00:00 |
| bf_opening_unit | updated_at_utc | 420788 | 2026-09-20T11:39:06+00:00 | 2026-10-06T15:12:21+00:00 |
| bf_partition | updated_at_utc | 314 | 2026-09-16T14:19:52+00:00 | 2026-09-20T11:22:20+00:00 |
| bf_record_conflict | detected_at_utc | 30 | 2026-09-18T15:14:00+00:00 | 2026-09-20T12:05:36+00:00 |
| bf_roster_row | updated_at_utc | 0 | 미확인 | 미확인 |
| bf_task | created_at_utc | 27959 | 2026-09-16T14:20:13+00:00 | 2026-09-20T11:22:20+00:00 |
| bf_task | updated_at_utc | 27959 | 2026-09-16 14:20:19 | 2026-09-20T11:22:21+00:00 |
| rc_study | created_at_utc | 2 | 2026-09-16T23:53:37+00:00 | 2026-09-17T15:09:12+00:00 |
| rc_truth | updated_at_utc | 1679 | 2026-09-16T23:55:48+00:00 | 2026-09-17T15:22:15+00:00 |
| rc_window_query | updated_at_utc | 252 | 2026-09-16T23:53:54+00:00 | 2026-09-17T15:11:51+00:00 |
| request_budget_daily | updated_at_utc | 1 | 2026-09-16T14:04:00+00:00 | 2026-09-16T14:04:00+00:00 |
| schema_migrations | applied_at_utc | 6 | 2026-09-16T13:36:16+00:00 | 2026-09-21T12:35:56+00:00 |
| source_response | requested_at_utc | 9825 | 2026-09-16T13:59:19+00:00 | 2026-10-06T15:12:44+00:00 |
| sw_job | created_at_utc | 1 | 2026-09-20T11:38:13+00:00 | 2026-09-20T11:38:13+00:00 |
| sw_job | updated_at_utc | 1 | 2026-10-06T15:12:44+00:00 | 2026-10-06T15:12:44+00:00 |
| sw_partition | updated_at_utc | 4452 | 2026-09-20T11:38:25+00:00 | 2026-10-06T15:12:44+00:00 |
| verify_notice_revision | bid_ntce_dt_raw | 13 | 2023-05-09 16:31:08 | 2026-08-25 19:44:04 |
| verify_notice_revision | rgst_dt_raw | 13 | 2023-05-09 16:31:08 | 2026-08-25 19:44:04 |
| verify_opening_unit | openg_dt_raw | 11 | 2023-05-23 11:00:00 | 2026-08-31 11:20:00 |
| verify_step | started_at_utc | 166 | 2026-09-16T13:59:20+00:00 | 2026-09-16T14:04:01+00:00 |
| verify_step | finished_at_utc | 166 | 2026-09-16T13:59:20+00:00 | 2026-09-16T14:04:01+00:00 |

## 부록 C. 원본 JSON 전수 집계

수신 items는 재수신·페이지 겹침을 포함한다. 고유키 후보 수는 각 응답에 존재하는 공고번호·차수·분류번호·재입찰번호·제한그룹·제한순번·투찰사업자번호를 조합한 기술적 수치이며, 사업 기회 중복제거의 정답이 아니다. 날짜는 전체 원본을 순회해 ISO 날짜 형식으로 시작하는 값을 집계했다.

| 오퍼레이션 | 파일 | 바이트 | 수신 items | 고유키 후보 | 고유 본문해시 | 파싱오류 |
| --- | --- | --- | --- | --- | --- | --- |
| getBidPblancListEvaluationIndstrytyMfrcInfo | 8 | 3012 | 3 | 3 | 3 | 0 |
| getBidPblancListInfoChgHstryCnstwk | 2 | 366 | 0 | 0 | 1 | 0 |
| getBidPblancListInfoCnstwk | 9 | 82137 | 13 | 13 | 9 | 0 |
| getBidPblancListInfoCnstwkBsisAmount | 9 | 8100 | 6 | 6 | 7 | 0 |
| getBidPblancListInfoCnstwkPPSSrch | 1803 | 3272910234 | 537227 | 502376 | 1579 | 0 |
| getBidPblancListInfoLicenseLimit | 4194 | 641741459 | 1738392 | 1734425 | 4055 | 0 |
| getBidPblancListInfoPrtcptPsblRgn | 2246 | 251530848 | 1105168 | 1097633 | 2208 | 0 |
| getIndstrytyBaseLawrgltInfoList | 2 | 72883 | 111 | 0 | 2 | 0 |
| getOpengResultListInfoCnstwk | 1514 | 305209355 | 421939 | 420789 | 1152 | 0 |
| getOpengResultListInfoOpengCompt | 7 | 338337 | 508 | 508 | 7 | 0 |
| getScsbidListSttusCnstwk | 8 | 6655 | 6 | 6 | 7 | 0 |

### `getBidPblancListEvaluationIndstrytyMfrcInfo`

실제 관측 필드: `VAT`, `bidNtceDt`, `bidNtceNo`, `bidNtceOrd`, `bidwinrSlctnBssCd`, `ciblAplYn`, `cnstrtWkaraMtltyAdvcPsblYn`, `cnstrtWkrarDivCd`, `cnsttyTyNm`, `evlRt`, `indstrytyMfrcFldNm`, `presmptAmt`, `presmptPrce`, `tmpNm`

| 날짜필드 | 값 수 | 최솟값 | 최댓값 |
| --- | --- | --- | --- |
| bidNtceDt | 3 | 2023-05-15 15:04:33 | 2026-04-25 11:37:34 |

### `getBidPblancListInfoChgHstryCnstwk`

실제 관측 필드: 

### `getBidPblancListInfoCnstwk`

실제 관측 필드: `VAT`, `aplBssCntnts`, `arsltApplDocRcptDt`, `arsltApplDocRcptMthdNm`, `arsltCmptYn`, `bdgtAmt`, `befBidBbancNo`, `bfSpecRgstNo`, `bidBeginDt`, `bidClseDt`, `bidGrntymnyPaymntYn`, `bidMethdNm`, `bidNtceDt`, `bidNtceDtlUrl`, `bidNtceNm`, `bidNtceNo`, `bidNtceOrd`, `bidNtceUrl`, `bidPrtcptFee`, `bidPrtcptFeePaymntYn`, `bidPrtcptLmtYn`, `bidQlfctRgstDt`, `bidWgrnteeRcptClseDt`, `brffcBidprcPermsnYn`, `chgDt`, `chgNtceRsn`, `ciblAplYn`, `cmmnSpldmdAgrmntClseDt`, `cmmnSpldmdAgrmntRcptdocMethd`, `cmmnSpldmdCnum`, `cmmnSpldmdCorpRgnLmtYn`, `cmmnSpldmdMethdCd`, `cmmnSpldmdMethdNm`, `cnstrtnAbltyEvlAmtList`, `cnstrtsiteRgnNm`, `cnsttyAccotShreRateList`, `cntrctCnclsMthdNm`, `contrctrcnstrtnGovsplyMtrlAmt`, `crdtrNm`, `dcmtgOprtnDt`, `dcmtgOprtnPlce`, `dminsttCd`, `dminsttNm`, `dminsttOfclEmailAdrs`, `drwtPrdprcNum`, `dsgntCmptYn`, `dtlsBidYn`, `exctvNm`, `govcnstrtnGovsplyMtrlAmt`, `govsplyAmt`, `incntvRgnNm1`, `incntvRgnNm2`, `incntvRgnNm3`, `incntvRgnNm4`, `indstrytyEvlRt`, `indstrytyLmtYn`, `indstrytyMfrcFldEvlYn`, `indutyVAT`, `intrbidYn`, `jntcontrctDutyRgnNm1`, `jntcontrctDutyRgnNm2`, `jntcontrctDutyRgnNm3`, `mainCnsttyCnstwkPrearngAmt`, `mainCnsttyNm`, `mainCnsttyPresmptPrce`, `mtltyAdvcPsblYn`, `mtltyAdvcPsblYnCnstwkNm`, `ntceDscrptYn`, `ntceInsttCd`, `ntceInsttNm`, `ntceInsttOfclEmailAdrs`, `ntceInsttOfclNm`, `ntceInsttOfclTelNo`, `ntceKindNm`, `ntceSpecDocUrl1`, `ntceSpecDocUrl10`, `ntceSpecDocUrl2`, `ntceSpecDocUrl3`, `ntceSpecDocUrl4`, `ntceSpecDocUrl5`, `ntceSpecDocUrl6`, `ntceSpecDocUrl7`, `ntceSpecDocUrl8`, `ntceSpecDocUrl9`, `ntceSpecFileNm1`, `ntceSpecFileNm10`, `ntceSpecFileNm2`, `ntceSpecFileNm3`, `ntceSpecFileNm4`, `ntceSpecFileNm5`, `ntceSpecFileNm6`, `ntceSpecFileNm7`, `ntceSpecFileNm8`, `ntceSpecFileNm9`, `opengDt`, `opengPlce`, `orderPlanUntyNo`, `pqApplDocRcptDt`, `pqApplDocRcptMthdNm`, `pqEvalYn`, `prearngPrceDcsnMthdNm`, `presmptPrce`, `rbidOpengDt`, `rbidPermsnYn`, `reNtceYn`, `refNo`, `rgnDutyJntcontrctRt`, `rgnDutyJntcontrctYn`, `rgnLmtBidLocplcJdgmBssCd`, `rgnLmtBidLocplcJdgmBssNm`, `rgstDt`, `rgstTyNm`, `rsrvtnPrceReMkngMthdNm`, `sptDscrptDocUrl1`, `sptDscrptDocUrl2`, `sptDscrptDocUrl3`, `sptDscrptDocUrl4`, `sptDscrptDocUrl5`, `stdNtceDocUrl`, `subsiCnsttyIndstrytyEvlRt1`, `subsiCnsttyIndstrytyEvlRt2`, `subsiCnsttyIndstrytyEvlRt3`, `subsiCnsttyIndstrytyEvlRt4`, `subsiCnsttyIndstrytyEvlRt5`, `subsiCnsttyIndstrytyEvlRt6`, `subsiCnsttyIndstrytyEvlRt7`, `subsiCnsttyIndstrytyEvlRt8`, `subsiCnsttyIndstrytyEvlRt9`, `subsiCnsttyNm1`, `subsiCnsttyNm2`, `subsiCnsttyNm3`, `subsiCnsttyNm4`, `subsiCnsttyNm5`, `subsiCnsttyNm6`, `subsiCnsttyNm7`, `subsiCnsttyNm8`, `subsiCnsttyNm9`, `sucsfbidLwltRate`, `sucsfbidMthdAppStd`, `sucsfbidMthdCd`, `sucsfbidMthdNm`, `totPrdprcNum`, `untyNtceNo`

| 날짜필드 | 값 수 | 최솟값 | 최댓값 |
| --- | --- | --- | --- |
| bidNtceDt | 13 | 2023-05-09 16:31:08 | 2026-08-25 19:44:04 |
| bidQlfctRgstDt | 13 | 2023-05-15 18:00 | 2026-08-30 18:00 |
| bidBeginDt | 13 | 2023-05-09 17:00:00 | 2026-08-26 10:00:00 |
| bidClseDt | 13 | 2023-05-16 10:00:00 | 2026-08-31 10:20:00 |
| opengDt | 13 | 2023-05-16 11:00:00 | 2026-08-31 11:20:00 |
| rgstDt | 13 | 2023-05-09 16:31:08 | 2026-08-25 19:44:04 |
| rbidOpengDt | 13 | 2023-05-16 11:00:00 | 2026-08-31 11:20:00 |
| cmmnSpldmdAgrmntClseDt | 3 | 2023-05-15 10:00:00 | 2023-05-15 10:00:00 |

### `getBidPblancListInfoCnstwkBsisAmount`

실제 관측 필드: `bidClsfcNo`, `bidNtceNm`, `bidNtceNo`, `bidNtceOrd`, `bidPrceCalclAYn`, `bssAmtPurcnstcst`, `bssamt`, `bssamtOpenDt`, `dfcltydgrCfcnt`, `envCnsrvcst`, `etcGnrlexpnsBssRate`, `evlBssAmt`, `gnrlMngcstBssRate`, `inptDt`, `lbrcstBssRate`, `mrfnHealthInsrprm`, `npnInsrprm`, `odsnLngtrmrcprInsrprm`, `prftBssRate`, `qltyMngcst`, `qltyMngcstAObjYn`, `rmrk1`, `rmrk2`, `rsrvtnPrceRngBgnRate`, `rsrvtnPrceRngEndRate`, `rtrfundNon`, `scontrctPayprcePayGrntyFee`, `sftyChckMngcst`, `sftyMngcst`, `smkpAmt`, `smkpAmtYn`, `usefulAmt`

| 날짜필드 | 값 수 | 최솟값 | 최댓값 |
| --- | --- | --- | --- |
| bssamtOpenDt | 6 | 2023-05-15 15:10:24 | 2026-08-26 14:49:06 |
| inptDt | 6 | 2023-05-15 15:10:24 | 2026-08-26 14:49:06 |

### `getBidPblancListInfoCnstwkPPSSrch`

실제 관측 필드: `VAT`, `aplBssCntnts`, `arsltApplDocRcptDt`, `arsltApplDocRcptMthdNm`, `arsltCmptYn`, `bdgtAmt`, `befBidBbancNo`, `bfSpecRgstNo`, `bidBeginDt`, `bidClseDt`, `bidGrntymnyPaymntYn`, `bidMethdNm`, `bidNtceDt`, `bidNtceDtlUrl`, `bidNtceNm`, `bidNtceNo`, `bidNtceOrd`, `bidNtceUrl`, `bidPrtcptFee`, `bidPrtcptFeePaymntYn`, `bidPrtcptLmtYn`, `bidQlfctRgstDt`, `bidWgrnteeRcptClseDt`, `brffcBidprcPermsnYn`, `chgDt`, `chgNtceRsn`, `ciblAplYn`, `cmmnSpldmdAgrmntClseDt`, `cmmnSpldmdAgrmntRcptdocMethd`, `cmmnSpldmdCnum`, `cmmnSpldmdCorpRgnLmtYn`, `cmmnSpldmdMethdCd`, `cmmnSpldmdMethdNm`, `cnstrtnAbltyEvlAmtList`, `cnstrtsiteRgnNm`, `cnsttyAccotShreRateList`, `cntrctCnclsMthdNm`, `contrctrcnstrtnGovsplyMtrlAmt`, `crdtrNm`, `dcmtgOprtnDt`, `dcmtgOprtnPlce`, `dminsttCd`, `dminsttNm`, `dminsttOfclEmailAdrs`, `drwtPrdprcNum`, `dsgntCmptYn`, `dtlsBidYn`, `exctvNm`, `govcnstrtnGovsplyMtrlAmt`, `govsplyAmt`, `incntvRgnNm1`, `incntvRgnNm2`, `incntvRgnNm3`, `incntvRgnNm4`, `indstrytyEvlRt`, `indstrytyLmtYn`, `indstrytyMfrcFldEvlYn`, `indutyVAT`, `intrbidYn`, `jntcontrctDutyRgnNm1`, `jntcontrctDutyRgnNm2`, `jntcontrctDutyRgnNm3`, `mainCnsttyCnstwkPrearngAmt`, `mainCnsttyNm`, `mainCnsttyPresmptPrce`, `mtltyAdvcPsblYn`, `mtltyAdvcPsblYnCnstwkNm`, `ntceDscrptYn`, `ntceInsttCd`, `ntceInsttNm`, `ntceInsttOfclEmailAdrs`, `ntceInsttOfclNm`, `ntceInsttOfclTelNo`, `ntceKindNm`, `ntceSpecDocUrl1`, `ntceSpecDocUrl10`, `ntceSpecDocUrl2`, `ntceSpecDocUrl3`, `ntceSpecDocUrl4`, `ntceSpecDocUrl5`, `ntceSpecDocUrl6`, `ntceSpecDocUrl7`, `ntceSpecDocUrl8`, `ntceSpecDocUrl9`, `ntceSpecFileNm1`, `ntceSpecFileNm10`, `ntceSpecFileNm2`, `ntceSpecFileNm3`, `ntceSpecFileNm4`, `ntceSpecFileNm5`, `ntceSpecFileNm6`, `ntceSpecFileNm7`, `ntceSpecFileNm8`, `ntceSpecFileNm9`, `opengDt`, `opengPlce`, `orderPlanUntyNo`, `pqApplDocRcptDt`, `pqApplDocRcptMthdNm`, `pqEvalYn`, `prearngPrceDcsnMthdNm`, `presmptPrce`, `rbidOpengDt`, `rbidPermsnYn`, `reNtceYn`, `refNo`, `rgnDutyJntcontrctRt`, `rgnDutyJntcontrctYn`, `rgnLmtBidLocplcJdgmBssCd`, `rgnLmtBidLocplcJdgmBssNm`, `rgstDt`, `rgstTyNm`, `rsrvtnPrceReMkngMthdNm`, `sptDscrptDocUrl1`, `sptDscrptDocUrl2`, `sptDscrptDocUrl3`, `sptDscrptDocUrl4`, `sptDscrptDocUrl5`, `stdNtceDocUrl`, `subsiCnsttyIndstrytyEvlRt1`, `subsiCnsttyIndstrytyEvlRt2`, `subsiCnsttyIndstrytyEvlRt3`, `subsiCnsttyIndstrytyEvlRt4`, `subsiCnsttyIndstrytyEvlRt5`, `subsiCnsttyIndstrytyEvlRt6`, `subsiCnsttyIndstrytyEvlRt7`, `subsiCnsttyIndstrytyEvlRt8`, `subsiCnsttyIndstrytyEvlRt9`, `subsiCnsttyNm1`, `subsiCnsttyNm2`, `subsiCnsttyNm3`, `subsiCnsttyNm4`, `subsiCnsttyNm5`, `subsiCnsttyNm6`, `subsiCnsttyNm7`, `subsiCnsttyNm8`, `subsiCnsttyNm9`, `sucsfbidLwltRate`, `sucsfbidMthdAppStd`, `sucsfbidMthdCd`, `sucsfbidMthdNm`, `totPrdprcNum`, `untyNtceNo`

| 날짜필드 | 값 수 | 최솟값 | 최댓값 |
| --- | --- | --- | --- |
| bidNtceDt | 537227 | 2023-05-15 09:08:39 | 2026-10-06 23:06:38 |
| bidQlfctRgstDt | 530810 | 2023-03-30 18:00 | 2424-03-26 18:00 |
| bidBeginDt | 530812 | 2023-03-24 10:00:00 | 2027-04-08 09:00:00 |
| bidClseDt | 530803 | 2023-03-31 10:00:00 | 2424-03-27 10:00:00 |
| opengDt | 537226 | 2023-03-31 11:00:00 | 2424-03-27 11:00:00 |
| rgstDt | 537227 | 2023-05-15 09:08:39 | 2026-10-06 23:06:38 |
| rbidOpengDt | 537227 | 2023-03-31 11:00:00 | 2424-03-27 11:00:00 |
| cmmnSpldmdAgrmntClseDt | 25561 | 2023-01-06 18:00:00 | 2026-11-17 18:00:00 |
| bidWgrnteeRcptClseDt | 28625 | 2019-12-19 18:00:00 | 2027-04-05 18:00:00 |
| dcmtgOprtnDt | 9684 | 2023-01-11 14:00:00 | 2026-11-09 13:00:00 |
| arsltApplDocRcptDt | 3959 | 2023-09-25 18:00:00 | 2026-10-28 18:00:00 |
| pqApplDocRcptDt | 1002 | 2023-01-06 18:00:00 | 2026-10-28 18:00:00 |
| chgDt | 3348 | 2023-09-20 10:59:50 | 2026-09-30 17:18:11 |

### `getBidPblancListInfoLicenseLimit`

실제 관측 필드: `bidNtceNo`, `bidNtceOrd`, `bsnsDivNm`, `indstrytyMfrcFldList`, `lcnsLmtNm`, `lmtGrpNo`, `lmtSno`, `permsnIndstrytyList`, `rgstDt`

| 날짜필드 | 값 수 | 최솟값 | 최댓값 |
| --- | --- | --- | --- |
| rgstDt | 1737317 | 2023-05-15 10:52:06 | 2026-10-06 23:52:59 |

### `getBidPblancListInfoPrtcptPsblRgn`

실제 관측 필드: `bidNtceNo`, `bidNtceOrd`, `bsnsDivNm`, `lmtSno`, `prtcptPsblRgnNm`, `rgstDt`

| 날짜필드 | 값 수 | 최솟값 | 최댓값 |
| --- | --- | --- | --- |
| rgstDt | 1105168 | 2023-05-15 10:52:06 | 2026-10-06 23:48:51 |

### `getIndstrytyBaseLawrgltInfoList`

실제 관측 필드: `baseLawordArtclClauseNm`, `baseLawordNm`, `baseLawordUrl`, `inclsnLcns`, `indstrytyCd`, `indstrytyChgDt`, `indstrytyClsfcCd`, `indstrytyClsfcNm`, `indstrytyNm`, `indstrytyRgstDt`, `indstrytyUseYn`, `rltnRgltCntnts`

| 날짜필드 | 값 수 | 최솟값 | 최댓값 |
| --- | --- | --- | --- |
| indstrytyRgstDt | 111 | 2002-08-14 13:32:47 | 2024-11-06 12:00:00 |
| indstrytyChgDt | 111 | 2014-10-29 16:55:00 | 2025-03-04 13:55:59 |

### `getOpengResultListInfoCnstwk`

실제 관측 필드: `bidClsfcNo`, `bidNtceNm`, `bidNtceNo`, `bidNtceOrd`, `dminsttCd`, `dminsttNm`, `inptDt`, `ntceInsttCd`, `ntceInsttNm`, `opengCorpInfo`, `opengDt`, `opengRsltNtcCntnts`, `progrsDivCdNm`, `prtcptCnum`, `rbidNo`, `rsrvtnPrceFileExistnceYn`

| 날짜필드 | 값 수 | 최솟값 | 최댓값 |
| --- | --- | --- | --- |
| opengDt | 421939 | 2023-05-23 11:00:00 | 2026-10-06 18:00:00 |
| inptDt | 421939 | 2023-05-23 11:07:13 | 2026-10-06 21:22:26 |

### `getOpengResultListInfoOpengCompt`

실제 관측 필드: `bidClsfcNo`, `bidNtceNo`, `bidNtceOrd`, `bidPrceEvlVal`, `bidprcAmt`, `bidprcDt`, `bidprcrt`, `cnsttyAccotBidAmtUrl`, `drwtNo1`, `drwtNo2`, `opengRank`, `opengRsltDivNm`, `prcbdrBizno`, `prcbdrCeoNm`, `prcbdrNm`, `rbidNo`, `rmrk`, `techEvlNaturVal`, `techEvlVal`, `totalEvlAmtVal`

| 날짜필드 | 값 수 | 최솟값 | 최댓값 |
| --- | --- | --- | --- |
| bidprcDt | 507 | 2024-05-14 15:03:47 | 2026-08-31 09:47:48 |

### `getScsbidListSttusCnstwk`

실제 관측 필드: `bidClsfcNo`, `bidNtceNm`, `bidNtceNo`, `bidNtceOrd`, `bidwinnrAdrs`, `bidwinnrBizno`, `bidwinnrCeoNm`, `bidwinnrNm`, `bidwinnrTelNo`, `dminsttCd`, `dminsttNm`, `fnlSucsfCorpOfcl`, `fnlSucsfDate`, `ntceDivCd`, `prtcptCnum`, `rbidNo`, `rgstDt`, `rlOpengDt`, `sucsfbidAmt`, `sucsfbidRate`

| 날짜필드 | 값 수 | 최솟값 | 최댓값 |
| --- | --- | --- | --- |
| rlOpengDt | 6 | 2023-05-23 11:00:00 | 2026-08-31 11:20:00 |
| rgstDt | 6 | 2023-05-31 17:21:06 | 2026-09-08 16:50:06 |
| fnlSucsfDate | 6 | 2023-05-31 | 2026-09-08 |

## 부록 D. 공고월별 정확한 행 수

| month | n |
| --- | --- |
| 2023-09 | 3738 |
| 2023-10 | 15702 |
| 2023-11 | 20477 |
| 2023-12 | 18991 |
| 2024-01 | 8941 |
| 2024-02 | 12218 |
| 2024-03 | 18587 |
| 2024-04 | 17139 |
| 2024-05 | 15515 |
| 2024-06 | 15647 |
| 2024-07 | 14526 |
| 2024-08 | 10609 |
| 2024-09 | 9894 |
| 2024-10 | 14197 |
| 2024-11 | 16696 |
| 2024-12 | 15208 |
| 2025-01 | 6601 |
| 2025-02 | 12395 |
| 2025-03 | 19064 |
| 2025-04 | 16185 |
| 2025-05 | 15222 |
| 2025-06 | 15794 |
| 2025-07 | 13284 |
| 2025-08 | 9653 |
| 2025-09 | 10742 |
| 2025-10 | 12103 |
| 2025-11 | 15822 |
| 2025-12 | 16053 |
| 2026-01 | 7776 |
| 2026-02 | 10734 |
| 2026-03 | 18861 |
| 2026-04 | 16678 |
| 2026-05 | 12158 |
| 2026-06 | 14199 |
| 2026-07 | 11286 |
| 2026-08 | 8830 |
| 2026-09 | 9208 |
| 2026-10 | 1613 |

## 부록 E. 실행 검증 결과

최종 검사 `validate_profile.py`: **21 PASS / 1 FAIL (총 22개), exit 1**. FAIL은 진단 중 원본 DB/파일집합 불변 검사다. 고정 사본-시작 시 원본의 동일성, 검사한 원본 9,802파일-사본 응답 해시 manifest 동일성, 전수 JSON 파싱(오류 0), 연도/월/상태별 행 수 대조, 수신 items-메타데이터 대조, 요청 항목 및 문서 형식 검사는 PASS다. 결과: `.local/data_profile/validation.json`. 동시 변경: `.local/data_profile/concurrent_change.json`. 실패를 숨기거나 테스트를 통과 처리하지 않았다.

