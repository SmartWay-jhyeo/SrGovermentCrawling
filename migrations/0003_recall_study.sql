-- 후보 필터 recall 측정과 면허제한 조회 재사용.

-- 면허제한 조회 완료 기록: (공고번호, 차수)를 완전 수집했는지. 행 0건도 '조회 완료'로 남겨 재호출을 막는다.
CREATE TABLE bf_license_fetch (
    bid_ntce_no     TEXT NOT NULL,
    bid_ntce_ord    TEXT NOT NULL,
    rows_received   INTEGER NOT NULL CHECK (rows_received >= 0),
    response_id     INTEGER REFERENCES source_response (id),
    fetched_at_utc  TEXT NOT NULL,
    PRIMARY KEY (bid_ntce_no, bid_ntce_ord)
);

-- recall 측정 연구: 무작위 시간창에서 전체 공사공고(필터 없음)를 받고, 필터 조회 결과와 비교한다.
CREATE TABLE rc_study (
    study_id         TEXT PRIMARY KEY,
    seed             INTEGER NOT NULL,
    range_begin_kst  TEXT NOT NULL,
    range_end_kst    TEXT NOT NULL,
    window_minutes   INTEGER NOT NULL CHECK (window_minutes > 0),
    config_json      TEXT NOT NULL,
    created_at_utc   TEXT NOT NULL
);

-- 시간창 × 조회방식(ALL=필터 없음, 그 외=설정한 필터 이름) 페이지 커서
CREATE TABLE rc_window_query (
    study_id        TEXT NOT NULL REFERENCES rc_study (study_id),
    window_begin    TEXT NOT NULL,
    window_end      TEXT NOT NULL,
    stratum         TEXT NOT NULL,
    query_kind      TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('PENDING', 'IN_PROGRESS', 'DONE', 'FAILED')),
    next_page       INTEGER NOT NULL DEFAULT 1,
    total_count     INTEGER,
    rows_received   INTEGER NOT NULL DEFAULT 0,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    updated_at_utc  TEXT NOT NULL,
    PRIMARY KEY (study_id, window_begin, query_kind)
);

-- 조회방식별로 나온 공고(공고번호·차수)
CREATE TABLE rc_hit (
    study_id      TEXT NOT NULL REFERENCES rc_study (study_id),
    query_kind    TEXT NOT NULL,
    window_begin  TEXT NOT NULL,
    bid_ntce_no   TEXT NOT NULL,
    bid_ntce_ord  TEXT NOT NULL,
    PRIMARY KEY (study_id, query_kind, window_begin, bid_ntce_no, bid_ntce_ord)
);

-- 전체 공고(ALL)의 실제 면허제한 판정(정답 집합)
CREATE TABLE rc_truth (
    study_id        TEXT NOT NULL REFERENCES rc_study (study_id),
    bid_ntce_no     TEXT NOT NULL,
    bid_ntce_ord    TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('PENDING', 'DONE', 'FAILED')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    license_rows    INTEGER,
    has_target_lcns INTEGER CHECK (has_target_lcns IN (0, 1)),
    has_target_any  INTEGER CHECK (has_target_any IN (0, 1)),
    flagged_rows    INTEGER,
    reused          INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    updated_at_utc  TEXT NOT NULL,
    PRIMARY KEY (study_id, bid_ntce_no)
);
