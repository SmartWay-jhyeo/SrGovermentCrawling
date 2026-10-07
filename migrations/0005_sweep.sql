-- 기간 스윕 수집: 공고마다 상세를 부르지 않고, 날짜 구간으로 목록·면허제한·참가가능지역·개찰결과를
-- 한 번에 받는다(각 API의 inqryDiv 기간 조회). 행은 기존 bf_* 테이블에 그대로 누적한다.
CREATE TABLE IF NOT EXISTS sw_job (
    job_id           TEXT PRIMARY KEY,
    job_name         TEXT NOT NULL,
    range_begin_kst  TEXT NOT NULL,
    range_end_kst    TEXT NOT NULL,
    config_json      TEXT NOT NULL,
    config_sha256    TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('ACTIVE', 'COMPLETED', 'STOPPED')),
    created_at_utc   TEXT NOT NULL,
    updated_at_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sw_partition (
    job_id         TEXT NOT NULL REFERENCES sw_job(job_id),
    stage          TEXT NOT NULL,          -- LIST / LICENSE / REGION / OPENING
    window_begin   TEXT NOT NULL,          -- inqryBgnDt (YYYYMMDDHHMM, KST)
    window_end     TEXT NOT NULL,          -- inqryEndDt
    status         TEXT NOT NULL CHECK (status IN ('PENDING', 'IN_PROGRESS', 'DONE', 'FAILED')),
    next_page      INTEGER NOT NULL DEFAULT 1,
    total_count    INTEGER,
    rows_received  INTEGER NOT NULL DEFAULT 0,
    restarts       INTEGER NOT NULL DEFAULT 0,
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_outcome   TEXT,
    last_error     TEXT,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY (job_id, stage, window_begin)
);

CREATE INDEX IF NOT EXISTS ix_sw_partition_queue ON sw_partition (job_id, stage, status, window_begin);
