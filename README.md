# 면허 입지 분석기

## 실행형 앱 — 2026-10-08

사용자가 선택한 [목업](docs/ui/README.md)을 기준으로 만든 **Streamlit 앱**이다. 저장된 SQLite 실데이터에서 공고 검색·조건 필터·공고 상세·CSV/JSON 다운로드·지역 비교·단가 분석·검토 대기·수집 품질 화면을 제공한다. 아래의 P4 HTML은 이전 파일 기반 보고서이며 현재 앱 실행 방식과 다르다.

```powershell
# 최초 설치 / 의존성 갱신
py -3.11 -m venv .venv
.venv/Scripts/python -m pip install -r requirements-lock.txt
.venv/Scripts/python -m pip install -e .
# 앱 실행
.venv/Scripts/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

또는 `./start-app.ps1`을 실행하고 **http://127.0.0.1:8501**에 접속한다. Python 3.11.9 / Streamlit 1.64.0 / pandas 3.0.6 / SQLite 3.45.1에서 검증한다. 세부 시각 조정은 후속 작업이다.

앱은 `.env`의 `DATABASE_PATH`를 읽는다. 기본값은 `.local/real/bidloc.sqlite3`이며, 저장 데이터가 없으면 **미수집**을 표시한다. 초기 화면의 기간은 저장된 작업 종료 연도의 1월 1일부터 작업 종료일까지다. 범위를 바꾸고 **검색**을 눌러 적용한다. 현재 검색은 **4992 관련 공고와 단가 분석 후보**를 대상으로 하며 나라장터 전체 공고 검색이라고 주장하지 않는다. 회사 프로필과 본점은 분석 가정이며 실제 자격 확정이 아니다.

첫 조회는 DB에서 일관된 스냅샷을 만든다. 이후에는 메모리와 DB 옆 `ui-cache/`의 압축 JSON을 재사용한다. DB·WAL의 파일 상태 및 분석 코드가 바뀌면 디스크 캐시는 무효화한다. **조회 새로고침**으로 현재 DB 상태를 다시 확인한다. 화면 하단의 조회 시점과 스냅샷을 확인한다. UI rerun·검색·다운로드는 수집/API 호출을 시작하지 않는다. 캐시는 실데이터이므로 DB와 함께 `.local/` 아래에 유지한다.

**API 연동 메뉴는 후속 HTTP API의 설계 화면이다.** endpoint·앱 인증·원격 연결은 아직 구현하지 않았다. UI와 분리된 `query_service.py`를 추후 HTTP 어댑터에서 재사용한다. 공개 서비스 배포는 수행하지 않았다.

GitHub: [SmartWay-jhyeo/bid-location-lab](https://github.com/SmartWay-jhyeo/bid-location-lab) (비공개). `.env`, DB, 원본 응답, 캐시, 실데이터 export와 로그는 업로드하지 않는다. 목업 이미지의 공고·수치는 합성 예시다. 과거 단계 기록은 아래에 보존한다.

## 2026-10-08 — P1~P4 실행 안내

현재 기능은 `docs/CODEX_PROMPT.md`의 단계 구분을 따른다. 검증한 로컬 분석 UI는 **`.local/real/reports/p4_20261008/progress.html`을 브라우저로 열면 된다.** 기본 갱신 파일은 `.local/real/reports/progress.html`이다. 입지 비교, 단가계약, 마감 전 후보 공고, 수집·품질을 한 화면에서 확인한다. 기본 본점은 경기도 남양주시이며 화면에서 변경할 수 있다. 공개 배포·스케줄러 등록은 수행하지 않았다.

실데이터 범위는 저장된 본 작업의 **2023-09-20~2026-10-06**이다. 4단계 × 1,113구간이 DONE이고 수신 행수/totalCount 불일치는 0건이다. P1은 8회 실응답으로 재검증했고 P2는 강제 종료 후 pageNo=2 재개를 확인했다. 확정된 공사 RELEVANT 34,339건에서 취소·연결 재공고·기간을 적용한 분석 대상은 34,111건이다. 상세 기준은 [ANALYSIS.md](docs/ANALYSIS.md), 실행 근거는 [VALIDATION_REPORT.md](docs/VALIDATION_REPORT.md)에 있다.

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$OutputEncoding = [System.Text.UTF8Encoding]::new()
.venv/Scripts/python -m bidloc init-db
.venv/Scripts/python -m bidloc sweep status
.venv/Scripts/python -m bidloc analyze location --hq '경기도 남양주시'
.venv/Scripts/python -m bidloc analyze unitprice
.venv/Scripts/python -m bidloc shortlist --hq '경기도 남양주시' --license 4992
.venv/Scripts/python tools/report_progress.py
.venv/Scripts/python -m pytest -q
```

분석 CLI의 선택 옵션은 `--from YYYY-MM-DD --to YYYY-MM-DD --contract 계약방식 --min-amount 원 --max-amount 원 --profile '기존 법인 면허 추가|신규 법인|본점 이전'`이다. 프로필별 전체 참가자격은 UNKNOWN이다. 현재 검증된 분석 면허는 4992이며 다른 코드는 명확히 거부한다.

`config/collector.yaml`의 수집 설정을 쓰는 `sweep init/run/status/finalize/extend`와 기존 `backfill sweep-*`를 함께 지원한다. 저장된 job 기간과 페이지 크기를 재사용하며, 같은 DB의 수집 잠금은 강제 종료 시 OS가 해제한다. 실제 수집은 기존 환경의 허용과 CLI `--live`가 모두 필요하다. 예: `.venv/Scripts/python -m bidloc sweep run --live --max-calls 20`. P1 소량 확인 명령은 `.venv/Scripts/python -m bidloc probe --live --day 20261006 --page-sizes 100,999 --max-calls 20`이다. 일일 서비스 합산 상한 800회와 실패·재시도·미완결 예약을 모두 반영한다.

CSV/JSON은 `.local/real/reports/`에 생성된다. `location.*`, `unitprice.*`, `shortlist.*`, 근거 `*_notices.*`, 지자체 연간 합계 `unitprice_annual.csv`, 기관 표본 `agency_samples.json`을 사용한다. CSV는 UTF-8 BOM과 수식 주입 방어를 적용한다. HTML은 약 33 MB의 데이터 포함 파일이며 외부 통신 없이 동작한다. 보고서 생성은 DB I/O 때문에 시간이 걸릴 수 있다.

검증 환경은 Windows, Python 3.11.9, SQLite 3.45.1이다. 최종 오프라인 테스트는 **232 passed / 2 skipped**이고, 실연동 통합 테스트 2개는 `--live` 미지정으로 건너뛰었다(별도 probe/수집기 실호출로 P1/P2 검증). UI는 임시 검증용 Playwright core **1.63.0**, 설치되어 있던 Chromium **147.0.7727.15**로 PC 1440×1100 / 모바일 375×900, 라이트·다크, 필터·검색·정렬·다운로드·상세 보기를 검사했다. Browser plugin not available 때문에 Playwright를 사용했다. 검증 도구는 `.local/p1_p4/browser`에만 설치했고 앱 Python 의존성은 바꾸지 않았다.

화면은 지역·면허에 맞는 후보를 보여주며 실제 참가자격/낙찰 가능성을 확정하지 않는다. 데이터 시점 이후의 정정·취소는 원공고를 확인한다. 신규 자동수집(P5)과 스케줄러 등록은 이번 P1~P4 범위에 포함하지 않았다.

---

아래는 P0 및 이전 작업의 보존 이력이다. 과거의 단계 상태·마이그레이션 수·호출 수를 현재 상태로 해석하지 않는다.

> **2026-10-07 P0 재검증:** 현재 작업 단계는 `docs/CODEX_PROMPT.md` 13장의 구분을 따른다. 이 문서의 P0는 **오프라인 기반 구현, API 호출 0회**다. 이전 `PROJECT_SPEC.md`의 P0(실연동 포함)와 구분한다. 기존 코드·데이터를 보존하며 P0를 보완했다. 최신 실행 근거는 `docs/STATUS.md`, `docs/VALIDATION_REPORT.md`의 2026-10-07 절을 확인한다. 아래 2026-09 실연동 기록은 과거 이력이며 이번에 재호출해 확인한 결과가 아니다.

## CODEX_PROMPT P0 실행 환경과 명령 (2026-10-07)

Windows PowerShell, Python **3.11.9**, SQLite **3.45.1**에서 실행했다. 패키지는 httpx 0.28.1, PyYAML 6.0.3, defusedxml 0.7.1, python-dotenv 1.2.3, pytest 9.1.1이다. 시스템 `python`에는 pytest가 없어 기존 프로젝트 가상환경을 사용했다. 이번에 패키지를 새로 설치하지 않았다.

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$OutputEncoding = [System.Text.UTF8Encoding]::new()
.venv/Scripts/python -m bidloc doctor
.venv/Scripts/python -m pytest -q
```

오프라인 테스트 결과: **182 passed, 2 skipped**. doctor 결과: **FAIL 0 / BLOCKED 0 / WARN 1**, 9,970개 파일에서 현재 인증키 변형 미검출. 경고는 과거 카탈로그의 PARTIAL_LIVE_VERIFIED 상태다. 단위테스트는 소켓·DNS 접근을 차단하고 합성 데이터만 사용한다. 실연동 테스트는 `--live`가 없으면 환경변수 값과 관계없이 SKIPPED다. 실제 실행에는 `--live`, `BIDLOC_RUN_LIVE_TESTS=1`, `ALLOW_LIVE_API=true`, 인증키, `DATA_MODE=real`이 모두 필요하다.

`doctor` 기본 실행에는 네트워크가 없다. Python·환경설정·수집 설정·디스크 여유·DB 마이그레이션 이력·시크릿 노출을 점검한다. 저장 볼륨당 여유 20 GiB는 내부 참고선이며 부족하면 WARN이다. DB는 읽기 전용으로 점검한다. 현재 설정된 키의 원문·인코딩 변형을 소스, 문서, 설정, 테스트, 원본, DB, 로그, 내보내기에서 청크 단위로 검사하며 큰 파일도 제외하지 않는다. `.env` 계열(설정 양식 제외), 가상환경·Git·도구 캐시는 검사 제외다. 읽기 실패·링크로 미검사한 파일이 있으면 WARN으로 표시하므로 전체 통과로 오인하지 않는다. 키 미설정 시 시크릿 검사는 SKIPPED다.

새 `config/collector.yaml`은 3년·하루 구간·999행·4단계·재시도/재시작 최대 3회·서비스별 800회 한도를 담고, P0에서 `doctor`로 형식을 검증한다. 코드 4992는 요청된 분석 설정값이며 이번 실행의 실응답 검증 주장이 아니다. 기존 작업의 `config/backfill.yaml`과 수집 커서는 보존했다. 새 수집 CLI와 설정 연결은 P2에서 검증한다.

마이그레이션 CLI는 `.local/p0_revalidation/synthetic/bidloc.sqlite3`에 6개 적용 후 재실행 시 추가 적용 0개를 확인했다. 운영 데이터가 있는 DB에는 `init-db`를 실행하지 않았다. 현재 폴더는 Git 저장소가 아니므로 추적 여부를 검증하지 못했으며 `.gitignore`의 `.env`, `.local/` 제외 규칙만 확인했다. 변경 전 파일은 `.local/p0_revalidation/before/`에 보관했다.

> **2026-09-16 갱신:** P0(API 계약·연결 검증) 코드가 추가되었다. 설치·실행·테스트 방법은 이 문서 아래쪽의 [P0 설치·실행·테스트](#p0-설치실행테스트) 절에 있다.
> 2026-09-16 인증키로 P0 실연동 검증을 했다(실호출 75회, 핵심 오퍼레이션 10개 LIVE_VERIFIED). 현재 상태는 `docs/STATUS.md`, 근거는 `docs/VALIDATION_REPORT.md`에 있다.
> 아래 원래 안내문은 착수 패키지 설명으로 그대로 둔다.

이 패키지는 **기획서·개발 프롬프트·에이전트 지침·설정 양식**이다. 완성된 프로그램이나 실제 지역별 분석 결과가 아니다. 실제 API 인증키 호출도 아직 수행하지 않았다.

## 사용할 순서

새 작업 폴더에 압축을 풀고 `bid-location-lab` 폴더를 연다. 기존 프로젝트에 추가할 때는 AGENTS.md·CLAUDE.md·.gitignore를 무조건 덮어쓰지 말고 기존 규칙과 병합한다.

Claude Code 또는 Codex를 이 폴더에서 실행한 뒤 `START_PROMPT.md` 내용을 전달한다. 첫 작업은 P0(API 계약·연결 검증)다. 코딩 에이전트는 프로젝트 규칙과 설계서를 읽고 실제 검증 도구를 구현해야 한다.

API 키가 없어도 명세 확인·설정검사·클라이언트·단위테스트를 만들 수 있다. 실제 데이터 확인은 공공데이터포털에서 필요한 서비스 활용신청을 하고 로컬 `.env`에 키를 설정한 뒤 수행한다. 사용자정보 서비스는 후순위이며 초기 참여수·공고 비교를 반드시 막을 필요는 없다. 주력분야와 보유계획은 임의로 실제 값처럼 채우지 않는다.

`.env.example`을 `.env`로 복사해 로컬 편집기로 입력한다. 실제 호출을 원할 때 `ALLOW_LIVE_API=true`로 바꾼다. 코드가 준비된 후 CLI에서도 `--live`를 지정해야 한다. 키를 채팅·Git·스크린샷에 넣지 않는다.

처음 호출예산은 내부 안전설정 100회/실행 및 100회/일이다. 제공기관의 승인 쿼터가 아니며, 실제 승인량과 수집계획을 확인한 뒤 조정한다. 숫자를 늘렸다는 이유로 제공기관의 쿼터가 늘어나지 않는다.

P0 보고서에서 실제 검증/미검증 범위를 확인한 뒤 START_PROMPT.md 하단의 P1, P2 후속 지시를 차례로 전달한다. 한 세션에서 뼈대와 화면을 급히 완성하고 전국 분석까지 된 것으로 판단하지 않는다.

## 파일 안내

| 파일 | 용도 |
|---|---|
| PROJECT_SPEC.md | 목적, 범위, 데이터 모델, 지표, UI, 단계별 인수 기준 |
| START_PROMPT.md | Claude Code·Codex 공통 시작 지시 및 다음 단계 프롬프트 |
| AGENTS.md | API·자격·경쟁수·보안 관련 공통 작업 규칙 |
| CLAUDE.md | AGENTS.md를 불러오는 Claude Code 지침 |
| .env.example | 인증키와 로컬 실행 설정 양식 |
| .gitignore | 키·원본·DB·로그·실데이터 내보내기 제외 |
| docs/SOURCES.md | 공식 API 및 공고 사례, 확인/미확인 범위 |
| docs/VALIDATION_PLAN.md | 단위·통합·데이터품질 테스트 요구사항 |

## 이번 설계에서 중요한 구분

면적당 업체 수 대신 동일조건 공고의 기회와 실제 경쟁을 비교한다. 시·도와 세부지역·복수지역을 구분한다. 통합업종과 주력분야, 신규 면허와 신규 법인, 참가조건과 낙찰심사, 기초금액과 계약금액을 섞지 않는다. 미조회는 0이 아니고, 데이터 부족은 경쟁이 낮다는 뜻이 아니다.

지역 순위·수주확률·기대매출을 미리 넣지 않는다. 자료가 쌓인 후에도 근거 공고와 결측 비율을 보여주는 비교도구로 사용한다.

## P0 설치·실행·테스트

이 절의 명령은 2026-09-16 Windows 11 + Python 3.11.9 환경에서 실제로 실행해 확인했다. 프로젝트 기준 Python 3.12에서는 아직 실행하지 않았다.

### 설치 (확인됨)

PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

Git Bash·Linux 셸에서는 `.venv/Scripts/python`(Windows) 또는 `.venv/bin/python`(Linux)을 쓴다. Linux 실행은 검증하지 않았다. 고정 버전 전체 목록은 `requirements-lock.txt`에 있다.

### 명령 (구현·실행 확인됨)

| 명령 | 하는 일 | 네트워크 |
|---|---|---|
| `.venv\Scripts\python -m bidloc doctor` | 설정·키 설정 여부(값 비표시)·실호출 조건·DB·카탈로그·Git 제외 규칙·TLS 컨텍스트 점검 | 없음 |
| `.venv\Scripts\python -m bidloc doctor --network` | 위 점검 + `apis.data.go.kr` DNS 조회와 TLS 핸드셰이크 | HTTP 요청·키 전송 없음 |
| `.venv\Scripts\python -m bidloc init-db` | SQLite DB 생성과 마이그레이션 적용 | 없음 |
| `.venv\Scripts\python -m bidloc catalog-check` | `config/api_catalog.yaml` 형식·상태 요약 | 없음 |
| `.venv\Scripts\python -m bidloc verify-api` | 표본 검증 계획만 표시(SKIPPED 기록) | 없음 |
| `.venv\Scripts\python -m bidloc verify-api --live` | 호출예산 안에서 표본 실호출 검증 | 실제 API 호출 |

`verify-api --live`는 `.env`(또는 환경변수)의 `ALLOW_LIVE_API=true`, `DATA_GO_KR_SERVICE_KEY`, `DATA_MODE=real`이 모두 있어야 실행된다. 하나라도 없으면 네트워크 호출 없이 BLOCKED(종료코드 2)로 끝난다. 2026-09-16 키 설정 후 `--max-calls`와 `--resume`으로 8·10·24·33회씩 나눠 실행해 확인했다.

옵션: `--max-calls N`(실행 예산을 더 낮춤), `--fixed-only`(탐색 조회 생략), `--resume <run_id>`(이전 실행의 완료 단계 재사용), `--plan <yaml>`.
종료코드: 0 완료·dry-run, 1 실패, 2 BLOCKED, 3 PARTIAL(예산·일일한도·서비스 차단으로 일부 단계 미실행).

### 실연동 검증 준비 (사용자 작업)

1. 공공데이터포털에서 입찰공고정보(15129394), 낙찰정보(15129397), 업종 및 근거법규(15129467) 서비스를 활용신청한다.
2. `.env.example`을 `.env`로 복사하고 로컬 편집기로 `DATA_GO_KR_SERVICE_KEY`에 Decoding 키를 넣는다. Encoding 키라면 `DATA_GO_KR_SERVICE_KEY_FORMAT=encoded`로 둔다.
3. 실호출을 허용할 때 `ALLOW_LIVE_API=true`로 바꾼다.
4. `doctor --network`로 점검한 뒤 `verify-api --live`를 실행한다. 결과 보고서는 `.local/real/reports/`(Git 제외)에 생긴다.

### 테스트 (확인됨)

```powershell
.venv\Scripts\python -m pytest
```

결과(2026-09-16): 150 passed, 2 skipped. 단위테스트는 소켓 연결을 차단한 상태로 돌며 합성 데이터만 쓴다. 실연동 통합테스트는 다음 조건에서만 실행되고, 그 외에는 SKIPPED다.

```powershell
$env:BIDLOC_RUN_LIVE_TESTS = "1"
.venv\Scripts\python -m pytest tests/integration --live -rs
```

### 공식 명세 카탈로그 재생성 (확인됨)

```powershell
.venv\Scripts\python tools\build_api_catalog.py --spec-dir .local\official_specs\2026-09-16 --checked-on 2026-09-16 --live-status BLOCKED --live-reason "<사유>" --out config\api_catalog.yaml
```

입력 파일(포털 페이지 HTML 4개, 참고자료 docx 4개)은 `.local/official_specs/2026-09-16/`에 있고 Git에서 제외된다.

### 제안 단계 명령 (아직 없음)

`plan`, `collect`, `analyze`, `export`, Streamlit `app.py`는 P1·P2에서 구현할 인터페이스다. 현재 실행되지 않는다.

### P0에서 추가한 파일

| 경로 | 용도 |
|---|---|
| `pyproject.toml`, `requirements-lock.txt` | 패키지 정의와 고정 버전 |
| `src/bidloc/` | 설정, 마스킹, 카탈로그, 클라이언트, 저장소, 정규화 도우미, verify 실행기, doctor, CLI |
| `migrations/0001_p0_core.sql` | 실행·예산·원본 메타데이터·검증 관측 테이블 |
| `config/api_catalog.yaml` | 공식 문서 기반 API 계약(기계 판독) |
| `config/verify_samples.yaml` | 실연동 표본 계획 |
| `tools/build_api_catalog.py` | 공식 문서 → 카탈로그 생성 도구(오프라인) |
| `tests/` | 단위테스트(오프라인), 실연동 통합테스트(기본 SKIPPED), 합성 fixture |
| `docs/API_CONTRACT.md`, `docs/DATA_DICTIONARY.md`, `docs/VALIDATION_REPORT.md`, `docs/STATUS.md` | 계약, 데이터 사전, 검증 보고서, 진행 상태 |

## 3개년 백필 (P1 수집)

설명과 재개 규칙은 `docs/BACKFILL.md`에 있다. 2026-09-16 소량 실제 실행 2회로 수집·재개를 확인했다.

```powershell
.venv\Scripts\python -m bidloc backfill init
.venv\Scripts\python -m bidloc backfill run --live
.venv\Scripts\python -m bidloc backfill status
.venv\Scripts\python -m bidloc backfill stats
```

일일 한도는 OpenAPI 서비스 단위 합산이다(`.env`의 `BACKFILL_MAX_CALLS_PER_SERVICE_PER_DAY`, 기본 800). 후보 필터 recall 검증은 `backfill recall-check --live` / `backfill recall-report`로 한다. `run`을 다시 실행하면 마지막 파티션 페이지와 작업 큐부터 이어간다.
