# 면허 입지 분석기

추가 조달 API 수집(2026-10-08): K-apt·LH·K-water 세 곳 모두 새 공통 키로 실제 응답과 조회 범위 내 페이지 수집을 확인했다. 나라장터는 기존 `DATA_GO_KR_SERVICE_KEY`, 추가 세 곳은 `PROVIDER_DATA_GO_KR_SERVICE_KEY`를 사용한다. 키가 Encoding 값이면 `PROVIDER_DATA_GO_KR_SERVICE_KEY_FORMAT=encoded`, Decoding 값이면 `decoded`로 설정한다. 키는 채팅·Git에 올리지 않는다. [검증 결과와 범위](docs/PROVIDER_API_VALIDATION.md)를 참조한다. 한전은 별도 인증·어댑터 준비 전이다.

추가 소스 수집 명령: `.venv/Scripts/python -m bidloc.provider_collect --live --begin 2026-10-01 --end 2026-10-08 --rows 100 --max-calls 16` (`ALLOW_LIVE_API=true`도 필요). 원본은 기존 raw 저장소, 응답 항목과 페이지 체크포인트는 `.local/real/providers/notices.sqlite3`에 저장한다. 같은 조건 재실행은 이어받기이며 완료된 범위는 다시 호출하지 않는다. 다른 날짜 범위는 별도 스냅샷으로 저장하므로 범위 간 행을 단순 합산하지 않는다. 기본 추가 서비스 일예산 20회와 실행 예산을 함께 지킨다. K-water는 월 단위 조회여서 월을 넘는 범위는 거부한다.

매일 자동수집(2026-10-08 연결, 10-09 순서 조정): 예약 작업 `bidloc-backfill-daily`(매일 00:12)의 실행 순서는 다음과 같다.
1. 나라장터 범위 확장
2. `.venv/Scripts/python -m bidloc.provider_collect --live --daily --max-calls 40`
3. 나라장터 스윕 → 확정
4. `.venv/Scripts/python -m bidloc.recommend --daily`(저장한 조건으로 오늘의 추천 목록 기록, 네트워크 없음)

추가 수집처를 긴 나라장터 스윕보다 먼저 받는 이유는, 스윕이 중간에 끊겨도(2026-10-09 00:12 사례) 추가 수집처는 받기 위해서다. 대상은 K-apt·LH·K-water·국방조달(D2B)이다. 최근 3일(K-water는 이번 달, 월초에는 지난달도)을 그날 날짜의 스냅샷으로 다시 읽는다. D2B는 공사 공고만 상세(위치·지역제한·면허제한)까지 받는다(`--max-details`, 기본 10건). 같은 날 다시 실행하면 이어받기다. 예약 스크립트는 Git 제외 `.local/scheduled/backfill_daily.ps1`이며, 수정 전 원본은 같은 폴더의 `backfill_daily.before-provider-20261008.ps1`이다. 외부 HTTP API는 아직 미연결이다.

출처 검증 probe(2026-10-08): `.venv/Scripts/python -m bidloc.provider_probe --live --providers <id> --begin YYYY-MM-DD --end YYYY-MM-DD --rows N --max-calls 2`. `bid_notice_reg`·`bid_notice_etc`는 나라장터 응답의 등록유형(연계 공고 여부)을 확인할 때 쓴다. `d2b`와 `pps_openstd`는 추가 서비스 키(`PROVIDER_DATA_GO_KR_SERVICE_KEY`)를 쓰며, 2026-10-08 활용신청 후 실응답을 확인했다. `--detail`은 d2b 목록 1페이지에서 공사 1건의 상세를 1회 더 호출한다. `--rows`는 1~999이고 provider마다 최대 2페이지를 조회한다. 기본 대상은 `kapt,lh,kwater` 그대로다. [검증 결과](docs/CLAUDE_REMAINING_PROVIDERS_FINDINGS.md).

**NAS 배포(2026-10-09 준비):** `Dockerfile`, `docker-compose.yml`, `deploy/daily.sh`(리눅스용 일일 수집), [배포 안내](docs/NAS_DEPLOY.md). 수집기는 메모리 3GB·CPU 2개 상한과 디스크 유휴 우선순위로 매일 00:12에 돈다. 화면·API는 `--profile web`으로 선택 실행한다.

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

**조건 검색(대시보드, 첫 화면).** 회사 조건(본점 소재지·면허 4992·현장 범위·출처)을 저장하면 그 조건의 마감 전 추천 공고를 보여준다.
- 요약 수치는 조건 일치 / 확인 필요 / 새 추천 / 마감 임박(3일)이다.
- 목록은 새 추천 / 마감 임박 / 전체 추천 탭으로 나뉘고, 공고 상세를 볼 수 있다.
- 조건은 `.local/real/recommend/profile.json`, 날마다의 추천 목록은 `.local/real/recommend/daily/YYYY-MM-DD.json`에 남는다(Git 제외).
- "새 추천"은 같은 조건의 전날 목록에 없던 공고다. 전날 목록이 없으면 최근 공고일(어제 이후)로 대신하고, 화면에 기준을 표시한다.
- 현장 범위는 참가지역 정보가 없는 공고(K-apt 등)에만 적용하는 추천 범위이며 자격 판정이 아니다.

공고 검색에는 **나라장터 / 추가 수집처** 탭이 있다. 추가 수집처 탭은 같은 조건(기간·검색어·업종 범위·본점 소재지·금액·계약방식·상태)으로 K-apt·LH·K-water·D2B의 공사 공고를 보여주며, 공고마다 **조건 일치 / 확인 필요 / 불충족**을 표시한다.
- D2B·LH는 제공되는 면허·참가지역으로 판정한다.
- K-apt·K-water는 이 정보를 주지 않아 "확인 필요"이고, 기본값에서는 방수·도장·도색·차선 등 제목 후보만 보인다.
- 참가지역, 현장 위치(K-apt 단지 소재 시·도, D2B `lc`), LH 담당 본부는 서로 다른 정보로 표시한다.
- 같은 공고의 여러 차수는 최신 차수 하나로 보이고, 취소공고는 "마감 전"에서 빠진다.
- 이 행들은 지역 비교·단가 분석 등 나라장터 분석에 섞지 않는다.

첫 조회는 DB에서 일관된 스냅샷을 만든다. 이후에는 메모리와 DB 옆 `ui-cache/`의 압축 JSON을 재사용한다. DB·WAL의 파일 상태 및 분석 코드가 바뀌면 디스크 캐시는 무효화한다. **조회 새로고침**으로 현재 DB 상태를 다시 확인한다. 화면 하단의 조회 시점과 스냅샷을 확인한다. UI rerun·검색·다운로드는 수집/API 호출을 시작하지 않는다. 캐시는 실데이터이므로 DB와 함께 `.local/` 아래에 유지한다.

**공고 추천 API (2026-10-08, 로컬).** `./start-api.ps1`(또는 `.venv/Scripts/python -m bidloc.api --port 8600`)로 실행한다. 회사 조건을 넣으면 마감 전 공고를 나라장터와 추가 수집처에서 함께 추천한다.

```http
GET http://127.0.0.1:8600/api/v1/recommendations?region=남양주&license=4992&limit=20
```

- **입력:** `region`은 "남양주 / 남양주시 / 경기도 남양주시"를 받고, 같은 이름이 여러 곳이면 후보를 400으로 돌려준다. `license`는 "4992"나 "도장습식방수"를 받는다(현재 4992만 검증).
- **선택 파라미터:** `sources`, `keyword`, `min_amount`/`max_amount`, `include_unknown`, `title_shortlist`, `site_provinces`(참가지역 정보가 없는 공고의 현장 시·도 범위, 기본 본점 시·도, `all` 가능), `sort`, `limit`/`offset`.
- **응답 순서:** "조건 일치"가 먼저, 그다음 "확인 필요"가 마감이 빠른 순으로 온다.
- **응답 내용:** 공고마다 면허·참가지역 판정과 근거, 참가지역과 현장 위치(별도 필드), 종류별 금액, 근거 응답 ID가 붙는다. 조건 때문에 빠진 공고는 `counts.held_back`에 사유별 건수로 남는다.
- **다른 엔드포인트:** `/api/v1/collection/status`, `/api/v1/health`.

서버는 읽기 전용이며 수집이나 외부 API 호출을 하지 않는다.
- 나라장터 스냅샷은 뒤에서 읽고 갱신한다. DB가 바뀌면 다시 읽는 데 수 분이 걸리며, 그동안은 직전 스냅샷으로 응답한다. 첫 로딩 중에는 `complete:false`와 경고를 붙이고 추가 수집처만 돌려준다.
- 기본으로 127.0.0.1에만 열린다. 다른 주소로 열려면 환경변수 `BIDLOC_API_TOKEN`이 필요하며, 그때 요청은 `Authorization: Bearer <token>`을 보낸다.
- 공개 배포는 수행하지 않았다(별도 승인 사항).
- 공고 상세·지역 비교 API는 아직 미구현이다.

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
