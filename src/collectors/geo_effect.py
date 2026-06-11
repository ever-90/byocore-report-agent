"""
geo_effect.py — 발행 제품 GEO 인용 추적 (READ-ONLY · 발행효과 측정).

[정체 — geo_monitor 와 상보]
geo_monitor : 타겟 능동측정(GEO_Product_Measure→publish_tracking) 집계.
geo_effect  : 중립풀(BYOCORE_GEO_ANALYSIS) strict 추출 + 진짜 시간창 before/after A/B.
→ 같은 질문(발행효과)의 두 관점. 중복 아님.

[출처] sales-agent/geo_publish_tracker.py 포팅.
  - 시트 접근만 report 인프라로 교체(geo_citation SCOPES/_service_account_path 재사용, 무시크릿).
  - geo_matcher 4함수는 순수함수라 인라인.
  - ★계산 로직(build_report/_product_citation/strict매칭/시간창분할/render_text)은 원본 그대로.

[목적]
측정 엔진(시트 WRITE)은 건드리지 않고, 이미 적재된 BYOCORE_GEO_ANALYSIS 에서
'발행 제품 타겟 질의'만 골라 baseline(전체 인용률) 대비 before/after 인용률을
비교한다. 발행이 AI 인용에 준 효과를 증명하기 위한 얇은 리더 레이어.

[★ strict 전용 매칭 — OR 폴백 금지]
- 발행 추적에서 OR 폴백은 치명적: "이너케어 유산균" → "유산균" 한 토큰으로 OR 매칭되어
  거의 모든 유산균 질의를 끌어와 baseline 과 구분이 사라진다(실측 확인됨).
- 따라서 strict(모든 토큰 포함) 매칭만 사용. 제품을 실제 지칭하는 질의만 집계.

[before/after 분할]
- measured_at 의 날짜부분 < publish_date → before, >= → after.
- 각 구간 안에서 intent_id 당 최신 1행만 채택.

[지표]
- measured : 해당 구간에서 측정된(매칭) 질의 수
- cited    : byocore_mentions>0 인 질의 수
- rate     : cited/measured*100 (%) — None 이면 측정 없음
- delta_pp : after.rate - before.rate (퍼센트포인트)
- vs_baseline_pp : after.rate - baseline.after.rate (전체 대비 초과분)

[제약 — 절대]
- 시트 WRITE 없음. 측정 엔진 호출 없음. 데이터 창작 0.
- 측정 없음(매칭 0/구간 데이터 0)은 거짓값 대신 None + 사유 명시.
- 색인 지연(발행→색인 수일~수주) → after 데이터 적으면 '관찰기간 부족'으로 안내.

[CLI]
  python -m src.collectors.geo_effect --publish-date 2026-06-04
  python -m src.collectors.geo_effect --publish-date 2026-06-04 --report   # 사람용 텍스트
  python -m src.collectors.geo_effect --products-file products.json --publish-date 2026-06-04
  (stdout: 순수 JSON, stderr: 로그)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Dict, List, Optional, Tuple

import gspread
from google.oauth2.service_account import Credentials

from .. import config
from .geo_citation import SCOPES, _service_account_path   # 인증 재사용(무시크릿)

# 시트 탭명 (geo_client 와 동일)
_TAB_INTENT   = "BYOCORE_INTENT_LIBRARY"
_TAB_PROMPTS  = "BYOCORE_GEO_PROMPTS"
_TAB_ANALYSIS = "BYOCORE_GEO_ANALYSIS"

_SPACE_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# [인라인] geo_matcher 4함수 (순수함수 — 원본 로직 그대로)
# ---------------------------------------------------------------------------
def _tokenize(keyword: str) -> List[str]:
    """키워드를 공백 분리 후 2자 이상 토큰만 반환 (조사/단어 단위)."""
    return [t for t in _SPACE_RE.split(keyword.strip()) if len(t) >= 2]


def _matches_strict(text_low: str, tokens: List[str]) -> bool:
    """모든 토큰이 질의에 포함 (AND)."""
    return all(t in text_low for t in tokens)


def _to_int(v) -> int:
    try:
        return int(v) if v not in (None, "", False) else 0
    except (ValueError, TypeError):
        return 0


def _latest_rows_by_id(matched_ids: List[str], analysis_rows: List[dict]) -> List[dict]:
    """
    매칭된 ID에 해당하는 GEO_ANALYSIS 행 중,
    intent_id 당 가장 최신 measured_at 1행만 선택 (날짜 중복 방지).
    """
    id_set = set(matched_ids)
    latest: Dict[str, dict] = {}  # intent_id → latest row
    for row in analysis_rows:
        iid = str(row.get("intent_id") or row.get("prompt_id") or "").strip()
        if iid not in id_set:
            continue
        ts = str(row.get("measured_at", "")).strip()
        prev = latest.get(iid)
        if prev is None or ts > str(prev.get("measured_at", "")):
            latest[iid] = row
    return list(latest.values())


# ---------------------------------------------------------------------------
# [신규] 소형 시트 리더 (geo_client.GeoSheetClient 대체 · geo_uncovered 패턴)
# ---------------------------------------------------------------------------
def _open_spreadsheet():
    """READ-ONLY gspread spreadsheet. config.GEO_SHEET_ID 폴백 포함."""
    creds = Credentials.from_service_account_file(_service_account_path(), scopes=SCOPES)
    sid = config.GEO_SHEET_ID or config.DEFAULT_GEO_SHEET_ID
    return gspread.authorize(creds).open_by_key(sid)


def _fetch_query_map(ss) -> Dict[str, str]:
    """intent_id / prompt_id → 질의 원문. GEO_PROMPTS + INTENT_LIBRARY 합산."""
    qmap: Dict[str, str] = {}
    # 1) 시드 질의 (GEO_PROMPTS)
    for row in ss.worksheet(_TAB_PROMPTS).get_all_records():
        pid = str(row.get("prompt_id", "")).strip()
        text = str(row.get("text", "")).strip()
        if pid and text:
            qmap[pid] = text
    # 2) Intent Library (더 많고 우선)
    for row in ss.worksheet(_TAB_INTENT).get_all_records():
        iid = str(row.get("intent_id", "")).strip()
        question = str(row.get("question", "")).strip()
        if iid and question:
            qmap[iid] = question
    return qmap


def _fetch_analysis_rows(ss) -> List[dict]:
    """GEO_ANALYSIS 전체 행 반환. 빈 마지막 컬럼('') 제거."""
    rows = ss.worksheet(_TAB_ANALYSIS).get_all_records()
    cleaned = []
    for row in rows:
        cleaned.append({k: v for k, v in row.items() if k})  # 빈 헤더 제거
    return cleaned


# ---------------------------------------------------------------------------
# 발행 제품 타겟 질의 (원본 그대로)
# ---------------------------------------------------------------------------
DEFAULT_PRODUCT_QUERIES: List[dict] = [
    {
        "product": "피부면역 유산균",
        "product_no": [84, 107, 122, 121],
        "keywords": ["피부 유산균", "피부 면역", "피부면역 유산균"],
    },
    {
        "product": "이너케어 유산균",
        "product_no": [62, 63],
        "keywords": ["이너케어", "이너케어 유산균"],
    },
]


# ---------------------------------------------------------------------------
# 날짜 유틸 (원본 그대로)
# ---------------------------------------------------------------------------
def _row_date(row: dict) -> str:
    """measured_at 의 날짜부분(YYYY-MM-DD). 없으면 ''."""
    return str(row.get("measured_at", "")).strip()[:10]


def _split_by_date(rows: List[dict], publish_date: str) -> Tuple[List[dict], List[dict]]:
    """measured_at 날짜 < publish_date → before, >= publish_date → after."""
    before, after = [], []
    for r in rows:
        d = _row_date(r)
        if not d:
            continue
        (after if d >= publish_date else before).append(r)
    return before, after


# ---------------------------------------------------------------------------
# 인용률 계산 (원본 그대로)
# ---------------------------------------------------------------------------
def _rate(cited: int, measured: int) -> Optional[float]:
    return round(cited / measured * 100, 1) if measured > 0 else None


def _overall_citation(rows: List[dict]) -> dict:
    """
    baseline — 구간 전체 인용률. intent_id 당 최신 1행으로 dedup 후 mentions>0 비율.
    """
    latest: Dict[str, dict] = {}
    for r in rows:
        iid = str(r.get("intent_id") or r.get("prompt_id") or "").strip()
        if not iid:
            continue
        ts = str(r.get("measured_at", "")).strip()
        prev = latest.get(iid)
        if prev is None or ts > str(prev.get("measured_at", "")):
            latest[iid] = r
    selected = list(latest.values())
    cited = sum(1 for r in selected if _to_int(r.get("byocore_mentions")) > 0)
    measured = len(selected)
    return {"measured": measured, "cited": cited, "rate": _rate(cited, measured)}


def _strict_match_ids(keyword: str, qmap: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """
    strict(AND) 전용 매칭 — 키워드의 모든 토큰이 포함된 질의만.
    _tokenize / _matches_strict 재사용. OR 폴백 없음(발행 추적 정확도).
    """
    tokens = [t.lower() for t in _tokenize(keyword)]
    if not tokens:
        return [], []
    ids, texts = [], []
    for qid, text in qmap.items():
        if _matches_strict(str(text).lower(), tokens):
            ids.append(qid)
            texts.append(text)
    return ids, texts


def _product_citation(keywords: List[str], qmap: Dict[str, str], rows: List[dict]) -> dict:
    """
    제품 키워드 집합 → strict 매칭 intent_id 합집합 → 구간 내 최신행 인용률.
    OR 폴백 금지 — 제품을 실제 지칭하는 질의만 집계.
    """
    matched: set = set()
    samples: List[str] = []
    per_kw: Dict[str, int] = {}
    for kw in keywords:
        ids, texts = _strict_match_ids(kw, qmap)
        per_kw[kw] = len(ids)
        matched.update(ids)
        for t in texts:
            if t not in samples:
                samples.append(t)

    selected = _latest_rows_by_id(list(matched), rows)
    cited = sum(1 for r in selected if _to_int(r.get("byocore_mentions")) > 0)
    measured = len(selected)
    return {
        "matched_queries": len(matched),   # strict 매칭된 질의 총수(구간 무관)
        "measured": measured,              # 이 구간에서 실제 측정된 질의 수
        "cited": cited,
        "rate": _rate(cited, measured),
        "match_per_keyword": per_kw,
        "sample_queries": samples[:5],
    }


# ---------------------------------------------------------------------------
# 메인 — 발행효과 리포트 (원본 그대로)
# ---------------------------------------------------------------------------
def build_report(
    publish_date: str,
    products: List[dict],
    qmap: Dict[str, str],
    rows: List[dict],
) -> dict:
    before_rows, after_rows = _split_by_date(rows, publish_date)

    base_before = _overall_citation(before_rows)
    base_after = _overall_citation(after_rows)

    prod_reports: List[dict] = []
    for p in products:
        b = _product_citation(p["keywords"], qmap, before_rows)
        a = _product_citation(p["keywords"], qmap, after_rows)

        delta_pp = (
            round(a["rate"] - b["rate"], 1)
            if a["rate"] is not None and b["rate"] is not None else None
        )
        vs_base_pp = (
            round(a["rate"] - base_after["rate"], 1)
            if a["rate"] is not None and base_after["rate"] is not None else None
        )

        # 관찰기간 부족 경고 (색인 지연 인지)
        warn = None
        if a["measured"] == 0:
            warn = "발행 후 구간에서 해당 질의 측정 없음(관찰기간 부족 또는 미샘플)"
        elif a["measured"] < 3:
            warn = f"발행 후 측정 질의 {a['measured']}건뿐 — 표본 부족(색인 지연 가능)"

        prod_reports.append({
            "product": p["product"],
            "product_no": p.get("product_no"),
            "keywords": p["keywords"],
            "before": b,
            "after": a,
            "delta_pp": delta_pp,
            "vs_baseline_pp": vs_base_pp,
            "warning": warn,
        })

    return {
        "publish_anchor": publish_date,
        "windows": {
            "before": f"measured_at < {publish_date}",
            "after": f"measured_at >= {publish_date}",
        },
        "data_span": {
            "total_rows": len(rows),
            "before_rows": len(before_rows),
            "after_rows": len(after_rows),
        },
        "baseline": {"before": base_before, "after": base_after},
        "products": prod_reports,
        "notes": [
            "측정 엔진 미변경(READ-ONLY). openai gpt-4o web_search 기반 일일 측정 시트 재사용.",
            "색인 지연(발행→AI 색인 수일~수주) — after 표본 적으면 효과 판단 보류.",
            "baseline = 구간 전체 중립질의 인용률(intent 최신행 dedup).",
            "rate=None 은 거짓값이 아니라 '해당 구간 측정 없음'.",
        ],
    }


def run(publish_date: str, products: Optional[List[dict]] = None) -> dict:
    """READ-ONLY 실행: 시트 1회 로드 → 발행효과 리포트 dict."""
    ss = _open_spreadsheet()
    qmap = _fetch_query_map(ss)
    rows = _fetch_analysis_rows(ss)
    return build_report(publish_date, products or DEFAULT_PRODUCT_QUERIES, qmap, rows)


# ---------------------------------------------------------------------------
# 사람용 텍스트 리포트 (원본 그대로)
# ---------------------------------------------------------------------------
def _fmt_rate(x: Optional[float]) -> str:
    return f"{x:.1f}%" if isinstance(x, (int, float)) else "측정없음"


def render_text(rep: dict) -> str:
    L: List[str] = []
    L.append("=" * 60)
    L.append(f"[발행효과 GEO 인용 리포트]  앵커일: {rep['publish_anchor']}")
    L.append(f"  데이터: 전체 {rep['data_span']['total_rows']}행 "
             f"(before {rep['data_span']['before_rows']} / after {rep['data_span']['after_rows']})")
    bb, ba = rep["baseline"]["before"], rep["baseline"]["after"]
    L.append(f"  baseline(전체 인용률): before {_fmt_rate(bb['rate'])} "
             f"({bb['cited']}/{bb['measured']})  →  after {_fmt_rate(ba['rate'])} "
             f"({ba['cited']}/{ba['measured']})")
    L.append("-" * 60)
    for p in rep["products"]:
        b, a = p["before"], p["after"]
        L.append(f"● {p['product']}  (no={p.get('product_no')})")
        L.append(f"    키워드: {', '.join(p['keywords'])}")
        L.append(f"    before: {_fmt_rate(b['rate'])}  ({b['cited']}/{b['measured']} 인용)")
        L.append(f"    after : {_fmt_rate(a['rate'])}  ({a['cited']}/{a['measured']} 인용)")
        if p["delta_pp"] is not None:
            sign = "+" if p["delta_pp"] >= 0 else ""
            L.append(f"    변화  : {sign}{p['delta_pp']}pp (발행 전→후)")
        if p["vs_baseline_pp"] is not None:
            sign = "+" if p["vs_baseline_pp"] >= 0 else ""
            L.append(f"    vs baseline(after): {sign}{p['vs_baseline_pp']}pp")
        if p["warning"]:
            L.append(f"    ⚠ {p['warning']}")
        if a["sample_queries"]:
            L.append(f"    예시질의: {a['sample_queries'][0]}")
    L.append("-" * 60)
    for n in rep["notes"]:
        L.append(f"  · {n}")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# CLI (원본 그대로)
# ---------------------------------------------------------------------------
def _err(msg: str) -> None:
    print(f"[geo_effect] {msg}", file=sys.stderr)


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(prog="geo_effect",
                                 description="발행 제품 GEO 인용 추적 (READ-ONLY).")
    ap.add_argument("--publish-date", required=True, help="발행 앵커일 YYYY-MM-DD (이 날짜부터 after)")
    ap.add_argument("--products-file", help="제품 키워드 JSON override (스키마 동일)")
    ap.add_argument("--report", action="store_true", help="사람용 텍스트 출력(미지정 시 순수 JSON)")
    args = ap.parse_args()

    products = None
    if args.products_file:
        try:
            with open(args.products_file, "r", encoding="utf-8") as f:
                products = json.load(f)
        except Exception as e:
            _err(f"products-file 읽기 실패: {e}")
            sys.exit(1)

    _err(f"시트 로드 + 발행효과 산출 시작 — 앵커 {args.publish_date}")
    try:
        rep = run(args.publish_date, products)
    except Exception as e:
        _err(f"오류: {type(e).__name__}: {e}")
        sys.exit(1)
    _err(f"완료 — 제품 {len(rep['products'])}개, after_rows {rep['data_span']['after_rows']}")

    if args.report:
        print(render_text(rep))
    else:
        print(json.dumps(rep, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
