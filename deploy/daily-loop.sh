#!/usr/bin/env bash
# 수집기 컨테이너의 시작 명령. 매일 DAILY_RUN_AT(기본 00:12, Asia/Seoul)에 deploy/daily.sh를 1회 실행한다.
# DAILY_RUN_ON_START=1이면 컨테이너가 켜질 때 한 번 먼저 실행한다(첫 설치 확인용).
# 한 번 실패해도 다음 날 다시 실행한다. 같은 날 다시 돌리면 이미 받은 범위는 이어받기라 호출이 거의 없다.
set -u
RUN_AT="${DAILY_RUN_AT:-00:12}"

if [ "${DAILY_RUN_ON_START:-0}" = "1" ]; then
    echo "start-up run $(date '+%F %T %Z')"
    bash /app/deploy/daily.sh || echo "daily run exited with $?"
fi

while true; do
    now=$(date +%s)
    next=$(date -d "today ${RUN_AT}" +%s)
    if [ "$next" -le "$now" ]; then
        next=$(date -d "tomorrow ${RUN_AT}" +%s)
    fi
    echo "next daily run $(date -d "@${next}" '+%F %T %Z')"
    sleep $((next - now))
    bash /app/deploy/daily.sh || echo "daily run exited with $?"
done
