"""
own_facts_sync.py — 구글시트 OWN_FACTS_INPUT 탭 → own_facts.json 변환 (출처 게이트).

[원칙]
- ★ 창작 방지 게이트: 한 행의 사실 값은 '출처' 채워짐(식약처/패키지/공식페이지) AND '상태=확정'
  일 때만 반영. 아니면 '확인필요'로 기록 → supervisor 가 자동 생략(발행 불가).
- full regenerate: 시트 = 단일 출처. own_facts.json 전체 재생성.
- ★ 쓰기 전 백업 필수: own_facts.<ts>.backup.json.
- 균주 1행 = strains[] 1개. 제품명으로 그룹. 정량/근거/코드는 제품 첫 행 값 사용.

[CLI]
  python -m src.collectors.own_facts_sync --sync          # 시트 → own_facts.json (백업 후 재생성)
  python -m src.collectors.own_facts_sync --sync --dry    # 변환 결과만 stdout (파일 안 씀)
  python -m src.collectors.own_facts_sync --seed          # 시트 탭 생성 + 현재 own_facts 보존 시딩 (쓰기 권한 필요)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from .. import config
from .geo_citation import SCOPES, _service_account_path  # readonly

TAB = "OWN_FACTS_INPUT"
SENTINEL = "확인필요"
ALLOWED_SOURCES = {"식약처", "패키지", "공식페이지"}
COLUMNS = ["제품명", "product_code", "균주명", "학명", "기능성문구", "인정유형",
           "cfu", "dose", "form", "근거", "출처", "상태", "메모"]
WRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_REPO = Path(__file__).resolve().parents[2]
_DEFAULT_OWN_FACTS = _REPO.parent / "byocore-supervisor-agent" / "data" / "own_facts.json"


def _own_facts_path() -> Path:
    raw = (config.get("OWN_FACTS_PATH") or "").strip()
    return Path(raw) if raw else _DEFAULT_OWN_FACTS


# ---------------------------------------------------------------------------
# 변환 (순수 함수 — 외부 의존 0, 단위 테스트 용이)
# ---------------------------------------------------------------------------
def _row_gated(row: dict) -> bool:
    """행이 사실 게이트 통과? 출처 ∈ 허용 AND 상태==확정."""
    src = str(row.get("출처", "")).strip()
    status = str(row.get("상태", "")).strip()
    return src in ALLOWED_SOURCES and status == "확정"


def _gv(row: dict, col: str, gated: bool) -> str:
    """셀 값. 비어있으면 ''. 게이트 미통과면 '확인필요'(창작 방지)."""
    v = str(row.get(col, "")).strip()
    if not v:
        return ""
    return v if gated else SENTINEL


def build_own_facts(rows: list[dict]) -> dict:
    """OWN_FACTS_INPUT 행들 → own_facts.json dict. 제품명으로 그룹, 균주 1행=strains 1개."""
    facts: dict = {}
    order: list[str] = []
    for row in rows:
        name = str(row.get("제품명", "")).strip()
        if not name:
            continue
        gated = _row_gated(row)
        if name not in facts:
            facts[name] = {"_code": None, "strains": [], "quantitative": {}, "evidence": []}
            order.append(name)
        e = facts[name]

        # product_code: 제품 첫 비어있지 않은 값 (게이트 무관 — 식별자)
        code = str(row.get("product_code", "")).strip()
        if code and e["_code"] is None:
            parts = [c.strip() for c in code.split(",") if c.strip()]
            e["_code"] = parts[0] if len(parts) == 1 else parts

        # 균주 (이 행) — 균주명 있으면 strains 추가
        if str(row.get("균주명", "")).strip():
            e["strains"].append({
                "name": _gv(row, "균주명", gated),
                "scientific": _gv(row, "학명", gated),
                "function": _gv(row, "기능성문구", gated),
                "approval_type": _gv(row, "인정유형", gated),
            })

        # 정량 — 제품 첫 값 (게이트 적용)
        for col in ("cfu", "dose", "form"):
            if str(row.get(col, "")).strip() and col not in e["quantitative"]:
                e["quantitative"][col] = _gv(row, col, gated)

        # 근거 — ';' 분리 (게이트 적용)
        ev = str(row.get("근거", "")).strip()
        if ev:
            for part in ev.split(";"):
                part = part.strip()
                if part:
                    e["evidence"].append(part if gated else SENTINEL)

    out: dict = {}
    for name in order:
        e = facts[name]
        rec: dict = {}
        if e["_code"]:
            rec["product_code"] = e["_code"]
        rec["strains"] = e["strains"] or [{"name": "", "scientific": "", "function": "", "approval_type": ""}]
        rec["quantitative"] = e["quantitative"] or {"cfu": "", "dose": "", "form": "", "storage": ""}
        rec["evidence"] = e["evidence"] or [""]
        out[name] = rec
    return out


# ---------------------------------------------------------------------------
# 시트 읽기 (READ-ONLY)
# ---------------------------------------------------------------------------
def fetch_rows(sheet_id: str, sa_path: Optional[str] = None, tab: str = TAB) -> list[dict]:
    sa = sa_path or _service_account_path()
    creds = Credentials.from_service_account_file(sa, scopes=SCOPES)
    client = gspread.authorize(creds)
    ws = client.open_by_key(sheet_id).worksheet(tab)
    return ws.get_all_records()


# ---------------------------------------------------------------------------
# 백업 + 쓰기
# ---------------------------------------------------------------------------
def _backup(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bpath = path.with_name(f"own_facts.{ts}.backup.json")
    bpath.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return str(bpath)


def sync(sheet_id: str, dry: bool = False, sa_path: Optional[str] = None) -> dict:
    """시트 → own_facts.json (백업 후 full regenerate). 반환: 요약 dict."""
    rows = fetch_rows(sheet_id, sa_path=sa_path)
    own_facts = build_own_facts(rows)
    out_path = _own_facts_path()
    # 메타 안내 키 보존 (있으면)
    guide_note = None
    if out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            guide_note = prev.get("_안내")
        except Exception:
            pass
    payload = {}
    if guide_note:
        payload["_안내"] = guide_note
    payload.update(own_facts)

    summary = {"제품수": len(own_facts), "행수": len(rows), "dry_run": dry, "out": str(out_path)}
    if dry:
        summary["preview"] = own_facts
        return summary

    backup_path = _backup(out_path)
    summary["backup"] = backup_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


# ---------------------------------------------------------------------------
# 시딩 (쓰기 — write scope + 편집권한 필요. graceful)
# ---------------------------------------------------------------------------
def _seed_rows_from_current() -> list[list[str]]:
    """현재 own_facts.json 보존 시딩 행 + 우선순위 빈 행. (헤더 제외, 2D 리스트)"""
    rows: list[list[str]] = []
    path = _own_facts_path()
    cur = {}
    if path.exists():
        try:
            cur = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            cur = {}

    def code_str(c):
        if isinstance(c, list):
            return ",".join(c)
        return str(c or "")

    for name, e in cur.items():
        if name.startswith("_") or not isinstance(e, dict):
            continue
        strains = e.get("strains") or [{}]
        q = e.get("quantitative", {}) or {}
        ev = "; ".join(x for x in (e.get("evidence") or []) if str(x).strip())
        filled = any(str(s.get("name", "")).strip() for s in strains)
        status = "확정" if filled else SENTINEL
        source = "식약처" if filled else ""
        for i, s in enumerate(strains):
            first = (i == 0)
            rows.append([
                name,
                code_str(e.get("product_code")) if first else "",
                str(s.get("name", "")), str(s.get("scientific", "")),
                str(s.get("function", "")), str(s.get("approval_type", "")),
                q.get("cfu", "") if first else "", q.get("dose", "") if first else "",
                q.get("form", "") if first else "", ev if first else "",
                source, status, "",
            ])

    # 우선순위 빈 행 (제품명+code 미리 — 에버는 사실/출처만)
    seeds_blank = [
        ("바이오코어 100억 유산균", "P00000EE"),   # 100억 분말 90포 (대표)
        ("카무트 효소", "P00000DB"),                # 카무트 90포 (대표)
    ]
    for name, code in seeds_blank:
        if name not in cur:
            rows.append([name, code, "", "", "", "", "", "", "", "", "", SENTINEL, ""])
    return rows


def seed_sheet(sheet_id: str, sa_path: Optional[str] = None) -> dict:
    """OWN_FACTS_INPUT 탭 생성(없으면) + 헤더 + 시딩 행 작성. 쓰기 권한 필요."""
    sa = sa_path or _service_account_path()
    creds = Credentials.from_service_account_file(sa, scopes=WRITE_SCOPES)
    client = gspread.authorize(creds)
    ss = client.open_by_key(sheet_id)
    try:
        ws = ss.worksheet(TAB)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=TAB, rows=200, cols=len(COLUMNS))
    seed = _seed_rows_from_current()
    ws.clear()
    ws.update([COLUMNS] + seed, value_input_option="RAW")
    _set_dropdowns(ss, ws)
    return {"tab": TAB, "헤더": len(COLUMNS), "시딩행": len(seed), "드롭다운": ["인정유형", "출처", "상태"]}


def _set_dropdowns(ss, ws) -> None:
    """인정유형/출처/상태 컬럼에 드롭다운(데이터 유효성) 적용. strict=False(가이드)."""
    sid = ws.id
    dd = {
        "인정유형": (COLUMNS.index("인정유형"), ["고시형", "개별인정형", SENTINEL]),
        "출처": (COLUMNS.index("출처"), ["식약처", "패키지", "공식페이지"]),
        "상태": (COLUMNS.index("상태"), ["확정", SENTINEL]),
    }
    reqs = []
    for _, (col, opts) in dd.items():
        reqs.append({"setDataValidation": {
            "range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 500,
                      "startColumnIndex": col, "endColumnIndex": col + 1},
            "rule": {"condition": {"type": "ONE_OF_LIST",
                                   "values": [{"userEnteredValue": o} for o in opts]},
                     "showCustomUi": True, "strict": False},
        }})
    ss.batch_update({"requests": reqs})


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="own_facts_sync")
    ap.add_argument("--sync", action="store_true", help="시트 → own_facts.json")
    ap.add_argument("--seed", action="store_true", help="시트 탭 생성+시딩(쓰기)")
    ap.add_argument("--dry", action="store_true", help="--sync 와 함께: 파일 안 쓰고 미리보기")
    ap.add_argument("--sheet", default=None, help="시트 ID (기본 config.GEO_SHEET_ID)")
    args = ap.parse_args()

    sheet_id = args.sheet or config.get("GEO_SHEET_ID")
    if not sheet_id:
        print(json.dumps({"error": "GEO_SHEET_ID 없음 — --sheet 로 지정"}, ensure_ascii=False)); sys.exit(1)

    try:
        if args.seed:
            res = seed_sheet(sheet_id)
        elif args.sync:
            res = sync(sheet_id, dry=args.dry)
        else:
            print(json.dumps({"error": "--sync 또는 --seed 필요"}, ensure_ascii=False)); sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)); sys.exit(1)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
