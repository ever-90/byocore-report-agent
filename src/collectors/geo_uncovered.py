"""
geo_uncovered.py — UNCOVERED GEO 질의 추출 어댑터 (Supervisor 연동키).

[목적]
BYOCORE_GEO_ANALYSIS(UNCOVERED, intent_id별 최신) ⨝ BYOCORE_INTENT_LIBRARY(question, category)
→ Supervisor / designer.design_worker 에 넘길 question_normalized 리스트 생성.

[계약]
입력: sheet_id, sa_path, category, exclude_truncated, exclude_orphan_ids
출력: list[dict] — 7필드 고정:
  question_normalized  : re.sub(r'\\s+', ' ', question_raw).strip()
  question_raw         : INTENT_LIBRARY.question 원본
  prompt_id            : GEO_ANALYSIS.prompt_id (== intent_id, 동일 확인됨)
  category             : GEO_ANALYSIS.category
  coverage_status      : "UNCOVERED" (고정)
  measured_at          : intent_id 별 최신 measured_at (ISO 8601)
  is_truncated         : bool — pool_promote_v1 + 단음절 시작 규칙

[is_truncated 규칙]
  source == 'pool_promote_v1'
  AND re.match(r'^[가-힣]{1,2}\\s', question_raw)
  → naver_step25_v3.1 등 타 소스는 source 게이트로 오탐 차단.

[고아 정의]
  INTENT_LIBRARY 메인 탭에 intent_id 없는 것 (ARCHIVE_v2 포함 여부 무관).

[멱등 보장]
  - intent_id 별 measured_at max 1건 → 동일 시트 상태 → 동일 출력.
  - 결과는 (category, question_normalized) 오름차순 정렬.

[READ-ONLY]
  쓰기·측정·트리거 없음. gspread SCOPES = spreadsheets.readonly.

[재사용]
  geo_citation.py 의 인증 상수(_service_account_path, SCOPES) 임포트.
  새 gspread 클라이언트 클래스 없음.
"""

import re
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from .geo_citation import SCOPES, _service_account_path

_GEO_ANALYSIS_TAB = "BYOCORE_GEO_ANALYSIS"
_INTENT_LIB_TAB   = "BYOCORE_INTENT_LIBRARY"

# ---------------------------------------------------------------------------
# is_truncated ALLOWLIST
# ---------------------------------------------------------------------------
# pool_promote_v1 에서 정상적인 2음절 어두(성분명/성분약어/브랜드 등).
# 룰(^[가-힣]{1,2}\s)에 걸리지만 실제 잘림이 아닌 항목을 여기에 추가한다.
# → question_raw 가 이 항목으로 시작하면 is_truncated=False.
# 확장 시: 이 튜플에만 추가 (함수 로직 변경 불필요).
_TRUNCATED_ALLOWLIST: tuple[str, ...] = (
    "가바",   # GABA (γ-aminobutyric acid) — 성분명 약어
)


# ---------------------------------------------------------------------------
# 잘림 탐지
# ---------------------------------------------------------------------------
def _is_truncated(question_raw: str, source: str) -> bool:
    """
    pool_promote_v1 소스에서 한국어 1~2음절+공백으로 시작하는 질의 = 스크래퍼 잘림.
    타 소스(naver_step25_v3.1 등)는 source 게이트로 오탐 원천 차단.
    ALLOWLIST 어두로 시작하면 정상 질의로 판정 (성분명 오탐 방지).
    """
    if source != "pool_promote_v1":
        return False
    q = question_raw.strip()
    if not re.match(r"^[가-힣]{1,2}\s", q):
        return False
    # ALLOWLIST: 정상 어두이면 잘림 아님
    for prefix in _TRUNCATED_ALLOWLIST:
        if q.startswith(prefix):
            return False
    return True


# ---------------------------------------------------------------------------
# 메인 어댑터
# ---------------------------------------------------------------------------
def fetch_uncovered_questions(
    sheet_id: str,
    sa_path: Optional[str] = None,
    category: Optional[str] = None,
    exclude_truncated: bool = True,
    exclude_orphan_ids: bool = True,
) -> list[dict]:
    """
    UNCOVERED 질의를 INTENT_LIBRARY와 JOIN하여 반환. READ-ONLY.

    파라미터
    --------
    sheet_id          : Google Sheet ID
    sa_path           : 서비스 계정 JSON 경로 (None → geo_citation 기본경로)
    category          : 'A_health' 등 카테고리 필터 (None → 전체)
    exclude_truncated : pool_promote_v1 잘림 항목 제외 (기본 True)
    exclude_orphan_ids: LIBRARY 메인탭 미매칭 intent_id 제외 (기본 True)

    반환 스키마 (7필드, 순서 고정)
    --------------------------------
    question_normalized  str   정규화 질의 (→ designer.design_worker uncovered_queries)
    question_raw         str   원본 질의
    prompt_id            str   GEO_ANALYSIS.prompt_id
    category             str   GEO_ANALYSIS.category
    coverage_status      str   "UNCOVERED" 고정
    measured_at          str   intent_id별 최신 measured_at
    is_truncated         bool  잘림 여부
    """
    sa = sa_path if sa_path else _service_account_path()
    creds  = Credentials.from_service_account_file(sa, scopes=SCOPES)
    client = gspread.authorize(creds)
    ss     = client.open_by_key(sheet_id)

    # READ-ONLY: 두 탭 순차 fetch (1 spreadsheet open, 2 worksheet reads)
    geo_records = ss.worksheet(_GEO_ANALYSIS_TAB).get_all_records()
    lib_records = ss.worksheet(_INTENT_LIB_TAB).get_all_records()

    # INTENT_LIBRARY: intent_id → {question, source}
    lib_map: dict[str, dict] = {}
    for r in lib_records:
        iid = str(r.get("intent_id", "")).strip()
        if iid:
            lib_map[iid] = {
                "question": str(r.get("question", "")).strip(),
                "source":   str(r.get("source",   "")).strip(),
            }
    lib_ids = frozenset(lib_map)

    # GEO_ANALYSIS: UNCOVERED만, intent_id별 measured_at 최신 1건 선택
    latest: dict[str, dict] = {}
    for r in geo_records:
        if r.get("coverage_status") != "UNCOVERED":
            continue
        iid = str(r.get("intent_id", "")).strip()
        if not iid:
            continue
        ts = str(r.get("measured_at", ""))
        if iid not in latest or ts > latest[iid]["_ts"]:
            latest[iid] = {
                "prompt_id":   str(r.get("prompt_id", "")),
                "category":    str(r.get("category",  "")),
                "measured_at": ts,
                "_ts":         ts,   # 정렬용, 최종 출력 제외
            }

    # JOIN + 필터 조합
    results: list[dict] = []
    for iid, geo in latest.items():

        # ① exclude_orphan_ids: LIBRARY 메인탭 미매칭 제외
        if exclude_orphan_ids and iid not in lib_ids:
            continue

        lib          = lib_map.get(iid, {"question": "", "source": ""})
        question_raw = lib["question"]
        source       = lib["source"]
        trunc        = _is_truncated(question_raw, source)

        # ② exclude_truncated: 잘림 항목 제외
        if exclude_truncated and trunc:
            continue

        # ③ category 필터
        if category and geo["category"] != category:
            continue

        question_norm = re.sub(r"\s+", " ", question_raw).strip()

        results.append({
            "question_normalized": question_norm,
            "question_raw":        question_raw,
            "prompt_id":           geo["prompt_id"],
            "category":            geo["category"],
            "coverage_status":     "UNCOVERED",
            "measured_at":         geo["measured_at"],
            "is_truncated":        trunc,
        })

    # 결정론적 정렬: (category, question_normalized) 오름차순 → 멱등 보장
    results.sort(key=lambda x: (x["category"], x["question_normalized"]))
    return results
