"""
geo_monitor.py — 발행 효과 측정용 AI 인용 모니터링 (읽기 전용 집계).

[원칙 — LLM 0원]
- 직접 질의 금지: 외부 측정 엔진이 GEO_Product_Measure/publish_tracking 에
  써둔 실측만 읽어 집계한다. 측정 엔진 신설 아님.
- READ-ONLY: gspread spreadsheets.readonly (geo_citation 재사용) + 로컬 JSON read.
- 측정 없으면 rate=None ("측정 대기") — 가짜값/창작 없음.

[입력]
- GEO_Target_Queries 탭        : 타겟 질의 정의 (product_code, query, priority, status)
- publish_tracking.json        : supervisor 가 동기화한 per-product 실측
                                 (baseline.citation, measurements[]: engine=openai)
- BYOCORE_GEO_SUMMARY          : 전사 citation_rate (geo_citation 경유, baseline≈3%)

[CLI]
  python -m src.collectors.geo_monitor --targets        # (a) 타겟 질의 고정 측정
  python -m src.collectors.geo_monitor --before-after   # (b) baseline 비교
  python -m src.collectors.geo_monitor --all
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from .. import config
from .geo_citation import SCOPES, _service_account_path, recent_unbiased_citations

_TARGET_TAB = "GEO_Target_Queries"
_REPO = Path(__file__).resolve().parents[2]
_DEFAULT_TRACKING = _REPO.parent / "byocore-supervisor-agent" / "data" / "publish_tracking.json"


def _tracking_path() -> Path:
    raw = (config.get("PUBLISH_TRACKING_PATH") or "").strip()
    return Path(raw) if raw else _DEFAULT_TRACKING


def _load_tracking() -> dict:
    """publish_tracking.json → dict. 없음/파손 → {} (graceful)."""
    p = _tracking_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _norm(q: str) -> str:
    return re.sub(r"\s+", " ", str(q)).strip()


def _fetch_target_rows(sheet_id: str | None = None) -> list[dict]:
    """GEO_Target_Queries 전 행. READ-ONLY. 탭 없음 → []."""
    sid = sheet_id or config.GEO_SHEET_ID or config.DEFAULT_GEO_SHEET_ID
    creds = Credentials.from_service_account_file(_service_account_path(), scopes=SCOPES)
    try:
        return gspread.authorize(creds).open_by_key(sid).worksheet(_TARGET_TAB).get_all_records()
    except gspread.WorksheetNotFound:
        return []


# ── (a) 타겟 질의 고정 측정 ────────────────────────────────────────────────
def monitor_target_queries(sheet_id: str | None = None) -> list[dict]:
    """
    타겟 질의(정의) × 외부 실측(publish_tracking.measurements) 집계. READ-ONLY.
    반환: (product_code, query)별
      {product_code, query, status, priority, n, mentioned_n, cited_n,
       mentioned_rate, cited_rate, last_measured_at}
    - n=0 (실측 없음) → rate=None — "측정 대기". 가짜값 없음.
    - 정의에 없는 실측 질의도 status='(미등록)' 으로 포함 (누락 가시화).
    """
    # 실측: (product_code, query_norm) → 집계
    meas: dict[tuple[str, str], dict] = {}
    for no, e in _load_tracking().items():
        if no.startswith("_") or not isinstance(e, dict):
            continue
        codes = e.get("product_code")
        code = str((codes[0] if isinstance(codes, list) and codes else codes) or "").strip()
        for m in e.get("measurements", []):
            if str(m.get("engine", "")).lower() != "openai":
                continue                      # supervisor 와 동일 게이트: openai 실측만
            key = (code, _norm(m.get("query", "")))
            agg = meas.setdefault(key, {"n": 0, "mentioned_n": 0, "cited_n": 0, "last": ""})
            agg["n"] += 1
            agg["mentioned_n"] += 1 if m.get("mentioned") else 0
            agg["cited_n"] += 1 if m.get("cited") else 0
            agg["last"] = max(agg["last"], str(m.get("measured_at", "")))

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for r in _fetch_target_rows(sheet_id):                 # ① 정의된 타겟 질의
        code, q = str(r.get("product_code", "")).strip(), _norm(r.get("query", ""))
        if not q:
            continue
        seen.add((code, q))
        a = meas.get((code, q))
        out.append({
            "product_code": code, "query": q,
            "status": str(r.get("status", "")), "priority": r.get("priority"),
            "n": a["n"] if a else 0,
            "mentioned_n": a["mentioned_n"] if a else 0,
            "cited_n": a["cited_n"] if a else 0,
            "mentioned_rate": round(a["mentioned_n"] / a["n"] * 100, 1) if a else None,
            "cited_rate": round(a["cited_n"] / a["n"] * 100, 1) if a else None,
            "last_measured_at": a["last"] if a else "",
        })
    for (code, q), a in sorted(meas.items()):              # ② 미등록 실측 (가시화)
        if (code, q) in seen:
            continue
        out.append({
            "product_code": code, "query": q, "status": "(미등록)", "priority": None,
            "n": a["n"], "mentioned_n": a["mentioned_n"], "cited_n": a["cited_n"],
            "mentioned_rate": round(a["mentioned_n"] / a["n"] * 100, 1),
            "cited_rate": round(a["cited_n"] / a["n"] * 100, 1),
            "last_measured_at": a["last"],
        })
    out.sort(key=lambda x: (x["product_code"], x["query"]))   # 멱등 정렬
    return out


# ── (b) before/after baseline 비교 ─────────────────────────────────────────
def monitor_before_after() -> dict:
    """
    발행 전후 비교. READ-ONLY. 두 단위:
    - products: baseline.citation(발행 전 실측, 보통 0) vs measurements 집계(cited_rate)
    - company : BYOCORE_GEO_SUMMARY 비-biased 최근 30개 중 最古(baseline≈3%) vs 최신
    측정 없으면 after=None — "측정 대기".
    """
    products: list[dict] = []
    for no, e in _load_tracking().items():
        if no.startswith("_") or not isinstance(e, dict):
            continue
        ms = [m for m in e.get("measurements", [])
              if str(m.get("engine", "")).lower() == "openai"]
        cited = sum(1 for m in ms if m.get("cited"))
        after = round(cited / len(ms) * 100, 1) if ms else None
        base = (e.get("baseline") or {}).get("citation", 0)
        products.append({
            "product_no": no, "product_code": e.get("product_code"),
            "baseline_citation": base,
            "after_n": len(ms), "after_cited_n": cited, "after_cited_rate": after,
            "delta": (round(after - base, 1) if after is not None else None),
            "note": "측정 대기" if not ms else "",
        })
    products.sort(key=lambda x: str(x["product_no"]))

    company = None
    hist = recent_unbiased_citations(30)        # date 내림차순, 비-biased만
    rated = [h for h in hist if isinstance(h.get("citation_rate"), (int, float))]
    if rated:
        newest, oldest = rated[0], rated[-1]
        company = {
            "baseline": {"date": oldest["date"], "citation_rate": oldest["citation_rate"]},
            "latest":   {"date": newest["date"], "citation_rate": newest["citation_rate"]},
            "delta": round(newest["citation_rate"] - oldest["citation_rate"], 2),
            "sample_note": "핵심 인용률(Top-100) · 비-biased 구간만",
        }
    return {"products": products, "company": company,
            "_안내": "외부 엔진 실측만 집계 (LLM 호출 0 · 쓰기 0)"}


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="geo_monitor")
    ap.add_argument("--targets", action="store_true", help="(a) 타겟 질의 고정 측정 집계")
    ap.add_argument("--before-after", action="store_true", help="(b) before/after baseline 비교")
    ap.add_argument("--all", action="store_true", help="(a)+(b) 모두")
    args = ap.parse_args()
    res: dict = {}
    if args.targets or args.all:
        res["target_queries"] = monitor_target_queries()
    if args.before_after or args.all:
        res["before_after"] = monitor_before_after()
    if not res:
        ap.error("--targets / --before-after / --all 중 하나 필요")
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
