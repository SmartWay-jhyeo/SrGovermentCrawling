-- 3개년 백필: API별(오퍼레이션별) 일일 사용량, 재개 커서(파티션·작업 큐), 누적 정규화 테이블.
-- 원본 응답 본문은 source_response + RAW_RESPONSE_DIR에 전부 쌓이고, 아래 테이블은 키 단위로 누적한 해석 결과다.
-- 규칙: 키는 TEXT, 빈값·미조회는 NULL(0 아님), 원문 항목 JSON과 원본 응답 ID를 함께 보관한다.

CREATE TABLE api_daily_usage (
    budget_day_kst        TEXT NOT NULL CHECK (length(budget_day_kst) = 10),
    service_id            TEXT NOT NULL,
    operation             TEXT NOT NULL,
    calls_reserved        INTEGER NOT NULL DEFAULT 0 CHECK (calls_reserved >= 0),
    seeded_calls          INTEGER NOT NULL DEFAULT 0 CHECK (seeded_calls >= 0),
    quota_exhausted_at_utc TEXT,
    updated_at_utc        TEXT NOT NULL,
    PRIMARY KEY (budget_day_kst, service_id, operation)
);

CREATE TABLE bf_job (
    job_id           TEXT PRIMARY KEY,
    job_name         TEXT NOT NULL,
    range_begin_kst  TEXT NOT NULL,
    range_end_kst    TEXT NOT NULL,
    config_json      TEXT NOT NULL,
    config_sha256    TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('ACTIVE', 'COMPLETED', 'COMPLETED_WITH_GAPS')),
    created_at_utc   TEXT NOT NULL,
    updated_at_utc   TEXT NOT NULL
);
CREATE INDEX ix_bf_job_name ON bf_job (job_name, status);

-- 목록 수집 커서: 기간 파티션 + 다음 페이지
CREATE TABLE bf_partition (
    job_id          TEXT NOT NULL REFERENCES bf_job (job_id),
    window_begin    TEXT NOT NULL,
    window_end      TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('PENDING', 'IN_PROGRESS', 'DONE', 'FAILED')),
    next_page       INTEGER NOT NULL DEFAULT 1 CHECK (next_page >= 1),
    total_count     INTEGER,
    rows_received   INTEGER NOT NULL DEFAULT 0,
    restarts        INTEGER NOT NULL DEFAULT 0,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_outcome    TEXT,
    last_error      TEXT,
    updated_at_utc  TEXT NOT NULL,
    PRIMARY KEY (job_id, window_begin)
);

-- 상세 수집 커서: 공고·개찰단위별 작업 큐
CREATE TABLE bf_task (
    job_id          TEXT NOT NULL REFERENCES bf_job (job_id),
    task_type       TEXT NOT NULL CHECK (task_type IN ('LICENSE', 'REGION', 'OPENING', 'AWARD', 'ROSTER')),
    bid_ntce_no     TEXT NOT NULL,
    bid_ntce_ord    TEXT NOT NULL DEFAULT '',
    bid_clsfc_no    TEXT NOT NULL DEFAULT '',
    rbid_no         TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL CHECK (status IN ('PENDING', 'DEFERRED', 'DONE', 'SKIPPED', 'FAILED')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    not_before_kst  TEXT,
    total_count     INTEGER,
    rows_received   INTEGER,
    last_outcome    TEXT,
    last_error      TEXT,
    reason          TEXT,
    created_at_utc  TEXT NOT NULL,
    updated_at_utc  TEXT NOT NULL,
    PRIMARY KEY (job_id, task_type, bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no)
);
CREATE INDEX ix_bf_task_queue ON bf_task (job_id, task_type, status, not_before_kst);

CREATE TABLE bf_notice_revision (
    bid_ntce_no           TEXT NOT NULL,
    bid_ntce_ord          TEXT NOT NULL,
    ntce_kind_nm          TEXT,
    re_ntce_yn            TEXT,
    bef_bid_ntce_no       TEXT,
    bid_ntce_nm           TEXT,
    bid_ntce_dt           TEXT,
    rgst_dt               TEXT,
    openg_dt              TEXT,
    cntrct_cncls_mthd_nm  TEXT,
    sucsfbid_mthd_nm      TEXT,
    ntce_instt_cd         TEXT,
    ntce_instt_nm         TEXT,
    dminstt_cd            TEXT,
    dminstt_nm            TEXT,
    cnstrtsite_rgn_nm     TEXT,
    main_cnstty_nm        TEXT,
    indstryty_lmt_yn      TEXT,
    bdgt_amt              INTEGER,
    presmpt_prce          INTEGER,
    vat                   INTEGER,
    item_json             TEXT NOT NULL,
    item_sha256           TEXT NOT NULL,
    first_response_id     INTEGER REFERENCES source_response (id),
    last_response_id      INTEGER REFERENCES source_response (id),
    first_seen_utc        TEXT NOT NULL,
    last_seen_utc         TEXT NOT NULL,
    PRIMARY KEY (bid_ntce_no, bid_ntce_ord)
);
CREATE INDEX ix_bf_notice_dt ON bf_notice_revision (bid_ntce_dt);

-- 같은 키로 내용이 다른 응답이 오면 덮어쓰기 전에 이전 값을 남긴다.
CREATE TABLE bf_record_conflict (
    id               INTEGER PRIMARY KEY,
    table_name       TEXT NOT NULL,
    record_key       TEXT NOT NULL,
    old_sha256       TEXT NOT NULL,
    new_sha256       TEXT NOT NULL,
    old_item_json    TEXT NOT NULL,
    response_id      INTEGER REFERENCES source_response (id),
    detected_at_utc  TEXT NOT NULL
);

CREATE TABLE bf_notice_state (
    bid_ntce_no       TEXT PRIMARY KEY,
    relevance         TEXT NOT NULL CHECK (relevance IN ('PENDING', 'RELEVANT', 'NOT_RELEVANT', 'UNKNOWN', 'CANCELLED')),
    relevance_basis   TEXT,
    license_ord       TEXT,
    region_ord        TEXT,
    updated_at_utc    TEXT NOT NULL
);

CREATE TABLE bf_license_limit (
    bid_ntce_no              TEXT NOT NULL,
    bid_ntce_ord             TEXT NOT NULL,
    lmt_grp_no               TEXT NOT NULL,
    lmt_sno                  TEXT NOT NULL,
    lcns_lmt_nm              TEXT,
    license_code             TEXT,
    permsn_indstryty_list    TEXT,
    indstryty_mfrc_fld_list  TEXT,
    rgst_dt                  TEXT,
    bsns_div_nm              TEXT,
    quality_flag             TEXT,
    item_json                TEXT NOT NULL,
    response_id              INTEGER REFERENCES source_response (id),
    updated_at_utc           TEXT NOT NULL,
    PRIMARY KEY (bid_ntce_no, bid_ntce_ord, lmt_grp_no, lmt_sno)
);

CREATE TABLE bf_allowed_region (
    bid_ntce_no         TEXT NOT NULL,
    bid_ntce_ord        TEXT NOT NULL,
    lmt_sno             TEXT NOT NULL,
    prtcpt_psbl_rgn_nm  TEXT,
    item_json           TEXT NOT NULL,
    response_id         INTEGER REFERENCES source_response (id),
    updated_at_utc      TEXT NOT NULL,
    PRIMARY KEY (bid_ntce_no, bid_ntce_ord, lmt_sno)
);

CREATE TABLE bf_opening_unit (
    bid_ntce_no       TEXT NOT NULL,
    bid_ntce_ord      TEXT NOT NULL,
    bid_clsfc_no      TEXT NOT NULL,
    rbid_no           TEXT NOT NULL,
    progrs_div_cd_nm  TEXT,
    openg_dt          TEXT,
    prtcpt_cnum       INTEGER CHECK (prtcpt_cnum IS NULL OR prtcpt_cnum >= 0),
    prtcpt_cnum_raw   TEXT,
    openg_corp_info   TEXT,
    item_json         TEXT NOT NULL,
    response_id       INTEGER REFERENCES source_response (id),
    updated_at_utc    TEXT NOT NULL,
    PRIMARY KEY (bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no)
);

CREATE TABLE bf_award (
    bid_ntce_no       TEXT NOT NULL,
    bid_ntce_ord      TEXT NOT NULL,
    bid_clsfc_no      TEXT NOT NULL,
    rbid_no           TEXT NOT NULL,
    bidwinnr_bizno    TEXT,
    sucsfbid_amt      INTEGER,
    sucsfbid_rate     TEXT,
    rl_openg_dt       TEXT,
    fnl_sucsf_date    TEXT,
    prtcpt_cnum       INTEGER,
    item_json         TEXT NOT NULL,
    response_id       INTEGER REFERENCES source_response (id),
    updated_at_utc    TEXT NOT NULL,
    PRIMARY KEY (bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no)
);

CREATE TABLE bf_roster_row (
    bid_ntce_no    TEXT NOT NULL,
    bid_ntce_ord   TEXT NOT NULL,
    bid_clsfc_no   TEXT NOT NULL,
    rbid_no        TEXT NOT NULL,
    row_key        TEXT NOT NULL,
    prcbdr_bizno   TEXT,
    openg_rank     TEXT,
    bidprc_amt     INTEGER,
    rmrk           TEXT,
    item_json      TEXT NOT NULL,
    response_id    INTEGER REFERENCES source_response (id),
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY (bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no, row_key)
);
