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
- [ ] `geo_citation`: 구글시트 인용률 파싱 (google-auth / sheets API 또는 gspread 추가)
- [ ] `market_trend`: repo 산출물(파일) 파싱 → 경쟁사 추세·Top 이슈

### 리포트 / 운영
- [~] 리포트 포맷터: **일간 매출 파트 완료 (src/reporter.py, 2026-05-30)**
      - build_daily_report(date) / send_daily_report(date), 단독실행 시 어제(KST) 생성→발송
      - net 중심 박스 텍스트 + 카카오 '나에게 보내기' **실발송 성공(result_code=0)**, 본문 139자
      - [ ] 일간에 인용률·이상알림 추가 / 주간(추세+Top이슈3) / 월간(종합+KPI)
- [ ] 이상 알림 임계치 정의 및 탐지
- [ ] `schedule` 기반 스케줄러 (일/주/월 트리거)
- [ ] 토큰 만료시각(timestamp) 추적 및 사전 갱신
- [ ] 카카오 본문 길이 초과 시 분할 발송 (현재 길이 경고만 추가됨)

### 비고
- 전송 대상 확장(구성원 추가)은 RULESET R5 검토를 거친 뒤에만 진행.
- 모든 신규 코드는 RULESET(특히 R1 READ-ONLY, R2 비밀관리) 준수.
