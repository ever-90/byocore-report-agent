"""
geo_map_sync.py — GEO_Query_Product_Map 탭에 query↔product_code 매핑 기록 (write).

[원칙]
- ★ 발행 제품만 매핑 (현재 107 피부면역=P00000ED, 63 이너케어/리스펙타=P00000CL)
- col1=query, col2=product_code 고정 (+product_name, note 보조)
- GEO 예시 "어린이 유산균=107"은 틀림(107은 피부면역) → 사용 안 함

[CLI]
  python -m src.collectors.geo_map_sync --seed-map --sheet <GEO_SHEET_ID>
"""
from __future__ import annotations

import argparse
import json
import sys

import gspread
from google.oauth2.service_account import Credentials

from .. import config
from .geo_citation import _service_account_path

TAB = "GEO_Query_Product_Map"
COLUMNS = ["query", "product_code", "product_name", "note"]
WRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ★ 발행 제품만. (query, product_code, product_name)
MAPPING = [
    ("피부 유산균",        "P00000ED", "피부면역 유산균(107)"),
    ("피부에 좋은 유산균",  "P00000ED", "피부면역 유산균(107)"),
    ("여성 유산균",        "P00000CL", "이너케어 유산균(63, 리스펙타 질건강)"),
    ("질 유산균",          "P00000CL", "이너케어 유산균(63, 리스펙타 질건강)"),
    ("질 건강 유산균",      "P00000CL", "이너케어 유산균(63, 리스펙타 질건강)"),
]


def seed_map(sheet_id: str, sa_path: str | None = None) -> dict:
    """GEO_Query_Product_Map 탭 생성(없으면) + 매핑 기록. 쓰기 권한 필요."""
    sa = sa_path or _service_account_path()
    creds = Credentials.from_service_account_file(sa, scopes=WRITE_SCOPES)
    client = gspread.authorize(creds)
    ss = client.open_by_key(sheet_id)
    try:
        ws = ss.worksheet(TAB)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=TAB, rows=100, cols=len(COLUMNS))
    rows = [[q, code, name, "발행 제품 매핑(local seed)"] for (q, code, name) in MAPPING]
    ws.clear()
    ws.update([COLUMNS] + rows, value_input_option="RAW")
    return {"tab": TAB, "매핑수": len(rows),
            "제품": sorted({c for (_, c, _) in MAPPING})}


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="geo_map_sync")
    ap.add_argument("--seed-map", action="store_true")
    ap.add_argument("--sheet", default=None)
    args = ap.parse_args()
    sheet_id = args.sheet or config.get("GEO_SHEET_ID")
    if not sheet_id:
        print(json.dumps({"error": "GEO_SHEET_ID 없음 — --sheet 지정"}, ensure_ascii=False)); sys.exit(1)
    if not args.seed_map:
        print(json.dumps({"error": "--seed-map 필요"}, ensure_ascii=False)); sys.exit(1)
    try:
        res = seed_map(sheet_id)
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)); sys.exit(1)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
