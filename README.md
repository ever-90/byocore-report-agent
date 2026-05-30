# BYOCORE 브랜드몰 운영 보고 에이전트

BYOCORE 브랜드몰의 운영 지표를 자동 수집·집계하여 **카카오톡 '나에게 보내기'** 로 보고하는 로컬 에이전트.

> **1단계 = READ-ONLY.** 수집·조회·보고만 수행하며, 데이터 소스를 변경하는 쓰기/변경 API 는 절대 호출하지 않습니다.

---

## 전체 맵

```
byocore-report-agent/
├─ README.md              ← (현재 파일) 전체 맵 / 빠른 시작
├─ docs/
│  ├─ SPEC.md             명세: 목적·데이터 소스·리포트·환경변수·아키텍처
│  ├─ RULESET.md          가드레일: READ-ONLY / 비밀관리 / 토큰 / 전송정책
│  └─ PROGRESS.md         진행 상황 체크리스트 (1단계 완료 / 2단계 예정)
├─ src/
│  ├─ config.py           .env 로더 + 토큰 자동 기록 헬퍼 (설정 단일 진입점)
│  ├─ cafe24_auth.py      Cafe24 OAuth 토큰 발급/자동갱신
│  ├─ kakao_auth.py       카카오 인가코드→토큰 / 자동갱신
│  ├─ kakao_send.py       '나에게 보내기' 메모 발송 (보고 채널)
│  └─ collectors/         [1단계 스텁]
│     ├─ cafe24_sales.py  주문 GET → 매출 집계
│     ├─ geo_citation.py  구글시트 GEO 인용률
│     └─ market_trend.py  market repo 로컬 clone 읽기
├─ .env                   로컬 비밀/설정 (git 제외)
├─ .env.example           환경변수 템플릿
├─ .gitignore
└─ requirements.txt       requests / python-dotenv / schedule
```

---

## 실행 환경
- **OS:** Windows
- **Python:** 3.14 (로컬 실행)
- **의존성:** `requests`, `python-dotenv`, `schedule`

---

## 빠른 시작 (PowerShell, 프로젝트 루트에서)

### 1) 가상환경 + 의존성 설치
```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2) 환경변수 준비
```powershell
Copy-Item .env.example .env
# .env 를 열어 Cafe24 / Kakao / Google / market repo 값을 채웁니다.
# 토큰 4종(ACCESS/REFRESH)은 아래 auth 단계가 자동으로 채웁니다.
```
설정이 잘 들어갔는지 점검 (값은 노출되지 않고 채워짐 여부만 표시):
```powershell
python -m src.config
```

### 3) Cafe24 토큰 발급 (최초 1회)
```powershell
python -m src.cafe24_auth authorize        # 출력된 URL 을 브라우저로 열어 동의
python -m src.cafe24_auth issue <CODE>      # 리다이렉트 주소의 code 값으로 발급
python -m src.cafe24_auth refresh           # (선택) 갱신 테스트
```

### 4) 카카오 토큰 발급 (최초 1회)
```powershell
python -m src.kakao_auth authorize
python -m src.kakao_auth issue <CODE>
python -m src.kakao_send "BYOCORE 연결 테스트"   # '나에게 보내기' 발송 확인
```

---

## RECON 확정 사실 (요약)
| 항목 | 내용 |
|------|------|
| Cafe24 | 기존 GEO 앱 재사용 · 주문 읽기 scope · `GET .../api/v2/admin/orders` · access 2h / refresh 2주 · 헤더 `X-Cafe24-Api-Version: 2026-03-01` |
| Kakao | '나에게 보내기' `POST kapi.kakao.com/v2/api/talk/memo/default/send` · access 6h / refresh 2개월 · 동의 `talk_message` |
| GEO 인용률 | Google Sheet ID `18rrHs_ifXSxZlY3mboRftCXO3KRl3JRBnZ2P-c2ufZA` |
| 경쟁동향 | repo `ever-90/market-insight-byocore` 로컬 clone 읽기 |
| 전송 | 카카오 '나에게 보내기' 단독 (구성원 확장 없음) |
| 리포트 | 일간(매출+인용률+이상알림) / 주간(추세+Top이슈3) / 월간(종합+KPI) |

자세한 내용은 [docs/SPEC.md](docs/SPEC.md), 규칙은 [docs/RULESET.md](docs/RULESET.md), 진행 상황은 [docs/PROGRESS.md](docs/PROGRESS.md) 참고.

---

## 이번(1단계) 범위
폴더/문서/설정/인증 스크립트 **골격**까지. 실제 수집 로직은 `collectors/` 에 **스텁(TODO)** 으로만 존재하며 2단계에서 구현합니다.
