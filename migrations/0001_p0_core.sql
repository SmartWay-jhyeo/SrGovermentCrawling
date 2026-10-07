-- P0 기본 스키마: 실행 기록, 호출예산, 원본 응답 메타데이터, 검증 단계, 검증 관측값.
-- 공고·면허·지역·금액 정규화 테이블은 실응답으로 필드 의미를 확인한 뒤 P1 마이그레이션에서 추가한다.
-- 규칙: 공고번호·차수·분류번호·재입찰번호·사업자번호는 TEXT. 빈값/미조회는 NULL, 0과 구분한다.

CREATE TABLE api_run (
    run_id              TEXT PRIMARY KEY,
    command             TEXT NOT NULL,
    data_mode           TEXT NOT NULL CHECK (data_mode IN ('real', 'demo')),
    live                INTEGER NOT NULL CHECK (live IN (0, 1)),
    status              TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'PARTIAL', 'BLOCKED', 'SKIPPED', 'FAILED', 'ABORTED')),
    started_at_utc      TEXT NOT NULL,
    finished_at_utc     TEXT,
    max_calls_run       INTEGER NOT NULL CHECK (max_calls_run >= 0),
    calls_attempted     INTEGER NOT NULL DEFAULT 0 CHECK (calls_attempted >= 0),
    stop_reason         TEXT,
    resumed_from_run_id TEXT REFERENCES api_run (run_id),
    code_version        TEXT NOT NULL,
    catalog_sha256      TEXT,
    notes_json          TEXT
);

CREATE TABLE request_budget_daily (
    budget_day_kst        TEXT PRIMARY KEY CHECK (length(budget_day_kst) = 10),
    calls_reserved        INTEGER NOT NULL DEFAULT 0 CHECK (calls_reserved >= 0),
    daily_limit_last_seen INTEGER,
    updated_at_utc        TEXT NOT NULL
);

CREATE TABLE source_response (
    id                            INTEGER PRIMARY KEY,
    run_id                        TEXT REFERENCES api_run (run_id),
    service_id                    TEXT NOT NULL,
    operation                     TEXT NOT NULL,
    request_params_redacted_json  TEXT NOT NULL,
    request_url_redacted          TEXT NOT NULL,
    attempt_no                    INTEGER NOT NULL CHECK (attempt_no >= 1),
    requested_at_utc              TEXT NOT NULL,
    elapsed_ms                    INTEGER,
    http_status                   INTEGER,
    content_type                  TEXT,
    response_format               TEXT CHECK (response_format IN ('json', 'xml', 'text', 'empty', 'none')),
    envelope_shape                TEXT,
    result_code                   TEXT,
    result_msg                    TEXT,
    outcome                       TEXT NOT NULL,
    classification_basis          TEXT,
    page_no                       INTEGER,
    num_of_rows                   INTEGER,
    total_count                   INTEGER,
    item_count                    INTEGER,
    body_sha256                   TEXT,
    raw_path                      TEXT,
    body_bytes                    INTEGER,
    parser_version                TEXT NOT NULL,
    redaction_applied             INTEGER NOT NULL CHECK (redaction_applied IN (0, 1)),
    data_mode                     TEXT NOT NULL CHECK (data_mode IN ('real', 'demo')),
    error_detail                  TEXT
);
CREATE INDEX ix_source_response_run ON source_response (run_id);
CREATE INDEX ix_source_response_op ON source_response (service_id, operation, requested_at_utc);
CREATE INDEX ix_source_response_hash ON source_response (body_sha256);

-- verify-api 단계별 상태. 일일 한도 등으로 중단되면 NOT_RUN_* 상태로 이어받기 지점을 남긴다.
CREATE TABLE verify_step (
    id              INTEGER PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES api_run (run_id),
    step_key        TEXT NOT NULL,
    sample_key      TEXT,
    service_id      TEXT,
    operation       TEXT,
    status          TEXT NOT NULL CHECK (status IN ('DONE', 'DONE_EMPTY', 'FAILED', 'BLOCKED', 'SKIPPED', 'NOT_RUN_BUDGET', 'NOT_RUN_QUOTA', 'NOT_RUN_SERVICE_BLOCKED')),
    calls_used      INTEGER NOT NULL DEFAULT 0 CHECK (calls_used >= 0),
    summary_json    TEXT,
    started_at_utc  TEXT,
    finished_at_utc TEXT,
    UNIQUE (run_id, step_key)
);

-- 검증용 관측: 공고 차수 단위. (공고번호, 공고차수) 복합키. 공고번호 단독 키를 쓰지 않는다.
CREATE TABLE verify_notice_revision (
    id                 INTEGER PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES api_run (run_id),
    bid_ntce_no        TEXT NOT NULL,
    bid_ntce_ord       TEXT NOT NULL,
    ntce_kind_nm       TEXT,
    re_ntce_yn         TEXT,
    bef_bid_ntce_no    TEXT,
    bid_ntce_dt_raw    TEXT,
    rgst_dt_raw        TEXT,
    source_response_id INTEGER REFERENCES source_response (id),
    UNIQUE (run_id, bid_ntce_no, bid_ntce_ord)
);

-- 검증용 관측: 개찰 단위. (공고번호, 공고차수, 입찰분류번호, 재입찰번호) 복합키.
-- 공식 참가업체수(prtcptCnum)와 명부에서 센 수를 별도 컬럼으로 둔다. NULL은 0이 아니다.
CREATE TABLE verify_opening_unit (
    id                          INTEGER PRIMARY KEY,
    run_id                      TEXT NOT NULL REFERENCES api_run (run_id),
    bid_ntce_no                 TEXT NOT NULL,
    bid_ntce_ord                TEXT NOT NULL,
    bid_clsfc_no                TEXT NOT NULL,
    rbid_no                     TEXT NOT NULL,
    progrs_div_cd_nm            TEXT,
    openg_dt_raw                TEXT,
    official_prtcpt_cnum        INTEGER CHECK (official_prtcpt_cnum IS NULL OR official_prtcpt_cnum >= 0),
    official_prtcpt_cnum_status TEXT NOT NULL CHECK (official_prtcpt_cnum_status IN ('OBSERVED', 'BLANK_IN_RESPONSE', 'FIELD_ABSENT', 'INVALID', 'NOT_QUERIED', 'QUERY_FAILED')),
    roster_row_count            INTEGER CHECK (roster_row_count IS NULL OR roster_row_count >= 0),
    roster_unique_bizno         INTEGER CHECK (roster_unique_bizno IS NULL OR roster_unique_bizno >= 0),
    roster_status               TEXT NOT NULL CHECK (roster_status IN ('COMPLETE', 'INCOMPLETE', 'NO_DATA', 'NOT_QUERIED', 'QUERY_FAILED')),
    count_comparison            TEXT CHECK (count_comparison IN ('MATCH', 'MISMATCH', 'NOT_COMPARABLE')),
    linked_notice_revision_id   INTEGER REFERENCES verify_notice_revision (id),
    link_status                 TEXT NOT NULL CHECK (link_status IN ('LINKED', 'SAME_NO_OTHER_ORD', 'FORMAT_MISMATCH', 'NOT_LINKED')),
    source_response_id          INTEGER REFERENCES source_response (id),
    UNIQUE (run_id, bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no)
);
CREATE INDEX ix_verify_opening_notice ON verify_opening_unit (run_id, bid_ntce_no, bid_ntce_ord);
