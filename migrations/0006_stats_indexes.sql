-- 기간 스윕으로 행이 수십만~수백만 건이 되면서 지역 통계 집계가 느려졌다.
-- 복합 PK의 앞부분만으로는 커버되지 않는 조회 경로에 인덱스를 만든다.
CREATE INDEX IF NOT EXISTS ix_bf_notice_state_relevance ON bf_notice_state (relevance);
CREATE INDEX IF NOT EXISTS ix_bf_license_code ON bf_license_limit (license_code, bid_ntce_no);
CREATE INDEX IF NOT EXISTS ix_bf_region_name ON bf_allowed_region (prtcpt_psbl_rgn_nm, bid_ntce_no);
CREATE INDEX IF NOT EXISTS ix_bf_opening_notice ON bf_opening_unit (bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no);
CREATE INDEX IF NOT EXISTS ix_bf_notice_site_rgn ON bf_notice_revision (cnstrtsite_rgn_nm);
