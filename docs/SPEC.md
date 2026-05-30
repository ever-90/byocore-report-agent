# SPEC — BYOCORE 브랜드몰 운영 보고 에이전트

## 0. 목적
BYOCORE 브랜드몰의 일/주/월 운영 지표를 자동 수집·집계하여 카카오톡 '나에게 보내기' 로 보고하는 로컬 에이전트.

## 1. 단계 구분
- **1단계 (현재): READ-ONLY** — 수집·조회·보고만. 데이터 소스를 변경하는 API 호출 금지.
  - 산출: 폴더 구조 · 문서 · 설정 · 인증 스크립트 골격 · collectors 스텁.
- **2단계 (예정):** collectors 실제 구현(주문 매출 집계 / GEO 인용률 / 경쟁동향), 리포트 포맷터, 스케줄러, 이상 알림 임계치, 토큰 만료 추적.

## 2. 실행 환경
- OS: Windows
- Python: 3.14 (로컬 실행)
- 의존 라이브러리: `requests`, `python-dotenv`, `schedule`
- 비밀/설정: `.env` 만 사용(코드 하드코딩 금지). `.env` 는 git 제외(`.gitignore`).

## 3. 데이터 소스 (RECON 확정)

### 3.1 Cafe24 — 매출
- 기존 **GEO 앱 재사용**. scope: 주문(Order) 읽기(`mall.read_order`).
- Admin API: `GET https://{mall_id}.cafe24api.com/api/v2/admin/orders`
  → 기간별 주문 조회 후 매출 집계.
- 인증: OAuth2. **access_token 2시간 / refresh_token 2주.**
- 필수 헤더: **`X-Cafe24-Api-Version: 2026-03-01`**.
- 모듈: `src/cafe24_auth.py` (인증), `src/collectors/cafe24_sales.py` (집계, 스텁).

### 3.2 Kakao — 보고 전송
- '나에게 보내기' 메모 API: `POST https://kapi.kakao.com/v2/api/talk/memo/default/send`.
- 인증: 인가코드 → 토큰. **access_token 6시간 / refresh_token 2개월.**
- 동의항목(scope): **`talk_message`**.
- 전송 대상: **나에게 단독** (현재 구성원 확장 없음).
- 모듈: `src/kakao_auth.py` (인증), `src/kakao_send.py` (발송).

### 3.3 GEO 인용률 — Google Sheet
- 시트 ID: **`18rrHs_ifXSxZlY3mboRftCXO3KRl3JRBnZ2P-c2ufZA`**.
- 인증: Google 서비스 계정(`GOOGLE_SERVICE_ACCOUNT_JSON`). **읽기 전용.**
- 모듈: `src/collectors/geo_citation.py` (스텁). google 라이브러리는 2단계 추가.

### 3.4 경쟁동향 — market repo
- repo: **`ever-90/market-insight-byocore`**. 로컬 clone 산출물 읽기.
- 경로: `MARKET_REPO_PATH`.
- 모듈: `src/collectors/market_trend.py` (스텁).

## 4. 리포트 종류
| 주기 | 구성 |
|------|------|
| 일간 | 매출 + GEO 인용률 + 이상 알림 |
| 주간 | 추세 + Top 이슈 3 |
| 월간 | 종합 + KPI |

> 1단계에서는 리포트 포맷/스케줄을 구현하지 않는다. 데이터 계약(각 collector 반환 형태)은 각 스텁의 TODO 에 명시.

## 5. 환경변수 (.env 키)
| 키 | 설명 | 채우는 주체 |
|----|------|------------|
| `CAFE24_MALL_ID` | Cafe24 몰 ID | 사용자 |
| `CAFE24_CLIENT_ID` | 앱 client_id | 사용자 |
| `CAFE24_CLIENT_SECRET` | 앱 client_secret | 사용자 |
| `CAFE24_REDIRECT_URI` | OAuth 리다이렉트 URI | 사용자 |
| `KAKAO_REST_API_KEY` | 카카오 REST API 키(=client_id) | 사용자 |
| `KAKAO_CLIENT_SECRET` | 카카오 client_secret(설정 시) | 사용자 |
| `KAKAO_REDIRECT_URI` | 카카오 리다이렉트 URI | 사용자 |
| `GEO_SHEET_ID` | GEO 인용률 시트 ID (기본값 내장) | 사용자/기본값 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 서비스 계정 키 경로/JSON | 사용자 |
| `MARKET_REPO_PATH` | market repo 로컬 clone 경로 | 사용자 |
| `CAFE24_ACCESS_TOKEN` | Cafe24 액세스 토큰 | **auth 스크립트 자동** |
| `CAFE24_REFRESH_TOKEN` | Cafe24 리프레시 토큰 | **auth 스크립트 자동** |
| `KAKAO_ACCESS_TOKEN` | 카카오 액세스 토큰 | **auth 스크립트 자동** |
| `KAKAO_REFRESH_TOKEN` | 카카오 리프레시 토큰 | **auth 스크립트 자동** |

## 6. 아키텍처 (모듈 맵)
```
config.py ──(.env 로드 / 토큰 기록)── 모든 모듈의 설정 진입점
   ├─ cafe24_auth.py ── Cafe24 토큰 발급/갱신 → CAFE24_*_TOKEN 저장
   ├─ kakao_auth.py  ── 카카오 토큰 발급/갱신 → KAKAO_*_TOKEN 저장
   ├─ kakao_send.py  ── '나에게 보내기' 발송 (보고)
   └─ collectors/    ── [스텁] 수집
        ├─ cafe24_sales.py  (cafe24_auth 토큰 사용, orders GET)
        ├─ geo_citation.py  (Google Sheet 읽기)
        └─ market_trend.py  (로컬 repo 파일 읽기)
```
실행은 패키지 모듈로: `python -m src.<module>` (프로젝트 루트에서).
