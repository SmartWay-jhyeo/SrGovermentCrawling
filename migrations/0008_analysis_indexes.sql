-- 최신 차수·취소·재공고·제목 후보를 원본 JSON 페이지 전체 읽기 없이 탐색한다.
CREATE INDEX ix_bf_notice_analysis ON bf_notice_revision
    (bid_ntce_no, bid_ntce_ord, ntce_kind_nm, re_ntce_yn, bef_bid_ntce_no, bid_ntce_nm, quality_flag);
CREATE INDEX ix_bf_license_quality ON bf_license_limit (quality_flag);
