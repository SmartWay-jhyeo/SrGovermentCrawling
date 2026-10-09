# 면허 입지 분석기 — NAS(Synology DS1525+, x86_64)용 이미지.
# 데이터(.local)와 키(.env)는 이미지에 넣지 않고 docker-compose.yml에서 실행 시 연결한다.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Seoul

# date 명령이 한국 시간으로 매일 실행 시각을 계산하도록 시간대 자료를 넣는다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-lock.txt ./
RUN pip install -r requirements-lock.txt

COPY . .
# Windows에서 복사한 스크립트의 CRLF가 bash 실행을 깨지 않게 정리한다.
RUN pip install --no-deps -e . && sed -i 's/\r$//' deploy/*.sh

CMD ["bash", "deploy/daily-loop.sh"]
