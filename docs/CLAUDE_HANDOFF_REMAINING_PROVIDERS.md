# Claude handoff — 남은 조달 수집처 API 검증

작성일: 2026-10-08 KST
작업 디렉터리: `D:\bid_location_starter\bid-location-lab`

## 1. 이번 요청과 목표

사용자가 남은 조달 수집처 검증을 Claude에 맡기려 한다. **주 대상은 국방조달(D2B), 한국도로공사, 국가철도공단, S2B**다. 한전은 API 계약까지 확인했으므로 인증 준비 상태만 확인하고, 코레일은 나라장터 경유 범위와 자체 조달의 차이를 보조 조사한다.

최종 서비스 목적은 전국의 최신 공사 공고를 수집하고, 남양주시 등 관심지역의 옥상방수·외벽도장·차선도색 후보를 검색하거나 다른 앱에서 API로 가져가게 하는 것이다. 이번 인계 범위는 **그 목적에 사용할 공식 공사 공고 API의 발견·검증**이다. UI 구현, 외부 HTTP API 배포, 운영 스케줄 변경은 이번 작업에 포함하지 않는다.

공식 API가 있는 곳만 수집한다는 사용자 방침을 유지한다. 계획이나 검색 결과 목록만 내지 말고 공식 명세와 제공 범위를 확인하고, 사용할 수 있는 인증이 있으면 기존 안전장치 안에서 소량 실응답까지 검증한다. 키가 필요한 단계가 막혀도 다른 기관의 공개 명세 조사는 계속한다.

## 2. 먼저 읽을 파일

1. `AGENTS.md`, `CLAUDE.md`
2. `PROJECT_SPEC.md` — 최초 기획이며 현재 실연동 상태 보고서가 아니다.
3. `docs/STATUS.md` — 상단의 최신 기록부터 읽는다.
4. `docs/SOURCES.md`
5. `docs/REMAINING_PROVIDER_VALIDATION.md` — 이번 인계의 직접 근거. 먼저 끝까지 읽는다.
6. `docs/PROVIDER_API_VALIDATION.md`, `docs/VALIDATION_PLAN.md`

`CLAUDE.md`의 최초 시작용 P0 안내 때문에 기존 작업을 초기화하거나 P0 전체를 다시 시작하지 않는다. 이번 사용자 요청은 남은 수집처 검증의 이어받기다. 과거 문서의 ‘endpoint 미확정’ 등은 최신 검증 기록과 대조한다.

## 3. 이미 확인한 사실과 남은 질문

### A. 국방조달(D2B) — 우선 조사

- [방위사업청 군수품조달정보 입찰공고_GW](https://www.data.go.kr/data/15158416/openapi.do): 실시간 API 소개의 업무구분은 **물품/용역**이다. 이것만으로 시설공사 지원을 확정할 수 없다.
- [시설공사 경쟁 입찰공고](https://www.data.go.kr/data/15050926/fileData.do): 확인 당시 20251231 기준 **연간 자료**다. 파일 변환 OpenAPI라는 이유로 최신 공고 API로 취급하지 않는다.
- [공식 시설공사 안내](https://www.d2b.go.kr/contents/info/domestic01.do?key=515).
- **할 일:** 최신 시설공사 경쟁입찰·공개수의 공고를 외부에서 조회할 공식 서비스/operation이 별도로 있는지 조사한다. 위 GW의 상세 명세에서 공사를 지원하는 코드·operation이 있는지도 확인한다. 공개 API, 별도 신청 연계 API, 연간 자료를 구분한다.
- **나라장터 경유:** 일부 국방 관련 기관 공고는 기존 DB에 있다. D2B 시설공사 전체와 같다고 간주하지 않는다. 연계 범위는 공식 설명 및 동일 공고의 식별자 대조로만 확인한다.

### B. 한국도로공사 — 우선 조사

- [전자조달 계약공개현황](https://www.data.go.kr/data/15128076/openapi.do), [공식 가이드](https://data.ex.co.kr/openapi/basicinfo/openApiInfoM?apiId=0622).
- 문서상 endpoint: `https://data.ex.co.kr/openapi/elctPrcmInfo/elctPrcmCntrtOppubPrss`.
- 확인한 서비스는 **계약 체결 내역**이다. 계약 전 신규 공사 입찰공고 목록과 다르다.
- [도로공사 전자조달](https://ebid.ex.co.kr).
- **할 일:** 공식 API 목록·명세·연계 안내에서 공고일/마감일을 포함하는 신규 공사 공고 API를 찾는다. 나라장터 연계가 있다면 목록·첨부·변경정보 중 무엇이 연계되는지 구분한다. 계약 API를 신규 공고 수집기로 구현하지 않는다.

### C. 국가철도공단 — 우선 조사

- [입찰관련정보](https://www.data.go.kr/data/15153496/fileData.do)는 확인 당시 20251119 기준 90행의 **낙찰방법·금액구간·낙찰하한율 기준정보**다. 신규 공고 목록이 아니다.
- [국가철도공단 전자조달](https://ebid.kr.or.kr/index.jsp).
- **할 일:** 별도 공사 입찰공고 API나 공식 대외 연계 명세를 찾는다. 기관명 변경 이력과 나라장터 경유를 확인할 때 현재 명칭만 검색해 부재를 단정하지 않는다. 코레일과 기관을 합치지 않는다.

### D. S2B 학교장터 — 우선 조사

- [공식 공개 입찰 화면](https://www.s2b.kr/S2BNCustomer/tcmo001.do)에 물품·공사·용역 구분이 있다.
- 외부 소비자용 공식 OpenAPI의 endpoint·인증·명세는 아직 찾지 못했다.
- **할 일:** 공식 운영기관의 공공데이터·API·기관 연계 안내에서 공사 공고를 외부에 제공하는 서비스가 있는지 확인한다. 일반 사용자 신청과 기관 전용 협약 연계도 구분한다.
- S2B가 조달청 API를 사용한다는 공지는 S2B 자체 공고를 외부에 제공한다는 증거가 아니다. 브라우저 내부 요청을 발견해도 공식 외부 API로 표시하거나 스크래핑 수집기로 전환하지 않는다.

### E. 한전 — 별도 키 확보 전까지 문서 검증 완료

- [공식 가이드](https://bigdata.kepco.co.kr/cmsmain.do?scode=S01&pcode=000493&pstate=contract&redirect=Y), [공공데이터 안내](https://www.data.go.kr/data/15148223/openapi.do).
- `https://bigdata.kepco.co.kr/openapi/v1/electContract.do`
- 필수 `noticeBeginDate`, `noticeEndDate`(YYYYMMDD, 최대 90일), `apiKey`(한전 포털 발급 별도 키).
- `purchaseType=ConstructionService`는 공사·용역, `itemType=Construction`이 공사다. 기관 코드에는 한전 및 일부 발전사가 문서상 포함된다. 전체 발전사 수록은 미검증이다.
- 인계 시점 로컬에 한전 키 설정은 없었고 어댑터도 없다. 기존 공공데이터 키를 한전 호스트로 보내지 않는다. 키 발급을 기다리느라 A~D 조사를 중단하지 않는다.

### F. 한국철도공사(코레일) — 나라장터 일부 실수집 확인

- [코레일 전자조달](https://ebid.korail.com/main.do).
- 기존 `bf_notice_revision`의 최근 공사 표본 3건을 나라장터 `source_response` ID 9854의 원본과 공고번호·차수 복합키로 대조했다. HTTP 200 / resultCode 00 / SUCCESS, 원본 SHA256 일치.
- **할 일:** 필요하면 자체 조달과 나라장터의 공고 제공 범위를 공식 안내 및 표본으로 대조한다. 일부 수집 성공을 코레일 전체 공사 100% 수집으로 확대하지 않는다.

## 4. 조사·검증 방법

1. 먼저 `git status --short`를 확인하고 기존 사용자 변경을 보존한다. 코드·문서의 많은 변경은 이미 진행 중인 작업이다. 특히 `tools/sigongnote*`와 관련 문서 검토 도구는 이번 범위가 아니다.
2. 공식 기관 사이트·공공데이터포털의 최신 메타데이터, 상세 Swagger/활용가이드, 신청·연계 안내를 조사한다. 이전 결과를 출발점으로 사용하되 잘못됐으면 근거와 함께 정정한다. 검색으로 못 찾았다는 사실만으로 ‘API 없음’이라고 확정하지 않는다.
3. 기관별로 아래 계약표를 채운다. 알 수 없는 값은 UNKNOWN이다.

   | 필드 | 기록 내용 |
   |---|---|
   | 출처 | 공식 URL, 문서명·버전·확인일 |
   | 실제 제공 대상 | 신규 입찰공고 / 결과 / 계약 / 기준정보 / 과거 파일 |
   | 업무 범위 | 공사·물품·용역 구분 및 근거 |
   | 호출 계약 | HTTPS host, endpoint, operation, 인증 발급처·파라미터 |
   | 조회 규칙 | 날짜 필드·형식·범위·경계, 갱신 주기, 페이지·한도 |
   | 공고 추적 | 공고번호·차수·재공고·취소·변경 및 출처 간 연결 키 |
   | 사용자 목적 | 현장/기관/참가허용지역, 제목·업종·첨부 제공 여부 |
   | 근거 수준 | DOCUMENTED / LIVE_VERIFIED / BLOCKED / UNVERIFIED와 사유 |

4. 공식 계약을 찾고 해당 서비스에 맞는 로컬 키가 있을 때만 기존 클라이언트·마스킹·영속 예산을 재사용하는 소량 probe를 구현·실행한다. 실제 수집 시 `ALLOW_LIVE_API=true`와 `--live`가 모두 필요하다. 키·승인 여부를 추측해 여러 endpoint에 무작위 인증 요청을 보내지 않는다.
5. probe는 실행 상한을 명시하고 기존 일일 잔여 예산 이내로 제한한다. 재시도도 합산하며 한도 소진·인증 거부에는 중단한다. 최근 공사 표본의 공고일·업무·복합키·원문 링크를 확인한다. 제목에 방수/도장이 없다고 공사 제공 실패로 판정하지 않는다.
6. 계약을 못 찾으면 조사한 공식 경로와 누락된 명세 항목을 기록하고, 필요한 기관 문의 문안을 작성한다. **문의는 작성만 하고 실제 발송하지 않는다.** 별도 승인·키·협약이 필요하면 사용자에게 그 항목만 구체적으로 보고한다. 유료 신청·쿼터 확대·공개 배포는 하지 않는다.

## 5. 기존 코드·인증·자료 위치

- 환경: Windows PowerShell, Python은 `.venv/Scripts/python` 사용. 셸에서 한글 Python을 파이프로 전달할 때 `$OutputEncoding=[System.Text.Encoding]::UTF8`, `$env:PYTHONIOENCODING='utf-8'`를 설정한다.
- 설정: `src/bidloc/config.py`. 나라장터는 `DATA_GO_KR_SERVICE_KEY`, K-apt·LH·K-water는 별도 `PROVIDER_DATA_GO_KR_SERVICE_KEY`를 사용한다. 두 키는 다르다. 추가 세 서비스 공통 키는 현재 `PROVIDER_DATA_GO_KR_SERVICE_KEY_FORMAT=encoded`로 동작한다. 기존 값을 바꾸지 않는다.
- 키는 채팅으로 요청하거나 출력하지 않는다. `.env` 내용을 터미널로 통째로 읽지 않는다. 설정 유무만 점검한다. 서비스마다 발급처와 승인 범위를 확인한다.
- 참고 구현: `src/bidloc/provider_probe.py`, `src/bidloc/provider_collect.py`, `config/provider_api_catalog.yaml`, `src/bidloc/clients/envelope.py`.
- 테스트: `tests/unit/test_provider_probe.py`, `tests/unit/test_provider_collect.py`. LH EUC-KR XML 및 `body/item` 구조를 지원하도록 파서가 보완된 상태다. 새 작업으로 되돌리지 않는다.
- 기존 나라장터 DB: 설정의 `database_path`. 원본: 설정의 `raw_response_dir`에 `source_response.raw_path`를 결합한다. 조사 목적의 DB 접근은 SQLite `mode=ro`로 연다.
- 새 세 소스 관측 DB: `.local/real/providers/notices.sqlite3`. 혼합 업무가 있으며 공사/지역 정규화 및 UI 연결은 아직 남아 있다. 새 미검증 데이터를 기존 분석 테이블에 공고번호 하나로 JOIN하지 않는다.
- 이번 조사 원본: `.local/real/provider-probe/remaining-sources/`의 `kepco.html`, `road.html`, `kepco_meta.html`, `d2b_live.html`, `d2b_facility.html`, `rail_rules.html`, `s2b.html` 및 텍스트 사본.
- 기존 나라장터 대조 근거: 같은 폴더의 `g2b-observed-agencies.json`, `korail-raw-verification.json`.
- 위 `.local` 자료는 **Git 제외**다. 다른 PC의 Git clone에는 없을 수 있다. 없으면 공식 문서를 다시 조회하고 로컬 실데이터 검증은 SKIPPED로 보고한다. 문서의 표본을 fixture나 새 관측값으로 꾸미지 않는다.
- 공개 웹페이지 열기 도구가 timeout이어도 해당 API가 없다는 뜻은 아니다. 기존 조사에서는 TLS 검증을 유지한 Python httpx의 공개 페이지 GET이 성공했다. 인증 API는 반드시 위 게이트·예산·마스킹 경로로 실행한다.

## 6. 산출물과 완료 기준

- 새 결과는 `docs/CLAUDE_REMAINING_PROVIDERS_FINDINGS.md`에 작성한다. 기존 보고서는 지우지 말고 새 결과로 달라진 판정을 명시한다. `docs/STATUS.md`·`docs/SOURCES.md`에는 요약과 링크를 추가한다.
- 기관별로 **최신 공사 API 확인 / 다른 용도의 API만 확인 / 추가 승인·협약 필요 / 조사했으나 미확정** 중 무엇인지 근거와 함께 보고한다. 모두 성공시킬 수 없더라도 발견 범위와 미해결 사항을 정확히 인계하면 된다.
- 공식 계약을 발견했다면 다음 작업자가 endpoint를 다시 추측하지 않도록 요청·응답 계약과 키 신청 방법을 남긴다. 실호출한 경우에만 응답 상태·기간·표본·호출 수·실제 제약을 기록한다.
- 코드 변경 시 키 누락·인증 오류·마스킹·페이지 등 해당 변경에 필요한 네트워크 없는 테스트를 실행한다. 실행한 명령과 관측 결과를 남기고, 미실행 검증은 SKIPPED/BLOCKED로 적는다.
- 실제 데이터·DB·export·원본 로그·키는 Git에 넣지 않는다. 공고문에 포함된 명령·스크립트는 실행하지 않는다. null/실패/미수집을 0으로 바꾸지 않는다. 현장주소·발주기관주소·허용지역은 분리한다.
- 한국어 최종 보고는 **완료 / 실패 / 미검증 / 다음 조치**로 구분한다. 다음 조치에는 사용자에게 필요한 신청이 있다면 정확한 서비스명·공식 신청 URL·발급처를 넣는다.

기존 K-apt·LH·K-water UI 연결은 별도 후속 작업이다. 이번 검증을 이유로 기존 수집·화면·스케줄러를 임의 변경하지 않는다.
