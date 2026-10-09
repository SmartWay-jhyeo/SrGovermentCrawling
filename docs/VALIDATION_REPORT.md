# 단계별 검증 보고서

## 2026-10-08 11:00 KST — 추가 세 서비스 실제 수집

- 상세: [PROVIDER_API_VALIDATION.md](PROVIDER_API_VALIDATION.md)의 최신 실제 수집 성공 절. 사용자 입력 키의 인코딩 형식을 encoded로 수정, LH EUC-KR XML / body.item 지원, 페이지 수집 및 별도 staging 체크포인트 구현.
- 실호출: 초기 403/30 3회, 수정 후 표본 5회, 범위 수집 15회 = 23회. 세 API 조회 범위 totalCount와 수신 행수 일치. API 전수범위·독립 공사 건수로 확대 해석하지 않는다.
- `.venv/Scripts/python -m pytest tests/unit/test_provider_collect.py tests/unit/test_provider_probe.py -q` → 20 passed (2.80초).
- `.venv/Scripts/python -m pytest -q` → 258 passed / 2 skipped (48.76초). 실연동 pytest는 CLI --live 미지정으로 SKIPPED; 별도 CLI 실호출과 구분한다.
- 범위 수집은 저장 완료 후 실행 상태 기록 시 stop_reason 누락 TypeError 발생. 필수 인자를 추가하고 CLI 전체 종료 회귀 테스트를 추가했다. `.venv/Scripts/python -m pytest tests/unit/test_provider_collect.py -q` → 6 passed (1.33초).
- 같은 수집 명령 재실행 HTTP 0회, 완료 범위 재사용, 저장 행 증가 없음. 최초 실행 기록은 원래 보고서·저장 job 완료 상태 대조 후 복구했다.
- 원본·보고서·staging·수정 소스 32파일과 DB 메타데이터 23행에서 현재 두 키 변형 검출 0 / 미검사 0. staging integrity_check=ok. 세 소스 날짜 필드 파싱 실패 0 / 10월 1~8일 밖 행 0.
- 현장주소·참가지역·면허 조건·버전 관계·UI/스케줄러 연결은 미검증 또는 미구현. K-apt 제목 후보는 탐색 분류이며 면허/공종 전수 수집 판정이 아니다.

## 2026-10-08 — 추가 서비스 공통 키 분리 검증

- 사용자 확인: K-apt·LH·K-water는 서로 같은 승인 키를 사용하지만 기존 나라장터 키와는 다르다. 앞선 403/30은 기존 키에 대한 응답이다.
- 구현: 별도 `PROVIDER_DATA_GO_KR_SERVICE_KEY` 및 형식 설정, 양쪽 키 마스킹, 제공자별 Settings/클라이언트 선택. 기존 나라장터 키·수집 경로 보존. 새 키 미설정 시 기존 키로 fallback하지 않는다. 누락된 로컬 `.env` 입력란만 추가했다.
- `.venv/Scripts/python -m pytest tests/unit/test_provider_probe.py tests/unit/test_config.py -q` → 40 passed (5.72초).
- `.venv/Scripts/python -m pytest -q` → 253 passed / 2 skipped (47.25초). 실연동 테스트는 CLI --live 미지정으로 SKIPPED.
- `.venv/Scripts/python -m bidloc.provider_probe --live --max-calls 6` → 추가 키 미입력으로 세 서비스 모두 호출 전 BLOCKED, HTTP 0회.
- `git diff --check` → 오류 없음, 일부 파일 CRLF 전환 경고. 실제 새 키·응답·공고 수집은 사용자 로컬 입력 전 BLOCKED. 정식 수집 성공으로 표시하지 않는다.

## 2026-10-08 10:46 KST — 사용자 활용승인 확인 후 호출

- 사용자 확인: K-apt·LH·K-water 승인 완료, 세 서비스의 키가 서로 같음. 기존 로컬 나라장터 키와의 일치 여부는 아직 확인하지 않았다.
- 실행: `.venv/Scripts/python -m bidloc.provider_probe --live --providers kapt,lh,kwater --begin 2026-10-01 --end 2026-10-08 --max-calls 6`.
- 결과: 각 1회, 총 3회 모두 HTTP 403 / code 30 / AUTH_KEY_INVALID. source_response ID 9875~9877. 데이터 미수집.
- 근거: Git 제외 `.local/real/reports/provider-probe/provider-probe-20261008T104622-c2bc149f.json`.
- 로컬 설정 확인: 유효 키 출처 `.env`, 키 형식 decoded, 프로세스 환경변수 덮어쓰기 없음, 설정 경고 없음. 실제 키 값은 출력하지 않았다.
- 수집 코드 변경 없음. 공고 파싱·정규화 검증 BLOCKED. 승인 반영 지연이나 키 불일치를 확정 원인으로 단정하지 않는다.
- 검증: `.venv/Scripts/python -m pytest tests/unit/test_provider_probe.py -q` → 8 passed (0.62초). 이번 보고서·원본 4개 파일과 DB 메타데이터 3행의 시크릿 검사 미검사 0 / 검출 0. 문서 `git diff --check` 오류 없음(CRLF 전환 경고만 관측).

## 2026-10-08 10:34 KST — 추가 조달 API 수집 재시도

- 실행: `.venv/Scripts/python -m bidloc.provider_probe --live --providers kapt,lh,kwater --begin 2026-10-01 --end 2026-10-08 --max-calls 6`.
- 관측: 세 서비스 각 1회, 총 3회. 모두 HTTP 403 / code 30 / AUTH_KEY_INVALID. 종료코드 1. 원본 source_response ID 9872~9874. 공고 데이터 미수집, 정식 수집 BLOCKED.
- 보고서: Git 제외 `.local/real/reports/provider-probe/provider-probe-20261008T103447-3a78810c.json`. 최상위 PARTIAL을 성공으로 해석하지 않으며 각 제공자 상태는 BLOCKED다.
- 오프라인 검증: `.venv/Scripts/python -m pytest tests/unit/test_provider_probe.py -q` → 8 passed (0.31초). 코드 변경 없음. 전체 테스트 재실행 SKIPPED; 새 데이터의 페이지·현장주소·공종·정정 검증은 인증 오류로 BLOCKED.

## 2026-10-08 — 추가 조달 API probe

상세 계약·공식 출처·재실행 명령: [PROVIDER_API_VALIDATION.md](PROVIDER_API_VALIDATION.md).

- `python -m bidloc.provider_probe` → --live 없는 실행 BLOCKED, HTTP 0회.
- `python -m bidloc.provider_probe --live --begin 2026-10-01 --end 2026-10-08 --max-calls 6` → K-apt·LH·K-water 각 1회 HTTP 403 / code 30. 총 3회, PARTIAL, 종료코드 1.
- `python -m bidloc.provider_probe --live --providers bid_notice --begin 2026-10-07 --end 2026-10-07 --max-calls 2` → 나라장터 2페이지 HTTP 200 / code 00, 최근 구간 공고 표본 확인. 총 2회. 표본만 확인했으므로 전체 보고 상태 PARTIAL.
- `.venv/Scripts/python -m pytest tests/unit/test_provider_probe.py -q` → 합성 응답 8 passed (0.28초). 인증/쿼터 중단, 오류→0 변환 방지, 반복페이지, 오래된 응답의 최신성 미확정, 월 경계 거부, 허용된 operation/parameter 검증.
- `.venv/Scripts/python -m pytest -q` → 246 passed / 2 skipped (40.77초). 기존 실연동 테스트 2개는 명시적 --live 미지정으로 SKIPPED; 위 5회 probe와 별도다.
- 최종 도구 메시지 수정 후 해당 단위테스트 재실행 8 passed (0.19초). 변경 소스·공개 명세·로컬 보고서·실응답 원본 합계 26개 파일과 DB 요청 메타데이터 5행에서 현재 키·인코딩 변형 검출 0건. `.local/`·`.env` Git 제외 확인. 이번 변경 파일 `git diff --check` 오류 없음.

문서 가져오기 중 중부발전 페이지의 내장 Swagger는 JSONDecodeError로 파싱 실패하여 자동 호출 대상으로 추가하지 않았다. 초기 메타데이터 추출의 K-apt operation 검색도 StopIteration으로 중단되어 경로별 Swagger parameters로 수정했다. 이 과정에는 인증키를 사용하는 API 호출이 없었다.

## 2026-10-08 — 목업 채택 후 실행형 Streamlit 앱 검증

- 환경: Windows / Python 3.11.9 / SQLite 3.45.1 / Streamlit 1.64.0 / pandas 3.0.6. `pip check`: No broken requirements found. 의존성은 `requirements-lock.txt`에 고정했다.
- 앱: `http://127.0.0.1:8501`, loopback 바인딩 확인. 수집 CLI와 분리해 DB 읽기만 수행한다. 공개 서비스 배포는 하지 않았다.
- 전체 테스트: `.venv/Scripts/python -m pytest -q` → **238 passed, 2 skipped (57.57초)**. 실호출 두 테스트는 `--live` 미지정으로 SKIPPED. 마지막 레이아웃 수정 뒤 `tests/unit/test_query_service.py` 재검증 → **6 passed**.
- Browser plugin not available. 기존 Playwright core 1.63.0 / Chromium 147.0.7727.15 사용. PC 1512×1040, 모바일 390×844.

| 브라우저 검사 | 결과 | 근거 |
|---|---|---|
| 페이지 식별·비어 있지 않음 | PASS | 제목 면허 입지 분석기, 실데이터 검색 결과 7,426건 |
| 검색 | PASS | 방수 입력·검색 → 1,112건, 결과 제목 확인 |
| 상세·복귀 | PASS | 첫 결과 상세 조건·UNKNOWN 확인, 복귀 시 필터 유지 |
| 빈 결과·초기화 | PASS | 존재하지 않는 검색어 → 0건, 초기화 → 7,426건 |
| 다운로드 | PASS | 실제 CSV 저장, 공고번호·조회조건 포함 |
| 탐색 | PASS | 지역 비교·단가계약·검토 대기·수집 품질·API 화면 진입 |
| API 미구현 상태 | PASS | 설계 단계 표시, 연결 테스트 비활성 |
| 오류·외부 통신 | PASS | 관련 콘솔/페이지 오류 0, 외부 요청 0 |
| 모바일 | PASS | 접힌 메뉴·세로 필터, 가로 넘침 없음 |

검사 스크립트: `%TEMP%/bidloc-app-full-qa.cjs`. 결과 JSON·PC 검색/상세/API 및 모바일 화면: `%TEMP%/bidloc-app-qa/`. 실데이터 캡처와 CSV는 Git에 포함하지 않는다. 최신 목업과 브라우저 화면을 `view_image`로 대조했고 차이·유예 사항은 `ui/IMPLEMENTATION.md`에 기록했다.

추가한 네트워크 없는 테스트는 금액 null/0 분리, 지역 토큰 경계, 검색어 안전 처리, 마감 미수집, 면허 4992/49920 구분, 복합키 상세, 잘못된 날짜 범위, 빈 DB 무생성, CSV 수식 방어·메타데이터, WAL 변화 캐시 무효화, 네이티브 UI 메뉴·검색·상세·복귀를 검증한다. Windows asyncio의 내부 socketpair만 loopback 연결을 허용하며 DNS와 외부 연결은 차단한다.

### 발견·수정과 한계

1. 최초 브라우저 진입 두 차례가 300초 대기 내 완료되지 않았다. EXPLAIN QUERY PLAN과 로컬 py-spy 호출 스택으로 큰 원문 조회 순서 및 화면에서 쓰지 않는 전역 분류 집계를 확인했다. 공고·지역·개찰 JOIN은 작은 후보 키부터 시작하게 변경했다. 앱에서 전역 relevance census를 생략할 때 `relevance=null`, `relevance_status=NOT_REQUESTED`로 표시하며 0으로 대체하지 않는다.
2. 첫 조회는 전체 분석 후보의 원문 필드를 읽으므로 여전히 수 분 걸릴 수 있다. 생성 후에는 DB·WAL 파일 상태 및 분석 코드 해시로 검증한 gzip JSON을 재사용한다. 캐시는 `.local/real/ui-cache/`에 저장하고 원본 DB를 변경하지 않는다. 별도 프로세스 캐시 읽기 2.091초, 최종 서버 재시작 뒤 브라우저 첫 결과 10.102초 관측. 모든 장비의 성능 보장치는 아니다.
3. CSV 검사의 최초 대기는 버튼 접근성 이름에 Material 아이콘명이 포함돼 테스트 선택자가 일치하지 않은 문제였다. 실제 이름에 맞춰 수정 후 파일 저장까지 PASS. 원천 API 호출이나 다운로드 로직 실패가 아니었다.
4. Material favicon의 외부 요청은 로컬 표시 가능한 emoji favicon으로 교체해 최종 브라우저 외부 요청 0을 확인했다.
5. 원천 응답 수는 검사 전후 9,866으로 동일했다. API 수집 명령은 실행하지 않았다. 합성 데이터로 자동 대체하지 않았다.
6. 전체 최초 Git diff 공백 검사에는 기존 `data_profile.md`의 trailing whitespace/마지막 빈 줄 2건이 있다. 관련 없는 기존 문서를 재정리하지 않고 보존했다. 새 앱 변경 파일은 별도 검사한다.

조회 HTTP API, 외부 앱 인증/접근통제, 원격 배포, 전체 정정 이력 열람은 미구현이며 테스트 성공으로 간주하지 않는다. 실제 모바일 기기와 Safari/Firefox는 미검증이다. Git 업로드 대상은 소스·설계·합성 목업이며 DB·키·원본·실데이터 산출물을 제외한다.

---

## 2026-10-08 — CODEX_PROMPT P1~P4 완료

사용자의 후속 지시로 단계마다 승인을 묻지 않고 P1부터 P4까지 연속 진행했다. 아래 P0/2026-09 내용은 보존 이력이며 현재 상태는 이 절을 우선한다.

- **완료:** P1 네 API resultCode 00, P2 실제 강제 종료 후 pageNo=2 재개, P3 기존 3년 이상 본 수집 job의 4,452개 파티션 DONE·실패 0·수신/totalCount 불일치 0, 최신 차수 기준 finalize, 마이그레이션 0001~0008 체크섬 정상. SQLite `quick_check=ok`, 외래키 위반 0. P4 입지·단가·shortlist CLI, CSV/JSON, 로컬 HTML UI 생성 및 브라우저 검증 완료.
- **실패:** 잔여 실패 없음. 검증 중 발견한 기존 job 읽기의 json import 누락, HTML 초기화 버튼과 form.reset 이름 충돌을 수정하고 재검증했다. 큰 원문을 반복 읽던 보고서 작업은 자체 프로세스만 종료한 뒤 분석 인덱스를 추가해 다시 생성했다.
- **미검증:** 명시적 `--live` 없는 통합 테스트 2개는 SKIPPED(별도 P1/P2 실연동으로 검증). 실제 휴대폰 하드웨어·Safari/Firefox, 적격심사·실적·시평액·주력분야/복수면허 AND/OR·과거 주소 조건은 미검증/UNKNOWN. 과거 수집 페이지는 당시 지문 검사가 없어 새 반복 감지 규칙을 소급 검증하지 않았으며 전 기간을 재호출하지 않았다. Git 저장소가 아니므로 추적·커밋 상태는 미검증.
- **다음 조치:** 검증한 화면 `.local/real/reports/p4_20261008/progress.html`을 열어 본점·기간·계약방식·추정가격 조건을 선택한다. P5의 신규 자동수집/스케줄러 등록은 이번 요청 범위 밖이며 새 작업을 등록하지 않았다.

최종 오프라인 검증: `.venv/Scripts/python -m pytest -q` → **232 passed, 2 skipped (34.21초)**. UI: Playwright core 1.63.0 / 기존 Chromium 147.0.7727.15, PC 1440×1100·모바일 375×900, 라이트·다크, 검색·정렬·본점/프로필/금액 필터·초기화·근거 공고·CSV 다운로드 PASS. 콘솔 오류 0, 외부 요청 0, 모바일 가로 넘침 없음.

검증 스냅샷 범위: **2023-09-20~2026-10-06**. 확정 공사 공고는 RELEVANT 34,339 / NOT_RELEVANT 346,416 / UNKNOWN 35,009 / CANCELLED 28,145건. 분석 A는 정정·취소·연결 재공고·기간 적용 후 34,111건, 314개 지역, 지역 연결률 97.9039%. 분석 B는 분류 대상 단가 공고 4,786건·17개 분류/연도·290개 기관 코드. 기본 본점 경기도 남양주시의 스냅샷 마감 전 지역 일치 후보 4건, 지역 미기재 5건이며 참가자격 전체를 확정한 수치가 아니다.

원본 품질 표시는 면허 필드 밀림 의심 1,062행 / 금액 int64 범위 초과 2행 / 동일 키 내용 충돌 243건이다. 삭제하거나 0으로 바꾸지 않았다. 추가 원본·보고서·로그·소스 등 301개 파일에서 현재 키 변형 미검출, 미검사 파일 0건. 기존 큰 DB 전체 재시크릿 스캔은 P0 기록을 유지하고 이번에는 추가/수정 파일 범위를 검사했다.

P1 응답 8건, P2 응답 18건(1차 7건, 재개 11건). 2026-10-07 서비스 예약은 입찰공고 32 / 개찰 7, 실제 기록은 31 / 7이었다. 강제 종료 시 미완결 예약 1건도 호출예산에서 빼지 않았다. 작업 전 예약 12건 대비 증가분은 27건이며, 응답 기록 증가분은 26건이다. P3/P4 검증·분석은 API 호출 0회다.

별도 스윕 프로세스 실행이 2026-10-08 00시대에 관측되어 검증 산출물 14개를 `p4_20261008/`에 고정 보관하고 SHA-256 manifest를 남겼다. 기존 별도 수집 프로세스/등록 설정을 종료·변경하지 않았다. 기본 `progress.html`은 별도 실행이 갱신할 수 있다.

근거: `.local/p1_p4/resume-evidence.json`, `budget-evidence.json`, `p3-evidence.json`, `secret-scan.json`, `browser-qa.json`; 스냅샷 폴더의 `snapshot_manifest.json`. 분석 정의와 기관명 표본 근거는 `docs/ANALYSIS.md` 참조.

---

## 2026-10-07 — CODEX_PROMPT P0 오프라인 재검증

이번 인수 기준은 `docs/CODEX_PROMPT.md` 13장의 P0: 설정·키 마스킹·라이브 게이트·HTTP 클라이언트·원본 저장·DB 마이그레이션·doctor, **실제 API 호출 예산 0회**다. 기존 `PROJECT_SPEC.md`에서 실연동을 포함해 부르던 P0와 구분한다. 아래 과거 LIVE_VERIFIED/DOCUMENTED 이력은 보존했으며 상태를 새 실응답 없이 상향하지 않았다.

### 실행 환경과 보존

- Windows PowerShell, Python 3.11.9, SQLite 3.45.1.
- 기존 `.venv`: httpx 0.28.1, PyYAML 6.0.3, defusedxml 0.7.1, python-dotenv 1.2.3, pytest 9.1.1. 설치·업그레이드는 하지 않았다.
- PowerShell 실행에 `PYTHONIOENCODING=utf-8` 적용. 파일은 UTF-8 사용.
- Git 저장소가 아님을 관측했다. 변경 대상 16개 원본을 `.local/p0_revalidation/before/`에 보관하고 SHA-256 목록을 같은 상위 폴더의 `before_hashes.json`에 남겼다. `.env` 값과 API 원본은 이 변경 검토 사본에 포함하지 않았다.
- 기존 실데이터 DB는 doctor에서 읽기 전용으로 점검한다. 빈 검증용 DB에만 마이그레이션 CLI를 실행했다. 기존 수집 job·파티션·데이터, 예약 작업 설정은 변경하지 않았다.

### 실행 명령과 관측 결과

아래 Python 명령 앞에는 `$env:PYTHONIOENCODING='utf-8'`을 적용했다.

| 실행 | 관측 결과 |
|---|---|
| `Get-Content -Raw -Encoding UTF8 docs/CODEX_PROMPT.md` | 처음 턴에서는 실행 도구 승인 정책 때문에 실행 전 BLOCKED. 이번 턴에서는 성공하여 0~15장 전체 읽음 |
| `git status --short` | `fatal: not a git repository` 관측. 초기화·커밋 미실행 |
| `python --version` | Python 3.11.9 |
| `python -m pytest -q -m 'not live'` | exit 1: 시스템 Python에 pytest 없음 |
| `.venv/Scripts/python -m pytest -q -m 'not live'` (변경 전) | **149 passed, 1 failed, 2 deselected**, 73.68초. 기존 테스트가 마이그레이션 1개만 기대했지만 실제 6개 적용 |
| `.venv/Scripts/python -m pytest -q` (보완 후) | **182 passed, 2 skipped**, 71.72초. 실연동 2개는 CLI `--live` 미지정으로 SKIPPED |
| `$env:BIDLOC_RUN_LIVE_TESTS='1'; .venv/Scripts/python -m pytest tests/integration -q` | **2 skipped**, 3.11초. 환경변수만으로 실행되지 않음, 네트워크 호출 없음 |
| 아래 검증용 경로 설정 후 `.venv/Scripts/python -m bidloc init-db` 2회 | 1회차 0001~0006 적용, 2회차 추가 적용 없음. 두 번 모두 exit 0 |
| `.venv/Scripts/python -m bidloc doctor` | **exit 0, FAIL 0 / BLOCKED 0 / WARN 1**. 시크릿 검사 9,970개 파일에서 현재 키 변형 미검출, 미검사 경고 없음. D: 여유 880.66 GiB, DB 마이그레이션 0001~0006 PASS |
| `doctor --network`, P1 probe/verify 실연동 | **SKIPPED**: 이번 P0의 호출예산 0 및 단계 경계 유지 |

검증용 DB 명령의 프로세스 환경변수(기존 `.env` 수정 없음):

```powershell
$env:DATA_MODE='demo'
$env:ALLOW_LIVE_API='false'
$env:DATABASE_PATH='.local/p0_revalidation/synthetic/bidloc.sqlite3'
$env:RAW_RESPONSE_DIR='.local/p0_revalidation/synthetic/raw'
$env:EXPORT_DIR='.local/p0_revalidation/synthetic/exports'
$env:REPORT_DIR='.local/p0_revalidation/synthetic/reports'
.venv/Scripts/python -m bidloc init-db
.venv/Scripts/python -m bidloc init-db
```

버전 조회용 최초 `python -c`는 PowerShell 인용 처리로 NameError가 났다. here-string을 표준입력으로 전달하는 방식으로 재실행하여 위 실제 버전들을 확인했다. 키·설정값은 출력하지 않았다.

최종 변경 파일 19개의 별도 시크릿 검사도 검출 0건이었다. 문서 완료 문구 검사는 PowerShell 파이프의 기본 문자 인코딩으로 한글이 `?`로 전달되어 처음 AssertionError가 발생했다. `$OutputEncoding = [System.Text.UTF8Encoding]::new()`를 적용해 재실행한 뒤 STATUS·VALIDATION_REPORT 모두 PASS를 확인했다. 코드 테스트 실패와 구분되는 점검 스크립트 실행 오류다.

### 검증 내용과 범위

기존 150개 오프라인 테스트에 32개 사례를 추가했다. 단위테스트의 소켓·DNS 차단 fixture를 유지했으며 실제 API 응답·실제 지역 순위를 테스트 정답으로 추가하지 않았다.

- API-01/04, SEC-01: env/CLI 라이브 게이트 네 조합, 원문/encoded 키 인코딩, URL·예외·로그·원본·반환 항목·오류 분류 마스킹. 응답에 되돌아온 키를 파싱·문자열 절단 전에 제거한다.
- 시크릿 검사: 기존 50 MiB 제한을 제거. 51 MiB 초과 합성 파일, 청크 경계에 걸친 키, src·루트 문서·별도 로그 경로 탐지, 읽기 거부 시 WARN 검증. 키 주입 파일 `.env`는 검사 대상에서 제외한다.
- 디스크: 저장 경로 미생성 상태에서 부모 볼륨 점검, 같은 볼륨 중복 보고 방지, 부족 WARN·조회 실패 FAIL 검증. 20 GiB는 내부 참고값이다.
- DB: 마이그레이션 6개 적용·재실행 무중복·외래키·체크섬 변조·실패 롤백·미확인 이력 차단, doctor의 미마이그레이션 DB 무변경 점검.
- 원본: 빈 본문도 SHA-256과 파일·0바이트 길이를 기록한다. 기존 경로 이탈 방어 검증 유지.
- 설정: `collector.yaml`의 하루 구간·행 수 상한 999·재시도/재시작 상한·단계 목록·서비스별 내부 호출 한도를 검증한다. 기존 수집 명령을 새 설정에 연결하는 작업은 P2에서 한다.

이번 수행한 실제 data.go.kr 호출: **0회**. 재시도·유료 호출·배포·OS 작업 등록: **0회**. 공개 OpenAI 문서 조회는 앞선 도구 승인 오류 설명을 위한 조회이며 data.go.kr API 실연동 증거가 아니다.

### 미검증과 다음 단계

**P0 완료.** 오프라인 테스트 통과와 현재 인증키 노출 미검출을 확인했다. 실제 파일 검사는 큰 DB·원본을 포함한 9,970개 파일 대상이며, `.env` 계열(양식 제외), Git·가상환경·도구 캐시를 제외한 현재 파일 상태에서 등록된 키와 인코딩 변형을 검사한 결과다. 삭제 이력·다른 키·암호화/압축 내부 전체를 검증한 주장은 아니다. doctor WARN 1건은 보존된 카탈로그의 PARTIAL_LIVE_VERIFIED 상태다(기존 DOCUMENTED 44 / LIVE_VERIFIED 10, 이번 재호출 아님).

현재 폴더가 Git 저장소가 아니므로 추적 파일 제외는 미검증이고 `.gitignore` 규칙만 확인했다. P1 실연동·권한·실제 envelope·999행 반환, Python 3.12/다른 OS, 이후 P2~P5 수집·분석 인수는 이번에 실행하지 않았다. 요청대로 P0에서 멈췄으며 다음 요청에서 P1을 진행한다.

---

## 기존 실연동 검증 이력 (2026-09)

작성일: 2026-09-16 (KST), 실연동 검증 반영
범위: P0 — API 계약 및 실제 연결 검증

## 인수 상태

| 구분 | 상태 |
|---|---|
| 코드 준비 | 완료. 설정, 안전한 클라이언트, 원본 저장, DB·마이그레이션, doctor, verify-api, 오프라인 테스트 |
| 공식 명세 확인 | 완료(DOCUMENTED). 4개 서비스의 포털 Swagger와 참고자료 docx |
| 실연동 검증 | **부분 완료(PARTIAL_LIVE_VERIFIED).** P0 핵심 오퍼레이션 10개 LIVE_VERIFIED |
| P0 통과 기준 "최소 공사 1건에서 공고·면허·지역·결과 연결 확인" | **충족.** 개찰완료 공사 6건에서 공고 → 면허제한 → 참가가능지역 → 개찰결과 → 낙찰이 실제 키로 연결됨 |

표본 검증이다. 전 기간·전 공고의 수집 완전성을 확인한 것은 아니다.

## 1. 실행 환경

| 항목 | 값 |
|---|---|
| OS / Python | Windows 11 Home 10.0.26200 / Python 3.11.9 (`.venv`). 3.12 실행은 미검증 |
| 주요 패키지 | httpx 0.28.1, PyYAML 6.0.3, defusedxml 0.7.1, python-dotenv 1.2.3, pytest 9.1.1 |
| 인증키 | `.env`의 `DATA_GO_KR_SERVICE_KEY`, 형식 decoded. 값은 출력·기록하지 않음 |
| 실호출 허용 | `.env`의 `ALLOW_LIVE_API=true` + CLI `--live` |

## 2. 실행한 명령과 결과

### 2.1 사전 점검

| 명령 | 결과 |
|---|---|
| `python -m bidloc doctor --network` (1차) | 키 PASS, DNS·TLS PASS, **live-gate BLOCKED**: `.env`의 `ALLOW_LIVE_API`가 `false`로 저장돼 있었음. 실호출하지 않고 멈춤 |
| (사용자가 `.env`를 `ALLOW_LIVE_API=true`로 수정) | - |
| `python -m bidloc doctor` (2차) | `ALLOW_LIVE_API true (출처: .env)`, service-key PASS, live-gate PASS, FAIL 0, BLOCKED 0 |

1차 점검 직후 이번 세션 한정 환경변수로 허용값을 주고 doctor만 다시 실행한 적이 있다. 이때 API 호출은 없었다. 실제 호출은 모두 사용자가 `.env`를 고친 뒤에 했다.

### 2.2 실연동 실행 (소량부터 단계적으로)

| 순서 | 명령 | 실행 ID | HTTP 호출 | 결과 |
|---|---|---|---|---|
| 1 | `verify-api --live --max-calls 8 --fixed-only` | verify-20260916T225918-8629be3e | 8 | PARTIAL(예산 상한). 인증·3개 서비스 엔드포인트 정상, 표본 1건 핵심 연결 확인 |
| 2 | `verify-api --live --max-calls 10 --fixed-only --resume <1>` | verify-20260916T225953-41002ec3 | 10 | PARTIAL. 명부·낙찰 확인, 표본 2건 참가업체수 MATCH |
| 3 | `verify-api --live --max-calls 24 --plan <창별 1건 계획> --resume <2>` | verify-20260916T230100-c03ddb1e | 24 | PARTIAL. 2023~2026 탐색 성공. 그러나 선정된 공고가 취소공고라 개찰결과 없음 |
| - | 표본 선정 규칙 수정(취소공고 제외, 개찰일 지난 공고 우선), 재사용 탐색은 원본에서 재선정 | - | 0 | 단위테스트 150 통과 후 진행 |
| 4 | `verify-api --live --max-calls 40 --plan <창별 1건 계획> --resume <3>` | verify-20260916T230325-4979399e | 33 | **COMPLETED.** 연도별 개찰완료 공사 4건 연결 확인, 참가업체수 MATCH 3건 |

**이번 실행의 실제 API 호출 수: 75회.** 재시도 0회, HTTP 오류 0회. 내부 일일 예산 카운터(2026-09-16 KST)도 75다.
"창별 1건 계획"은 `config/verify_samples.yaml`에서 `pick_per_window`만 1로 바꾼 임시 파일이다(저장소 밖 작업 폴더).

### 2.3 오퍼레이션별 호출 결과

| 서비스.오퍼레이션 | 호출 | 결과 | 수신 행 |
|---|---|---|---|
| industry_law.getIndstrytyBaseLawrgltInfoList | 2 | SUCCESS 2 | 111 |
| bid_notice.getBidPblancListInfoCnstwk | 9 | SUCCESS 9 | 13 |
| bid_notice.getBidPblancListInfoCnstwkPPSSrch | 4 | SUCCESS 4 | 120 |
| bid_notice.getBidPblancListInfoLicenseLimit | 9 | SUCCESS 9 | 22 |
| bid_notice.getBidPblancListInfoPrtcptPsblRgn | 9 | SUCCESS 9 | 14 |
| bid_notice.getBidPblancListInfoCnstwkBsisAmount | 9 | SUCCESS 6, 결과 0건 3 | 6 |
| bid_notice.getBidPblancListEvaluationIndstrytyMfrcInfo | 8 | SUCCESS 2, 결과 0건 6 | 3 |
| bid_notice.getBidPblancListInfoChgHstryCnstwk | 2 | 결과 0건 2 | 0 |
| bid_award.getOpengResultListInfoCnstwk | 8 | SUCCESS 6, 결과 0건 2 | 6 |
| bid_award.getOpengResultListInfoOpengCompt | 7 | SUCCESS 7 | 508 |
| bid_award.getScsbidListSttusCnstwk | 8 | SUCCESS 6, 결과 0건 2 | 6 |

실패한 API 호출은 없다. 모든 응답은 HTTP 200, `resultCode 00`, `resultMsg 정상`이었다.

### 2.4 테스트

| 명령 | 결과 |
|---|---|
| `python -m pytest` (실연동 전, 이전 기록) | 148 passed, 2 skipped |
| 표본 선정 규칙·재선정 기능 추가 후 `python -m pytest` | 150 passed, 2 skipped |
| 카탈로그에 실응답 근거 병합 후 `python -m pytest` | 처음 3건 실패. 실연동 전 상태값(DOCUMENTED·UNVERIFIED·BLOCKED)을 고정한 테스트였음. 근거가 있으면 LIVE_VERIFIED를 허용하도록 고친 뒤 **150 passed, 2 skipped** |
| `python -m bidloc doctor` (최종) | FAIL 0, BLOCKED 0, secret-scan PASS(147개 파일에서 인증키 변형 미검출) |

SKIPPED 2건은 `BIDLOC_RUN_LIVE_TESTS=1`일 때만 도는 통합테스트다. 이번 실연동은 verify-api로 했으므로 통합테스트는 따로 실행하지 않았다.

## 3. 인증 방식

- 포털 Decoding 키를 클라이언트가 한 번만 URL 인코딩하는 방식(`DATA_GO_KR_SERVICE_KEY_FORMAT=decoded`)으로 75회 모두 인증에 성공했다.
- 파라미터명 `serviceKey`(Swagger 표기)가 동작했다. 참고자료 표기 `ServiceKey`는 시험하지 않았다.
- `SERVICE_KEY_IS_NOT_REGISTERED_ERROR` 같은 인증 오류가 한 번도 나지 않았으므로 Encoding 키 이중 인코딩 문제는 발생하지 않았다. 호출을 아끼려고 Encoding 키 형식은 별도로 시험하지 않았다.
- 요청 URL, 원본 파일, DB, 보고서에서 키는 모두 마스킹되었고 doctor 저장파일 검사도 통과했다.

## 4. 실응답으로 검증한 항목 (LIVE_VERIFIED)

### 4.1 공사 입찰공고와 키

- 상세 조회한 공고 9건, 공고 차수 행 13건. 탐색 조회로 받은 공고 목록 행 120건(창별 첫 페이지 30행).
- `inqryDiv=2`로 공고번호를 조회하면 그 공고의 모든 차수(000, 001, 002)가 함께 온다.
- 공고번호, 공고차수, 입찰분류번호, 재입찰번호가 실제 응답에 있다. 관측한 개찰단위는 모두 입찰분류번호 `0`, 재입찰번호 `000`이었다.
- 기초금액(`bssamt`)은 6건, 추정가격·예산금액·VAT는 공고 13행 모두에 있었다. 기초금액이 없는 공고는 오류가 아니라 결과 0건으로 온다.

### 4.2 연결 확인 (복합키 JOIN)

| 공고번호 | 연도 | 계약방법 | 면허제한 | 참가가능지역 | 개찰단위 | 공식 참가업체수 | 명부 행 = 고유 사업자번호 | 낙찰자 = 1순위 |
|---|---|---|---|---|---|---|---|---|
| R26BK01695462 | 2026 | 수의계약(소액) | 4992, 주력 습식·방수 | 경기도 성남시 | LINKED | 51 | 51 = 51 | 예 |
| R26BK01448245 | 2026 | 수의계약(소액) | 4992, 주력 습식·방수 | 경기도 남양주시, 경기도 가평군 | LINKED | 48 | 48 = 48 | 예 |
| R26BK01505825 | 2026 | 수의계약(소액) | 4992, 주력 도장 | 충청북도 5개 시·군 | LINKED | 198 | 198 = 198 | 예 |
| R25BK00832425 | 2025 | 수의계약(소액) | 4992, 주력 습식·방수 | 인천광역시 | LINKED | 176 | 176 = 176 | 예 |
| 20240508583 | 2024 | 수의계약 | 4992, 주력 습식·방수 | 강원특별자치도 강릉시 | LINKED | 35 | 35 = 35 | 예 |
| 20230517961 | 2023 | 제한경쟁 | 3개 그룹(4992 포함) | 경상북도 | LINKED | 845 | 명부 미조회(페이지 한도 500 초과) | 미확인 |

- 연결 방식: 공고 (공고번호, 공고차수) → 면허제한·참가가능지역 요청 → 개찰결과의 (공고번호, 공고차수, 입찰분류번호, 재입찰번호) → 같은 네 키로 명부·낙찰 목록.
- 취소된 공고 3건(R26BK01485179, 20230446915, 20240507578)은 개찰결과·낙찰이 0건이었다. 참가 0으로 기록하지 않고 "개찰기록 없음"으로 남겼다.

### 4.3 참가업체 수의 정의 (표본 관측)

- 5개 개찰단위에서 `prtcptCnum` = 명부 전체 행 수 = 고유 사업자번호 수였다.
- 명부 비고(`rmrk`) 관측값: `정상`, `낙찰하한선 미달`, `전자입찰취소신청`. 정상이 아닌 행은 개찰순위가 비어 있다.
- 따라서 **공식 참가업체수는 개찰 명부에 오른 전체 투찰 업체 수이며, 낙찰하한선 미달·전자입찰취소신청 업체를 포함한다.** 유효 투찰 수가 아니다. 예: 51명 중 정상 47, 미달 4.
- 낙찰 목록의 `prtcptCnum`은 개찰결과 목록 값과 6건 모두 같았다.
- 미확인: 공동수급 투찰 표현(관측 공고가 모두 공동수급불허), 재입찰 회차별 값, 무효 처리 표기.

### 4.4 과거자료 제공 여부

| 연도 | 탐색 조회(7일, 업종명 "도장") totalCount | 개찰완료 공고 상세 연결 |
|---|---|---|
| 2023 | 421 | 공고·면허·지역·기초금액·개찰결과·낙찰 조회됨. 명부는 참가 845라 미조회 |
| 2024 | 295 | 전 항목 조회됨 |
| 2025 | 302 | 전 항목 조회됨 |
| 2026 | 332 | 전 항목 조회됨 |

새 서비스(ad·as)가 2023·2024년의 구 나라장터 공고번호 체계(11자리 숫자)도 제공한다. 연도별 1~2건 표본이므로 해당 연도 전체의 누락률은 모른다.

### 4.5 업종코드 (업종 API 실응답, 조회시점 현재값)

`indstrytyClsfcCd=49`(건설업) 전체 111건을 받아 이름에 도장·방수·석공이 들어간 항목을 찾았다.

| 코드 | 업종명 | 사용여부 | 근거법령 |
|---|---|---|---|
| 4992 | 도장ㆍ습식ㆍ방수ㆍ석공사업 | Y | 건설산업기본법 |
| 4926 | 해외건설업(전문건설업-습식·방수공사업) | Y | 해외건설촉진법 |
| 4927 | 해외건설업(전문건설업-석공사업) | Y | 해외건설촉진법 |
| 4928 | 해외건설업(전문건설업-도장공사업) | Y | 해외건설촉진법 |

- 공고 면허제한에서 실제로 쓰인 코드는 4992뿐이었다(`도장ㆍ습식ㆍ방수ㆍ석공사업/4992`).
- 주력분야 표기 관측값: `[1^습식·방수공사]`, `[1^도장공사]`. 석공 주력분야 표기는 이번 표본에 없었다.
- 옛 개별 업종(도장공사업 등)은 건설업 분류 조회 결과에 없었다. 업종 API는 조회시점 유효 업종만 준다고 문서화되어 있으므로 과거 코드 이력으로 쓰지 않는다.

### 4.6 지역

- 참가가능지역은 시·도 단위(경기도, 경상북도, 인천광역시)와 시·군 단위(경기도 성남시, 강원특별자치도 강릉시)가 모두 온다.
- 복수지역은 행 단위다. R26BK01448245는 2행, R26BK01505825는 충청북도 5개 시·군 5행이었다.
- 공사현장(`cnstrtsiteRgnNm`)은 허용지역과 다를 수 있다. 예: 허용지역 경상북도, 현장 경상북도 경주시. 현장이 시·도로만 적힌 경우도 있었다.

## 5. 문서와 실제 응답의 차이

| 항목 | 문서 | 실제 응답 |
|---|---|---|
| JSON 목록 구조 | 참고자료·Swagger: `body.items.item[]` | `body.items`가 바로 리스트 |
| envelope 숫자 | Swagger: 문자열 | `totalCount`, `pageNo`, `numOfRows`는 JSON 정수. 항목 값은 모두 문자열 |
| 정상 무자료 | 참고자료 오류코드 03 "No Data" | `resultCode 00` + `totalCount 0`. 03은 관측 안 됨 |
| 무자료 envelope | 문서 구분 없음 | 입찰공고 서비스는 `items: []`, 낙찰정보 서비스는 `items` 키 자체가 없음 |
| 참고자료 1.2 신규 필드 | Swagger에 없음 | 실제 응답에 있음: `befBidBbancNo`, `rgnLmtBidLocplcJdgmBssCd/Nm`, `sucsfbidMthdAppStd`, `VAT`(검색조건 조회), `smkpAmt/Yn`, 명부 평가점수 4종 |
| Swagger 전용 필드 | `d2bMngRgnLmtYn`(검색조건 공고), `undefined`(업종) | 실제 응답에 없음 |
| 기초금액 조회 필수값 | Swagger: 조회기간 항상 필수 | 공고번호만으로 조회 성공 |
| 평가대상 주력분야 필드명 | 참고자료 표: 첫 글자 대문자 | 소문자 |
| 참가가능지역 제한그룹번호 | 오퍼레이션 설명에는 있음 | 응답에 없음 |
| 면허제한 데이터 품질 | 필드별 형식 정의 | 20230517961 마지막 행에서 값이 한 칸씩 밀림: 주력분야 목록 자리에 등록일시, 등록일시 자리에 "공사", 업무구분은 빈값 |
| 업종명 필터 | "업종명 일부 입력시 조회 가능" | 주공종이 건축공사업인 공고도 반환. 상세 조회한 필터 결과 6건은 모두 면허제한에 4992가 있었음(면허제한 업종에 매칭되는 것으로 보이나 표본 수준) |

## 6. VALIDATION_PLAN P0 항목 상태

| 항목 | 상태 | 근거 |
|---|---|---|
| 공식 공사 목록과 원문 공고 일치 | 부분 | 공고명·공고번호·차수는 API로 확인. 공고문 PDF와의 대조는 이번에 하지 않음 |
| 면허제한·주력분야 위치 | LIVE_VERIFIED | 면허제한 오퍼레이션 `lcnsLmtNm`, `indstrytyMfrcFldList` |
| 참가가능지역과 발주기관 주소 구별 | LIVE_VERIFIED(필드 분리) | 허용지역·공사현장·기관명이 서로 다른 필드. 기관 주소는 공고 응답에 없음 |
| 공고 버전·분할단위·개찰회차 키 | 부분 | 공고차수 000~002 관측. 입찰분류번호≠0, 재입찰번호≠000 사례 없음 |
| 기초금액 단위·VAT·총액 여부 | 부분 | 원 단위 정수 관측. 부가세 포함 여부는 응답만으로 판단 불가 |
| 개찰 요약 count 의미 | LIVE_VERIFIED(표본 5건) | 4.3 |
| 명부 전체 페이지 수집 시 count 일치 | LIVE_VERIFIED(표본 5건) | 2페이지 명부 2건 포함 |
| 유효·무효·미달·공동수급 표현 | 부분 | 미달·취소신청 표기 관측. 무효·공동수급 미관측 |
| 1순위와 최종낙찰 별도 제공 | LIVE_VERIFIED | 별도 오퍼레이션. 표본 5건은 동일인 |
| 빈 응답 구분 | LIVE_VERIFIED | 정상 무자료는 00+0건. 오류 응답은 미발생 |

오프라인 자동화 테스트 항목 상태는 이전 보고와 같다: API-01~08, KEY-01, CNT-02·03, SEC-01은 오프라인 PASS. KEY-02~04, REG, LIC, ELG, CNT-04, AMT-02·03, TIME, QLT, REP, SEC-03, UI는 P1·P2 범위로 미구현.

## 7. 실패하거나 검증하지 못한 항목

1. 실패한 API 호출: 없음.
2. 재입찰 목록·유찰 목록(`getOpengResultListInfoRebid`, `…Failing`): 해당 사례를 찾지 못해 호출하지 않음.
3. 변경이력(`getBidPblancListInfoChgHstryCnstwk`): 2회 모두 결과 0건이라 필드 미확인. 취소공고가 있는 공고였는데도 이력이 비어 있었다.
4. 검색조건 기반 개찰결과·낙찰 목록, 예비가격상세, 입찰가격산식A, 사용자정보 서비스: 미호출.
5. 입찰분류번호가 0이 아닌 분할 공고, 재입찰번호가 000이 아닌 재입찰 공고의 키 동작.
6. 공동수급 투찰의 명부 표현, 무효 투찰 표기.
7. 면허 제한그룹 사이의 AND·OR 의미. 응답에는 그룹번호만 있어 공고문 원문 대조가 필요하다.
8. 참가 845명 같은 대형 명부의 count 일치(페이지 한도로 미조회).
9. numOfRows 최대값(100만 사용), 조회기간 상한, 날짜 경계 포함 여부.
10. 오류 응답(인증 실패·쿼터 초과) 실제 형식: 발생하지 않아 미확인.
11. 제공기관 실제 일일 트래픽 한도(개발계정 1,000 표기)의 적용 단위.
12. 공고문 PDF(S05~S07)와 API 응답 대조.
13. Python 3.12 실행.

## 8. 사용자 준비 설정 상태

- 활용신청: 입찰공고정보, 낙찰정보, 업종 및 근거법규 서비스는 실호출로 승인 확인됨. 사용자정보 서비스는 미호출.
- `.env`: `DATA_GO_KR_SERVICE_KEY`(decoded), `ALLOW_LIVE_API=true` 확인됨.
- 오늘(KST 2026-09-16) 내부 일일 예산 100회 중 75회 사용. 추가 실연동은 25회 이내 또는 다음 날 진행한다.

## 9. P1 진행 판단과 선행 과제

**P1 진행 가능.** P0 통과 기준인 공사 1건 이상의 실제 키 연결을 6건에서 확인했고, 핵심 오퍼레이션 10개의 계약을 실응답으로 확정했다. 다만 아래 과제는 P1 초기에 소규모로 먼저 풀어야 한다.

1. **호출량 설계:** 공고마다 면허·지역을 따로 부르면 호출이 급증한다. 면허제한·참가가능지역의 등록일시 기간 조회(`inqryDiv=1`)가 대량 수집에 쓸 수 있는지, numOfRows 최대값과 기간 상한은 얼마인지 실측한다. 개발계정 일일 1,000회 표기가 오퍼레이션별인지 확인하고, 필요하면 운영계정 트래픽 증가를 신청한다(사용자 승인 필요).
2. **데이터 결함 방어:** 면허제한 행의 값 밀림을 감지하는 형식 검증(등록일시 형식, 목록 필드 대괄호 형식, 업무구분 값)을 정규화 단계에 넣고, 감지 행은 검토 필요로 보류한다.
3. **분할·재입찰 키:** 입찰분류번호≠0, 재입찰번호≠000, 유찰·재입찰 사례를 찾아 재입찰·유찰 목록과 함께 키 동작을 확인한다. 공고차수와 재입찰번호의 증가 규칙이 기회 단위 중복 제거의 전제다.
4. **정정·취소 이력:** 취소공고가 있는데 변경이력이 0건이었다. 공고 목록의 차수별 `ntceKindNm`을 이력의 1차 근거로 쓸지 결정한다.
5. **참가업체수 정책:** 공식 수가 미달·취소신청을 포함하므로, 경쟁도 지표를 공식 수로 할지 명부의 `정상` 행 수로 할지 정하고 두 값을 따로 저장한다. 공동수급 사례를 확인한다.
6. **대형 명부:** 참가 수백~수천 명 명부는 페이지 호출이 많다. 전수 명부 대신 공식 참가업체수를 기본 경쟁도로 쓰고 명부는 표본 검증용으로 제한할지 정한다.
7. **면허 조건 해석:** 제한그룹 AND·OR 의미를 공고문 PDF 몇 건과 대조한다. 확인 전에는 참가조건 판정에 쓰지 않는다.
8. **업종명 필터:** 검색조건 조회의 업종명 필터가 면허제한 업종 기준인지 확인하고, 필터에 의존한 수집의 누락 가능성을 측정한다. → 10장에서 1차 측정.

## 10. 후속 검증 (2026-09-17 KST)

### 10.1 일일 트래픽 적용 단위

- 포털 서비스 상세 페이지는 "신청 가능 트래픽 개발계정 : 1,000"을 OpenAPI 서비스 단위로 표기한다. 참고자료 오류코드 22 설명에 "서비스 상세기능별 일일 트래픽량" 문구가 있으나 오퍼레이션별 독립 할당의 실측 근거는 없다.
- 판정: 확인 전까지 **서비스 단위 합산**으로 관리한다(DOCUMENTED 해석, LIVE_VERIFIED 아님). 2026-09-17 입찰공고정보서비스 800회 사용 동안 오류 22는 발생하지 않았다. 이는 1,000회 한도 안이라 적용 단위를 판별하는 증거가 아니다.

### 10.2 업종코드 직접 검색조건

- `getBidPblancListInfoCnstwkPPSSrch` 명세: `indstrytyCd`(업종코드), `indstrytyNm`(업종명) 요청변수 있음. 면허코드·제한업종코드 전용 변수 없음. `indstrytyCd`의 매칭 대상 필드 설명 없음.
- 실응답: `indstrytyCd=4992` 요청이 정상(resultCode 00) 응답했고, 표본 시간창에서 업종명 `도장` 필터와 같은 공고 집합을 돌려줬다.

### 10.3 후보 필터 recall 1차 측정 (recall-20260917T085352-603f0772)

- 설계: 2023~2026 연도 층마다 시드 고정 무작위 2시간 창 9개. 창마다 필터 없음(ALL)·`indstrytyNm=도장`·`indstrytyCd=4992`를 전체 페이지까지 조회하고, ALL 공고 전부의 면허제한(최신 차수)으로 4992 보유 여부를 판정했다.
- 호출: 입찰공고정보서비스 800회(목록 111, 면허제한 689). 서비스 일일 예산 소진으로 중단, 면허 판정 106건 대기(2026년 층).
- 결과: 판정 완료 689건 중 양성 54건(2023 17, 2024 21, 2025 7, 2026 9). 두 필터 모두 recall 54/54 = 1.0(Wilson 95% 0.9336~1.0), 정밀도 1.0, 결과 집합 동일.
- 판정: **미입증.** 기준(양성 ≥ 100, recall 하한 ≥ 0.95)에 미달한다. 백필 결과를 "전체 모집단"이라고 부르지 않고, `backfill run`은 게이트로 막았다.
- 한계: 정답은 면허제한 API 응답 기준(필드 밀림 의심 2행 포함)이며 공고문 원문과 대조하지 않았다. 2시간 창 표본이라 층별 추정은 불안정하다.
- 상세 JSON: `.local/real/reports/recall-filter-recall-4992-20260917T091118.json`(Git 제외).

### 10.4 recall 2차 측정과 게이트 판정 (2026-09-18 KST)

- 호출: 입찰공고정보서비스 800회 — 1차 남은 면허 판정 106(recall-20260918T000719-0ec669d2), 2차 목록 149 + 면허 545(recall-20260918T000913-10b18cea, 한도 소진으로 판정 339건 대기). 오류 없음.
- 1차 최종: 판정 795/795, 양성 63건, 두 필터 recall 63/63(Wilson 95% 0.9425~1.0).
- 2차(시드 20260918, 층마다 2시간 창 12개): 판정 545건 중 양성 52건(2023 1, 2024 35, 2025 16), 두 필터 recall 52/52(0.9312~1.0).
- 합산(연구 간 중복 양성 1건 제거): 양성 114, `indstrytyCd=4992` 찾음 114, 놓침 0, **recall 1.0(Wilson 95% 0.9674~1.0)**. 판정 끝난 필터 결과의 정밀도 1.0. 업종명 필터와 결과 집합 동일.
- 판정: **게이트 통과**(기준 양성 ≥ 100, 하한 ≥ 0.95). 표본 시간창 추정이며 층별 양성(2023·2026 각 18건)으로는 층별 누락률을 입증하지 못한다. 백필 결과는 "필터 기반 수집 집합"으로 표시한다.
- 누락 사례: 없음(조사 대상 0건).

## 2026-10-07 P1 재검증 (P1~P4 연속 진행 요청)

- 완료: `.venv/Scripts/python -m bidloc probe --live --day 20261006 --page-sizes 100,999 --max-calls 20` → 8회, 모두 resultCode 00 / SUCCESS, 복합키 누락 0. LICENSE와 REGION에서 999행 반환 LIVE_VERIFIED. LIST 641행 / OPENING 368행(전체 건수가 999 미만).
- 완료: `.venv/Scripts/python -m pytest -q` → 191 passed, 2 skipped (32.70초).
- 실패: 없음.
- 미검증: 하루 표본이므로 전 기간 필드 품질·완전성은 이 결과만으로 보장하지 않음. 통합 테스트 2개는 --live 미지정으로 SKIPPED. 실응답은 별도 probe로 검증.
- 근거: `.local/real/reports/probe-20261007T232438-4131ebdb.json`, 마스킹된 source_response 및 원본.
- 다음 조치: P2 수집기 품질 방어·강제 중단/재개 검증, P3 전체 구간 점검, P4 분석/로컬 화면 구현을 연속 수행.

## 2026-10-07 P2 완료

- 완료: collector.yaml 기반 `sweep init/run/status/finalize/extend` CLI, 서비스별 최대 800회 강제, 실행 상한/일일 한도 구분, OS 수집 잠금, 페이지 반복·복합키·응답 페이지 검사, 차수별 관련성·밀린 필드의 UNKNOWN 처리, recollect 초기화 수정. 기존 DB에는 0007 마이그레이션을 추가했다(기존 0001~0006 보존).
- 완료: `.venv/Scripts/python -m pytest -q` → 204 passed, 2 skipped (39.85초).
- 완료(LIVE): `.venv/Scripts/python -u .local/p1_p4/verify_resume.py`가 `sweep run --live --max-calls 20 --job-name p2-validation-20261007 --begin 2026-09-14 --end 2026-09-16`을 두 번 실행. 1차 7회 후 자체 자식 PID 19184 강제 종료, LICENSE 999/1746행·next_page=2 커밋 확인. 2차 첫 요청은 같은 구간의 pageNo=2, 11회로 12개 파티션 DONE.
- 실패: 의도한 강제 종료는 ABORTED로 기록. 비정상 테스트 실패 없음.
- 미검증: 기존 3년 수집 당시에는 새 페이지 지문 검사가 없었으므로 역사적 전체 페이지 반복 여부를 소급 보증하지 않는다. 전체 재호출 대신 P3 기존 원본/파티션 검사를 진행.
- 근거: `.local/p1_p4/resume-evidence.json`, resume-first.log, resume-second.log. 호출수에는 재시도 포함. 예약과 실제 기록의 큰 값을 예산으로 사용한다.
- 다음 조치: P3 전체 구간 검사·관련성 확정 후 P4 분석/화면 생성.

### P3/P4 명령과 화면 검증 상세

| 실행 | 관측 |
|---|---|
| `.venv/Scripts/python -m bidloc sweep finalize` | 공사 최신 차수 기준 상태 확정, 네트워크 0 |
| `.venv/Scripts/python -m bidloc init-db` | 0008 분석 인덱스 적용, 기존 0001~0007 보존 |
| `.venv/Scripts/python -m bidloc sweep status` | 본 job의 각 단계 1,113 DONE, 남은 호출 추정 0. 누락 import 수정 후 성공 |
| 읽기 전용 `PRAGMA quick_check`, `PRAGMA foreign_key_check` | ok / 위반 0. 전체 DB I/O 검사 완료 |
| `.venv/Scripts/python tools/report_progress.py` | HTML + 분석 A/B/shortlist CSV·JSON 생성. 이후 동일 스냅샷을 render_snapshot으로 재출력해 정규화·부분 연도·CSV 숫자 열 반영 |
| `analyze location --hq '경기도 남양주시' --from 2026-10-06 --to 2026-10-06` | 분리된 cli_validation 출력 폴더에 1행, API 0 |
| `analyze unitprice --from 2026-10-06 --to 2026-10-06` | cli_validation에 분류/연도 1행, API 0 |
| `shortlist --hq '경기도 남양주시' --from 2026-10-06 --to 2026-10-06` | cli_validation에 지역 일치 후보 1행, API 0 |
| `node --check src/bidloc/report_assets/dashboard.js` | 구문 검사 PASS |
| Playwright `qa.cjs` → 합성 UI, 실제 UI, 고정 스냅샷 UI | 최종 모두 PASS. 최초 초기화 오류 수정 후 같은 동작 재실행 |

환경: `file:///D:/bid_location_starter/bid-location-lab/.local/real/reports/p4_20261008/progress.html`, Chromium 147.0.7727.15, Playwright core 1.63.0. Browser plugin not available; 기존 Chromium 실행 파일을 사용했으며 새 브라우저를 설치하지 않았다. 검증용 Node 패키지만 `.local/p1_p4/browser`에 설치했다.

| 화면 검사 | 결과 |
|---|---|
| URL·페이지 제목 / 빈 화면 / 프레임워크 오류 화면 | PASS |
| 지역 검색 → 결과 없음 → 검색 해제 | PASS |
| 합계 오름차순·내림차순 → aria-sort 변화 | PASS |
| 지역 선택 → 근거 공고 → 개별 공고 복합키·응답 ID | PASS |
| 본점 수원시·신규 법인 적용 → 조건·후보 변화 | PASS |
| 최소 추정가격 적용 → 빈 결과 → 초기화 → 원래 표본 복원 | PASS |
| shortlist 미기재 유형·공고 검색 | PASS |
| CSV 다운로드·UTF-8 BOM | PASS |
| PC 1440×1100 / 모바일 375×900, 라이트·다크 | PASS, 모바일 문서 가로 넘침 없음 |
| 앱 콘솔 오류 / 외부 네트워크 요청 | 0 / 0 |

검증 스크립트와 스크린샷은 `%TEMP%/bidloc-p4-qa/`에 두었다. 최종 PC/모바일 라이트·다크 및 모바일 지역 표 스크린샷은 `final/` 하위에 있다. 실제 휴대폰 하드웨어와 다른 브라우저는 SKIPPED다. UI에 들어간 숫자는 DB 관측치이며 합성 검증 자료는 `.local/p1_p4/synthetic_ui/`에 분리했다.

추가 시크릿 검사는 처음에 source_response.raw_path를 작업 루트 기준으로 읽어 26개 파일이 미검사였다. RAW_RESPONSE_DIR 기준의 상대경로임을 확인해 26개를 다시 검사했고 최종 301개 파일 / 키 변형 검출 0 / 미검사 0이다. 미검사 상태를 통과로 보고하지 않았다.

변경 전 85개 파일을 `.local/p1_p4/before/`에 보존했다. `changed_files.json`, `changes.patch`로 검토할 수 있다. DB·원본·키·내보내기는 코드 변경 패치에 포함하지 않는다. 스크린샷에서 비슷하게 보였던 삼척시 도계읍 황조리/흥전리는 별개 지역임을 원문으로 확인했으며 합치지 않았다.
