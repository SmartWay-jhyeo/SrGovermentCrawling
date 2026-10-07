-- 공고 목록 행의 값 이상(범위 초과 금액 등)을 표시한다. 값 자체는 item_json 원본에 남는다.
ALTER TABLE bf_notice_revision ADD COLUMN quality_flag TEXT;
