# 한전·국방·도로·철도·S2B 추가 검증

> **후속 정정(2026-10-08):** 아래 D2B 판정 “최신 시설공사 API 경로 미확정”은 정정되었다. 같은 GW Swagger에 시설 경쟁입찰공고 operation이 있다(DOCUMENTED). 나라장터 경유 범위, 도로공사·국가철도공단 연간 파일, S2B 조사 결과도 [CLAUDE_REMAINING_PROVIDERS_FINDINGS.md](CLAUDE_REMAINING_PROVIDERS_FINDINGS.md)를 따른다. 이 문서는 당시 기록으로 보존한다.

확인일: 2026-10-08. 사용자 요청에 따라 새 소스의 UI 연결보다 남은 수집처 검증을 먼저 수행했다. 공식 안내의 존재, 최신 공사 공고 API 계약, 인증 실응답을 구분한다. 이 조사에서 새 인증 API 호출은 **0회**다.

## 판정

| 수집처 | 확인한 사실 | 최신 공사 수집 상태 |
|---|---|---|
| 한전 | 공식 가이드에서 공고일 조회 endpoint, 인증 파라미터, 공사 구분 필드 확인 | DOCUMENTED. 별도 한전 키 미설정으로 실호출 SKIPPED / 미수집 |
| 국방조달 D2B | 실시간 입찰공고 API 설명은 물품·용역. 별도 시설공사 자료는 연간 파일 및 변환 API | 최신 시설공사 API 경로 미확정. API 부재 판정 아님 |
| 한국도로공사 | 공식 API는 전자조달 계약공개현황 | 계약 체결 내역은 확인. 최신 공사 공고 API 경로 미확정 |
| 한국철도공사(코레일) | 기존 나라장터 수집 DB와 성공 응답 원본에서 최근 공사 3건 대조 | 나라장터 경유 일부 공사 LIVE_VERIFIED. 자체 조달 전체 포괄 여부 미검증 |
| 국가철도공단 | ‘입찰관련정보’는 계약구분·낙찰방법·금액구간·낙찰하한율 기준표 | 공고 목록이 아니므로 최신 공사 수집 API로 사용하지 않음 |
| S2B | 공식 공개 입찰 화면에 물품·공사·용역 구분 존재 | 외부 제공 OpenAPI 경로·인증·명세 미확정. HTML 화면을 API로 간주하지 않음 |

## 한전: 호출 계약 확인

- [공공데이터포털 전자입찰계약정보](https://www.data.go.kr/data/15148223/openapi.do)
- [한전 공식 API 가이드](https://bigdata.kepco.co.kr/cmsmain.do?scode=S01&pcode=000493&pstate=contract&redirect=Y)
- Endpoint: `https://bigdata.kepco.co.kr/openapi/v1/electContract.do`, GET/POST.
- 필수: `noticeBeginDate`, `noticeEndDate`(YYYYMMDD, 조회기간 최대 90일), `apiKey`(한전 포털 발급 40자리 인증키).
- 선택: `companyId`, `no`, `name`, `bidAttendReqCloseDatetime`, `progressState`, `returnType`(json/xml, 기본 json).
- 응답 목록: `data`. `purchaseType=ConstructionService`는 공사·용역이고, 별도 `itemType=Construction`이 공사, `Service`가 용역이다.
- 응답 필드: 공고번호 `no`, 입찰건명 `name`, 공고일 `noticeDate`, 마감 `endDatetime`, 진행상태 `progressState`, 참가제한 `bidAttendRestrict`, 기관 `placeName`, 금액·첨부 관련 필드 등.
- `deliveryLocation`·발주기관 위치를 현장주소로 단정하지 않는다. 페이지 처리·갱신·정정 키·누락률·현장주소 의미는 실응답 검증 대상이다.
- `companyId` 안내에는 한전(COM01), 서부발전(COM02), 남부발전(COM04), 중부발전(COM05), 남동발전(COM06), 동서발전(COM08), 한국전력기술(COM09), 한전KPS(COM10) 등이 있다. 이는 문서상 지원이며 각 기관의 실제 응답을 검증한 것은 아니다. 모든 발전사가 포함된다고 확대하지 않는다.
- 로컬 환경과 `.env`에서 KEPCO 이름을 포함한 키/토큰 설정 유무만 확인했으며 설정값은 출력하지 않았다. 해당 설정은 없었다. 기존 공공데이터 키를 한전 호스트로 보내지 않았다.

다음 실검증은 한전 포털 발급 키를 별도로 로컬 설정한 후, `ALLOW_LIVE_API=true`와 `--live`를 모두 요구하는 수집 경로에서 작은 기간을 호출한다. 공사 구분·날짜·복합 식별키·첨부·최신성 및 완전성 규칙을 실제 응답으로 확인한 뒤 수집기에 연결한다. 현재 한전 어댑터는 미구현이다.

## 국방조달: 공사와 물품·용역을 구분

- [군수품조달정보 입찰공고_GW](https://www.data.go.kr/data/15158416/openapi.do): 실시간 자료이며 공식 설명의 업무구분은 **물품/용역**이다. 이 설명만으로 시설공사까지 제공한다고 표시할 수 없다.
- [시설공사 경쟁 입찰공고](https://www.data.go.kr/data/15050926/fileData.do): 확인한 자료명은 `20251231` 기준, 갱신 주기는 **연간**, 차기 등록 예정일은 2027-01-12다. 파일의 OpenAPI 변환 제공은 매일 새 공사가 올라오는 API라는 뜻이 아니다.
- [D2B 시설공사 안내](https://www.d2b.go.kr/contents/info/domestic01.do?key=515)는 웹 입찰 업무의 존재 근거이며 공개 API 명세가 아니다.

최신 시설공사를 조회하는 공식 operation과 제공 범위는 미확정이다. 나라장터 DB에 국방부 관련 기관 공고가 일부 있어도 D2B 전체의 대체 경로로 판정하지 않는다.

## 도로공사: 계약 내역과 공고를 구분

- [전자조달 계약공개현황](https://www.data.go.kr/data/15128076/openapi.do)
- [도로공사 공식 가이드](https://data.ex.co.kr/openapi/basicinfo/openApiInfoM?apiId=0622)
- 문서상 endpoint: `https://data.ex.co.kr/openapi/elctPrcmInfo/elctPrcmCntrtOppubPrss`.
- 제공 내용은 계약명·계약방법·업체·계약금액·계약일자 등이다. 공고번호 필드가 있다는 이유로 신규 입찰공고 목록으로 대체하지 않는다.
- [전자조달 웹사이트](https://ebid.ex.co.kr)의 최신 공고를 외부 API로 받는 계약은 이번 공개자료 조사에서 확정하지 못했다.

## 철도: 운영기관을 분리

### 한국철도공사(코레일)

기존 `bf_notice_revision`에서 2026년 공고일과 공고기관/수요기관 이름으로 조회한 뒤 `source_response` 성공 기록 및 저장 원본을 대조했다. 최근 공사 3건의 공고번호가 모두 같은 나라장터 성공 응답 원본에 존재했다. 실제 공고 식별자·제목·기관·날짜는 Git 제외 근거 파일에 보존한다.

이는 **나라장터 경유 일부 공사 실수집**의 근거다. [코레일 전자조달](https://ebid.korail.com/main.do) 전체 공고의 수록률이나 별도 API는 확인하지 않았다. UI 후보 필터에 기관을 추가하는 일과 전국 모든 코레일 공사 수집을 보장하는 일은 구분한다.

### 국가철도공단

[입찰관련정보](https://www.data.go.kr/data/15153496/fileData.do)는 확인 당시 `20251119` 기준 90행의 연간 자료다. 계약구분, 낙찰방법 코드·명칭, 최소·최대 금액, 낙찰하한율, 사용여부를 제공한다. 신규 공고 목록이 아닌 기준정보다. [공단 전자조달](https://ebid.kr.or.kr/index.jsp)의 신규 공사 공고를 조회하는 API 경로는 미확정이다.

## S2B

[공식 공개 입찰 화면](https://www.s2b.kr/S2BNCustomer/tcmo001.do)의 공사 구분은 확인했다. 외부 소비자용 공식 OpenAPI endpoint·인증 방식·제공 필드 문서는 이번 조사에서 찾지 못했다. S2B가 조달청 API를 이용한다는 공지는 S2B 자체 공고를 외부에 제공하는 API의 근거가 아니다. API 전용 수집 방침을 유지하며 웹 스크래핑은 구현하지 않았다.

## 실행 및 증거

- 공식 URL은 웹 검색/열기로 조회했다. 일부 도구의 timeout/internal error는 로컬 Python `httpx.get(..., follow_redirects=True, timeout=20)`의 TLS 검증을 유지한 공개 페이지 GET으로 재확인했다. 한전·도로공사 가이드, 한전/D2B/공단 메타데이터 및 S2B 공개 화면은 HTTP 200으로 저장했다. 공개 HTML을 코드로 실행하지 않았다.
- 공개 페이지 원본: `.local/real/provider-probe/remaining-sources/`의 `kepco.html`, `road.html`, `kepco_meta.html`, `d2b_live.html`, `d2b_facility.html`, `rail_rules.html`, `s2b.html` 및 텍스트 사본.
- SQLite 조회는 `sqlite3.connect(database_path.as_uri() + '?mode=ro', uri=True)`로 수행했다. `bid_ntce_dt >= '2026-01-01'` 및 기관명 조건으로 최신 사례를 확인했다. 해당 조건에서 도로공사/공단 결과가 없다는 사실은 API 부재나 전체 나라장터 미수록의 증거가 아니다.
- 나라장터 관측 근거: `g2b-observed-agencies.json`, `korail-raw-verification.json`(동일 로컬 폴더). `source_response` ID 9854는 `getBidPblancListInfoCnstwkPPSSrch`, HTTP 200 / resultCode 00 / SUCCESS다. 저장 원본 SHA256이 DB 기록과 일치하고, 파싱한 응답의 공고번호·차수 복합키와 DB 표본 3개가 모두 일치함을 assertion으로 확인했다(PASS).
- 한전 키 점검은 `dotenv_values` 및 프로세스 환경의 KEPCO 관련 키/토큰 **설정 유무만** 출력했다: false.
- 원본·실데이터 근거는 Git 제외다. 기존 DB와 사용자 작업 파일은 수정하지 않았다.
- 인증 API 호출: 0회. 새 수집기 실연동 테스트: SKIPPED(한전 키 없음, 나머지 최신 공사 API 계약 미확정). 코드 변경이 없는 문서 조사이므로 단위테스트 재실행: SKIPPED.

## 다음 조치

1. 한전은 별도 포털 인증키 발급·로컬 설정 후 명세 기반 소량 실호출 검증으로 진행한다.
2. 코레일은 이미 수집되는 나라장터 공사를 활용하되 출처 전체를 포괄한다고 표시하지 않는다.
3. D2B 시설공사·도로공사 신규 공고·국가철도공단 신규 공고·S2B는 제공기관의 공식 연계 명세 확인 대상이다. 문의나 메시지는 이번 작업에서 발송하지 않았다.
4. 기존 K-apt·LH·K-water 공사 분류·주소 검증 및 UI 연결은 이 조사 이후의 작업으로 남긴다.
