# API 계약 (P0)

확인일: 2026-09-16 (KST)
기계 판독용 카탈로그: `config/api_catalog.yaml`. 이 카탈로그는 `tools/build_api_catalog.py`가 아래 공식 문서에서 생성한다.

> **현재 상태(2026-09-16 실연동 반영): 오퍼레이션 10개 LIVE_VERIFIED, 나머지 DOCUMENTED.**
> 인증키로 75회 실호출해 확인한 결과는 12장과 `config/live_evidence.yaml`에 있다. 1~11장은 공식 문서 기준 계약이며, 실제 응답과 다른 점은 12장이 우선한다.
> LIVE_VERIFIED는 표본 관측이다. 전 기간·전 공고 완전성 보증이 아니다.

## 0. 상태 범례

| 상태 | 의미 |
|---|---|
| DOCUMENTED | 포털 Swagger와 조달청 참고자료(docx)에서 확인했다. 실응답은 확인하지 않았다. |
| LIVE_VERIFIED | 인증된 실응답으로 확인했다. 카탈로그에 실행 ID·일시(`live_evidence`)가 있어야 한다. |
| UNVERIFIED | 문서 근거가 부족하거나 문서끼리 달라 확정하지 못했다. |
| BLOCKED | 인증키·활용승인·쿼터 때문에 확인할 수 없었다. |

## 1. 근거 문서

모두 공공데이터포털에서 직접 받았다. 원본 사본은 Git 제외 경로 `.local/official_specs/2026-09-16/`에 있다.

| ID | 서비스 | 포털 페이지 | 포털 수정일 | 참고자료(docx) | 문서상 서비스 버전 | docx SHA-256 앞 16자 |
|---|---|---|---|---|---|---|
| S01 | 조달청_나라장터 입찰공고정보서비스 | https://www.data.go.kr/data/15129394/openapi.do | 2026-06-29 | 조달청_OpenAPI참고자료_나라장터_입찰공고정보서비스_1.2.docx | 3.1 | c1e45e978d8d5286 |
| S02 | 조달청_나라장터 낙찰정보서비스 | https://www.data.go.kr/data/15129397/openapi.do | 2026-06-30 | 조달청_OpenAPI참고자료_나라장터_낙찰정보서비스_1.1.docx | 1.5 | 7f4165f67b9810e1 |
| S03 | 조달청_나라장터 업종 및 근거법규서비스 | https://www.data.go.kr/data/15129467/openapi.do | 2026-05-15 | 조달청_OpenAPI참고자료_나라장터_업종및근거법규서비스_1.1.docx | 1.0 | a44252c9a9a82301 |
| S04 | 조달청_나라장터 사용자정보 서비스 | https://www.data.go.kr/data/15129466/openapi.do | 2026-05-15 | 조달청_OpenAPI참고자료_나라장터_사용자정보서비스_1.1.docx | 1.0 | 150083d6ca00840d |

- **포털 Swagger**: 각 포털 페이지 HTML에 Swagger 2.0 JSON이 내장되어 있다. host·오퍼레이션·파라미터·응답 필드를 여기서 읽었다.
- **참고자료 docx**: 조회구분 코드값, 날짜 형식, 필드 설명, 코드표, 오류코드, 개정이력을 여기서 읽었다. 첨부 다운로드 주소는 카탈로그 `sources.*.reference_doc.download_url`에 있다.
- 두 문서가 다르면 둘 다 기록했다(8장). 어느 한쪽을 임의로 정답으로 고르지 않았다.
- 참고자료 개정이력: 입찰공고 1.2(2026-04-10 이전입찰공고번호·낙찰방법적용기준 추가, 2026-04 전남광주통합특별시 지역코드 추가), 낙찰정보 1.1(2025-09-25 bizno 요청·평가점수 응답 추가, 2026-06-30 지역코드 추가).

## 2. 게이트웨이 공통 계약

| 항목 | 문서 내용 | 상태 |
|---|---|---|
| 호출 호스트 | `apis.data.go.kr` | DOCUMENTED |
| 서비스 경로 | 입찰공고 `/1230000/ad/BidPublicInfoService`, 낙찰정보 `/1230000/as/ScsbidInfoService`, 업종 `/1230000/ao/IndstrytyBaseLawrgltInfoService`, 사용자정보 `/1230000/ao/UsrInfoService02` | DOCUMENTED |
| 스킴 | Swagger schemes: https·http(사용자정보는 https만). 참고자료 URL 예시는 http. **클라이언트는 https만 쓴다.** | DOCUMENTED / https 실호출 UNVERIFIED |
| 호출 형식 | `GET {base}/{operation}?serviceKey=...&pageNo=..&numOfRows=..&...` | DOCUMENTED |
| 인증 파라미터 이름 | Swagger `serviceKey`(사용자정보 부정당제재 조회만 `ServiceKey`), 참고자료 표 `ServiceKey` | UNVERIFIED(대소문자 민감 여부) |
| 인증키 형식 | 포털 Swagger 가이드: "serviceKey는 일반 인증키(Decoding)을 입력". 참고자료 오류 30: "서비스키를 URL 인코딩하지 않음"도 원인 | DOCUMENTED. 클라이언트는 Decoding 키를 1회만 인코딩 |
| 응답 형식 | `type=json`이면 JSON, 지정하지 않으면 XML(참고자료 설명) | DOCUMENTED |
| 정상 envelope | `response.header.resultCode/resultMsg`, `response.body.items.item[]`, `numOfRows`, `pageNo`, `totalCount` | DOCUMENTED (JSON items 실제 형태 UNVERIFIED) |
| 날짜 조건 | `inqryBgnDt`/`inqryEndDt` = `YYYYMMDDHHMM` | DOCUMENTED |
| 날짜 응답 | 대부분 `YYYY-MM-DD HH:MM:SS`, 일부 예시는 초 없음(`2025-07-07 18:00`), 업종 서비스는 `YYYY-MM-DD HH:MM` | DOCUMENTED(형식 혼재) |
| 조회기간 상한 | 평가대상주력분야·입찰가격산식A 조회만 "최대 1개월". 나머지는 문서에 없음 | 대부분 UNVERIFIED |
| 경계 포함 여부 | 문서에 없음 | UNVERIFIED |
| numOfRows 최대값 | 문서에 없음(항목크기 4자리, 예시 응답에 999·500) | UNVERIFIED |
| 처리량 | 오퍼레이션 표에 "초당 최대 트랜잭션 30 tps", "평균 응답 500ms" | DOCUMENTED(제공기관 표기) |
| 일일 트래픽 | 포털: 개발계정 1,000(입찰공고·낙찰정보·업종), 10,000(사용자정보). 운영계정은 활용사례 등록 후 증가 신청 | DOCUMENTED. 오퍼레이션별인지 서비스별인지 UNVERIFIED |
| 승인 | 포털: 개발·운영 단계 자동승인 | DOCUMENTED. 사용자 키의 실제 승인 상태 BLOCKED |
| 시간범위 표기 | 입찰공고 포털 "1995년 10월 - 2025년 1월", 낙찰정보 "실시간". 참고자료 서비스 시작일 2025-01-06 | 문서 간 불일치. 2023·2024년 데이터 제공 범위 UNVERIFIED |

내부 안전값(제공기관 쿼터 아님): 실행 100회, 일일 100회, 요청 간격 1.0초, 요청당 시도 최대 3회(첫 시도 포함, 재시도도 예산 차감).

### 2.1 오류코드

포털 "오픈API 에러코드 안내"와 참고자료 "OPEN API 에러코드별 조치방안"이 같은 번호에 다른 이름을 쓰는 경우가 있다. 클라이언트(`src/bidloc/clients/errors.py`)는 메시지 토큰을 먼저 보고, 번호만 있으면 재시도하지 않는 쪽으로 분류한다.

| 코드 | 포털 메시지 | 참고자료 명칭 | 클라이언트 분류 | 재시도 | 중단 범위 |
|---|---|---|---|---|---|
| 00 | - | 정상 | SUCCESS / SUCCESS_EMPTY | - | - |
| 01 | APPLICATION_ERROR | Application Error | UPSTREAM_ERROR | 제한적 | - |
| 02 | - | DB Error | UPSTREAM_ERROR | 제한적 | - |
| 03 | - | No Data | NO_DATA (0건으로 쓰지 않음) | 아니오 | - |
| 04 | HTTP_ERROR | HTTP Error | UPSTREAM_ERROR | 제한적 | - |
| 05 | SERVICETIMEOUT_ERROR | service time out | UPSTREAM_ERROR | 제한적 | - |
| 06 | - | 날짜Format 에러 | INVALID_PARAMETER | 아니오 | - |
| 07 | - | 입력범위값 초과 에러 | INVALID_PARAMETER | 아니오 | - |
| 08 | - | 필수값 입력 에러 | INVALID_PARAMETER | 아니오 | - |
| 10 | INVALID_REQUEST_PARAMETER_ERROR | 잘못된 요청 파라미터(ServiceKey 없음) | INVALID_PARAMETER | 아니오 | - |
| 11 | - | 필수 요청 파라미터가 없음 | INVALID_PARAMETER | 아니오 | - |
| 12 | NO_OPENAPI_SERVICE_ERROR | 서비스 없거나 폐기 | SERVICE_NOT_FOUND | 아니오 | 해당 서비스 |
| 20 | SERVICE_KEY_IS_NULL / PERMISSION_DENIED / SERVICE_ACCESS_DENIED_ERROR | 서비스 접근 거부(활용승인 안 됨) | AUTH_KEY_MISSING 또는 ACCESS_DENIED | 아니오 | 해당 서비스 |
| 22 | LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR | 서비스 요청 제한 횟수 초과 | QUOTA_DAILY_EXCEEDED | 아니오 | 실행 전체, 이어받기 상태 기록 |
| 23 | LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR | - | RATE_LIMITED | 제한적(Retry-After 존중) | - |
| 29 | BLACKLIST_IP_ACCESS_ERROR | - | IP_NOT_ALLOWED | 아니오 | 실행 전체 |
| 30 | SERVICE_KEY_IS_NOT_REGISTERED_ERROR | 등록되지 않은 서비스 키 | AUTH_KEY_INVALID | 아니오 | 해당 서비스 |
| 31 | DEADLINE_HAS_EXPIRED_ERROR | 기한 만료된 서비스 키 | AUTH_KEY_EXPIRED | 아니오 | 해당 서비스 |
| 32 | - | 등록되지 않은 도메인명 또는 IP | IP_NOT_ALLOWED | 아니오 | 실행 전체 |

오류 응답의 envelope 형식은 두 문서 모두 정의하지 않는다. 클라이언트는 표준 envelope, `OpenAPI_ServiceResponse/cmmMsgHeader` 형식, 평문, HTTP 상태코드(401·403·404·429·5xx)를 모두 오류로 인식한다. HTTP 200 안의 XML 오류도 JSON 요청 여부와 관계없이 오류로 처리한다. 실제로 어떤 형식이 오는지는 UNVERIFIED다.

## 3. P0 대상 오퍼레이션

표기: 요청 파라미터 `[참고자료 필수여부/Swagger 필수여부]`. `-`는 해당 문서에 없음. 전체 필드 목록과 설명은 카탈로그에 있다. 이 장은 문서 기준 기술이다. 실응답 상태와 차이는 12장을 본다.

### 3.1 입찰공고정보서비스 (S01, `bid_notice`)

#### getBidPblancListInfoCnstwk — 입찰공고목록 정보에 대한 공사조회
- 용도: 공사 입찰공고 기본정보. 공고 차수, 공고종류, 기관, 계약·낙찰방법, 예산·추정가격·VAT, 공사현장 지역.
- 요청: `numOfRows`[필수/필수], `pageNo`[필수/필수], `type`[옵션/옵션], `inqryDiv`[필수/필수] (1=등록일시, 2=입찰공고번호, 3=변경일시), `inqryBgnDt`·`inqryEndDt`[옵션/옵션] (YYYYMMDDHHMM, 조회구분 1·3일 때 필수, 기간 상한 미기재), `bidNtceNo`[옵션/옵션] (조회구분 2일 때 필수).
- 행 키 후보: `bidNtceNo + bidNtceOrd` (UNVERIFIED). 응답에 입찰분류번호·재입찰번호가 없다.
- 핵심 필드: `bidNtceNo`, `bidNtceOrd`, `reNtceYn`, `ntceKindNm`(등록공고/변경공고/취소공고/재공고), `chgNtceRsn`, `bidNtceDt`, `rgstDt`, `chgDt`, `ntceInsttCd/Nm`, `dminsttCd/Nm`, `cntrctCnclsMthdNm`, `bidMethdNm`, `sucsfbidMthdCd/Nm`, `bdgtAmt`, `presmptPrce`, `VAT`, `govsplyAmt`, `mainCnsttyNm`, `subsiCnsttyNm1~9`, `indstrytyLmtYn`, `bidPrtcptLmtYn`, `cnstrtsiteRgnNm`, `incntvRgnNm1~4`, `jntcontrctDutyRgnNm1~3`, `cmmnSpldmdMethdCd/Nm`, `opengDt`, `rbidPermsnYn`, `untyNtceNo`, `bidNtceDtlUrl`.
- 문서 불일치: `befBidBbancNo`(이전입찰공고번호), `rgnLmtBidLocplcJdgmBssCd/Nm`(지역제한입찰 소재지 판단기준), `sucsfbidMthdAppStd`는 참고자료 1.2에만 있고 포털 Swagger에는 없다.

#### getBidPblancListInfoCnstwkPPSSrch — 나라장터검색조건에 의한 입찰공고공사조회
- 용도: 검색조건으로 공사 공고 조회. P0에서는 표본 탐색에만 쓴다.
- 요청: `inqryDiv`[필수/필수] (1=공고게시일시, 2=개찰일시), `inqryBgnDt`·`inqryEndDt`, `type`[옵션/필수], `bidNtceNm`, `ntceInsttCd/Nm`, `dminsttCd/Nm`, `refNo`, `prtcptLmtRgnCd`(2자리 시·도 코드, 6장 표), `prtcptLmtRgnNm`, `indstrytyCd`, `indstrytyNm`, `presmptPrceBgn/End`, `prcrmntReqNo`, `bidClseExcpYn`, `intrntnlDivCd`, `dtilPrdctClsfcNo`·`masYn`(참고자료에만 있고 "공사업무는 검색 불가").
- 응답: 공사조회와 같은 필드. Swagger에만 `d2bMngRgnLmtYn`(방사청관리지역제한여부)이 있고 `VAT`가 없다.
- 제약: 지역 필터는 시·도 2자리 코드뿐이다. 업종명은 "일부 입력시에도 조회 가능"으로 문서화되어 있으나 필터 정확성·누락률은 UNVERIFIED다. 전수 수집 근거로 쓰지 않는다.

#### getBidPblancListInfoLicenseLimit — 면허제한정보조회
- 용도: 공고별 면허제한. 업무구분 공통 오퍼레이션이며 응답 `bsnsDivNm`으로 업무를 확인한다.
- 요청: `inqryDiv`[필수/필수] (1=등록일시, 2=입찰공고번호), `inqryBgnDt`·`inqryEndDt`, `bidNtceNo`, `bidNtceOrd`[옵션/필수] (참고자료: 조회구분 2일 때 필수).
- 응답: `bidNtceNo`, `bidNtceOrd`, `lmtGrpNo`(제한그룹번호), `lmtSno`(제한순번), `lcnsLmtNm`(예시 `액화석유가스판매사업/4617`), `permsnIndstrytyList`(`[허용업종명/코드],[...]`), `indstrytyMfrcFldList`(`[주력분야제한그룹순번^주력업종명1^주력업종명2],[...]`), `rgstDt`, `bsnsDivNm`.
- 행 키 후보: `bidNtceNo + bidNtceOrd + lmtGrpNo + lmtSno` (UNVERIFIED).
- 주력분야 목록: 참고자료는 "나라장터 화면에서 '와'는 '^'로, '또는'은 대괄호 []로 구분"이라고 쓴다. 문서상 한 대괄호 안은 AND, 대괄호끼리는 OR로 읽힌다. **실응답·공고문 대조 전에는 판정 규칙으로 쓰지 않는다.**
- 제한그룹(`lmtGrpNo`) 사이와 그룹 안의 AND·OR 의미는 **문서에 없다**(UNVERIFIED).

#### getBidPblancListInfoPrtcptPsblRgn — 참가가능지역정보조회
- 용도: 입찰 허용지역. 업무구분 공통.
- 요청: `inqryDiv` (1=등록일시, 2=입찰공고번호), `inqryBgnDt`·`inqryEndDt`, `bidNtceNo`, `bidNtceOrd`[옵션/필수].
- 응답: `bidNtceNo`, `bidNtceOrd`, `lmtSno`, `prtcptPsblRgnNm`(예시 `광주광역시`), `rgstDt`, `bsnsDivNm`.
- 행 키 후보: `bidNtceNo + bidNtceOrd + lmtSno` (UNVERIFIED).
- 문서 내부 불일치: 오퍼레이션 설명은 "제한그룹번호"를 반환한다고 쓰지만 응답 필드 표에는 `lmtGrpNo`가 없다.
- 시·군 단위 이름(예: 성남시)과 복수지역이 어떻게 오는지는 UNVERIFIED다. S05·S06 표본으로 확인할 계획이다.

#### getBidPblancListInfoCnstwkBsisAmount — 공사기초금액조회
- 요청: `inqryDiv` (1=입력일시, 2=입찰공고번호), `inqryBgnDt`·`inqryEndDt`[옵션/필수], `bidNtceNo`.
- 응답: `bidNtceNo`, `bidNtceOrd`, `bidClsfcNo`, `bssamt`(기초금액, 원), `bssamtOpenDt`, `rsrvtnPrceRngBgnRate/EndRate`, `evlBssAmt`, 비용항목(안전관리비·보험료 등), `usefulAmt`, `inptDt`, `bidPrceCalclAYn`, `smkpAmt/Yn`(참고자료에만).
- 행 키 후보: `bidNtceNo + bidNtceOrd + bidClsfcNo` (UNVERIFIED).

#### getBidPblancListInfoChgHstryCnstwk — 공사변경이력조회
- 요청: `inqryDiv` (1=변경일시, 2=입찰공고번호), `inqryBgnDt`·`inqryEndDt`, `bidNtceNo`.
- 응답: `bsnsDivNm`, `chgDataDivNm`("입찰공고" 또는 "개찰결과"), `chgDt`, `bidNtceNo`, `bidNtceOrd`, `bidClsfcNo`, `rbidNo`, `chgItemNm`, `bfchgVal`, `afchgVal`, `lcnsLmtCdRgstList`. Swagger에만 `sucsfbidMthdCd/Nm`.
- 변경이력 추출 대상 항목에 "참가가능지역", "면허제한코드", "추정가격" 등이 포함된다(참고자료). 전후값 형식은 예시마다 다르다(`20260126 18:00`, `2025/08/19 13:26`).

#### getBidPblancListEvaluationIndstrytyMfrcInfo — 평가대상 주력분야 조회
- 요청: `inqryDiv` (1=공고게시일시, 2=입찰공고번호), `inqryBgnDt`·`inqryEndDt`(조회구분 1은 **최대 1개월**), `bidNtceNo`.
- 응답: `bidNtceDt`, `ciblAplYn`, `cnstrtWkaraMtltyAdvcPsblYn`, `cnstrtWkrarDivCd`(건060001 종합/건060002 전문/건060003 유지보수/건060004 기타), `bidwinrSlctnBssCd`(계040000 해당없음/계040001 국가계약법/계040002 지방계약법/계040003 자체기준), `cnsttyTyNm`, `tmpNm`, `indstrytyMfrcFldNm`, `presmptAmt`, `presmptPrce`, `VAT`, `evlRt`.
- 주의: 이름상 "평가대상"이다. 적격심사 등 **낙찰심사 관련 정보일 수 있으므로 참가자격 판정과 분리**한다. 참고자료 응답 표는 필드명 첫 글자가 대문자(`CiblAplYn` 등)지만 같은 문서의 응답 예시와 Swagger는 소문자다.

#### getBidPblancListBidPrceCalclAInfo — 입찰가격산식A정보조회
- 조회구분 1 기간 최대 1개월. A값 구성 비용항목이다. P0 분석 대상은 아니며 계약만 기록했다.

### 3.2 낙찰정보서비스 (S02, `bid_award`)

#### getOpengResultListInfoCnstwk — 개찰결과 공사 목록 조회
- 용도: 개찰단위별 결과. 참고자료 설명: "유찰, 개찰완료, 재입찰건에 대한 개찰결과를 제공".
- 요청: `inqryDiv`[필수/필수] (1=입력일시, 2=공고일시, 3=개찰일시, 4=입찰공고번호), `inqryBgnDt`·`inqryEndDt`(조회구분 1·2·3일 때 필수), `bidNtceNo`(조회구분 4), `numOfRows`·`pageNo`[옵션/필수].
- 응답: `bidNtceNo`, `bidNtceOrd`, `bidClsfcNo`(참고자료: "동일한 입찰공고번호에 대한 집행일련번호"), `rbidNo`, `bidNtceNm`, `opengDt`, `prtcptCnum`(참가업체수), `opengCorpInfo`, `progrsDivCdNm`(유찰/개찰완료/재입찰), `inptDt`, `rsrvtnPrceFileExistnceYn`, `ntceInsttCd/Nm`, `dminsttCd/Nm`, `opengRsltNtcCntnts`.
- 행 키 후보: **`bidNtceNo + bidNtceOrd + bidClsfcNo + rbidNo`** (UNVERIFIED).
- `opengCorpInfo` 형식(참고자료): 단일 낙찰자 `업체명^사업자번호^대표자명^투찰금액^투찰율`, 다수 낙찰자 "낙찰예정자 다수"와 1순위 금액·율, 협상계약은 금액·율 없음.

#### getOpengResultListInfoCnstwkPPSSrch — 검색조건 기반 개찰결과 공사 목록
- 요청: `inqryDiv` (1=공고일시, 2=개찰일시, 3=입찰공고번호) 외 입찰공고 검색조건과 같은 필터(`prtcptLmtRgnCd`, `indstrytyCd/Nm`, `presmptPrceBgn/End` 등).
- 응답·행 키 후보: 개찰결과 공사 목록과 같다.

#### getOpengResultListInfoOpengCompt — 개찰결과 개찰완료 목록 조회 (명부)
- 용도: 개찰완료 건의 투찰업체별 개찰순위. 업무구분 공통.
- 요청: `bidNtceNo`[필수/필수], `bidNtceOrd`, `bidClsfcNo`, `rbidNo`[옵션/옵션].
- 응답: `opengRsltDivNm`, `bidNtceNo`, `bidNtceOrd`, `bidClsfcNo`, `rbidNo`, `opengRank`(협상계약은 협상순위), `prcbdrBizno`, `prcbdrNm`, `prcbdrCeoNm`, `bidprcAmt`, `bidprcrt`(투찰금액/예정가격×100), `rmrk`(예시 "낙찰"), `drwtNo1/2`, `bidprcDt`, `cnsttyAccotBidAmtUrl`("차세대 나라장터 개편 이후 제공 불가"), 참고자료에만 `bidPrceEvlVal`, `techEvlVal`, `techEvlNaturVal`, `totalEvlAmtVal`.
- 행 키 후보: `bidNtceNo + bidNtceOrd + bidClsfcNo + rbidNo + prcbdrBizno` (UNVERIFIED).
- 문서 불일치: 오퍼레이션 설명은 "최종낙찰업체사업자등록번호, 최종낙찰업체명"을 준다고 쓰지만 응답 필드는 투찰업체(`prcbdr*`)다.
- 무효·탈락·공동수급 행이 어떻게 표시되는지는 문서에 없다(UNVERIFIED).

#### getScsbidListSttusCnstwk — 낙찰된 목록 현황 공사조회
- 요청: `inqryDiv` (1=등록일시, 2=공고일시, 3=개찰일시, 4=입찰공고번호), `inqryBgnDt`·`inqryEndDt`, `bidNtceNo`.
- 응답: `bidNtceNo`, `bidNtceOrd`, `bidClsfcNo`, `rbidNo`, `ntceDivCd`, `bidNtceNm`, `prtcptCnum`, `bidwinnrNm`, `bidwinnrBizno`, `bidwinnrCeoNm`, `bidwinnrAdrs`, `bidwinnrTelNo`, `sucsfbidAmt`(최종낙찰금액), `sucsfbidRate`(최종낙찰금액/예정가격×100), `rlOpengDt`(실개찰일시), `dminsttCd/Nm`, `rgstDt`, `fnlSucsfDate`, `fnlSucsfCorpOfcl`.
- 참고자료: "최종낙찰은 개찰순위 순서대로 협상 등을 통해 최종 낙찰된 정보". 개찰순위 1위와 최종낙찰자를 같은 값으로 가정하지 않는다.

#### getScsbidListSttusCnstwkPPSSrch — 검색조건 기반 낙찰 목록 공사조회
- 요청: `inqryDiv` (1=공고게시일시, 2=개찰일시, 3=입찰공고번호) 외 검색조건. `bizno`는 참고자료 1.1에만 있다.
- 응답: 낙찰 목록과 같다. `linkInsttNm`은 Swagger에만 있다.

#### getOpengResultListInfoRebid / getOpengResultListInfoFailing — 재입찰·유찰 목록
- 요청: `bidNtceNo`[필수/필수], `bidNtceOrd`, `bidClsfcNo`(유찰: [옵션/필수]), `rbidNo`.
- 응답: 재입찰은 `opengRsltDivNm`, 네 키, `bidClseDt`, `opengDt`, `rbidRsn`, `cmmnSpldmdAgrmntClseDt`. 유찰은 `opengRsltDivNm`, 네 키, `nobidRsn`(예시 "단독응찰").
- 유찰·재입찰은 사유별 보조 관측이다. 경쟁이 낮다는 근거로 쓰지 않는다.

#### getOpengResultListInfoCnstwkPreparPcDetail — 공사 예비가격상세
- 요청: `inqryDiv` (1=입력일시, 2=입찰공고번호), `bidNtceNo`.
- 응답: `plnprc`(예정가격), `bssamt`(기초금액), `bsisPlnprc`, `totRsrvtnPrceNum`, `compnoRsrvtnPrceSno`, `drwtYn`, `drwtNum`, `rlOpengDt` 등.
- 문서 불일치: 재입찰번호 필드가 참고자료 `rbidNo`, Swagger `rbidNtceNo`다. P0 분석 대상은 아니다.

### 3.3 업종 및 근거법규서비스 (S03, `industry_law`)

#### getIndstrytyBaseLawrgltInfoList — 업종 및 근거법규 정보 조회
- 요청(모두 옵션): `indstrytyClsfcCd`, `indstrytyNm`, `indstrytyCd`, `inqryBgnDt`·`inqryEndDt`(업종등록일 기준), `indstrytyUseYn`. 조건이 없으면 전체 조회.
- 응답: `indstrytyClsfcCd`, `indstrytyClsfcNm`, `indstrytyCd`(4자리 숫자 코드), `indstrytyNm`, `baseLawordNm`, `baseLawordArtclClauseNm`, `baseLawordUrl`, `rltnRgltCntnts`, `inclsnLcns`(`[순번^제한업종코드^제한업종명^허용업종코드^허용업종명]`), `indstrytyUseYn`, `indstrytyRgstDt`, `indstrytyChgDt`. Swagger에는 `undefined`라는 필드도 있다(문서 오류로 보임).
- 서비스 설명: "조회시점에 유효한 업종 정보만 제공". 과거 코드 이력의 정답으로 쓰지 않는다.
- 참고자료 응답 예시에서 `indstrytyClsfcCd=49`가 "건설업"이다. 예시값이므로 실응답으로 확인한다.

### 3.4 사용자정보 서비스 (S04, `user_info`) — 후순위

- `getPrcrmntCorpBasicInfo02`: `inqryDiv` (1=등록일기준, 2=변경일기준, 3=사업자등록번호). 응답에 `bizno`, `corpNm`, `rgnCd`, `rgnNm`, `adrs`, `hdoffceDivNm`, `corpBsnsDivCd`(01 물품/07 공사/05 용역…), `rgstDt`, `chgDt` 등.
- `getPrcrmntCorpIndstrytyInfo02`: `inqryDiv` (1=사업자등록번호, 2=시스템등록일, 3=시스템변경일). 응답에 `indstrytyCd`, `indstrytyNm`, `rgstDt`, `vldPrdExprtDt`, `indstrytyStatsNm`, `rprsntIndstrytyYn`.
- `getDminsttInfo02`: 수요기관 코드·주소 등.
- 모두 **조회시점 현재 정보**다. 과거 입찰 당시 주소·업종으로 소급하지 않는다. 나라장터 등록업체이며 전국 건설업 면허 전체 명부가 아니다.

## 4. 연결 경로

```
getBidPblancListInfoCnstwk  (bidNtceNo, bidNtceOrd)
 ├─ getBidPblancListInfoLicenseLimit      요청: bidNtceNo + bidNtceOrd  → 행: + lmtGrpNo, lmtSno
 ├─ getBidPblancListInfoPrtcptPsblRgn     요청: bidNtceNo + bidNtceOrd  → 행: + lmtSno
 ├─ getBidPblancListInfoCnstwkBsisAmount  요청: bidNtceNo               → 행: + bidNtceOrd, bidClsfcNo
 ├─ getBidPblancListInfoChgHstryCnstwk    요청: bidNtceNo               → 행: + bidNtceOrd, bidClsfcNo, rbidNo
 └─ getOpengResultListInfoCnstwk          요청: bidNtceNo(조회구분 4)    → 개찰단위: bidNtceNo, bidNtceOrd, bidClsfcNo, rbidNo
      ├─ getOpengResultListInfoOpengCompt 요청: 네 키                   → 투찰 행: + prcbdrBizno, opengRank
      ├─ getScsbidListSttusCnstwk         행: 네 키                     → 최종낙찰
      └─ getOpengResultListInfoRebid / Failing  요청: bidNtceNo(+키)   → 재입찰·유찰 사유
```

| 연결 | 결합키 | 근거 | 상태 |
|---|---|---|---|
| 공고 → 면허제한 | bidNtceNo + bidNtceOrd | 면허제한 요청 파라미터 | DOCUMENTED / 실응답 UNVERIFIED |
| 공고 → 참가가능지역 | bidNtceNo + bidNtceOrd | 참가가능지역 요청 파라미터 | DOCUMENTED / UNVERIFIED |
| 공고 → 기초금액 | bidNtceNo + bidNtceOrd (행 단위 + bidClsfcNo) | 기초금액 응답 필드 | DOCUMENTED / UNVERIFIED |
| 공고 → 개찰단위 | bidNtceNo + bidNtceOrd, 개찰단위 키는 네 키 | 개찰결과 응답 필드 | DOCUMENTED / UNVERIFIED |
| 개찰단위 → 명부 | bidNtceNo + bidNtceOrd + bidClsfcNo + rbidNo | 개찰완료 요청 파라미터 | DOCUMENTED / UNVERIFIED |
| 개찰단위 → 최종낙찰 | 네 키 | 낙찰 목록 응답 필드 | DOCUMENTED / UNVERIFIED |
| 재공고 → 이전 공고 | befBidBbancNo → bidNtceNo | 참고자료 1.2에만 있음 | UNVERIFIED |
| 명부 업체 → 업체 기본정보 | prcbdrBizno → bizno | 사용자정보(현재값) | DOCUMENTED / UNVERIFIED |

규칙(코드에 반영: `src/bidloc/normalizers/keys.py`):
- **공고번호 단독 결합 금지.** 개찰단위를 공고에 붙일 때 차수까지 같아야 한다. 같은 공고번호·다른 차수는 "연결 미확인"(`SAME_NO_OTHER_ORD`)으로 남긴다.
- 키는 문자열 그대로 비교한다. `0`과 `000`처럼 숫자만 같은 경우는 `FORMAT_MISMATCH`로 검토 대상에 둔다.
- `bidNtceOrd` 설명은 "재공고 및 재입찰 등이 발생되었을 경우 증가". 그런데 `rbidNo`도 따로 있다. 두 번호가 어떤 사건에 증가하는지는 UNVERIFIED다. 공고차수와 재입찰번호를 같은 개념으로 합치지 않는다.

## 5. 참여업체 수 정의

| 숫자 | 출처 | 문서 정의 | 저장 방식 | 상태 |
|---|---|---|---|---|
| totalCount | 모든 목록 응답 | "전체 결과 수 / 데이터 총 개수" | 페이지 수집 완전성 검사에만 사용 | 무엇의 행 수인지 UNVERIFIED |
| 공식 참가업체수 `prtcptCnum` | 개찰결과 목록, 낙찰 목록 | "참가업체수" 외 설명 없음 | 별도 컬럼(`official_prtcpt_cnum`) | 무효·공동수급 포함 범위 UNVERIFIED |
| 명부 행 수 | 개찰완료 목록 | 내부 계산 | 모든 페이지 수집·검사 통과 시에만 산출 | UNVERIFIED |
| 명부 고유 사업자번호 수 | 개찰완료 목록 `prcbdrBizno` | 내부 계산 | 별도 컬럼 | 공동수급 표현 UNVERIFIED |
| 유효 투찰 수 | - | 해당 필드 없음 | 산출하지 않음 | UNVERIFIED |

- 조회실패·빈 응답·미개찰·미조회는 0이 아니다. 명시적으로 `0`이 관측된 값만 0으로 기록한다.
- 공식 수와 명부 수가 다르면 `MISMATCH`로 남기고 어느 한쪽으로 덮어쓰지 않는다.
- 페이지 `totalCount`, 순위 행 수, 유효 투찰 수를 같은 숫자로 취급하지 않는다.

## 6. 지역 정의

| 구분 | 필드 | 설명 |
|---|---|---|
| 입찰 허용지역 | `getBidPblancListInfoPrtcptPsblRgn.prtcptPsblRgnNm` (행 단위, `lmtSno`) | 입지 비교의 직접 기준. 복수 행은 집합으로 보존 |
| 공사현장 | `getBidPblancListInfoCnstwk.cnstrtsiteRgnNm` | "나라장터 화면 공사현장". 허용지역이 아니다 |
| 발주기관 | `ntceInsttCd/Nm`, `dminsttCd/Nm` | 공고 응답에 기관 주소 없음. `getDminsttInfo02` 주소는 현재값 |
| 허용지역이 아닌 지역 필드 | `incntvRgnNm1~4`(적격심사 가산지역), `jntcontrctDutyRgnNm1~3`(지역의무공동도급), `rgnLmtBidLocplcJdgmBssNm`(소재지 판단기준), `cmmnSpldmdCorpRgnLmtYn`, `brffcBidprcPermsnYn`(지사투찰 허용), `d2bMngRgnLmtYn` | 허용지역으로 대체하지 않는다 |

검색조건 `prtcptLmtRgnCd` 코드표(참고자료): 11 서울특별시, 26 부산광역시, 27 대구광역시, 28 인천광역시, 29 광주광역시, 30 대전광역시, 31 울산광역시, 36 세종특별자치시, 41 경기도, 42 강원도, 43 충청북도, 44 충청남도, 45 전라북도, 46 전라남도, 47 경상북도, 48 경상남도, 50 제주도, 51 강원특별자치도, 52 전북특별자치도, 12 전남광주통합특별시, 99 기타, 00 전국(지역제한을 설정하지 않은 공고).

- 시·도 코드만 있고 시·군 코드는 문서에 없다. 시·군 단위는 `prtcptPsblRgnNm` 원문으로만 확인할 수 있다(UNVERIFIED).
- 코드표에 옛 명칭과 새 명칭(42·51, 45·52)이 같이 있고, 2026년 개정에서 12(전남광주통합특별시)가 추가됐다. 과거 공고의 지역 매핑은 유효기간을 두고 관리해야 한다.

## 7. 금액 정의

| 금액 종류 | 필드 | 출처 | 부가세 | 비고 |
|---|---|---|---|---|
| 예산금액 | `bdgtAmt` | 공사조회 | UNKNOWN | "(원화,원)" |
| 추정가격 | `presmptPrce` | 공사조회, 평가대상주력분야 | 제외(문서: 부가가치세·조달수수료 제외) | |
| 부가가치세 | `VAT` | 공사조회 | - | |
| 관급금액 | `govsplyAmt` | 공사조회 | UNKNOWN | |
| 기초금액 | `bssamt` | 공사기초금액, 예비가격상세 | UNKNOWN | |
| 예정가격 | `plnprc` | 예비가격상세 | UNKNOWN | "계약체결 최고 상한 금액" |
| 최종낙찰금액 | `sucsfbidAmt` | 낙찰 목록 | UNKNOWN | 계약금액·매출과 다르다 |
| 투찰금액 | `bidprcAmt` | 개찰완료 목록 | UNKNOWN | |
| 계약금액 | 없음 | - | - | 4개 서비스 문서에 없음(계약정보 서비스 범위) |

- 단가·총액·장기계속 차수액을 구분하는 필드는 문서에 없다(UNKNOWN).
- 금액 종류를 서로 대체하지 않는다. 재입찰·재공고마다 기초금액을 합산하지 않는다.
- `mainCnsttyCnstwkPrearngAmt`는 이름이 "주공종공사예정금액"인데 설명은 "적격심사시 주공종추정금액"이다.

## 8. 업종·면허

- 목표 업종: 도장·습식·방수·석공사업. 주력분야: 도장 / 습식·방수 / 석공.
- **업종코드 값은 4개 서비스 문서 어디에도 없다.** 카탈로그 `industry_codes.code`는 null(UNVERIFIED)이다. `getIndstrytyBaseLawrgltInfoList` 실응답으로만 확인한다.
- 업종명 가운뎃점이 문서마다 다르다(예: `기계설비ㆍ가스공사업`의 `ㆍ`, `금속구조물·창호·온실공사`의 `·`). 비교할 때는 가운뎃점·공백을 제거한 문자열을 쓰고, 저장·표시는 원문을 쓴다.
- 통합업종(면허제한 `lcnsLmtNm`)과 주력분야(`indstrytyMfrcFldList`, 평가대상 `indstrytyMfrcFldNm`)는 필드가 다르다. 통합업종이 있다고 세 주력분야를 모두 보유한 것으로 보지 않는다.
- 평가대상 주력분야와 적격심사 관련 필드(`indstrytyEvlRt`, `sucsfbidLwltRate`, `incntvRgnNm*`, `cnstrtnAbltyEvlAmtList` 등)는 낙찰심사 쪽 정보다. 참가조건 판정과 분리한다.

## 9. 문서 간 불일치 요약

| 대상 | 참고자료(docx) | 포털 Swagger |
|---|---|---|
| 인증 파라미터명 | `ServiceKey` | `serviceKey` |
| 공사조회 응답 | `befBidBbancNo`, `rgnLmtBidLocplcJdgmBssCd/Nm`, `sucsfbidMthdAppStd` 있음 | 없음 |
| 검색조건 공사조회 | `VAT` 있음, `dtilPrdctClsfcNo`·`masYn` 요청 있음, `type` 옵션 | `d2bMngRgnLmtYn` 있음, `type` 필수 |
| 면허제한·참가가능지역 요청 `bidNtceOrd` | 조회구분 2일 때 필수 | 항상 필수 |
| 공사기초금액 요청 `inqryBgnDt/EndDt` | 조회구분 1일 때 필수 | 항상 필수 |
| 공사기초금액·A값 응답 `smkpAmt/Yn` | 있음 | 없음 |
| 공사변경이력 응답 `sucsfbidMthdCd/Nm` | 없음 | 있음 |
| 평가대상주력분야 응답 필드명 | 첫 글자 대문자(`CiblAplYn` 등), 예시는 소문자 | 소문자 |
| 개찰결과 공사 목록 `numOfRows/pageNo` | 옵션 | 필수 |
| 개찰완료 목록 응답 평가점수 4종 | 있음 | 없음 |
| 유찰 목록 `bidClsfcNo` | 옵션 | 필수 |
| 낙찰 검색조건 조회 | `bizno` 요청 있음 | 없음, 응답 `linkInsttNm`만 있음 |
| 공사 예비가격상세 재입찰번호 | `rbidNo` | `rbidNtceNo` |
| 업종 조회 응답 | - | `undefined` 필드 |
| 참가가능지역 설명 | "제한그룹번호" 반환이라고 씀 | 응답에 `lmtGrpNo` 없음 |
| 오류코드 10·20 | ServiceKey 없음 / 서비스 접근 거부 | INVALID_REQUEST_PARAMETER / SERVICE_KEY_IS_NULL·PERMISSION_DENIED |
| 입찰공고 제공기간 | 서비스 시작일 2025-01-06 | 포털 시간범위 1995-10 ~ 2025-01 |

클라이언트는 카탈로그의 두 문서 합집합에 있는 파라미터만 허용한다. 대소문자가 다른 이름은 거부한다. 실제로 어느 쪽이 맞는지는 실응답으로 정한다.

## 10. 실응답으로 확인해야 할 항목

`python -m bidloc verify-api --live`가 다음을 표본으로 확인하도록 구현되어 있다. 2026-09-16 현재 모두 **BLOCKED(키 없음)**다.

1. https 엔드포인트와 `serviceKey` 이름·Decoding 키 1회 인코딩이 실제로 동작하는가.
2. JSON `items`의 실제 형태, 오류 envelope 형식, 정상 무자료가 `00+totalCount 0`인지 `03`인지.
3. `numOfRows` 최대값, 조회기간 상한, 날짜 경계 포함 여부.
4. `bidNtceOrd`와 `rbidNo`가 각각 어떤 사건에 증가하는지, `bidClsfcNo`가 분할·분리 단위인지.
5. 참가가능지역에 시·군 이름과 복수지역이 어떻게 오는지(S05 성남시, S06 가평군·남양주시).
6. 면허제한 제한그룹의 AND·OR 의미(공고문 원문과 대조 필요).
7. `prtcptCnum`과 명부 행 수·고유 사업자번호 수의 일치 여부, 무효·공동수급 표기(`rmrk`).
8. 개찰순위 1위와 최종낙찰자가 다른 사례.
9. 2023·2024년 공고가 새 서비스(ad/as)로 조회되는가.
10. 도장·습식·방수·석공사업 업종코드와 면허제한 응답의 표기.
11. 참고자료 1.2 추가 필드가 실제 응답에 있는가.

## 11. P0 범위 밖 오퍼레이션

물품·용역·외자·기타 공고, 구매대상물품, e발주·혁신장터 첨부파일, 물품·용역·외자 개찰결과·낙찰·예비가격, 조달업체공급물품, 부정당제재업체 조회는 카탈로그에 이름·요청 파라미터·응답 필드명만 기록했다(`detail_level: compact`). 공사 분석에 쓰지 않는다.

## 12. 실응답 확인 결과 (2026-09-16, 호출 75회)

상세 근거: `config/live_evidence.yaml`, `docs/VALIDATION_REPORT.md`.

### 12.1 LIVE_VERIFIED 오퍼레이션

`getIndstrytyBaseLawrgltInfoList`, `getBidPblancListInfoCnstwk`, `getBidPblancListInfoCnstwkPPSSrch`, `getBidPblancListInfoLicenseLimit`, `getBidPblancListInfoPrtcptPsblRgn`, `getBidPblancListInfoCnstwkBsisAmount`, `getBidPblancListEvaluationIndstrytyMfrcInfo`, `getOpengResultListInfoCnstwk`, `getOpengResultListInfoOpengCompt`, `getScsbidListSttusCnstwk`.

변경이력(`getBidPblancListInfoChgHstryCnstwk`)은 호출은 성공했지만 결과 0건이라 DOCUMENTED로 둔다. 재입찰·유찰·검색조건 개찰/낙찰·예비가격·A값·사용자정보는 미호출이다.

### 12.2 이 문서 1~11장과 달라지는 점

| 항목 | 이 문서의 문서 기준 기술 | 실제 응답 |
|---|---|---|
| 스킴·인증 | https·`serviceKey` 실동작 UNVERIFIED | https + `serviceKey` + Decoding 키 1회 인코딩으로 75회 성공 |
| JSON 목록 | `body.items.item[]` | `body.items`가 리스트. envelope 숫자는 정수 |
| 정상 무자료 | 00+0건인지 03인지 UNVERIFIED | `resultCode 00` + `totalCount 0`. 입찰공고 서비스는 `items: []`, 낙찰정보 서비스는 `items` 키 없음 |
| 참고자료 1.2 신규 필드 | Swagger에 없음 | 실제로 제공됨 |
| Swagger 전용 필드 `d2bMngRgnLmtYn`, `undefined` | Swagger에만 있음 | 실제로 없음 |
| 기초금액 조회기간 필수 | Swagger 필수 | 공고번호만으로 조회됨 |
| `prtcptCnum` 의미 | UNVERIFIED | 개찰 명부 전체 행 수와 같음. 낙찰하한선 미달·전자입찰취소신청 포함(표본 5건) |
| 참가가능지역 | 시·군 표현 UNVERIFIED | 시·도 또는 시·군 단위 이름, 복수지역은 행 단위 |
| 업종코드 | 문서에 없음 | 4992 도장ㆍ습식ㆍ방수ㆍ석공사업(사용 Y). 면허제한 표기 `도장ㆍ습식ㆍ방수ㆍ석공사업/4992` |
| 주력분야 목록 | 형식만 문서화 | `[1^습식·방수공사]`, `[1^도장공사]` 관측 |
| 과거자료 | 2023·2024 제공 UNVERIFIED | 2023·2024년 구 번호체계 공고와 개찰결과 조회됨 |
| 공고 차수 조회 | - | 공고번호 조회 시 모든 차수 행이 함께 반환 |
| 데이터 품질 | - | 면허제한 행 1건에서 필드 값이 한 칸씩 밀린 결함 관측 |

### 12.3 연결 경로 실응답 상태

공고→면허제한, 공고→참가가능지역, 공고→기초금액, 공고→개찰단위, 개찰단위→명부, 개찰단위→최종낙찰: LIVE_VERIFIED(개찰완료 공사 6건). 관측 표본은 모두 입찰분류번호 `0`, 재입찰번호 `000`이므로 분할·재입찰 키 동작은 아직 UNVERIFIED다.
