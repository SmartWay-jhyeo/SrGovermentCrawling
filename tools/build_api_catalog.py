"""공식 문서에서 config/api_catalog.yaml을 생성한다 (오프라인).

입력 디렉터리(--spec-dir)에 다음 파일이 있어야 한다. 모두 공공데이터포털에서 공개 다운로드한 원본이다.
  page_15129394.html  guide_bid.docx        (입찰공고정보서비스)
  page_15129397.html  guide_award.docx      (낙찰정보서비스)
  page_15129467.html  guide_indstryty.docx  (업종 및 근거법규서비스)
  page_15129466.html  guide_usr.docx        (사용자정보 서비스)
포털 페이지에는 Swagger 2.0 JSON이 내장되어 있고, docx는 '조달청 OpenAPI 참고자료'다.
이 도구는 API를 호출하지 않는다. 의미 해석(역할·연결키·count 정의)은 아래 ANNOTATIONS에 명시하며,
실응답으로 확인되기 전 상태는 DOCUMENTED/UNVERIFIED로만 기록한다.

사용법:
  python tools/build_api_catalog.py --spec-dir .local/official_specs/2026-09-16 --checked-on 2026-09-16 \
      --live-status BLOCKED --live-reason "..." --out config/api_catalog.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import yaml

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

SERVICES = [
    {"service_id": "bid_notice", "source_id": "S01", "portal_id": "15129394", "guide": "guide_bid.docx",
     "guide_name": "조달청_OpenAPI참고자료_나라장터_입찰공고정보서비스_1.2.docx", "atch_file_id": "FILE_000000003665344"},
    {"service_id": "bid_award", "source_id": "S02", "portal_id": "15129397", "guide": "guide_award.docx",
     "guide_name": "조달청_OpenAPI참고자료_나라장터_낙찰정보서비스_1.1.docx", "atch_file_id": "FILE_000000003666253"},
    {"service_id": "industry_law", "source_id": "S03", "portal_id": "15129467", "guide": "guide_indstryty.docx",
     "guide_name": "조달청_OpenAPI참고자료_나라장터_업종및근거법규서비스_1.1.docx", "atch_file_id": "FILE_000000003641692"},
    {"service_id": "user_info", "source_id": "S04", "portal_id": "15129466", "guide": "guide_usr.docx",
     "guide_name": "조달청_OpenAPI참고자료_나라장터_사용자정보서비스_1.1.docx", "atch_file_id": "FILE_000000003641691"},
]

P0_SCOPE = {
    "getBidPblancListInfoCnstwk", "getBidPblancListInfoCnstwkPPSSrch", "getBidPblancListInfoCnstwkBsisAmount",
    "getBidPblancListInfoChgHstryCnstwk", "getBidPblancListInfoLicenseLimit", "getBidPblancListInfoPrtcptPsblRgn",
    "getBidPblancListEvaluationIndstrytyMfrcInfo", "getBidPblancListBidPrceCalclAInfo",
    "getOpengResultListInfoCnstwk", "getOpengResultListInfoCnstwkPPSSrch", "getOpengResultListInfoOpengCompt",
    "getOpengResultListInfoRebid", "getOpengResultListInfoFailing", "getScsbidListSttusCnstwk",
    "getScsbidListSttusCnstwkPPSSrch", "getOpengResultListInfoCnstwkPreparPcDetail",
    "getIndstrytyBaseLawrgltInfoList",
}

# 필드 설명까지 전부 싣는 오퍼레이션. 나머지(물품·용역·외자 등)는 이름·필수 여부만 싣는다.
DETAIL_SCOPE = P0_SCOPE | {"getPrcrmntCorpBasicInfo02", "getPrcrmntCorpIndstrytyInfo02", "getDminsttInfo02"}

PII_FIELDS = {
    "ntceInsttOfclNm", "ntceInsttOfclTelNo", "ntceInsttOfclEmailAdrs", "dminsttOfclEmailAdrs", "exctvNm", "crdtrNm",
    "bidwinnrNm", "bidwinnrBizno", "bidwinnrCeoNm", "bidwinnrAdrs", "bidwinnrTelNo", "fnlSucsfCorpOfcl",
    "prcbdrBizno", "prcbdrNm", "prcbdrCeoNm", "opengCorpInfo", "bizno", "corpNm", "engCorpNm", "ceoNm", "telNo",
    "faxNo", "adrs", "dtlAdrs", "hmpgAdrs", "zip", "rgnNm", "jrsdctnDivNm", "corprtRgstNo",
}

_PII_PATTERN = re.compile(r"(TelNo|FaxNo|Email|Ofcl|CeoNm|Adrs|Bizno|bizno|corpNm|CorpNm)")


def is_pii(name: str) -> bool:
    """연락처·대표자·주소·사업자번호 계열 필드. 카탈로그에 문서 예시값을 싣지 않는다."""
    return name in PII_FIELDS or bool(_PII_PATTERN.search(name))


ROLE_FIELDS: dict[str, set[str]] = {
    "key": {"bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo", "rbidNtceNo", "untyNtceNo", "refNo",
            "orderPlanUntyNo", "bfSpecRgstNo", "befBidBbancNo", "compnoRsrvtnPrceSno", "lmtGrpNo", "lmtSno"},
    "notice_version": {"ntceKindNm", "reNtceYn", "chgNtceRsn", "chgDt", "rgstDt", "bidNtceDt", "rgstTyNm",
                       "befBidBbancNo", "ntceDivCd", "chgDataDivNm", "chgItemNm", "bfchgVal", "afchgVal",
                       "lcnsLmtCdRgstList", "ntceNticeDt"},
    "agency": {"ntceInsttCd", "ntceInsttNm", "dminsttCd", "dminsttNm", "linkInsttNm"},
    "site_region": {"cnstrtsiteRgnNm"},
    "allowed_region": {"prtcptPsblRgnNm"},
    "region_related_not_allowed_region": {"incntvRgnNm1", "incntvRgnNm2", "incntvRgnNm3", "incntvRgnNm4",
                                          "jntcontrctDutyRgnNm1", "jntcontrctDutyRgnNm2", "jntcontrctDutyRgnNm3",
                                          "rgnDutyJntcontrctYn", "rgnDutyJntcontrctRt", "cmmnSpldmdCorpRgnLmtYn",
                                          "rgnLmtBidLocplcJdgmBssCd", "rgnLmtBidLocplcJdgmBssNm",
                                          "brffcBidprcPermsnYn", "d2bMngRgnLmtYn"},
    "license": {"lcnsLmtNm", "permsnIndstrytyList", "indstrytyMfrcFldList", "indstrytyLmtYn", "mainCnsttyNm",
                "subsiCnsttyNm1", "subsiCnsttyNm2", "subsiCnsttyNm3", "subsiCnsttyNm4", "subsiCnsttyNm5",
                "subsiCnsttyNm6", "subsiCnsttyNm7", "subsiCnsttyNm8", "subsiCnsttyNm9", "mtltyAdvcPsblYn",
                "mtltyAdvcPsblYnCnstwkNm", "ciblAplYn", "tmpNm", "indstrytyMfrcFldNm", "cnsttyTyNm",
                "cnstrtWkrarDivCd", "cnstrtWkaraMtltyAdvcPsblYn", "indstrytyCd", "indstrytyNm", "indstrytyClsfcCd",
                "indstrytyClsfcNm", "inclsnLcns", "indstrytyUseYn", "baseLawordNm", "bsnsDivNm"},
    "entry_condition": {"bidPrtcptLmtYn", "cntrctCnclsMthdNm", "bidMethdNm", "dsgntCmptYn", "cmmnSpldmdMethdCd",
                        "cmmnSpldmdMethdNm", "cmmnSpldmdCnum", "cmmnSpldmdAgrmntClseDt", "cmmnSpldmdAgrmntRcptdocMethd",
                        "bidQlfctRgstDt", "intrbidYn", "indstrytyLmtYn"},
    "award_review": {"sucsfbidMthdCd", "sucsfbidMthdNm", "sucsfbidMthdAppStd", "sucsfbidLwltRate", "indstrytyEvlRt",
                     "subsiCnsttyIndstrytyEvlRt1", "subsiCnsttyIndstrytyEvlRt2", "subsiCnsttyIndstrytyEvlRt3",
                     "subsiCnsttyIndstrytyEvlRt4", "subsiCnsttyIndstrytyEvlRt5", "subsiCnsttyIndstrytyEvlRt6",
                     "subsiCnsttyIndstrytyEvlRt7", "subsiCnsttyIndstrytyEvlRt8", "subsiCnsttyIndstrytyEvlRt9",
                     "cnsttyAccotShreRateList", "cnstrtnAbltyEvlAmtList", "pqEvalYn", "pqApplDocRcptMthdNm",
                     "pqApplDocRcptDt", "arsltCmptYn", "arsltApplDocRcptMthdNm", "arsltApplDocRcptDt",
                     "indstrytyMfrcFldEvlYn", "evlRt", "bidwinrSlctnBssCd", "aplBssCntnts", "incntvRgnNm1",
                     "incntvRgnNm2", "incntvRgnNm3", "incntvRgnNm4", "bidwinrSlctnAplBssCntnts", "bidPrceEvlVal",
                     "techEvlVal", "techEvlNaturVal", "totalEvlAmtVal"},
    "amount": {"bdgtAmt", "presmptPrce", "VAT", "indutyVAT", "govsplyAmt", "mainCnsttyCnstwkPrearngAmt",
               "mainCnsttyPresmptPrce", "contrctrcnstrtnGovsplyMtrlAmt", "govcnstrtnGovsplyMtrlAmt", "bssamt",
               "bssAmtPurcnstcst", "evlBssAmt", "usefulAmt", "plnprc", "bsisPlnprc", "PrearngPrcePurcnstcst",
               "sucsfbidAmt", "bidprcAmt", "presmptAmt", "smkpAmt", "presmptPrceBgn", "presmptPrceEnd"},
    "amount_component": {"sftyMngcst", "sftyChckMngcst", "rtrfundNon", "envCnsrvcst", "scontrctPayprcePayGrntyFee",
                         "mrfnHealthInsrprm", "npnInsrprm", "odsnLngtrmrcprInsrprm", "qltyMngcst"},
    "count": {"prtcptCnum", "totalCount", "numOfRows", "pageNo"},
    "opening": {"progrsDivCdNm", "opengDt", "rlOpengDt", "opengCorpInfo", "opengRank", "opengRsltDivNm", "nobidRsn",
                "rbidRsn", "rmrk", "prcbdrBizno", "bidprcAmt", "bidprcrt", "bidprcDt", "drwtNo1", "drwtNo2",
                "opengRsltNtcCntnts", "rsrvtnPrceFileExistnceYn", "rbidOpengDt", "rbidPermsnYn", "bidClseDt"},
    "final_award": {"bidwinnrBizno", "bidwinnrNm", "sucsfbidAmt", "sucsfbidRate", "fnlSucsfDate"},
}

FIELD_NOTES: dict[str, str] = {
    "bidNtceOrd": "참고자료 설명: '해당 입찰공고에 대한 재공고 및 재입찰 등이 발생되었을 경우 증가되는 수'. 재입찰번호(rbidNo)가 별도로 있어 두 개념의 관계는 실응답 검증 필요.",
    "bidClsfcNo": "낙찰정보 참고자료: '동일한 입찰공고번호에 대한 집행일련번호'. 입찰공고정보 참고자료: '공고의 입찰분류번호'. 분할·분리 단위 여부는 실응답 검증 필요.",
    "rbidNo": "재입찰번호. 재입찰 목록 설명: '재입찰이 발생되었을 경우 증가되는 재입찰번호'.",
    "rbidNtceNo": "공사 예비가격상세의 포털 Swagger 필드명. 참고자료 docx는 같은 항목을 rbidNo로 표기(문서 간 불일치).",
    "presmptPrce": "참고자료: 부가가치세 및 조달 수수료를 제외한 금액(원화,원).",
    "bdgtAmt": "참고자료: 공고의 예산금액(원화,원). 부가세 포함 여부 미기재(UNKNOWN).",
    "bssamt": "참고자료: 기초금액(원화,원). 부가세 포함 여부 미기재(UNKNOWN).",
    "plnprc": "참고자료: 예정가격(원화,원). 계약체결 최고 상한 금액 의미.",
    "sucsfbidAmt": "참고자료: 최종낙찰금액(원화,원), 개찰완료 건에 대해 제공. 계약금액·매출과 다르다.",
    "sucsfbidRate": "참고자료: 최종낙찰금액/예정가격*100.",
    "bidprcAmt": "참고자료: 투찰금액(원화,원).",
    "mainCnsttyCnstwkPrearngAmt": "명칭은 주공종공사예정금액, 참고자료 설명은 '적격심사시 주공종추정금액' — 명칭·설명 불일치.",
    "prtcptCnum": "참고자료 설명은 '참가업체수'뿐이다. 무효·공동수급·중복 포함 범위 미기재. 명부 행 수와 별도 저장.",
    "opengDt": "입찰공고 참고자료: '개찰을 수행할 수 있는 시작일시이며 실제 개찰을 수행한 시간을 의미하지 않음'. 낙찰정보 개찰결과에서는 '입찰서를 개찰하는 일시'로 설명 — 실제 시각은 rlOpengDt 사용.",
    "rlOpengDt": "참고자료: 실제 개찰일시.",
    "opengCorpInfo": "참고자료: 단일 낙찰자 '업체명^사업자번호^대표자명^투찰금액^투찰율', 다수 낙찰자 '낙찰예정자 다수'+1위 금액·율, 협상계약은 금액·율 없음.",
    "opengRank": "참고자료: 개찰순위. 협상에 의한 계약은 협상순위.",
    "cnstrtsiteRgnNm": "공사현장 지역명(나라장터 화면 '공사현장'). 입찰 허용지역이 아니다.",
    "prtcptPsblRgnNm": "참가가능지역명(입찰 허용지역). 복수 행이면 집합으로 보존.",
    "incntvRgnNm1": "적격심사 가산점 지역. 입찰 허용지역이 아니다.",
    "jntcontrctDutyRgnNm1": "지역의무공동도급 지역. 입찰 허용지역이 아니다.",
    "rgnLmtBidLocplcJdgmBssNm": "지역제한입찰 소재지 판단기준명(예: 본사또는참여지사소재지). 참고자료 v1.2에만 있고 포털 Swagger에는 없다.",
    "befBidBbancNo": "참고자료 v1.2: 재공고일 경우 이전 입찰공고번호. 포털 Swagger에는 없다.",
    "lcnsLmtNm": "참고자료 예시: '면허명/코드'(설명 문구는 '면허제한코드/면허제한명'으로 순서가 반대).",
    "permsnIndstrytyList": "참고자료: '[허용업종명/허용업종코드],[...]' 형식, 제한 면허에서 허용되는 업종 전체 목록.",
    "indstrytyMfrcFldList": "참고자료: '[주력분야제한그룹순번^주력업종명1^주력업종명2...],[...]'; 나라장터 화면의 '와'는 '^', '또는'은 대괄호로 구분.",
    "lmtGrpNo": "제한그룹번호. 그룹 간·그룹 내 AND/OR 의미는 문서에 없다(UNVERIFIED).",
    "cnstrtnAbltyEvlAmtList": "참고자료: '[제한그룹번호^면허지역통합코드명^면허지역통합코드^시공능력평가금액]' 목록.",
    "cmmnSpldmdMethdCd": "참고자료 코드: 공500001 공동이행, 공500002 분담이행, 공500003 주계약자관리방식, 공500004 단독계약, 공500005 혼합방식, 공500006 공동이행 또는 분담이행, 공500007 혼합_단독, 공500008 혼합_공동, 공500012 해당없음.",
    "cnstrtWkrarDivCd": "참고자료 코드: 건060001 종합공사, 건060002 전문공사, 건060003 유지보수공사, 건060004 기타.",
    "bidwinrSlctnBssCd": "참고자료 코드: 계040000 해당없음, 계040001 국가계약법, 계040002 지방계약법, 계040003 자체기준.",
    "progrsDivCdNm": "참고자료: 유찰, 개찰완료, 재입찰로 구분.",
    "rmrk": "개찰완료내역 비고(참고자료 샘플 '낙찰'). 무효·탈락 표기 여부는 실응답 검증 필요.",
    "totalCount": "참고자료: 전체 결과 수/데이터 총 개수. 무엇의 행 수인지는 오퍼레이션별 검증 필요.",
    "d2bMngRgnLmtYn": "포털 Swagger에만 있는 필드(방사청관리지역제한여부). 참고자료에 없다.",
    "indstrytyCd": "업종 참고자료: 조달청 업종DB 분류체계의 4자리 숫자 코드. 도장·습식·방수·석공사업의 코드값은 문서에 없다.",
    "inclsnLcns": "업종 참고자료: '[순번^제한업종코드^제한업종명^허용업종코드^허용업종명]' 서브데이터셋(예시 형식이 일정하지 않음).",
}

OP_ANNOTATIONS: dict[str, dict[str, Any]] = {
    "getBidPblancListInfoCnstwk": {
        "p0_role": "공사 입찰공고 기본정보(공고 차수, 공고종류, 기관, 계약·낙찰방법, 예산·추정가격·VAT, 공사현장 지역)",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd"], "status": "UNVERIFIED",
                       "note": "응답에 입찰분류번호·재입찰번호가 없다. (공고번호, 차수) 행 유일성은 실응답 확인 필요."},
        "total_count_meaning": {"documented": "전체 결과 수", "status": "UNVERIFIED"},
    },
    "getBidPblancListInfoCnstwkPPSSrch": {
        "p0_role": "나라장터 검색조건(공고게시일시/개찰일시, 참가제한지역코드(시·도 2자리), 업종코드·업종명, 추정가격 범위)으로 공사 공고 조회. 표본 탐색용",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd"], "status": "UNVERIFIED"},
        "notes": ["참가제한지역코드는 시·도 2자리 코드뿐이다. 시·군 단위 서버 필터는 문서에 없다.",
                  "업종명은 일부 입력 조회 가능(문서). 서버 필터의 정확성·누락률은 미검증이므로 전수 수집 근거로 쓰지 않는다."],
    },
    "getBidPblancListInfoCnstwkBsisAmount": {
        "p0_role": "공사 기초금액과 예비가격 범위",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo"], "status": "UNVERIFIED"},
        "notes": ["inqryBgnDt/inqryEndDt: 참고자료는 조회구분 1일 때만 필수, 포털 Swagger는 항상 필수로 표기."],
    },
    "getBidPblancListInfoChgHstryCnstwk": {
        "p0_role": "공사 공고 변경이력(변경항목·전후값). 정정·취소 이력 보존용",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo", "chgDt", "chgItemNm"], "status": "UNVERIFIED"},
    },
    "getBidPblancListInfoLicenseLimit": {
        "p0_role": "공고별 면허제한(제한그룹, 면허명/코드, 허용업종목록, 주력분야 목록)",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd", "lmtGrpNo", "lmtSno"], "status": "UNVERIFIED"},
        "notes": ["업무구분 전용 오퍼레이션이 아니다(응답 bsnsDivNm). 공사 공고번호로 조회해 업무구분을 확인한다.",
                  "조회구분 2일 때 bidNtceOrd 필수(참고자료). 포털 Swagger는 항상 필수로 표기.",
                  "제한그룹 간/그룹 내 AND·OR 의미는 문서에 없다. 공고문 원문 대조 전에는 판정 규칙으로 쓰지 않는다."],
    },
    "getBidPblancListInfoPrtcptPsblRgn": {
        "p0_role": "공고별 참가가능지역(입찰 허용지역) 목록",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd", "lmtSno"], "status": "UNVERIFIED"},
        "notes": ["오퍼레이션 설명에는 '제한그룹번호'가 있으나 응답 필드 목록에는 lmtGrpNo가 없다(문서 내부 불일치).",
                  "시·군 단위 지역명이 들어오는지, 복수지역이 행 단위로 오는지는 실응답 검증 필요(S05/S06 표본)."],
    },
    "getBidPblancListEvaluationIndstrytyMfrcInfo": {
        "p0_role": "평가대상 주력분야(적격심사 등 낙찰심사 관련 가능성). 참가자격 판정과 분리",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd", "cnsttyTyNm", "indstrytyMfrcFldNm"], "status": "UNVERIFIED"},
        "notes": ["조회구분 1(공고게시일시) 범위 최대 1개월(참고자료).",
                  "참고자료 응답 표의 필드명 첫 글자가 대문자(CiblAplYn 등)지만 같은 문서의 응답 예시와 Swagger는 소문자."],
    },
    "getBidPblancListBidPrceCalclAInfo": {
        "p0_role": "입찰가격산식 A값 구성항목(낙찰가격 산정 관련). P0 분석 대상 아님",
        "notes": ["조회구분 1 범위 최대 1개월(참고자료)."],
    },
    "getOpengResultListInfoCnstwk": {
        "p0_role": "공사 개찰결과 목록(개찰단위별 진행구분, 참가업체수, 개찰업체정보)",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"], "status": "UNVERIFIED"},
        "total_count_meaning": {"documented": "데이터 총 개수", "status": "UNVERIFIED",
                                "note": "개찰단위 행 수로 추정되나 미검증"},
    },
    "getOpengResultListInfoCnstwkPPSSrch": {
        "p0_role": "검색조건 기반 공사 개찰결과 목록",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"], "status": "UNVERIFIED"},
    },
    "getOpengResultListInfoOpengCompt": {
        "p0_role": "개찰완료 건의 투찰업체별 개찰순위 명부",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo", "prcbdrBizno"], "status": "UNVERIFIED"},
        "total_count_meaning": {"documented": "데이터 총 개수", "status": "UNVERIFIED",
                                "note": "투찰 행 수로 추정. 무효·공동수급 행 포함 여부 미기재"},
        "notes": ["오퍼레이션 설명은 '최종낙찰업체사업자등록번호…'라고 쓰지만 응답 필드는 투찰업체(prcbdr*)다.",
                  "공종별입찰금액URL은 '차세대 나라장터 개편 이후 제공 불가'(참고자료)."],
    },
    "getOpengResultListInfoRebid": {
        "p0_role": "재입찰 목록(재입찰번호·사유)",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"], "status": "UNVERIFIED"},
    },
    "getOpengResultListInfoFailing": {
        "p0_role": "유찰 목록(유찰사유)",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"], "status": "UNVERIFIED"},
        "notes": ["bidClsfcNo: 참고자료는 옵션, 포털 Swagger는 필수로 표기."],
    },
    "getScsbidListSttusCnstwk": {
        "p0_role": "공사 최종낙찰자 목록(최종낙찰금액·낙찰률·실개찰일시·참가업체수)",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"], "status": "UNVERIFIED"},
    },
    "getScsbidListSttusCnstwkPPSSrch": {
        "p0_role": "검색조건 기반 공사 최종낙찰자 목록",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"], "status": "UNVERIFIED"},
        "notes": ["요청 bizno는 참고자료 v1.1(2025-09-25)에만 있고 포털 Swagger에는 없다.",
                  "응답 linkInsttNm은 포털 Swagger에만 있다."],
    },
    "getOpengResultListInfoCnstwkPreparPcDetail": {
        "p0_role": "공사 예비가격상세(예정가격·기초금액·복수예가). P0 분석 대상 아님",
        "record_key": {"fields": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo|rbidNtceNo", "compnoRsrvtnPrceSno"],
                       "status": "UNVERIFIED"},
    },
    "getIndstrytyBaseLawrgltInfoList": {
        "p0_role": "업종코드·업종명·근거법령·포함면허 조회(조회시점 유효 업종만 제공)",
        "record_key": {"fields": ["indstrytyCd"], "status": "UNVERIFIED"},
        "notes": ["서비스 설명: 법령 제정·개정·폐지로 업종이 추가/수정/삭제되면 조회시점에 유효한 업종 정보만 제공. 과거 코드 이력의 정답으로 쓰지 않는다.",
                  "포털 Swagger 응답에 'undefined'라는 필드가 있다(문서 오류로 보임)."],
    },
}

REGION_CODE_TABLE_DOC = {
    "11": "서울특별시", "26": "부산광역시", "27": "대구광역시", "28": "인천광역시", "29": "광주광역시", "30": "대전광역시",
    "31": "울산광역시", "36": "세종특별자치시", "41": "경기도", "42": "강원도", "43": "충청북도", "44": "충청남도",
    "45": "전라북도", "46": "전라남도", "47": "경상북도", "48": "경상남도", "50": "제주도", "51": "강원특별자치도",
    "52": "전북특별자치도", "12": "전남광주통합특별시", "99": "기타", "00": "전국(지역제한을 설정하지 않은 공고)",
}


# ------------------------------------------------------------------ 추출


def docx_lines(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))

    def para_text(p: ET.Element) -> str:
        out = []
        for node in p.iter():
            if node.tag == W + "t" and node.text:
                out.append(node.text)
            elif node.tag == W + "tab":
                out.append("\t")
            elif node.tag in (W + "br", W + "cr"):
                out.append("\n")
        return "".join(out)

    lines: list[str] = []

    def walk(el: ET.Element) -> None:
        for child in el:
            if child.tag == W + "p":
                t = para_text(child).strip()
                if t:
                    lines.append(t)
            elif child.tag == W + "tbl":
                for tr in child.iter(W + "tr"):
                    cells = []
                    for tc in tr.findall(W + "tc"):
                        ct = " / ".join(filter(None, (para_text(p).strip() for p in tc.iter(W + "p"))))
                        cells.append(ct.replace("\n", " "))
                    lines.append("| " + " | ".join(cells) + " |")
                lines.append("")
            elif child.tag == W + "sdt":
                content = child.find(W + "sdtContent")
                if content is not None:
                    walk(content)

    body = root.find(W + "body")
    assert body is not None
    walk(body)
    return lines


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_guide(lines: list[str]) -> dict[str, Any]:
    ops: dict[str, dict[str, Any]] = {}
    info: dict[str, Any] = {"revisions": [], "error_codes": [], "urls": {}, "service_version": None}
    cur: dict[str, Any] | None = None
    mode = None
    section = None
    for line in lines:
        if line.startswith("개정 이력") or line.startswith("변경 사항"):
            section = "revisions"
            continue
        if line.startswith("OPEN API 에러코드"):
            section, cur, mode = "errors", None, None
            continue
        if re.search(r"\]\s*오퍼레이션 명세", line):
            section = "op"
            cur = {"req": [], "res": [], "desc": "", "tps": None, "example_url": None}
            mode = "meta"
            continue
        if line.startswith("|") and section != "op":
            c = cells(line)
            for env in ("개발환경", "운영환경"):
                if env in c and any(x.startswith("http") for x in c):
                    info["urls"][env] = [x for x in c if x.startswith("http")][0]
            if "서비스 버전" in c:
                idx = c.index("서비스 버전")
                if idx + 1 < len(c):
                    info["service_version"] = c[idx + 1]
        if section == "revisions" and line.startswith("|"):
            c = cells(line)
            if len(c) >= 4 and re.match(r"^\d+\.\d+$", c[0]):
                info["revisions"].append({"version": c[0], "date": c[1], "type": c[2], "detail": " / ".join(c[3:])})
            continue
        if section == "revisions" and line and not line.startswith("|"):
            section = None
        if section == "errors" and line.startswith("|"):
            c = cells(line)
            if len(c) >= 3 and re.match(r"^\d{2}$", c[0]):
                info["error_codes"].append({"code": c[0], "name": c[1], "description": c[2],
                                            "action": " / ".join(c[3:]) if len(c) > 3 else ""})
            continue
        if cur is None:
            continue
        m = re.search(r"오퍼레이션명\(영문\)\s*\|\s*(get[A-Za-z0-9]+)", line)
        if m and mode == "meta":
            ops[m.group(1)] = cur
        m = re.search(r"오퍼레이션 설명\s*\|\s*(.*?)\s*\|\s*$", line)
        if m and mode == "meta":
            cur["desc"] = m.group(1)
        m = re.search(r"초당 최대 트랜잭션\s*\|\s*\[\s*(\d+)\s*tps", line)
        if m:
            cur["tps"] = int(m.group(1))
        if line.startswith("요청 메시지 명세"):
            mode = "req"
            continue
        if (line.startswith("응답 메시지 명세") or line.startswith("응답 메시지 예제")) and mode == "req":
            mode = "res"
            continue
        if line.startswith("요청 / 응답 메시지 예제"):
            mode = "example"
            continue
        if mode in ("req", "res") and line.startswith("|"):
            c = cells(line)
            if len(c) >= 6 and re.match(r"^[A-Za-z][A-Za-z0-9_]*$", c[0]):
                cur[mode].append({"name": c[0], "ko": c[1], "size": c[2], "req": c[3], "sample": c[4],
                                  "desc": " | ".join(c[5:])})
        if mode == "example" and "apis.data.go.kr" in line and cur["example_url"] is None:
            cur["example_url"] = line.strip().strip("|").strip()
    return {"ops": ops, **info}


def portal_page(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"swaggerJson\s*=\s*`(.*?)`\s*;", text, re.S)
    if not m:
        raise SystemExit(f"Swagger JSON을 찾지 못함: {path}")
    swagger = json.loads(m.group(1))
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    body = html.unescape(re.sub(r"(?s)<[^>]+>", "\n", body))
    lines = [l.strip() for l in body.splitlines() if l.strip()]

    def after(label: str, n: int = 1) -> str | None:
        for i, l in enumerate(lines):
            if l == label and i + n < len(lines):
                return " ".join(lines[i + 1:i + 1 + n])
        return None

    errors = []
    for i, l in enumerate(lines):
        if l == "에러메시지" and i + 2 < len(lines) and lines[i + 1] == "에러코드" and lines[i + 2] == "설명":
            j = i + 3
            while j + 2 < len(lines) and re.match(r"^[A-Z_]+$", lines[j]) and re.match(r"^\d{2}$", lines[j + 1]):
                errors.append({"message": lines[j], "code": lines[j + 1], "description": lines[j + 2]})
                j += 3
    traffic = after("신청 가능 트래픽", 3) or ""
    return {
        "swagger": swagger,
        "title": after("OpenAPI 명"),
        "time_range": after("시간범위"),
        "modified": after("수정일"),
        "registered": after("등록일"),
        "format": after("데이터 포맷"),
        "traffic": traffic.replace("참고문서", "").strip(),
        "review": (after("심의유형", 4) or "").strip(),
        "error_codes": errors,
    }


def swagger_ops(swagger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    definitions = swagger.get("definitions") or {}

    def resolve(schema: Any, depth: int = 0) -> Any:
        if depth > 20 or not isinstance(schema, dict):
            return schema
        ref = schema.get("$ref")
        if ref and ref.startswith("#/definitions/"):
            return resolve(definitions.get(ref.split("/")[-1], {}), depth + 1)
        return schema

    out: dict[str, dict[str, Any]] = {}
    for path, spec in (swagger.get("paths") or {}).items():
        get = spec.get("get") or {}
        params = spec.get("parameters") or get.get("parameters") or []
        schema = resolve(((get.get("responses") or {}).get("200") or {}).get("schema"))
        fields: dict[str, str] = {}
        try:
            body = resolve(schema["properties"]["body"])
            items = resolve(body["properties"]["items"])
            item = resolve(items["properties"]["item"])
            if item.get("type") == "array":
                item = resolve(item["items"])
            fields = {k: str(v.get("description", "")) for k, v in (item.get("properties") or {}).items()}
        except (KeyError, TypeError):
            fields = {}
        out[path.lstrip("/")] = {
            "summary": get.get("summary", ""),
            "description": get.get("description", ""),
            "params": {p["name"]: {"required": bool(p.get("required")), "description": p.get("description", "")}
                       for p in params if isinstance(p, dict) and "name" in p},
            "fields": fields,
        }
    vo_urls = {}
    for vo in swagger.get("swaggerOprtinVOs") or []:
        if isinstance(vo, dict) and vo.get("operationId"):
            vo_urls[vo["operationId"]] = vo.get("oprtinUrl")
    for op, url in vo_urls.items():
        if op in out:
            out[op]["backend_url"] = url
    return out


# ------------------------------------------------------------------ 조립


def business_div(op: str) -> str:
    for suffix, name in (("Cnstwk", "공사"), ("Servc", "용역"), ("Thng", "물품"), ("Frgcpt", "외자")):
        if suffix in op:
            return name
    if op in {"getBidPblancListInfoEtc", "getBidPblancListInfoEtcPPSSrch"}:
        return "기타"
    if op in {"getBidPblancListInfoLicenseLimit", "getBidPblancListInfoPrtcptPsblRgn", "getOpengResultListInfoOpengCompt",
              "getOpengResultListInfoRebid", "getOpengResultListInfoFailing", "getBidPblancListBidPrceCalclAInfo",
              "getBidPblancListEvaluationIndstrytyMfrcInfo", "getBidPblancListInfoEorderAtchFileInfo",
              "getBidPblancListPPIFnlRfpIssAtchFileInfo"}:
        return "업무구분 공통(요청 공고번호·응답 bsnsDivNm/개찰결과구분명으로 확인)"
    return "해당없음"


def roles_for(name: str) -> list[str]:
    roles = sorted(role for role, names in ROLE_FIELDS.items() if name in names)
    if re.search(r"Dt$|Date$", name) and "datetime" not in roles:
        roles.append("datetime")
    if "Url" in name:
        roles.append("url")
    if is_pii(name):
        roles.append("pii_minimize")
    return roles


def inqry_div_values(desc: str) -> dict[str, str]:
    values = {}
    for m in re.finditer(r"(\d)\s*[:.]\s*([^,0-9]+)", desc):
        values[m.group(1)] = m.group(2).strip().rstrip(".").strip()
    return values


def build_operation(service: dict[str, Any], op: str, guide_op: dict[str, Any] | None,
                    sw_op: dict[str, Any] | None) -> dict[str, Any]:
    sw_params = (sw_op or {}).get("params", {})
    auth_candidates = [n for n in sw_params if n.lower() == "servicekey"]
    auth_param = auth_candidates[0] if auth_candidates else "serviceKey"
    guide_req = {r["name"]: r for r in (guide_op or {}).get("req", [])}
    guide_auth = [n for n in guide_req if n.lower() == "servicekey"]

    names: list[str] = []
    for n in list(guide_req) + list(sw_params):
        if n.lower() == "servicekey" or any(x.lower() == n.lower() for x in names):
            continue
        names.append(n)
    request_params = []
    for n in names:
        g = guide_req.get(n) or next((v for k, v in guide_req.items() if k.lower() == n.lower()), None)
        s = sw_params.get(n) or next((v for k, v in sw_params.items() if k.lower() == n.lower()), None)
        entry: dict[str, Any] = {
            "name": n,
            "ko": (g or {}).get("ko") or "",
            "in_guide": g is not None,
            "in_swagger": s is not None,
            "required_guide": (g["req"].strip() == "1") if g else None,
            "required_swagger": s["required"] if s else None,
            "description": (g or {}).get("desc") or (s or {}).get("description", ""),
        }
        if g and g.get("sample") and not is_pii(n) and len(g["sample"]) <= 120:
            entry["sample_guide"] = g["sample"]
        if n == "inqryDiv" and g:
            entry["values_guide"] = inqry_div_values(g["desc"])
        if n in ("inqryBgnDt", "inqryEndDt") and g:
            fmt = re.search(r"YYYYMMDDHHMM", g["desc"])
            entry["format_guide"] = "YYYYMMDDHHMM" if fmt else "문서 미기재"
            limit = re.search(r"최대\s*\d+\s*(개월|일)", g["desc"])
            entry["range_limit_guide"] = limit.group(0) if limit else "문서 미기재"
        if n == "prtcptLmtRgnCd":
            entry["values_guide"] = REGION_CODE_TABLE_DOC
        request_params.append(entry)

    guide_res = [r for r in (guide_op or {}).get("res", [])
                 if r["name"] not in {"resultCode", "resultMsg", "numOfRows", "pageNo", "totalCount"}]
    sw_fields = (sw_op or {}).get("fields", {})
    field_names: list[str] = []
    for n in list(sw_fields) + [r["name"] for r in guide_res]:
        if not any(x.lower() == n.lower() for x in field_names):
            field_names.append(n)
    response_fields = []
    for n in field_names:
        g = next((r for r in guide_res if r["name"].lower() == n.lower()), None)
        s_name = next((k for k in sw_fields if k.lower() == n.lower()), None)
        entry = {
            "name": s_name or n,
            "ko": (g or {}).get("ko") or sw_fields.get(s_name or "", ""),
            "in_guide": g is not None,
            "in_swagger": s_name is not None,
        }
        if g is not None and s_name is not None and g["name"] != s_name:
            entry["guide_name_variant"] = g["name"]
        if g is not None:
            entry["required_guide"] = g["req"]
            entry["description"] = g["desc"]
            if g.get("sample") and not is_pii(n) and len(g["sample"]) <= 120:
                entry["sample_guide"] = g["sample"]
        roles = roles_for(s_name or n)
        if roles:
            entry["roles"] = roles
        note = FIELD_NOTES.get(s_name or n)
        if note:
            entry["note"] = note
        response_fields.append(entry)

    discrepancies = []
    for p in request_params:
        if p["in_guide"] and not p["in_swagger"]:
            discrepancies.append(f"요청 {p['name']}: 참고자료에만 있음")
        if p["in_swagger"] and not p["in_guide"]:
            discrepancies.append(f"요청 {p['name']}: 포털 Swagger에만 있음")
        if p["required_guide"] is not None and p["required_swagger"] is not None and p["required_guide"] != p["required_swagger"]:
            discrepancies.append(f"요청 {p['name']}: 필수 여부 불일치(참고자료 {p['required_guide']}, Swagger {p['required_swagger']})")
    for f in response_fields:
        if f["in_guide"] and not f["in_swagger"]:
            discrepancies.append(f"응답 {f['name']}: 참고자료에만 있음")
        if f["in_swagger"] and not f["in_guide"] and guide_op is not None:
            discrepancies.append(f"응답 {f['name']}: 포털 Swagger에만 있음")
        if f.get("guide_name_variant"):
            discrepancies.append(f"응답 {f['name']}: 참고자료 표기 {f['guide_name_variant']}")
    if guide_auth and guide_auth[0] != auth_param:
        discrepancies.append(f"인증 파라미터 표기: 참고자료 {guide_auth[0]}, 포털 Swagger {auth_param}")

    ann = OP_ANNOTATIONS.get(op, {})
    if op not in DETAIL_SCOPE:
        return {
            "title_ko": (sw_op or {}).get("summary") or "",
            "business_div": business_div(op),
            "in_p0_scope": False,
            "detail_level": "compact (P0 범위 밖: 필드 설명 생략, 원문은 참고자료 docx 참조)",
            "status": "DOCUMENTED" if (guide_op and sw_op) else "UNVERIFIED",
            "live_evidence": None,
            "auth_param": auth_param,
            "request_params": [{k: p[k] for k in ("name", "in_guide", "in_swagger", "required_guide", "required_swagger")}
                               for p in request_params],
            "response_fields": [{"name": f["name"]} for f in response_fields],
            "doc_discrepancies": discrepancies,
        }
    result: dict[str, Any] = {
        "title_ko": (sw_op or {}).get("summary") or "",
        "business_div": business_div(op),
        "in_p0_scope": op in P0_SCOPE,
        "status": "DOCUMENTED" if (guide_op and sw_op) else "UNVERIFIED",
        "live_evidence": None,
        "description_guide": (guide_op or {}).get("desc", ""),
        "description_swagger": (sw_op or {}).get("description", ""),
        "doc_tps": (guide_op or {}).get("tps"),
        "auth_param": auth_param,
        "response_type_param": "type (json 지정 시 JSON, 미지정 시 XML — 참고자료 설명)",
        "max_num_of_rows": "문서 미기재",
        "request_params": request_params,
        "response_fields": response_fields,
        "doc_discrepancies": discrepancies,
        "example_request_guide": (guide_op or {}).get("example_url"),
    }
    if (sw_op or {}).get("backend_url"):
        result["backend_url_in_swagger"] = sw_op["backend_url"]
    for key in ("p0_role", "record_key", "total_count_meaning", "notes"):
        if key in ann:
            result[key] = ann[key]
    return result


def build_catalog(spec_dir: Path, checked_on: str, live_status: str, live_reason: str) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    services: dict[str, Any] = {}
    portal_errors: list[dict[str, str]] = []
    guide_errors: list[dict[str, str]] = []
    for svc in SERVICES:
        page_path = spec_dir / f"page_{svc['portal_id']}.html"
        guide_path = spec_dir / svc["guide"]
        page = portal_page(page_path)
        guide = parse_guide(docx_lines(guide_path))
        sw = page["swagger"]
        host = str(sw.get("host", "")).strip("/")
        base_path = str(sw.get("basePath", "")).strip("/")
        base_url = "https://" + "/".join(p for p in (host, base_path) if p)
        sw_ops = swagger_ops(sw)
        guide_ops = guide["ops"]
        all_ops = sorted(set(sw_ops) | set(guide_ops))
        if not portal_errors and page["error_codes"]:
            portal_errors = page["error_codes"]
        if not guide_errors and guide["error_codes"]:
            guide_errors = guide["error_codes"]
        sources[svc["source_id"]] = {
            "title": page["title"],
            "portal_url": f"https://www.data.go.kr/data/{svc['portal_id']}/openapi.do",
            "portal_modified": page["modified"],
            "portal_registered": page["registered"],
            "portal_time_range": page["time_range"],
            "portal_data_format": page["format"],
            "portal_dev_traffic": page["traffic"],
            "portal_review": page["review"],
            "portal_page_sha256": hashlib.sha256(page_path.read_bytes()).hexdigest(),
            "swagger": {"swagger": sw.get("swagger"), "info_version": (sw.get("info") or {}).get("version"),
                        "host": sw.get("host"), "basePath": sw.get("basePath"), "schemes": sw.get("schemes")},
            "reference_doc": {
                "name": svc["guide_name"],
                "download_url": f"https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId={svc['atch_file_id']}&fileDetailSn=1",
                "sha256": hashlib.sha256(guide_path.read_bytes()).hexdigest(),
                "service_version_in_doc": guide["service_version"],
                "service_urls_in_doc": guide["urls"],
                "revisions": guide["revisions"],
            },
        }
        services[svc["service_id"]] = {
            "source": svc["source_id"],
            "service_name_ko": page["title"],
            "base_url": base_url,
            "base_url_basis": "포털 Swagger host/basePath + schemes(https 포함). 참고자료 docx의 서비스 URL은 http로 표기",
            "status": "DOCUMENTED",
            "access_status": "UNVERIFIED",
            "operations": {op: build_operation(svc, op, guide_ops.get(op), sw_ops.get(op)) for op in all_ops},
        }
    return {
        "catalog_schema_version": 1,
        "generated_by": "tools/build_api_catalog.py",
        "checked_on_kst": checked_on,
        "status_legend": {
            "DOCUMENTED": "공식 포털 Swagger와 조달청 참고자료 docx에서 확인. 실응답 미확인",
            "LIVE_VERIFIED": "인증된 실응답으로 확인(live_evidence에 실행 ID·일시 필요)",
            "UNVERIFIED": "문서 근거가 부족하거나 문서 간 불일치로 확정 못함",
            "BLOCKED": "키·승인·쿼터 등으로 확인 불가",
        },
        "live_verification": {"status": live_status, "reason": live_reason, "evidence_runs": []},
        "gateway": {
            "allowed_hosts": ["apis.data.go.kr"],
            "scheme": "https",
            "scheme_note": "참고자료 docx 예시는 http, 포털 Swagger schemes는 https와 http. 클라이언트는 https만 사용한다(TLS 검증 유지).",
            "auth_note": "포털 Swagger 파라미터명은 서비스별 serviceKey 또는 ServiceKey, 참고자료는 ServiceKey. 포털 가이드: Swagger 호출 시 일반 인증키(Decoding) 입력. 클라이언트는 Decoding 키를 한 번만 URL 인코딩한다.",
            "response_format_note": "type=json이면 JSON, 미지정이면 XML(참고자료). 오류 응답 envelope 형식은 문서에 없다.",
            "portal_error_codes": portal_errors,
            "guide_error_codes": guide_errors,
            "error_code_conflicts": [
                "코드 10: 포털 INVALID_REQUEST_PARAMETER_ERROR / 참고자료 'ServiceKey 파라미터가 없음'",
                "코드 20: 포털 SERVICE_KEY_IS_NULL, PERMISSION_DENIED, SERVICE_ACCESS_DENIED_ERROR(인증 에러) / 참고자료 '서비스 접근 거부(활용승인 안 됨)'",
                "코드 03: 참고자료 'No Data(데이터 없음 에러)' — 정상 무자료(resultCode 00, totalCount 0)와 실제 어떤 형태로 오는지 미검증",
            ],
        },
        "sources": sources,
        "services": services,
        "join_paths": JOIN_PATHS,
        "count_definitions": COUNT_DEFINITIONS,
        "amount_semantics": AMOUNT_SEMANTICS,
        "region_semantics": REGION_SEMANTICS,
        "industry_codes": INDUSTRY_CODES,
        "open_questions_for_live_verification": OPEN_QUESTIONS,
    }


JOIN_PATHS = [
    {"id": "notice_to_license", "from": "bid_notice.getBidPblancListInfoCnstwk", "to": "bid_notice.getBidPblancListInfoLicenseLimit",
     "keys": ["bidNtceNo", "bidNtceOrd"], "documented_basis": "면허제한 조회 요청(조회구분 2)에 bidNtceNo·bidNtceOrd", "status": "DOCUMENTED", "live": "UNVERIFIED"},
    {"id": "notice_to_allowed_region", "from": "bid_notice.getBidPblancListInfoCnstwk", "to": "bid_notice.getBidPblancListInfoPrtcptPsblRgn",
     "keys": ["bidNtceNo", "bidNtceOrd"], "documented_basis": "참가가능지역 조회 요청(조회구분 2)에 bidNtceNo·bidNtceOrd", "status": "DOCUMENTED", "live": "UNVERIFIED"},
    {"id": "notice_to_basis_amount", "from": "bid_notice.getBidPblancListInfoCnstwk", "to": "bid_notice.getBidPblancListInfoCnstwkBsisAmount",
     "keys": ["bidNtceNo", "bidNtceOrd"], "result_unit_key": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo"],
     "documented_basis": "기초금액 응답에 bidNtceOrd·bidClsfcNo", "status": "DOCUMENTED", "live": "UNVERIFIED",
     "note": "한 공고 차수에 분류번호별 복수 기초금액 행이 올 수 있는지 미검증. 재입찰마다 합산 금지."},
    {"id": "notice_to_opening_unit", "from": "bid_notice.getBidPblancListInfoCnstwk", "to": "bid_award.getOpengResultListInfoCnstwk",
     "keys": ["bidNtceNo", "bidNtceOrd"], "result_unit_key": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"],
     "documented_basis": "개찰결과 응답에 네 키 필드", "status": "DOCUMENTED", "live": "UNVERIFIED",
     "note": "공고번호 단독 조회(조회구분 4)는 가능하지만 결합은 차수까지 일치해야 한다. 차수 불일치는 연결 미확인."},
    {"id": "opening_unit_to_roster", "from": "bid_award.getOpengResultListInfoCnstwk", "to": "bid_award.getOpengResultListInfoOpengCompt",
     "keys": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"], "documented_basis": "개찰완료 목록 요청 파라미터 4종", "status": "DOCUMENTED", "live": "UNVERIFIED"},
    {"id": "opening_unit_to_final_award", "from": "bid_award.getOpengResultListInfoCnstwk", "to": "bid_award.getScsbidListSttusCnstwk",
     "keys": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"], "documented_basis": "낙찰 목록 응답에 네 키 필드", "status": "DOCUMENTED", "live": "UNVERIFIED"},
    {"id": "opening_unit_to_rebid_failing", "from": "bid_award.getOpengResultListInfoCnstwk", "to": "bid_award.getOpengResultListInfoRebid / getOpengResultListInfoFailing",
     "keys": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"], "status": "DOCUMENTED", "live": "UNVERIFIED"},
    {"id": "opening_unit_to_prepar_price", "from": "bid_award.getOpengResultListInfoCnstwk", "to": "bid_award.getOpengResultListInfoCnstwkPreparPcDetail",
     "keys": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo 또는 rbidNtceNo"], "status": "UNVERIFIED",
     "note": "재입찰번호 필드명이 참고자료(rbidNo)와 Swagger(rbidNtceNo)에서 다르다."},
    {"id": "notice_to_change_history", "from": "bid_notice.getBidPblancListInfoCnstwk", "to": "bid_notice.getBidPblancListInfoChgHstryCnstwk",
     "keys": ["bidNtceNo", "bidNtceOrd"], "result_unit_key": ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"], "status": "DOCUMENTED", "live": "UNVERIFIED"},
    {"id": "renotice_to_previous_notice", "from": "bid_notice.getBidPblancListInfoCnstwk(befBidBbancNo)", "to": "bid_notice.getBidPblancListInfoCnstwk(bidNtceNo)",
     "keys": ["befBidBbancNo -> bidNtceNo"], "status": "UNVERIFIED",
     "note": "참고자료 v1.2(2026-04-10)에만 있는 필드. 포털 Swagger에 없어 실제 제공 여부 미검증. 차수는 연결키에 없다."},
    {"id": "opening_roster_to_company", "from": "bid_award.getOpengResultListInfoOpengCompt(prcbdrBizno)", "to": "user_info.getPrcrmntCorpBasicInfo02(bizno)",
     "keys": ["prcbdrBizno -> bizno"], "status": "DOCUMENTED", "live": "UNVERIFIED",
     "note": "사용자정보는 조회시점 현재 정보다. 과거 입찰 당시 주소·업종으로 소급하지 않는다(후순위)."},
]

COUNT_DEFINITIONS = {
    "totalCount": {"documented": "전체 결과 수 / 데이터 총 개수", "meaning_status": "UNVERIFIED",
                   "rule": "조회조건 결과 행 수로만 해석. 공고 수·개찰단위 수·투찰 수와 동일시하지 않는다."},
    "prtcptCnum": {"documented": "참가업체수(개찰결과 목록, 낙찰 목록)", "meaning_status": "UNVERIFIED",
                   "rule": "공식 참여수로 별도 저장. 무효·공동수급·중복 포함 범위 미기재. 명부 수와 다르면 재검토 대상."},
    "roster_row_count": {"documented": "개찰완료 목록(getOpengResultListInfoOpengCompt) 행 수 — 내부 계산값", "meaning_status": "UNVERIFIED",
                         "rule": "모든 페이지 수집(완전성 검사 통과) 시에만 산출. 페이지 totalCount와 구분."},
    "roster_unique_bizno": {"documented": "명부 prcbdrBizno 고유 수 — 내부 계산값", "meaning_status": "UNVERIFIED",
                            "rule": "공동수급 구성원 표현 방식 미확인. 확인 전까지 투찰 주체 수로 단정하지 않는다."},
    "valid_bid_count": {"documented": "문서에 해당 필드 없음", "meaning_status": "UNVERIFIED",
                        "rule": "비고(rmrk) 등으로 유효/무효 구분이 확인되기 전에는 산출하지 않는다."},
    "zero_policy": "빈 응답·조회실패·미개찰·미조회는 0이 아니다. 명시적으로 0이 관측된 값만 0으로 기록한다.",
}

AMOUNT_SEMANTICS = {
    "kinds": {
        "budget_amount": {"field": "bdgtAmt", "vat": "UNKNOWN"},
        "estimated_price": {"field": "presmptPrce", "vat": "EXCLUDED(참고자료: 부가세·조달수수료 제외)"},
        "vat": {"field": "VAT", "vat": "VAT 자체"},
        "base_amount": {"field": "bssamt", "vat": "UNKNOWN", "source": "getBidPblancListInfoCnstwkBsisAmount / PreparPcDetail"},
        "planned_price": {"field": "plnprc", "vat": "UNKNOWN", "source": "getOpengResultListInfoCnstwkPreparPcDetail"},
        "final_award_amount": {"field": "sucsfbidAmt", "vat": "UNKNOWN", "source": "getScsbidListSttusCnstwk"},
        "bid_amount": {"field": "bidprcAmt", "vat": "UNKNOWN", "source": "getOpengResultListInfoOpengCompt"},
        "contract_amount": {"field": None, "note": "이번 4개 서비스 문서에 계약금액 필드 없음(계약정보 서비스 범위)"},
    },
    "unit": "참고자료 다수 금액 필드 설명에 '(원화,원)'. 단가·총액·장기계속 차수액 구분 필드는 문서에 없다(UNKNOWN).",
    "rules": ["금액 종류를 서로 대체하지 않는다.", "재입찰·재공고마다 기초금액을 합산하지 않는다.", "단가와 총액을 합산하지 않는다."],
}

REGION_SEMANTICS = {
    "allowed_region": "getBidPblancListInfoPrtcptPsblRgn.prtcptPsblRgnNm (행 단위, lmtSno)",
    "construction_site": "getBidPblancListInfoCnstwk.cnstrtsiteRgnNm",
    "ordering_agency": "ntceInsttCd/Nm, dminsttCd/Nm — 기관 주소는 공고 응답에 없음. user_info.getDminsttInfo02 주소는 조회시점 현재값",
    "not_allowed_region_fields": ["incntvRgnNm1~4(적격심사 가산지역)", "jntcontrctDutyRgnNm1~3(지역의무공동도급)",
                                  "rgnLmtBidLocplcJdgmBssNm(소재지 판단기준)", "cmmnSpldmdCorpRgnLmtYn(공동수급체 지역제한 여부)"],
    "server_filter": "PPSSrch 계열 prtcptLmtRgnCd는 시·도 2자리 코드(참고자료 코드표). 시·군 단위 필터 문서 없음",
    "doc_region_code_table_prtcptLmtRgnCd": REGION_CODE_TABLE_DOC,
    "admin_change_notes": ["강원도(42)·강원특별자치도(51), 전라북도(45)·전북특별자치도(52)가 코드표에 함께 있다.",
                           "참고자료 2026년 개정: 전남광주통합특별시(12) 지역코드 추가. 과거 29(광주)·46(전남) 공고와의 매핑은 유효기간 관리 필요."],
}

INDUSTRY_CODES = {
    "target_industry": "도장·습식·방수·석공사업",
    "target_main_fields": ["도장", "습식·방수", "석공"],
    "code": None,
    "status": "UNVERIFIED",
    "note": "4개 서비스 문서 어디에도 해당 업종코드 값이 없다. industry_law.getIndstrytyBaseLawrgltInfoList 실응답으로만 확인한다(조회시점 현재값).",
}

OPEN_QUESTIONS = [
    "https 엔드포인트와 serviceKey/ServiceKey 파라미터명이 실제로 동작하는가",
    "JSON items 형태(list / items.item object / 빈 문자열)와 오류 응답 envelope",
    "정상 무자료가 resultCode 00+totalCount 0인지 03인지",
    "numOfRows 최대값과 조회기간 최대 범위(대부분 문서 미기재)",
    "inqryBgnDt/inqryEndDt 경계 포함 여부",
    "bidNtceOrd가 정정공고·재공고·재입찰 중 무엇에 증가하는지, rbidNo와의 관계",
    "bidClsfcNo가 분할·분리발주 단위인지",
    "참가가능지역에 시·군 단위와 복수지역이 어떻게 오는지(S05/S06)",
    "면허제한 lmtGrpNo 그룹 간/내 AND·OR 의미(공고문 원문 대조 필요)",
    "prtcptCnum이 명부 행 수·고유 사업자번호 수와 일치하는지, 무효·공동수급 포함 여부",
    "rmrk로 유효/무효/공동수급이 표현되는지",
    "개찰순위 1위와 최종낙찰자가 다른 사례",
    "2023·2024년(차세대 나라장터 이전) 공고가 새 서비스(ad/as)로 조회되는지 — 포털 시간범위 표기(1995-10~2025-01)와 서비스 시작일(2025-01-06) 불일치",
    "도장·습식·방수·석공사업 업종코드와 면허제한 응답 표기",
    "참고자료 v1.2 추가 필드(befBidBbancNo, rgnLmtBidLocplcJdgmBss*, sucsfbidMthdAppStd)가 실제 응답에 있는지",
]


def merge_live_evidence(catalog: dict[str, Any], evidence: dict[str, Any]) -> None:
    """실응답 근거 파일을 병합한다. 근거 파일에 적힌 항목만 상태를 바꾼다."""
    if evidence.get("schema_version") != 1:
        raise SystemExit("live evidence schema_version 1 필요")
    run_ids = [r["run_id"] for r in evidence.get("runs") or []]
    if not run_ids:
        raise SystemExit("live evidence에 runs가 없다")
    checked = evidence.get("checked_on_kst")
    catalog["live_verification"]["evidence_runs"] = evidence["runs"]
    catalog["live_verification"]["total_http_calls"] = evidence.get("total_http_calls")
    catalog["live_verification"]["key_format_used"] = evidence.get("key_format_used")
    catalog["live_verification"]["sample_notices"] = evidence.get("sample_notices")
    for sid, info in (evidence.get("services") or {}).items():
        catalog["services"][sid]["access_status"] = info["access_status"]
        if info.get("note"):
            catalog["services"][sid]["access_note"] = info["note"]
    for key, info in (evidence.get("operations") or {}).items():
        sid, op = key.split(".", 1)
        target = catalog["services"][sid]["operations"][op]
        target["status"] = info["status"]
        target["live_observations"] = info.get("observations") or []
        target["live_calls"] = info.get("calls")
        if info["status"] == "LIVE_VERIFIED":
            target["live_evidence"] = {"checked_on_kst": checked, "runs": run_ids}
    findings = evidence.get("findings") or {}
    live_joins = findings.get("join_paths_live") or {}
    for path in catalog["join_paths"]:
        if path["id"] in live_joins:
            path["live"] = live_joins[path["id"]]
            path["live_note"] = findings.get("join_note")
    if "prtcptCnum" in findings:
        catalog["count_definitions"]["prtcptCnum"].update(findings["prtcptCnum"])
    if "industry_codes" in findings:
        catalog["industry_codes"].update(findings["industry_codes"])
    for key in ("history_availability", "response_format"):
        if key in findings:
            catalog["live_verification"][key] = findings[key]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spec-dir", required=True)
    parser.add_argument("--checked-on", required=True)
    parser.add_argument("--live-status", default="BLOCKED", choices=["BLOCKED", "UNVERIFIED", "PARTIAL_LIVE_VERIFIED", "LIVE_VERIFIED"])
    parser.add_argument("--live-reason", default="")
    parser.add_argument("--live-evidence", help="실응답 근거 YAML(config/live_evidence.yaml)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    catalog = build_catalog(Path(args.spec_dir), args.checked_on, args.live_status, args.live_reason)
    if args.live_evidence:
        merge_live_evidence(catalog, yaml.safe_load(Path(args.live_evidence).read_text(encoding="utf-8")))
    header = (
        "# 이 파일은 tools/build_api_catalog.py가 공식 문서(포털 Swagger + 조달청 참고자료 docx)에서 생성했다.\n"
        "# 상태 DOCUMENTED는 문서 확인일 뿐 실응답 검증이 아니다. 실응답 확인 후 LIVE_VERIFIED와 live_evidence를 기록한다.\n"
    )
    text = yaml.safe_dump(catalog, allow_unicode=True, sort_keys=False, width=100000)
    Path(args.out).write_text(header + text, encoding="utf-8")
    op_count = sum(len(s["operations"]) for s in catalog["services"].values())
    print(f"written {args.out}: services={len(catalog['services'])} operations={op_count}")


if __name__ == "__main__":
    main()
