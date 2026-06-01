"""
cafe24_products.py — Cafe24 자사 상품 목록 + 판매가 수집 (READ-ONLY).

[목적]
세일즈 에이전트가 자사가(our_price)를 자동으로 읽을 수 있도록,
보고 에이전트가 Cafe24 상품 API를 호출해 공유 파일(data/own_products.json)로 저장.
(세일즈 에이전트의 Cafe24 직접 연동 → 토큰 충돌 방지를 위한 보고 에이전트 경유 설계)

[엔드포인트 — RECON/실측 확정 (2026-06-01)]
  목록  : GET https://{mall_id}.cafe24api.com/api/v2/admin/products
  건수  : GET https://{mall_id}.cafe24api.com/api/v2/admin/products/count
  헤더  : Authorization: Bearer {token}, X-Cafe24-Api-Version: 2026-03-01
  scope : mall.read_product (mall.read_order 와 공존)

[응답 필드 — 실측 확정]
  price        : 판매가 (세금 포함, 문자열 "39000.00") — 가격 경쟁 분석 기준
  product_name : 상품명
  product_code : 상품코드 (예: "P00000FM")
  product_no   : 상품번호 (정수)
  selling      : "T" = 판매중 / "F" = 판매중지
  display      : "T" = 진열 / "F" = 미진열
  sold_out     : "T" = 품절 / "F" = 재고 있음

[필터링]
  selling == "T" 인 상품만 수집 (판매 중지 상품은 경쟁 분석 대상 외)
  price == 0 인 상품은 제외 (무료/오류 상품)

[공유 파일]
  경로 : config.OWN_PRODUCTS_PATH (기본: <report-agent-root>/data/own_products.json)
  형식 : [{"product_name": "...", "price": 39000, "product_code": "...", ...}]
  쓰기 : 수집 후 덮어씀 (원자적 쓰기 — 임시파일 → rename)

[단독 실행]
  python -m src.collectors.cafe24_products          # 수집 → data/own_products.json 저장
  python -m src.collectors.cafe24_products --dry    # 저장 없이 stdout만 출력
"""
from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import requests

from .. import cafe24_auth, config

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
API_PATH_PRODUCTS       = "/api/v2/admin/products"
API_PATH_PRODUCTS_COUNT = "/api/v2/admin/products/count"
PAGE_LIMIT       = 100      # Cafe24 products API 최대 limit
PAGE_DELAY_SEC   = 0.3      # 페이지 간 호출 간격 (레이트리밋 배려)
REQUEST_TIMEOUT  = 20       # seconds
TRANSIENT_STATUSES = (429, 500, 502, 503, 504)

# 공유 파일 기본 경로 (config.OWN_PRODUCTS_PATH 로 override 가능)
_DEFAULT_OUT = Path(__file__).resolve().parents[2] / "data" / "own_products.json"


# ---------------------------------------------------------------------------
# HTTP (READ-ONLY GET)
# ---------------------------------------------------------------------------
def _base_url() -> str:
    mall_id = config.require("CAFE24_MALL_ID")
    return f"https://{mall_id}.cafe24api.com"


def _headers() -> dict[str, str]:
    token = cafe24_auth.get_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "X-Cafe24-Api-Version": config.CAFE24_API_VERSION,
    }


def _get(path: str, params: dict) -> dict:
    """READ-ONLY GET + JSON. 401→refresh 재시도, 429/5xx→백오프 재시도."""
    url = _base_url() + path
    resp = None
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=_headers(), params=params,
                                timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise
        if resp.status_code == 401:
            cafe24_auth.refresh_access_token()
            continue
        if resp.status_code < 400:
            return resp.json()
        if resp.status_code in TRANSIENT_STATUSES and attempt < 2:
            time.sleep(1.0 * (attempt + 1))
            continue
        break
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------
def _to_price_int(raw) -> Optional[int]:
    """가격 문자열/숫자 → int 원화. 0 이하이거나 파싱 실패 시 None."""
    if raw is None or raw == "":
        return None
    try:
        val = int(Decimal(str(raw)).to_integral_value())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return val if val > 0 else None


def _clean_name(name) -> str:
    return (name or "").strip()


# ---------------------------------------------------------------------------
# 수집
# ---------------------------------------------------------------------------
def get_product_count() -> int:
    """판매중 상품 건수(권위값). 교차검증용."""
    data = _get(API_PATH_PRODUCTS_COUNT, {"selling": "T"})
    return int(data.get("count", 0))


def fetch_all_products(selling_only: bool = True) -> list[dict]:
    """
    상품 목록을 offset 페이지네이션으로 전부 수집. READ-ONLY.
    selling_only=True(기본): 판매중(selling=T) 상품만.
    """
    results: list[dict] = []
    offset = 0
    params_base: dict = {"limit": PAGE_LIMIT}
    if selling_only:
        params_base["selling"] = "T"

    while True:
        params = {**params_base, "offset": offset}
        data = _get(API_PATH_PRODUCTS, params)
        batch = data.get("products", []) or []
        if not batch:
            break
        results.extend(batch)
        if len(batch) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
        time.sleep(PAGE_DELAY_SEC)

    return results


# ---------------------------------------------------------------------------
# 변환 / 필터
# ---------------------------------------------------------------------------
def build_own_products(raw_products: list[dict]) -> list[dict]:
    """
    API 원본 → 공유 파일 형식 변환.
    - price == 0 / 파싱 불가 상품 제외
    - selling != "T" 상품 제외 (fetch 단에서도 필터하지만 이중 방어)
    """
    out: list[dict] = []
    for p in raw_products:
        if str(p.get("selling", "")).upper() != "T":
            continue
        price = _to_price_int(p.get("price"))
        if price is None:
            continue
        out.append({
            "product_name": _clean_name(p.get("product_name")),
            "price": price,
            "product_code": str(p.get("product_code", "")).strip(),
            "product_no": int(p.get("product_no") or 0),
            "display": str(p.get("display", "")).upper(),
            "sold_out": str(p.get("sold_out", "F")).upper(),
        })
    # 가격 오름차순 정렬 (읽기 편의)
    out.sort(key=lambda x: x["price"])
    return out


# ---------------------------------------------------------------------------
# 공유 파일 저장
# ---------------------------------------------------------------------------
def _output_path() -> Path:
    raw = (config.get("OWN_PRODUCTS_PATH") or "").strip()
    return Path(raw) if raw else _DEFAULT_OUT


def save_own_products(products: list[dict], path: Optional[Path] = None) -> Path:
    """
    products 를 JSON 파일로 원자적 쓰기 (임시파일 → rename).
    반환: 저장된 파일 경로.
    """
    out = path or _output_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps(products, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(out)   # 원자적 교체 (부분 쓰기 방지)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
    return out


# ---------------------------------------------------------------------------
# 메인 진입점 (수퍼바이저 / 스케줄러 호출용)
# ---------------------------------------------------------------------------
def collect_products(dry_run: bool = False) -> dict:
    """
    자사 상품 목록 수집 → (dry_run=False 면) 공유 파일 저장.

    반환:
      {
        products      : list[dict]  공유 파일 형식 데이터,
        count_api     : int         count 엔드포인트 권위값,
        count_fetched : int         실제 수집 건수,
        count_saved   : int         가격 유효 저장 건수,
        saved_path    : str | None  저장 경로 (dry_run=True 면 None),
        warnings      : list[str],
      }
    """
    count_api  = get_product_count()
    raw        = fetch_all_products(selling_only=True)
    products   = build_own_products(raw)

    warnings: list[str] = []
    if len(raw) != count_api:
        warnings.append(
            f"count API({count_api}) vs 수집({len(raw)}) 불일치 — "
            "수집 중 상품 상태 변경 또는 페이지 경계 차이 가능."
        )
    zero_price = [p for p in raw if _to_price_int(p.get("price")) is None]
    if zero_price:
        warnings.append(
            f"가격 0/파싱불가 {len(zero_price)}건 제외: "
            + ", ".join(p.get("product_code", "?") for p in zero_price[:5])
        )

    saved_path: Optional[str] = None
    if not dry_run:
        path = save_own_products(products)
        saved_path = str(path)

    return {
        "products":      products,
        "count_api":     count_api,
        "count_fetched": len(raw),
        "count_saved":   len(products),
        "saved_path":    saved_path,
        "warnings":      warnings,
    }


# ---------------------------------------------------------------------------
# 단독 실행
# ---------------------------------------------------------------------------
def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    dry = "--dry" in sys.argv

    try:
        result = collect_products(dry_run=dry)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(f"[오류] Cafe24 API HTTP {status} — "
              "토큰 만료/폐기 시 'python -m src.cafe24_auth authorize' 로 재인가하세요.")
        sys.exit(1)
    except Exception as exc:
        print(f"[오류] {type(exc).__name__}: {exc}")
        sys.exit(1)

    print(f"[자사 상품 수집]  count_API={result['count_api']}  "
          f"수집={result['count_fetched']}  저장={result['count_saved']}건")
    for w in result["warnings"]:
        print(f"  [경고] {w}")

    if dry:
        print("\n[dry-run] 저장 없이 stdout 출력:\n")
        print(json.dumps(result["products"], ensure_ascii=False, indent=2))
    else:
        print(f"  → {result['saved_path']}")
        print("\n상품 목록 (가격 오름차순):")
        for p in result["products"]:
            sold = " [품절]" if p["sold_out"] == "T" else ""
            disp = "" if p["display"] == "T" else " [미진열]"
            print(f"  {p['product_code']}  {p['price']:>8,}원  {p['product_name']}{sold}{disp}")


if __name__ == "__main__":
    _cli()
