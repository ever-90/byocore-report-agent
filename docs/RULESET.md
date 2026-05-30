# RULESET — 운영 규칙 / 가드레일

본 문서는 에이전트가 반드시 지켜야 할 규칙이다. 코드 리뷰/확장 시 이 규칙을 우선한다.

## R1. READ-ONLY (1단계 최우선)
- **수집·조회·보고만 허용.** 데이터 소스를 변경하는 모든 쓰기/변경 API 호출 금지.
- **허용:**
  - HTTP `GET` (조회)
  - OAuth 토큰 발급/갱신 (인증 — 데이터 변경 아님)
  - 카카오 '나에게 보내기' (보고 채널)
  - 로컬 파일 읽기
- **금지:**
  - Cafe24 주문/상품/회원 `POST`/`PUT`/`DELETE`
  - Google Sheet `update`/`append`/`batchUpdate`
  - market repo `commit`/`push` 등 원격 변경
  - 카카오 친구/단체 메시지(타인 발송)

## R2. 비밀 관리
- 모든 키/토큰은 **`.env` 에만** 둔다. 코드/문서/로그에 하드코딩·출력 금지.
- `.env` 는 `.gitignore` 로 git 제외. **커밋 금지.** 공유 템플릿은 `.env.example`.
- 토큰 4종(`CAFE24_ACCESS/REFRESH_TOKEN`, `KAKAO_ACCESS/REFRESH_TOKEN`)은
  auth 스크립트가 `config.set_env_value()` 로 자동 기록한다(수동 입력 불필요).
- 설정 점검 시(`python -m src.config`)에도 값은 출력하지 않고 채움 여부만 표시한다.

## R3. 토큰 수명 / 갱신
| 대상 | access | refresh |
|------|--------|---------|
| Cafe24 | 2시간 | 2주 |
| Kakao | 6시간 | 2개월 |
- 만료 시 `refresh_access_token()` 으로 갱신한다.
- 카카오 `refresh_token` 은 만료가 임박할 때만 응답에 재발급되므로, **있을 때만** 덮어쓴다.
- refresh 실패(만료/폐기) 시 재인가(`authorize`)를 안내한다.
- 정밀한 만료시각 추적·사전 갱신은 2단계에서 도입.

## R4. Cafe24 API 버전
- 모든 Cafe24 요청 헤더에 **`X-Cafe24-Api-Version: 2026-03-01`** 을 포함한다
  (`config.CAFE24_API_VERSION`).

## R5. 전송 정책
- 보고는 **카카오 '나에게 보내기' 단독.** 타인/단체 발송 없음.
- 구성원 확장이 필요해지면 별도 검토(친구 메시지 동의/대상 관리)를 거친 뒤에만 확장한다.

## R6. 변경 범위 통제
- 1단계에서 `collectors/*` 는 스텁(`NotImplementedError`)으로 둔다.
- 실제 수집/리포트/스케줄 구현은 2단계 범위이며, 그 전까지 본 골격의 인터페이스를 유지한다.

## R7. 실행 규약
- 모듈 실행은 패키지 형태로: `python -m src.<module>` (프로젝트 루트에서).
- 외부 라이브러리는 `requirements.txt` 에 명시된 것만 사용한다
  (1단계: `requests`, `python-dotenv`, `schedule`).
