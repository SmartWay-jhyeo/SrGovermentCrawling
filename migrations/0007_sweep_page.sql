-- 페이지 반복 감지: 커서/행 저장과 같은 트랜잭션으로 기록한다.
CREATE TABLE sw_page (
    job_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    window_begin TEXT NOT NULL,
    page_no INTEGER NOT NULL,
    item_hash TEXT NOT NULL,
    response_id INTEGER REFERENCES source_response(id),
    PRIMARY KEY (job_id, stage, window_begin, page_no),
    FOREIGN KEY (job_id, stage, window_begin) REFERENCES sw_partition(job_id, stage, window_begin)
);
CREATE INDEX idx_sw_page_hash ON sw_page(job_id, stage, window_begin, item_hash);
