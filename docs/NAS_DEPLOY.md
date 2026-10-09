# NAS(Synology DS1525+) 배포 안내

작성일: 2026-10-09(실환경 정보 반영). 대상: DSM 7.4, Container Manager(Docker 24), 메모리 16GB. 같은 NAS에서 시공노트(markview)가 라이브로 돌고 있다.

## 0. 이 NAS의 실제 환경 (시공노트 저장소 기록에서 옮김)

출처: `SrLaneControlSystem/.claude/skills/deploy-nas/SKILL.md`, `agent_docs/deployment_synology.md`, `docs/trial and error/docker/synology-nas-migration.md`, `docs/trial and error/docker/nas-docker-system-df-locks-encrypted-volume-outage.md`.

| 항목 | 값 | 비고 |
|---|---|---|
| 내부 IP / SSH | `192.168.0.18`, 사용자 `jhyeo`, 키 `~/.ssh/id_ed25519_nas`, 별칭 `ssh markview-nas` | 무비번 sudo. User Home 서비스 켜져 있음 |
| docker 경로 | `sudo /usr/local/bin/docker` | **`sudo docker`는 command not found.** sudo의 PATH에 `/usr/local/bin`이 없다 |
| git 경로 | `/usr/bin/git` | DSM Git Server 패키지 설치됨 |
| 볼륨 | `/volume1`만 있음(RAID1 8TB, **암호화 볼륨** cryptvol_1). NVMe `/volume2`는 없음 | 2026-09 기준 사용 7% |
| 시공노트 위치 | `/volume1/web_markview` (compose 스택 5개: postgres·mosquitto·backend·frontend·cloudflared) | **이 폴더에서 compose 명령을 치지 않는다** |
| DSM이 잡은 포트 | 80, 443, 5432, 5000, 5001 | 이 프로젝트의 8501·8600과는 충돌 없음(미확인: 실제 기동 때 확인) |
| scp | `scp -O` 필요 | 최신 OpenSSH의 SFTP 모드가 DSM sshd와 안 맞아 `Connection closed` |
| 백업 | 시공노트 DB 백업이 매일 04:10 실행(`/volume1/backup/markview/...`) | 이 프로젝트 일일 수집 00:12와 겹치지 않음 |

**절대 금지 명령(2026-09-08 실장애).** `docker system df` 하나로 암호화 볼륨의 디스크 I/O가 포화돼 SSH·DSM·컨테이너가 죽고 시공노트 공개 URL이 끊겼다. 복구는 물리 재부팅뿐이었다.

- 금지: `docker system df`, `docker ... prune`, `du -sh`, `ncdu`, `find /volume1`, 시공노트 폴더의 `docker compose down`, `docker volume rm`, `rm -r /volume1/...`
- 허용(즉시 끝나는 조회만): `df -h /volume1`, `docker ps -a`, `docker logs --tail 50`, `docker stats --no-stream`, `cat`, `free -m`, `uptime`, `cat /proc/mdstat`

이미지 빌드(`pip install`)도 I/O를 쓴다. 첫 빌드는 시공노트 사용이 적은 시간에 하고, 빌드 중 `uptime`으로 부하를 본다.

## 구성

| 서비스 | 하는 일 | 상한 | 기본 실행 |
|---|---|---|---|
| `collector` | 매일 00:12(한국 시간) 일일 수집 → 매일 추천 기록 | 메모리 3GB · CPU 2개 | 예 |
| `ui` | 화면(Streamlit) 8501 | 메모리 2GB · CPU 1개 | 아니요(`--profile web`) |
| `api` | 추천 API 8600(토큰 필수) | 메모리 1.5GB · CPU 1개 | 아니요(`--profile web`) |

- 일일 수집 순서는 PC와 같다: 범위 확장 → 추가 수집처 → 나라장터 스윕 → 확정 → 현황 → 매일 추천 → 진행 현황 페이지(`deploy/daily.sh`).
- 데이터는 컨테이너 밖 `./.local`(약 9.2GB)에, 키는 `./.env`(읽기 전용 연결)에 둔다. 둘 다 Git 제외이며 이미지에도 들어가지 않는다.
- 메모리 상한 합계는 최대 6.5GB다. 시공노트 스택이 쓰는 몫(postgres, 모델 252MB를 포함한 backend 등)과 합쳐 16GB 안에 들어야 한다. 기동 전에 `free -m`으로 여유를 확인하고, 모자라면 `--profile web`을 켜지 않는다.
- 컨테이너는 root로 돈다. `.local` 아래에 새로 생기는 파일은 NAS에서 root 소유가 된다. 나중에 PC로 되가져올 때 `sudo`가 필요하다.

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
5. 복사 대상: `.local.zip`(이미 만들어 둠), `.env`.

## 2. NAS에 놓기

경로는 `/volume1/web_bidloc`으로 한다(시공노트 `/volume1/web_markview`와 같은 방식, 다른 폴더). `/volume1/docker/...`를 쓰려면 Container Manager가 만든 `docker` 공유 폴더가 있는지 먼저 확인한다.

- **코드:** SSH에서
  ```sh
  ssh markview-nas
  sudo mkdir -p /volume1/web_bidloc && sudo chown jhyeo /volume1/web_bidloc
  /usr/bin/git clone https://github.com/SmartWay-jhyeo/SrGovermentCrawling.git /volume1/web_bidloc
  ```
  비공개 저장소라면 GitHub 토큰(읽기 전용 권장)이 필요하다. NAS에 GitHub용 SSH 키가 등록돼 있는지는 미확인이다. 또는 PC의 코드 폴더를 `.venv`·`.local` 없이 복사한다.
- **데이터:** PC에서 `.local` 폴더 전체를 `.local.zip`으로 묶어 둔 상태다(1.7GB, 풀면 약 12.3GB. 안에 `.local/real/bidloc.sqlite3` 4.8GB와 0바이트 WAL이 들어 있어 체크포인트는 끝난 상태). zip 하나를 보내고 NAS에서 푼다. 반드시 `scp -O`.
  ```powershell
  scp -O -i ~/.ssh/id_ed25519_nas .local.zip jhyeo@192.168.0.18:/volume1/web_bidloc/.local.zip
  ```
  NAS(SSH)에서 푼다. zip 안이 `.local/...`로 시작하므로 프로젝트 폴더에서 바로 푼다.
  ```sh
  cd /volume1/web_bidloc
  unzip -q .local.zip                          # unzip이 없으면 아래 컨테이너 방식
  # sudo /usr/local/bin/docker run --rm -v /volume1/web_bidloc:/work -w /work python:3.11-slim python -m zipfile -e .local.zip .
  ls -la .local/real/bidloc.sqlite3            # 4,799,225,856 바이트여야 한다
  rm .local.zip
  ```
  12GB를 암호화 볼륨에 쓰는 작업이라 시공노트 사용이 적은 시간에 한다. 컨테이너 방식으로 풀면 파일 소유가 root가 되는데, 컨테이너도 root로 돌아서 문제 없다.
- **키:** `.env`를 같은 폴더에 둔다.
  ```powershell
  scp -O -i ~/.ssh/id_ed25519_nas .env jhyeo@192.168.0.18:/volume1/web_bidloc/.env
  ```
  SSH에서 `chmod 600 /volume1/web_bidloc/.env`로 다른 사용자가 못 읽게 한다. 컨테이너는 root로 읽으므로 600이어도 동작한다.

## 3. 실행 (SSH)

`sudo docker`가 아니라 항상 `sudo /usr/local/bin/docker`다. 아래 명령은 전부 `/volume1/web_bidloc` 안에서 친다. 시공노트 폴더에서 치면 시공노트 스택을 건드린다.

```sh
cd /volume1/web_bidloc
free -m; df -h /volume1                       # 여유 확인(즉시 끝나는 조회)
sudo /usr/local/bin/docker compose build
# 첫 확인: 일일 수집을 한 번 바로 실행한다(끝나면 컨테이너는 사라진다).
sudo /usr/local/bin/docker compose run --rm collector bash deploy/daily.sh
# 각 단계 exit=0, recommend-daily의 WRITTEN을 확인한다.
tail -n 30 .local/real/reports/daily/backfill-$(date +%Y%m%d).log
# 매일 실행 시작
sudo /usr/local/bin/docker compose up -d
sudo /usr/local/bin/docker compose logs collector    # "next daily run ..." 시각 확인
sudo /usr/local/bin/docker ps -a --format '{{.Names}} {{.Status}}'   # 시공노트 컨테이너가 그대로 Up인지 같이 본다
```

Container Manager 화면으로도 할 수 있다: 프로젝트 → 생성 → 위 경로 선택 → `docker-compose.yml` 사용. 단 `--profile web`은 화면에서 못 고르므로 화면·API까지 켤 때는 SSH를 쓴다.

## 4. 화면·API (선택)

1. `.env`에 `BIDLOC_API_TOKEN=<길고 임의인 문자열>`을 추가한다. 토큰이 없으면 API가 외부 주소로 시작하지 않는다.
2. `sudo /usr/local/bin/docker compose --profile web up -d`
3. 접속 주소:
   - 화면: `http://192.168.0.18:8501`
   - API: `http://192.168.0.18:8600/api/v1/recommendations?region=남양주&license=4992`, 헤더 `Authorization: Bearer <토큰>`
4. **화면에는 로그인이 없다.** 공유기 포트포워딩이나 시공노트의 Cloudflare 터널에 붙여 외부로 열지 않는다. 외부 공개는 별도 결정 사항이다.
5. 첫 화면은 나라장터 스냅샷을 만드느라 수 분 걸릴 수 있다(PC 기준 약 4분). 매일 추천 단계가 새벽에 캐시를 다시 만들어 두므로 아침에는 빠르다.

## 5. 운영

- **상태:** `sudo /usr/local/bin/docker compose ps`
- **메모리:** `sudo /usr/local/bin/docker stats --no-stream`
- **금지:** 0절의 금지 목록. 특히 `docker system df`·`prune`·`du`·`find /volume1`은 시공노트를 내린 전력이 있다.
- **로그:** 컨테이너 로그는 10MB×3개로 제한한다. 일일 로그(`.local/real/reports/daily`)는 90일 보관한다.
- **코드 업데이트:** `cd /volume1/web_bidloc && /usr/bin/git pull` → `sudo /usr/local/bin/docker compose build` → `sudo /usr/local/bin/docker compose up -d`. 데이터와 `.env`는 그대로 둔다.
- **중지:** `/volume1/web_bidloc`에서 `sudo /usr/local/bin/docker compose down`. 이 프로젝트 컨테이너만 내려가고 데이터는 유지된다. 시공노트 폴더에서는 절대 치지 않는다(cloudflared 터널이 끊긴다).
- **백업:** `.local/real`을 Hyper Backup 대상에 넣는 것을 권한다. SQLite가 쓰는 중에 복사되지 않게 일일 수집이 끝난 뒤(새벽 수집 소요 시간 확인 후, 시공노트 백업 04:10 뒤 권장) 시각으로 잡는다.

## 전환 체크리스트

- [ ] 코드 커밋·푸시
- [ ] PC 예약 작업 비활성화, 화면·API 종료
- [ ] `.local.zip`·`.env` 복사(`scp -O`) → NAS에서 압축 해제, `.env`는 600
- [ ] NAS `free -m`·`df -h /volume1` 확인
- [ ] NAS에서 build → `run --rm collector bash deploy/daily.sh` → 로그 exit=0 확인
- [ ] `up -d` 후 시공노트 컨테이너 Up 유지 확인, 다음 날 00:12 로그 확인
- [ ] (선택) 토큰 설정 후 `--profile web`
