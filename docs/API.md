# 공고 추천 API 연동 안내

다른 웹사이트(서버)가 회사 조건을 보내면, 그 조건으로 참여할 수 있는 마감 전 공고를 나라장터·K-apt·LH·K-water·국방조달(D2B)·누리장터(민간)·남부발전·한전에서 함께 받아 간다. 읽기 전용 API이며 수집이나 외부 API 호출을 하지 않는다. 데이터는 매일 00:12 수집 결과다.

## 주소와 인증

| 실행 위치 | 주소 | 인증 |
|---|---|---|
| 인터넷(회사 다른 웹사이트 서버, 2026-10-12부터) | `https://bidmap-api.k-smartroad.com` | 필수: `Authorization: Bearer <BIDLOC_API_TOKEN>` |
| NAS(`docker compose --profile web`) | `http://<NAS 내부 IP>:8600` | 필수: `Authorization: Bearer <BIDLOC_API_TOKEN>` |
| PC(`./start-api.ps1`) | `http://127.0.0.1:8600` | 토큰을 설정하지 않으면 없음(이 PC에서만 접속) |

- 토큰은 NAS의 `.env`에 `BIDLOC_API_TOKEN=<길고 임의인 문자열>`로 둔다. 토큰이 틀리거나 없으면 모든 요청이 `401`이다.
- **웹사이트의 서버에서 호출한다.** 브라우저 자바스크립트에서 직접 부르면 토큰이 노출된다. API는 CORS 헤더도 보내지 않는다.
- 인터넷 주소는 Cloudflare 터널 `bidloc`의 공개 경로(`bidmap-api.k-smartroad.com` → `api:8600`)다. 토큰 없는 요청은 401이다. 공유기 포트포워딩은 쓰지 않는다. 연동 안내 페이지: https://claude.ai/artifact/Hxv4UKDioNtL1kHuCQjdfV (비공개 링크, 공유는 대표가 결정).

## GET /api/v1/recommendations

| 파라미터 | 필수 | 설명 | 기본값 |
|---|---|---|---|
| `region` | 예 | 본점 소재지. `남양주` / `남양주시` / `경기도 남양주시` | — |
| `license` | | `4992` 또는 `도장습식방수`. 현재 4992(도장·습식·방수·석공사업)만 검증 | `4992` |
| `sources` | | 쉼표 목록: `g2b`(나라장터), `kapt`, `lh`, `kwater`, `d2b`, `nuri`(누리장터 민간), `kospo`(남부발전), `kepco`(한전·발전자회사) | 전체 |
| `keyword` | | 공고명·공고번호·기관 검색어 | — |
| `min_amount`, `max_amount` | | 추정가격 범위(원). 추정가격이 없는 공고는 빠지고 `held_back`에 센다 | — |
| `include_unknown` | | `false`면 "조건 일치"만 | `true` |
| `title_shortlist` | | 면허 정보가 없는 공고를 방수·도장·도색·차선 등 제목 후보로 좁힌다. K-apt·K-water·남부발전·한전과, 면허제한 목록이 없는(공고서 참조) 누리장터 공고가 대상이다. 한전은 입찰참가자격 문장에 도장·습식·방수·석공이 있어도 후보로 둔다 | `true` |
| `site_provinces` | | 참가지역 정보가 없는 공고의 현장 시·도 범위: `home`(본점 시·도), `capital`(서울·경기·인천), `all`, 또는 `경기도,서울특별시` | `home` |
| `sort` | | `deadline`(마감 빠른 순) / `notice_date` / `amount` | `deadline` |
| `limit`, `offset` | | 페이지. `limit`은 1~200 | `50`, `0` |

정렬 순서: "조건 일치" 전체가 먼저 오고, 그다음 "확인 필요"가 온다. 같은 판정 안에서는 `sort` 순서를 따른다. 다음 페이지는 `offset=next_offset`으로 요청하며, `next_offset`이 `null`이면 마지막 페이지다.

### 응답

```json
{
  "profile": {"region": "경기도 남양주시", "region_input": "남양주", "license": "4992",
              "license_name": "도장·습식·방수·석공사업", "site_provinces": ["경기도"]},
  "as_of_kst": "2026-10-09T10:30:35+09:00",
  "complete": true,
  "warnings": [],
  "counts": {"total": 67, "by_state": {"조건 일치": 9, "확인 필요": 58},
             "by_source": {"g2b": 10, "kapt": 57},
             "held_back": {"참가지역 불일치": 140, "요구 면허에 4992 없음": 21}},
  "limit": 50, "offset": 0, "next_offset": 50,
  "items": [ { "...": "아래 표" } ],
  "notes": ["조건 일치도 참가자격 확정이 아닙니다. ..."],
  "data": {"data_mode": "real", "g2b": {"snapshot_id": "...", "generated_at_kst": "...", "collection_status": "DONE"},
           "g2b_loading": false}
}
```

`items[]`의 필드:

| 필드 | 뜻 |
|---|---|
| `key` | 공고 식별자(출처 포함). 예: `g2b:R26BK01761369-000`, `lh:1234567`. 저장·중복 제거에 쓴다 |
| `source`, `source_label` | 출처 코드와 이름(`나라장터`, `K-apt 공동주택`, `LH`, `K-water`, `국방조달(D2B)`, `누리장터(민간)`, `남부발전`, `한전`) |
| `title`, `notice_no`, `version`, `agency`, `demand_agency` | 공고명, 공고번호, 차수·상태, 공고기관, 수요기관(나라장터만) |
| `notice_date` | 공고일 `YYYY-MM-DD` |
| `deadline`, `deadline_label` | 마감 시각(ISO 8601, 한국 시간 `+09:00`)과 마감의 종류(입찰 마감, 입찰서 제출 마감 등). K-water는 날짜만 공개해 그날 23:59로 둔다 |
| `estimated_price` | 추정가격(원). 없으면 `null`이다. 0으로 바꾸지 않는다. 누리장터는 추정가격 필드가 없고, 남부발전은 금액 라벨이 값과 맞지 않아 둘 다 `null`이다 |
| `amounts` | 종류별 금액(추정가격·배정예산·기초금액·설계가격 등). 서로 합산하지 않는다. 누리장터는 `배정예산`·`기준금액(투찰 상한)`, 남부발전은 `… 표기값`(라벨 그대로)으로 온다 |
| `allowed_regions` | 참가 허용 지역 목록. `null`이면 출처가 정보를 주지 않은 것이다 |
| `site` | `{value, kind}`. 현장·단지 소재지와 그 종류. 참가지역과 다른 정보다. 남부발전은 제목의 발전소 이름, 한전은 발주 본부 이름으로 추정한 값이며 `kind`에 '추정'으로 적힌다. 한전에서 여러 시·도를 맡는 본부는 `value`가 '광주광역시·전라남도 중 한 곳'이고 `provinces`에 후보 시·도가 온다 |
| `license_requirements` | 요구 면허 목록(제공되는 출처만) |
| `contract` | 계약방식 |
| `evaluation` | `{state, license, license_basis, region, region_basis}`. `state`는 `조건 일치` 또는 `확인 필요` |
| `flags` | 확인할 점(예: 상세 미수집, 분류 추정, 지역 자료 차수 불일치) |
| `url` | 원문·첨부 링크. 나라장터 상세, K-apt 첨부, 누리장터 공고서 첨부만 있고, 없으면 `null` |
| `evidence` | 근거 응답 ID(원본 추적용) |

- `complete`가 `false`면 나라장터 데이터를 읽는 중이라 결과에서 빠진 상태다. 잠시 후 다시 요청한다.
- `held_back`은 조건 때문에 빠진 공고의 사유별 건수다. 화면에 "제외 N건"으로 보여 줄 수 있다.

### 오류

| 상태 | 본문 | 예 |
|---|---|---|
| `400` | `{"error": "...", "candidates": [...]}` | `region` 누락, 같은 이름 지역이 여러 곳(후보 목록 제공), 지원하지 않는 면허, `limit` 범위 초과 |
| `401` | `{"error": "인증이 필요합니다."}` | 토큰 없음·불일치 |

## GET /api/v1/collection/status

출처별 마지막 수집 시각과 상태, 나라장터 스냅샷 시점을 돌려준다. 웹사이트에 "자료 기준 시각"을 표시할 때 쓴다.

## GET /api/v1/health

`{"status": "ok", "data_mode": "real", "g2b_snapshot": "READY"}`. 이 엔드포인트도 토큰이 필요하다.

## 호출 예시

```sh
curl -G "http://<NAS 내부 IP>:8600/api/v1/recommendations" \
  -H "Authorization: Bearer $BIDLOC_API_TOKEN" \
  --data-urlencode "region=남양주" --data-urlencode "license=4992" --data-urlencode "limit=20"
```

```js
// 웹사이트 서버(Node 18+). 토큰은 서버 환경변수에만 둔다.
const url = new URL("http://<NAS 내부 IP>:8600/api/v1/recommendations");
url.search = new URLSearchParams({ region: "남양주", license: "4992", limit: "20" });
const res = await fetch(url, { headers: { Authorization: `Bearer ${process.env.BIDLOC_API_TOKEN}` } });
if (!res.ok) throw new Error(`추천 API ${res.status}: ${(await res.json()).error}`);
const data = await res.json();   // data.items, data.counts, data.notes
```

```python
import httpx
r = httpx.get("http://<NAS 내부 IP>:8600/api/v1/recommendations",
              params={"region": "남양주", "license": "4992", "limit": 20},
              headers={"Authorization": f"Bearer {token}"}, timeout=30)
r.raise_for_status()
items = r.json()["items"]
```

## 웹사이트에 표시할 때

- 데이터는 하루 한 번(00:12) 바뀐다. 응답을 10~30분 캐시해도 된다.
- "조건 일치"도 참가자격 확정이 아니다. `notes`의 안내 문구를 함께 보여 준다.
- `allowed_regions`(참가 허용 지역)와 `site`(현장 위치)를 같은 칸에 섞어 쓰지 않는다.
- 금액은 `amounts`의 종류 이름과 함께 보여 준다. `null`은 "미제공"으로 표시한다.

## 아직 없는 기능

공고 하나의 상세 조회(`/api/v1/notices/...`), 지역 비교 API, 화면에 저장한 조건으로 매일 만든 추천 목록 조회는 아직 없다. 필요하면 추가한다.
