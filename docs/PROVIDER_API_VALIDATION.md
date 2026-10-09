# 추가 조달 API 검증 — 2026-10-08

사용자 요청: 공식 API로 제공되는 소스만 대상으로 실제 응답과 최근 공사 제공 여부를 확인한다. 이번 작업은 소량 연결 검증이며 정식 수집·스케줄러·외부 공개 API 배포가 아니다.

## 결과

### 2026-10-08 — 남은 수집처 후속 검증

한전 endpoint는 `https://bigdata.kepco.co.kr/openapi/v1/electContract.do`이며 `noticeBeginDate`·`noticeEndDate`·한전 발급 `apiKey`를 요구한다. 공식 가이드의 공사 구분까지 확인했으나 키 미설정으로 실호출은 SKIPPED다. 코레일 공사 일부는 이미 나라장터 원본에서 실수집 확인했다. D2B·도로공사·국가철도공단·S2B는 확인한 API/자료의 범위와 최신 공사 미확정 항목을 구분했다. [후속 검증 보고서](REMAINING_PROVIDER_VALIDATION.md)가 아래 최초 조사 당시의 한전 endpoint·철도 미확정 기록을 보완한다.

### 2026-10-08 11:00 KST — 실제 수집 성공 (최신 상태)

사용자가 입력한 새 공통 키는 URL 인코딩된 값이었다. `decoded` 설정의 첫 호출은 세 곳 모두 403/30으로 실패했다. 키 값과 기존 나라장터 설정을 유지하고 추가 키 형식만 `encoded`로 변경한 뒤 세 서비스 모두 HTTP 200 / resultCode 00 응답을 확인했다. K-apt의 YYYYMMDD 날짜 파라미터도 기간 내 날짜가 있는 실제 응답으로 확인했다.

LH 응답은 XML 선언 `EUC-KR`이며 항목은 `response/body/item`에 직접 반복된다. 기존 파서는 UTF-8 및 `body/items/item`을 예상해 최초 응답을 MALFORMED로 분류했다. 안전한 XML 파싱·엔티티 차단은 유지하면서 선언된 한글 인코딩과 직접 item 구조를 지원하고, 저장 원본을 재파싱해 정상임을 확인했다. 두 항목 구조가 섞이면 오류로 남긴다. 원래 source_response 오류 기록을 성공으로 덮어쓰지 않았으며 이후 새 수집 응답은 정상으로 기록했다.

`provider_collect`를 구현해 K-apt·LH는 2026-10-01~08, K-water는 202610 월 조건을 수집했다. 이 시점 수신된 세 소스 공고일은 모두 10월 1~8일이었다. 조회 조건별 수신 행수와 totalCount 일치, 반복 페이지·중복 payload 없음, 날짜 파싱 누락 없음, staging SQLite integrity_check=ok를 확인했다. 공고 건수·제목·업무구분 관측값은 Git 제외 `.local/real/providers/collection-summary.json`에 보관한다. LH에서 실제 `시설공사` 업무 값을 관측했으며 물품·용역과 구분한다. K-apt 코드 의미 및 시군구 현장주소 연결은 아직 검증하지 않았다.

저장과 완전성 범위:

- 원본: 기존 source_response/raw, 이번 기록 ID 9878~9900.
- 별도 관측 저장소: `.local/real/providers/notices.sqlite3`. 페이지별 원본 ID, payload, 해시, 조회 조건, 다음 페이지 체크포인트를 보존한다. 미검증 공고번호만으로 기존 분석 테이블에 합치지 않는다.
- COMPLETE_RANGE는 해당 API 조회 범위의 totalCount만큼 저장했다는 의미이며, 전국 모든 조달·공사 전수 또는 독립 사업 수가 아니다. 서로 겹치는 조회 job을 합산하지 않는다. 완료 job 재실행은 캐시 재사용이며 변경분 재조회가 아니다.
- 예산 중단 시 다음 페이지부터 재개한다. totalCount 변경·반복 페이지·중복 payload·조기 짧은 페이지는 REVIEW_REQUIRED로 멈춘다. 오류의 totalCount는 null로 유지한다.
- K-apt/LH는 혼합 업무 목록이며 제목 기반 도장·방수·차선 후보도 참가자격 확정을 뜻하지 않는다. 새 소스의 UI·스케줄러·외부 API 연결은 아직 구현하지 않았다.

실행:

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python -m bidloc.provider_probe --live --providers kapt,lh,kwater --begin 2026-10-01 --end 2026-10-08 --max-calls 6
.venv/Scripts/python -m bidloc.provider_collect --live --begin 2026-10-01 --end 2026-10-08 --rows 100 --max-calls 16
```

이번 호출 총 23회: 최초 형식 오류 3회, 형식 수정 후 표본 5회, 범위 수집 15회. 같은 조건 재실행은 0회. 내부 일예산·실행예산·재시작 예약은 기존 공통 budget을 사용하며 증액하지 않았다. 수집 완료 후 RunRepository.finish 필수 인자 누락이 발생했으나 데이터·보고서는 저장되어 있었다. 인자를 보완하고 무호출 재실행, 원래 보고서와 저장 job 상태를 대조해 원래 실행 기록도 COMPLETED로 복구했다. 테스트에도 CLI 종료·실행 기록 검증을 추가했다.

실행 보고서:
- `.local/real/reports/provider-probe/provider-probe-20261008T105522-ed125e80.json` (형식 오류)
- `.local/real/reports/provider-probe/provider-probe-20261008T105601-c3889a83.json` (표본; 당시 LH 파싱 오류)
- `.local/real/providers/provider-collect-20261008T105915-735a12fe.json` (범위 수집 15회)
- `.local/real/providers/provider-collect-20261008T110009-9730a9f8.json` (재실행 0회)
- `.local/real/providers/validation-evidence.json` (키 검사·날짜 범위·DB 무결성)

아래 절은 인증 및 구현 과정의 과거 이력이다.

### 2026-10-08 — 추가 서비스 공통 키 분리

사용자가 승인된 세 서비스 키는 서로 같지만 기존 나라장터 키와는 다르다고 확인했다. 앞선 호출은 기존 키로 실행했으므로 새 키의 유효성 검증이 아니다. 이제 `PROVIDER_DATA_GO_KR_SERVICE_KEY`와 `PROVIDER_DATA_GO_KR_SERVICE_KEY_FORMAT`으로 세 소스에 사용할 별도 공통 키를 읽는다. 나라장터는 기존 설정을 유지한다. 새 키가 비어 있으면 기존 키로 대체하지 않고 HTTP 0회로 BLOCKED 처리한다. 두 키 모두 기존 마스킹 registry에 등록되며 공개 설정 요약에는 설정 여부만 표시한다.

로컬 `.env`에는 누락된 입력란만 추가하고 기존 값은 보존했다. 새 키를 사용자가 로컬에서 입력한 뒤 동일 probe 명령으로 검증해야 한다. 별도 키 적용은 probe에서만 새 세 서비스로 라우팅하며 기존 나라장터 수집 경로는 그대로다. 혼합 `--providers bid_notice,kapt,lh,kwater` 실행도 제공자마다 키를 선택하고 호출예산은 공유한다.

관련 오프라인 검증: `.venv/Scripts/python -m pytest tests/unit/test_provider_probe.py tests/unit/test_config.py -q` → 40 passed (5.72초). 합성 키로 요청별 키 선택·인코딩·마스킹, 환경변수 우선순위, 키 누락 시 HTTP 차단을 검사했다. 실제 새 키 연동은 입력 전 미검증이다.

### 2026-10-08 10:46 KST — 승인 완료 확인 후 호출

사용자가 세 서비스 모두 승인되었고 키가 서로 같다고 확인하여 동일 명령으로 다시 검사했다. 로컬에 설정된 기존 키로 각 1회 호출했으나 모두 HTTP 403 / code 30으로 데이터 미수집이다. 승인 사실(사용자 확인)과 인증 결과(실응답)는 별개로 유지한다. 기존 나라장터 키와 승인된 세 서비스 키가 같은지는 아직 확인되지 않았다.

보고서: `.local/real/reports/provider-probe/provider-probe-20261008T104622-c2bc149f.json`, source_response ID 9875~9877, 총 3회. 로컬 키 출처는 `.env`, 형식 decoded, 프로세스 키 덮어쓰기와 설정 경고 없음. 키를 채팅으로 받거나 기존 나라장터 키를 임의 교체하지 않는다.

### 2026-10-08 10:34 KST — 사용자 요청에 따른 수집 재시도

실행: `.venv/Scripts/python -m bidloc.provider_probe --live --providers kapt,lh,kwater --begin 2026-10-01 --end 2026-10-08 --max-calls 6`.

K-apt·LH·K-water를 각 1회 호출했으며 모두 HTTP 403 / code 30 / `등록되지 않은 서비스키`로 중단했다. 이번 실호출은 총 3회, 종료코드 1이다. 공고 데이터는 미수집이며 조회 총건수는 null이다. 원본 기록 ID는 9872~9874, 보고서는 Git 제외 `.local/real/reports/provider-probe/provider-probe-20261008T103447-3a78810c.json`이다. 보고서의 최상위 PARTIAL은 실행 요약이며 세 제공자 상태는 모두 BLOCKED다. 정식 수집·정규화·자동수집 연결은 인증 성공 응답이 없어 BLOCKED로 유지한다.

오프라인 재검증: `.venv/Scripts/python -m pytest tests/unit/test_provider_probe.py -q` → 8 passed (0.31초). 수집 코드 변경은 없고 인증 실패 뒤 반복 호출하지 않았다. 활용신청 여부·승인 반영·등록 키 일치 중 정확한 원인은 현재 응답만으로 확정할 수 없다.

수집처 구분: 기존 나라장터는 실수집·실응답 확인, 추가 세 곳은 공식 API 계약 확인 후 인증 대기다. 한전은 별도 포털 인증과 어댑터 검증이 남아 있다. 국방조달·도로공사·철도·S2B는 최신 시설공사 API 수집 경로를 확정하지 않았다. 따라서 “조사한 곳 중 오직 세 곳만 API 수집 가능”으로 요약하지 않는다.

### 앞선 최초 검증

| 소스 | 문서 | 이번 실호출 | 판단 |
|---|---|---|---|
| 나라장터 | 기존 공식 계약 사용 | 2회, HTTP 200 / 00, 서로 다른 페이지에서 표본 수신, 조회기간 안의 공고일 확인 | 최근 공사 표본 LIVE_VERIFIED. 전체 최신성·완전성 검증은 아님 |
| K-apt | DOCUMENTED | 1회, HTTP 403 / 30, 등록되지 않은 서비스키 | BLOCKED_AUTH; 데이터 미수집 |
| LH | DOCUMENTED | 1회, HTTP 403 / 30, 등록되지 않은 서비스키 | BLOCKED_AUTH; 데이터 미수집 |
| K-water | DOCUMENTED | 1회, HTTP 403 / 30, 등록되지 않은 서비스키 | BLOCKED_AUTH; 데이터 미수집 |
| 한전 | 공식 API 안내와 기관 포털 링크 확인 | SKIPPED, 별도 포털 인증키 미설정 | BLOCKED_CREDENTIAL; 데이터 미수집 |

실호출 합계 5회. 인증 실패는 재시도하지 않았다. 같은 키로 나라장터는 성공했으므로 기존 키를 일괄 교체하지 않는다. 새 서비스의 활용신청·승인 상태, 해당 서비스에 연결된 인증키, GW 서비스 전환 여부를 먼저 확인한다. 403/30만으로 미신청·승인 반영 지연·키 등록 불일치를 단정하지 않는다.

## 공식 출처와 호출 계약

원본 공개 명세는 `.local/real/provider-probe/specs/`에 저장했다. `config/provider_api_catalog.yaml`은 포털 HTML의 `swaggerJson`에서 HTTPS host, operation, 요청 파라미터, 응답 필드만 추출한 계약이다. 페이지 원본은 실행하지 않았다. 생성된 파일의 DOCUMENTED 상태를 인증 실패 후 LIVE_VERIFIED로 바꾸지 않았다.

| 소스 | 공식 서비스 | 확인한 경로 / 조건 |
|---|---|---|
| K-apt | [공동주택 입찰공고 정보제공](https://www.data.go.kr/data/15058166/openapi.do) | `https://apis.data.go.kr/1613000/ApHusBidPblAncInfoOfferServiceV3/getPblAncDeSearchV3`; startDate, endDate, pageNo, numOfRows |
| LH | [입찰공고정보_GW](https://www.data.go.kr/data/15159012/openapi.do) | `https://apis.data.go.kr/B552555/OpenBidInfoList/getOpenBidInfo`; tndrbidRegDtStart, tndrbidRegDtEnd, pageNo, numOfRows |
| K-water | [전자조달 입찰공고](https://www.data.go.kr/data/15101635/openapi.do) | `https://apis.data.go.kr/B500001/ebid/tndr3/cntrwkList`; searchDt(YYYYMM), pageNo, numOfRows, _type |
| 한전 | [전자입찰계약정보](https://www.data.go.kr/data/15148223/openapi.do) | [전력데이터 개방포털](https://bigdata.kepco.co.kr/cmsmain.do?scode=S01&pcode=000493&pstate=contract&redirect=Y); 별도 가입·인증키 필요. 실제 호출 endpoint·인증 파라미터는 아직 미확정 |

K-apt 날짜 파라미터명은 공식 Swagger로 확인했지만 날짜 문자열 형식은 명세에 적혀 있지 않았다. 이번 `YYYYMMDD`는 검증 후보이며 인증 오류로 유효성을 확인하지 못했다. LH는 공식 reqList 예시의 8자리 날짜를 사용했다. K-water는 날짜 범위가 아니라 월 단위 조회이므로 월 경계를 넘는 probe는 호출 전에 거부한다.

## 성공 응답 후에도 남는 검증

- K-apt는 공사 이외 용역·물품도 포함한다. 분류코드 의미와 실제 공사 필터를 검증해야 한다. `bidArea`는 명세상 시도코드이며 남양주시 현장 주소가 아니다. `aptCode` 주소 연결 검증이 필요하다. `bidState`의 신규/수정/재공고, `codeAuth`의 원천 플랫폼, `bidNum`의 원천별 유일성도 확인해야 한다.
- LH는 업무구분 `cstrtnJobGbNm`으로 공사를 구분할 실제 값을 확인해야 한다. `bidNum`과 `bidDegree`를 함께 보존한다. `zoneHqCd`(담당지역본부), `zoneRstrct1..4`(참가지역), 현장 주소를 혼합하지 않는다. 복수 요구면허의 관계도 미검증이다.
- K-water 공사 전용 operation은 확인했지만 목록 명세에 현장주소·참가가능지역·면허조건·첨부 URL이 보이지 않는다. 제목이나 발주부서를 현장주소로 대체할 수 없다. 공고번호만으로 차수·정정·재입찰 구분이 충분한지도 미검증이다.
- 한전 API가 발전사 전체를 포함한다는 근거는 없다. 별도 API 계약과 키가 확인되기 전에는 기존 공공데이터 키를 한전 호스트로 전송하지 않는다.
- 국방조달·도로공사·철도·S2B는 이번 실호출 대상에서 제외했다. 앞선 조사에서 최신 시설공사를 공식 API로 받는 경로가 확정되지 않았다. 이는 API 부재의 확정 판정이 아니다.

## 재현 명령과 관측

PowerShell에서 먼저 `$env:PYTHONIOENCODING='utf-8'`을 설정한다.

```powershell
# 명시적 플래그 없는 호출 차단 확인: BLOCKED, HTTP 0회
.venv/Scripts/python -m bidloc.provider_probe

# 3개 추가 소스: 각 1회 인증 오류 후 중단, 총 3회
.venv/Scripts/python -m bidloc.provider_probe --live --begin 2026-10-01 --end 2026-10-08 --max-calls 6

# 기존 키 대조: 나라장터 2페이지 성공, 총 2회
.venv/Scripts/python -m bidloc.provider_probe --live --providers bid_notice --begin 2026-10-07 --end 2026-10-07 --max-calls 2

# 새 검증 도구의 합성 응답 테스트: 8 passed
.venv/Scripts/python -m pytest tests/unit/test_provider_probe.py -q
```

실응답 보고서(실제 데이터 포함 가능, Git 제외):

- `.local/real/reports/provider-probe/provider-probe-20261008T095130-7c0a8ed3.json`
- `.local/real/reports/provider-probe/provider-probe-20261008T095206-235be78e.json`

요청·응답 원본은 기존 `source_response` / raw 저장소에 마스킹하여 기록했다. 분석용 공고·면허·지역 테이블에 추가 소스 데이터를 넣지 않았다. 이번 표본의 건수·공고일 등 실데이터 값은 위 로컬 보고서에만 남긴다.

호출 도구는 기존 ALLOW_LIVE_API + --live + real 모드 + 키 게이트, HTTPS 검증, 리다이렉트 차단, 응답 크기 제한, 마스킹·원본 해시를 재사용한다. 추가 서비스 내부 일일 상한은 min(20, LIVE_MAX_CALLS_PER_DAY), 나라장터는 기존 서비스 합산 상한을 사용하며 재시작·동시 실행 예약도 합산한다. 이번 probe는 HTTP 재시도 없이 1회만 시도하고, 일일 한도 초과는 기존 budget에 기록한다. 이 한도는 제공기관 승인 쿼터를 뜻하지 않는다.

## 다음 조치

1. 위 공식 링크에서 K-apt·LH·K-water 서비스별 활용신청 상태를 확인하고 미신청이면 신청한다. 승인된 서비스와 로컬 키의 연결 상태를 확인한다. 키는 채팅에 붙이지 않는다.
2. 승인 반영 후 `.venv/Scripts/python -m bidloc.provider_probe --live --max-calls 6`으로 최근 구간을 재검증한다. `--providers kapt`처럼 필요한 소스만 선택할 수 있다. 자동 반복 재시도 작업은 등록하지 않았다.
3. 최근 공사 응답·페이지·복합키·현장/허용지역·정정이력 검증을 통과한 소스만 정식 수집기로 연결한다. 새 소스의 데이터가 아직 없음을 유지한다.
