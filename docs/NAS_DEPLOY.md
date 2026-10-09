# NAS(Synology DS1525+) 배포 안내

작성일: 2026-10-09. 대상: DSM 7.4, Container Manager(Docker 24), 메모리 16GB. 같은 NAS에서 시공노트가 돌고 있다.

## 구성

| 서비스 | 하는 일 | 상한 | 기본 실행 |
|---|---|---|---|
| `collector` | 매일 00:12(한국 시간) 일일 수집 → 매일 추천 기록 | 메모리 3GB · CPU 2개 · 디스크 유휴 우선순위 | 예 |
| `ui` | 화면(Streamlit) 8501 | 메모리 2GB · CPU 1개 | 아니요(`--profile web`) |
| `api` | 추천 API 8600(토큰 필수) | 메모리 1.5GB · CPU 1개 | 아니요(`--profile web`) |

- 일일 수집 순서는 PC와 같다: 범위 확장 → 추가 수집처 → 나라장터 스윕 → 확정 → 현황 → 매일 추천 → 진행 현황 페이지(`deploy/daily.sh`).
- 데이터는 컨테이너 밖 `./.local`(약 9.2GB)에, 키는 `./.env`(읽기 전용 연결)에 둔다. 둘 다 Git 제외이며 이미지에도 들어가지 않는다.
- 메모리 상한은 시공노트 보호용이다. 정확한 사용량은 NAS 첫 실행 때 확인한다. PC에서 추천 API가 데이터를 올린 뒤 약 300MB를 썼고, 스냅샷을 새로 만드는 단계는 그보다 많이 쓴다.

## 1. PC에서 준비

1. 코드 커밋·푸시(NAS에서 받기 위해).
2. PC 예약 작업 끄기. 같은 키로 PC와 NAS가 둘 다 수집하면 호출 한도를 같이 쓰지만, 내부 한도는 각자 자기 DB로만 센다.
   ```powershell
   Disable-ScheduledTask -TaskName 'bidloc-backfill-daily'
   ```
3. 화면·API 창을 닫아 DB 쓰기를 멈춘다.
4. DB를 한 파일로 정리한다(WAL 모드). 실행 후 `bidloc.sqlite3-wal`이 0바이트인지 확인한다.
   ```powershell
   .venv\Scripts\python -c "import sqlite3; c=sqlite3.connect('.local/real/bidloc.sqlite3'); print(c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()); c.close()"
   ```
5. 복사 대상: `.local\real\` 전체, `.env`.

## 2. NAS에 놓기

예시 경로는 `/volume1/docker/bid-location-lab`이다.

- **코드:** SSH에서 `git clone https://github.com/SmartWay-jhyeo/SrGovermentCrawling.git /volume1/docker/bid-location-lab`. 비공개 저장소라면 GitHub 토큰(읽기 전용 권장)이 필요하다. 또는 PC의 코드 폴더를 `.venv`·`.local` 없이 복사한다.
- **데이터:** PC의 `.local\real` → NAS의 `/volume1/docker/bid-location-lab/.local/real`.
  ```powershell
  robocopy .local\real \\<NAS>\docker\bid-location-lab\.local\real /E /COPY:DAT /R:2 /W:5
  ```
- **키:** `.env`를 같은 폴더에 둔다. SSH에서 `chmod 600 .env`로 다른 사용자가 못 읽게 한다.

## 3. 실행 (SSH, 관리자 권한)

```sh
cd /volume1/docker/bid-location-lab
sudo docker compose build
# 첫 확인: 일일 수집을 한 번 바로 실행한다(끝나면 컨테이너는 사라진다).
sudo docker compose run --rm collector bash deploy/daily.sh
# 각 단계 exit=0, recommend-daily의 WRITTEN을 확인한다.
tail -n 30 .local/real/reports/daily/backfill-$(date +%Y%m%d).log
# 매일 실행 시작
sudo docker compose up -d
sudo docker compose logs collector    # "next daily run ..." 시각 확인
```

Container Manager 화면으로도 할 수 있다: 프로젝트 → 생성 → 위 경로 선택 → `docker-compose.yml` 사용.

## 4. 화면·API (선택)

1. `.env`에 `BIDLOC_API_TOKEN=<길고 임의인 문자열>`을 추가한다. 토큰이 없으면 API가 외부 주소로 시작하지 않는다.
2. `sudo docker compose --profile web up -d`
3. 접속 주소:
   - 화면: `http://<NAS 내부 IP>:8501`
   - API: `http://<NAS 내부 IP>:8600/api/v1/recommendations?region=남양주&license=4992`, 헤더 `Authorization: Bearer <토큰>`
4. **화면에는 로그인이 없다.** 공유기 포트포워딩이나 cloudflared 터널로 외부에 열지 않는다. 외부 공개는 별도 결정 사항이다.
5. 첫 화면은 나라장터 스냅샷을 만드느라 수 분 걸릴 수 있다(PC 기준 약 4분). 매일 추천 단계가 새벽에 캐시를 다시 만들어 두므로 아침에는 빠르다.

## 5. 운영

- **상태:** `sudo docker compose ps`
- **메모리:** `sudo docker stats --no-stream`
- **금지:** `docker system df`는 쓰지 않는다. 디스크를 오래 붙잡아 NAS가 잠긴 이력이 있다.
- **로그:** 컨테이너 로그는 10MB×3개로 제한한다. 일일 로그(`.local/real/reports/daily`)는 90일 보관한다.
- **코드 업데이트:** `git pull` → `sudo docker compose build` → `sudo docker compose up -d`. 데이터와 `.env`는 그대로 둔다.
- **중지:** `sudo docker compose down`. 데이터는 유지된다.
- **백업:** `.local/real`을 Hyper Backup 대상에 넣는 것을 권한다.

## 전환 체크리스트

- [ ] 코드 커밋·푸시
- [ ] PC 예약 작업 비활성화, 화면·API 종료
- [ ] DB 체크포인트 후 `.local/real`·`.env` 복사(`.env`는 600)
- [ ] NAS에서 build → `run --rm collector bash deploy/daily.sh` → 로그 exit=0 확인
- [ ] `up -d` 후 다음 날 00:12 로그 확인
- [ ] (선택) 토큰 설정 후 `--profile web`
