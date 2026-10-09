#!/usr/bin/env bash
# 일일 수집 1회. Windows의 .local/scheduled/backfill_daily.ps1과 같은 순서로 실행한다.
# - 키는 /app/.env에서 읽고 출력하지 않는다. 실제 호출은 .env의 ALLOW_LIVE_API=true와 --live가 모두 있어야 한다.
# - 추가 수집처를 긴 나라장터 스윕보다 먼저 받는다. 스윕이 끊겨도 추가 수집처는 남는다.
# - NAS의 다른 서비스(시공노트)를 방해하지 않게 CPU는 낮은 우선순위, 디스크는 유휴(idle) 우선순위로 실행한다.
set -u
cd /app
LOG_DIR=.local/real/reports/daily
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/backfill-$(date +%Y%m%d).log"

step() {
    local title="$1"
    shift
    echo "===== ${title} $(date +%Y-%m-%dT%H:%M:%S) =====" >> "$LOG"
    nice -n 10 ionice -c3 -t "$@" >> "$LOG" 2>&1
    echo "exit=$?" >> "$LOG"
}

step sweep-extend python -m bidloc backfill sweep-extend --recollect-last 2
step provider-collect python -m bidloc.provider_collect --live --daily --max-calls 40
step sweep-run python -m bidloc backfill sweep-run --live
step sweep-finalize python -m bidloc backfill sweep-finalize
step sweep-status python -m bidloc backfill sweep-status
# 저장한 조건으로 오늘의 추천 목록을 남긴다. 저장된 DB만 읽고 네트워크를 쓰지 않는다.
step recommend-daily python -m bidloc.recommend --daily
step progress-html python tools/report_progress.py

# 90일 지난 일일 로그 정리
find "$LOG_DIR" -name '*.log' -mtime +90 -delete
