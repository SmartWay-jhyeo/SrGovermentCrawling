# 공식 출처와 확인 범위

## 실행형 앱 구현 문서 — 2026-10-08 추가

Streamlit 1.64.0을 설치해 Python 3.11.9에서 실행했다. 아래 공식 문서를 구현 기준으로 확인했으며 실제 호출 가능 여부는 AppTest와 브라우저로 따로 검사한다.

- [st.dataframe](https://docs.streamlit.io/develop/api-reference/data/st.dataframe): 표 표시와 행 선택.
- [st.button](https://docs.streamlit.io/develop/api-reference/widgets/st.button): 네이티브 버튼과 callback.
- [st.cache_data](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_data): 조회 스냅샷 캐싱.
- [배포 패키지](https://pypi.org/project/streamlit/): 설치 버전과 배포 정보.

아래 서비스 확인 기록은 최초 설계 시점의 이력이다. 최신 실연동 근거는 `VALIDATION_REPORT.md`를 우선한다.

확인일: 2026-09-16. 아래 출처는 설계의 근거와 개발 출발점이다.
실제 API 인증키로 공사 데이터를 호출·대조한 상태는 아니다. 소개 페이지는 API 요청 endpoint가 아니다.

## API 서비스

### S01. 조달청 나라장터 입찰공고정보서비스
`https://www.data.go.kr/data/15129394/openapi.do`

소개상 공사 공고와 금액·면허제한·참가가능지역 관련 정보가 제공된다. 업무에 맞는 오퍼레이션을 사용하라고 안내한다. 실제 필드, 결합키, 기간 제한, 2023~2026년 제공 범위는 P0에서 별도 검증한다.

### S02. 조달청 나라장터 낙찰정보서비스
`https://www.data.go.kr/data/15129397/openapi.do`

소개상 업무별 개찰순위·최종낙찰자와 개찰완료·재입찰·유찰 관련 정보를 제공한다. 참여업체수 필드와 명부의 포함범위·완전성은 응답 검증 대상이다. 개찰 1순위와 최종낙찰을 분리하는 설계의 출발점이다.

### S03. 조달청 나라장터 업종 및 근거법규서비스
`https://www.data.go.kr/data/15129467/openapi.do`

소개상 조회시점에 유효한 업종과 관련 업종 관계를 제공한다. 현재 조회값을 과거 코드의 완전한 이력으로 간주하지 않는다.

### S04. 조달청 나라장터 사용자정보 서비스
`https://www.data.go.kr/data/15129466/openapi.do`

나라장터에 등록된 업체·수요기관 정보다. 업체 식별·주소·업종 보강에 사용하되 건설업 면허 전체 등록명부와 구분한다. 과거 당시 주소를 복원할 수 있다고 가정하지 않는다.

## 실제 공고 사례 — API 응답이 아니라 공개 원문 사례

### S05. 성남시 소재지 제한·주력분야 사례
공고 식별자: `R26BK01695462`, 공고차수 `000`, 첨부 2.

`https://www.g2b.go.kr/pn/pnp/pnpe/UntyAtchFile/downloadFile.do?bidPbancNo=R26BK01695462&bidPbancOrd=000&fileSeq=2&fileType=&prcmBsneSeCd=07`

PDF 4쪽의 참가자격에서 습식·방수 주력분야와 성남시 소재지 조건을 확인했다. 특수공법 관련 별도 협약 항목도 있다. 지역과 업종만으로 모든 자격을 판정하지 않는 설계의 실제 사례다. 해당 공고 조건을 다른 공고 전체의 일반 법칙으로 적용하지 않는다.

### S06. 가평군·남양주시 복수지역 사례
공고 식별자: `R26BK01448245`, 공고차수 `000`, 첨부 2.

`https://www.g2b.go.kr/pn/pnp/pnpe/UntyAtchFile/downloadFile.do?bidPbancNo=R26BK01448245&bidPbancOrd=000&fileSeq=2&fileType=&prcmBsneSeCd=07`

2026-04-07 율길초 옥상방수공사 공고. PDF 3쪽에 두 지역 본점 조건과 습식·방수 주력분야가 나온다. 복수지역 집합·소액견적·단독참여 등을 구분하는 테스트 참고 사례다.

### S07. 참가 실적제한과 낙찰심사 구분 사례
공고 식별자: `R26BK01485179`, 공고차수 `000`, 첨부 2.

`https://www.g2b.go.kr/pn/pnp/pnpe/UntyAtchFile/downloadFile.do?bidPbancNo=R26BK01485179&bidPbancOrd=000&fileSeq=2&fileType=&prcmBsneSeCd=07`

PDF 2쪽 낙찰자 결정 항목에서 실적에 의한 참가제한 여부와 적격심사 평가를 별개로 다룬다. 특정 점수·금액 기준을 전 공고의 고정 규칙으로 가져오지 않는다.

## 개발도구 공식 문서

### S08. OpenAI Codex AGENTS.md
`https://developers.openai.com/codex/guides/agents-md/`

확인 시 공식 문서는 다음 주소로 이동되었다.
`https://learn.chatgpt.com/docs/agent-configuration/agents-md`

프로젝트 지침을 AGENTS.md로 관리하는 방식의 근거다.

### S09. Claude Code 프로젝트 지침·import
`https://code.claude.com/docs/en/memory`

CLAUDE.md 및 @경로 import의 근거다. 이 패키지의 CLAUDE.md는 공통 AGENTS.md를 참조한다.

### S10. Streamlit
`https://docs.streamlit.io/`

Python 기반 데이터 앱을 위한 공식 문서. 이 프로젝트는 UI 구현 선택으로 Streamlit을 제안한다. 외부 공개 배포는 MVP 범위가 아니다.

## 문서 확인 상태

| 항목 | 현재 상태 |
|---|---|
| 위 공식 서비스와 기능 소개 | 공개 설명 확인 |
| 공고 사례의 세부지역·주력분야·심사 조건 | 공개 PDF 및 해당 페이지 이미지 확인 |
| 개별 API endpoint·파라미터·필드의 구현 계약 | P0에서 작성 필요 |
| 사용자 키의 서비스 승인·실호출 성공 | 미검증 |
| 과거 전 기간 수집·누락률 | 미검증 |
| 지역별 시장규모·경쟁순위 | 산출하지 않음 |

설계의 임계값·아키텍처·집계 정책은 제안사항이며 제공기관 공식 기준이 아니다. 법적 참가·등록 조건은 해당 시점의 공고 원문과 적용 규정으로 별도 확인한다.
