# PROGRESS

## 1단계 — 세팅 (2026-05-30)

### 완료 ✅
- [x] 폴더 구조 생성 (`docs/`, `src/`, `src/collectors/`)
- [x] 문서: `README.md`(전체 맵), `docs/SPEC.md`, `docs/RULESET.md`, `docs/PROGRESS.md`
- [x] `.gitignore` (`.env` 및 자격증명 제외) / `requirements.txt`
- [x] `.env` / `.env.example` (키만 정의, 값 비움 / GEO_SHEET_ID 기본값은 example·config 에)
- [x] `src/config.py` — .env 로더 + `require()` + 토큰 기록(`set_env_value`) + 점검(`summary`)
- [x] `src/cafe24_auth.py` — OAuth 발급/갱신 골격 (`authorize`/`issue`/`refresh` CLI)
- [x] `src/kakao_auth.py` — 인가코드→토큰/갱신 골격 (CLI 동일)
- [x] `src/kakao_send.py` — '나에게 보내기' 발송 (401 시 1회 자동 갱신·재시도)
- [x] `src/collectors/` 스텁 3종 — `cafe24_sales` / `geo_citation` / `market_trend`
- [x] 패키지 인식용 `__init__.py` (`src/`, `src/collectors/`)

### 점검 방법
```powershell
pip install -r requirements.txt
python -m src.config           # 키 설정 여부 출력 (값 비노출)
python -m src.cafe24_auth      # 사용법(docstring) 출력
python -m src.kakao_auth       # 사용법(docstring) 출력
```

---

## 2단계 — 구현 (예정)

### 수집 (collectors)
- [x] `cafe24_sales`: 주문 페이지네이션 수집 + 매출/주문수/구매자수 집계 — **구현·실측 완료 (2026-05-30)**
      - GET /orders + /orders/count, X-Cafe24-Api-Version 2026-03-01, limit=100 offset 페이지네이션
      - payment_amount(실결제액) Decimal 합산, member_id 기준 구매자수, 401→refresh 재시도
      - 교차검증(count vs 합산) 내장. 실측: 2026-05-29 → 98건/₩3,375,240, 3일 328건 4페이지 정상
      - net 보강(2026-05-30): net_payment_amount / net_order_count / canceled_amount 추가(canceled 기준) + 정합성 교차검증
        · 실측 확인: 취소주문 3건은 payment_amount=0(미결제 취소) → net=gross, 건수만 98→95
      - TODO(후속): AOV, date_type(pay_date) 선택, 10k 초과 시 날짜분할
- [x] `geo_citation`: 구글시트 핵심 인용률(Top-100) 조회 — **구현·실측 완료 (2026-05-30)**
      - gspread + 서비스계정, 스코프 `spreadsheets.readonly`(쓰기 원천 차단), 키=config(GOOGLE_SERVICE_ACCOUNT_JSON, 기본 service_account.json)
      - 탭 BYOCORE_GEO_SUMMARY, `byocore_citation_rate`(F)/`sov_byocore`(J), **date max 행**(upsert 대비, 물리적 마지막 아님), 데이터 없으면 None→"측정 대기"
      - `is_biased`(date<=2026-05-29 과대계상→"(추정)"), 반환 {date, citation_rate, sov_byocore, is_biased, sample_note}
      - 실측: 2026-05-30 → **3.00%** (공식 (direct+indirect)/total×100, total=100 Top-100 교차검증 일치), 2026-05-25 → 73.2%(추정)
      - deps 추가: gspread, google-auth (requirements.txt)
      - 보강(2026-05-30): direct_count/indirect_count/citation_count 반환 + `recent_unbiased_citations(n)`(이상알림 건수비교용, biased 제외)
- [x] `market_trend`: 경쟁동향 수집 — **구현·실측 완료 (2026-05-30)**
      - RECON: ever-90/market-insight-byocore 커밋 트리엔 정형 경쟁동향 산출물 없음(.gitignore 보호),
        로컬 deploy 사본에도 reports/·*_trend.json 없음 → 두 repo 간 데이터 계약 부재 확인
      - 설계(사용자 승인): 명시적 '핸드오프 JSON 계약' 신설·문서화 (관측 스키마 위조 안 함)
        · 위치: <MARKET_REPO_PATH>/reports/ (또는 src/reports/), config 경유(하드코딩 0)
        · 우선순위: latest_trend.json → YYYY-MM-DD_trend.json(파일명 날짜 최댓값), 손상 시 이전본 폴백
        · schema_version=1: {as_of, generated_at, competitors[{name,change,signal,confidence,sources}], top_issues[]}
      - collect_market_trend(base_path=None)->dict|None, READ-ONLY(파일 읽기만)
        · None: 경로 미설정/경로 없음/reports 없음/핸드오프 없음/전부 손상 → 리포트 "경쟁동향 미연동" 처리
        · 정규화: signal(up/down/flat)/confidence(H/M/L, 기본 L 보수적)/sources, 18관점 교차검증→warnings
      - 단독 실행: python -m src.collectors.market_trend [<repo경로>]
      - 실측: .env MARKET_REPO_PATH 빈값 → None(미연동) 확인. 핸드오프 41케이스 테스트 전부 통과
      - 연동 활성화 조건(후속): market-insight 측이 reports/latest_trend.json 생성 + .env MARKET_REPO_PATH 설정

### 리포트 / 운영
- [x] 리포트 포맷터: **일간·주간·월간 완료 (src/reporter.py, 2026-05-30)**
      - build_daily_report(date) / send_daily_report(date), 단독실행 시 어제(KST) 생성→발송
      - net 중심 박스 텍스트 + 카카오 '나에게 보내기' **실발송 성공(result_code=0)**, 본문 139자
      - [x] 일간에 **GEO 핵심 인용률 통합** (2026-05-30): 매출(net) 블록 아래 줄, is_biased→"(추정)", None→"측정 대기", **독립 try**(GEO 실패해도 매출 정상 발송), 실발송 159자
      - [x] 일간 **이상알림 통합** (2026-05-30): 매출 7일중앙값比 ±30%(⚠️+편차%) / 인용 건수변화(비-biased 2개 비교, ▲▼/유지), 각 독립 try, 실발송 179자
      - [x] **주간 리포트** (2026-05-30): build_weekly_report()/send_weekly_report() + CLI `weekly`, 직전7일vs전주, net합계+전주比 / 일평균+최고·최저일 / 인용(누적중) / Top이슈3(median±30%+인용변화), **200자 우선순위 트림 가드**, 독립 try, 실발송 146자
      - [x] **월간 리포트** (2026-05-30): build_monthly_report()/send_monthly_report() + CLI `monthly`, 직전30일vs전월30일, net합계+전월比 / 일평균+최고·최저일 / KPI달성률(config경유, 미설정→"목표 미설정") / 인용추세(비-biased 당월만) / Top이슈3(median±30%), **200자 우선순위 트림 가드**(일평균5>KPI4>인용3>이슈2, 제목·net·푸터 보호), 독립 try, 실발송 179자
        · KPI 구조: MONTHLY_SALES_TARGET(.env) 설정 시 달성률 자동 계산, 미설정이면 "KPI 목표 미설정"
- [x] 이상 알림 임계치 정의 및 탐지 — 매출 ±30%(7일 median, 평균 아님) / 인용 건수변화(단일일 %비교 금지·biased 제외·2개미만 skip)
- [x] **판단 카드 v1 (GEO×매출×퍼널프록시)** (2026-06-01)
      - _collect_baseline_full(): 7일 베이스라인 1회 수집 → anomaly·funnel proxy 공용
      - _funnel_proxy(): AOV(±30%)/취소율 플래그 / _judgment_card(): 8-case 매칭 가설 1줄
      - 실발송 181자, result_code=0 ✓
- [x] **판단 카드 v2 — 인용률 추세 + 알림 아이콘 정리** (2026-06-01)
      - geo_citation.recent_unbiased_citations(): citation_rate 필드 추가(추세 판정용)
      - _cite_rate_trend(geo_recent): 비-biased 3개 이상 + 전부 rate 존재 + 연속 단조 변화 시만 판정
        · 3개 미만/rate None 포함 → "na"(데이터 축적 중, 단정 금지) — 보수적 설계
        · recent_unbiased_citations(2→3): 일간 리포트·대시보드 모두 n=3으로 변경
      - _judgment_card() 확장: cite_trend 신규 우선순위 추가
        · 취소율↑ → 인용률↓+매출↓ → AI유입가능성 → 인용률↓(단독) → 인지도하락 → GEO·매출시너지 → 객단가↓ → 매출↓ → 매출↑ → 정상
      - 알림 아이콘 의미 분리: ⚠️=나쁜신호만(매출급락·인용↓), 아이콘없음=중립/긍정(매출급등·인용건수▲)
        · build_daily_report: sales_anom ⚠️ → 하락(dev<-30%)만 적용
        · dashboard: warn_alerts(⚠️빨강) / info_alerts(파란테두리) 분리 표시
      - 실발송: "인용률 하락 추세 · GEO 콘텐츠 점검 권장" 164자, result_code=0 ✓
      - 대시보드: ⚠️ warn 1건(인용률 하락) + info 1건(인용 건수▲) 정상 분류 확인
- [x] **판단 v2 버그확인 + 라벨 개선** (2026-06-01)
      - 버그확인: 비-biased 3개 실존 확인(06-01/05-31/05-30, rates=1.2/2.0/3.0%), 단조 하락 → "down" 정상
      - 건수 vs 율 라벨 분리:
        · reporter: cite_cnt_anom "인용 건수: N→M건 ▲/▼" (priority 3, 트림 가능)
        · reporter: cite_rate_anom "인용률: X.X→Y.Y% ▲/▼" (priority 2, 율 우선 보호, rate 없으면 생략)
        · dashboard 카드 sub: "건수 N→M건 ▲/▼"
        · dashboard 알림: ⚠️ "인용률: 2.0→1.2% ▼ (연속 하락)" / info "인용 건수: 2→15건 ▲"
      - 실발송 185자, result_code=0 ✓
- [x] **콘솔 대시보드 HTML** (2026-06-01): `src/dashboard.py` → `docs/index.html` (GitHub Pages)
      - `build_dashboard_html(date)` / `save_dashboard(date)`, CLI `python -m src.dashboard [date]`
      - 카드 4종: net매출·핵심인용률·주문/구매자·판단카드(1줄), 최근 7일 net 막대차트(Chart.js CDN)
      - 이상알림 배너(매출 ±30% / 인용 건수변화), 마지막 갱신 시각 footer
      - self-contained(Chart.js CDN, 데이터 인라인), 모바일 반응형, 다크모드
      - `<meta name="robots" content="noindex,nofollow">` 검색 노출 차단
      - 보안: API키·토큰·서비스계정 HTML 미포함 자동 검증 통과 ✅
      - 신규 API 호출 0 — `_collect_baseline_full` 1회로 anomaly·funnel·차트 공용
      - 생성 결과: `docs/index.html` 4,451 bytes
- [ ] `schedule` 기반 스케줄러 (일/주/월 트리거)
- [ ] 토큰 만료시각(timestamp) 추적 및 사전 갱신
- [x] 카카오 본문 길이 초과 대응 — 일간·주간·월간 모두 **200자 우선순위 트림 가드** 적용 완료

### 비고
- 전송 대상 확장(구성원 추가)은 RULESET R5 검토를 거친 뒤에만 진행.
- 모든 신규 코드는 RULESET(특히 R1 READ-ONLY, R2 비밀관리) 준수.
