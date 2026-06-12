"""
cafe24_deploy.py — Cafe24 상품 description WRITE (반자동 발행). ★ 쓰기 모듈 (읽기 모듈과 분리).

[안전 원칙 — 절대]
- dry_run=True 기본: 실제 PUT 없이 "무엇을 바꿀지"만 반환.
- 백업 필수: 실제 PUT 전 기존 description 을 파일로 저장 (롤백 가능).
- ★ append-only + 멱등: 기존 HTML(이미지 등) 보존. AI 텍스트는 마커 블록으로만 추가/교체.
  · 기존에 마커 블록 있으면 그 블록만 교체(재발행 중복 방지), 없으면 끝에 추가.
  · 기존 description 의 나머지(이미지 <img> 포함)는 그대로 둔다 → 이미지 보존.
- 승인 책임은 호출측(supervisor): dry_run=False 는 승인된 경우에만 호출돼야 함.
- scope: mall.write_product 필요 (cafe24_auth.OAUTH_SCOPE).

[계약 — 고정]
deploy_detail(product_no, html_detail, dry_run=True) -> dict
  {product_no, status, backup_path, applied(bool), error}  (+ product_name·길이·이미지보존 진단필드)

[CLI]
  python -m src.collectors.cafe24_deploy --product-no 114 --html-file out.html         # dry-run
  python -m src.collectors.cafe24_deploy --product-no 114 --html-file out.html --apply  # 실제 PUT
  python -m src.collectors.cafe24_deploy --product-no 114 --rollback-file <backup.json> # 롤백
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import requests

from .. import cafe24_auth, config

AI_START = "<!-- BYOCORE_AI_TEXT_START -->"
AI_END = "<!-- BYOCORE_AI_TEXT_END -->"
# ★ JSON-LD(schema.org) 전용 마커 — AI 텍스트 마커와 독립(상호 미접촉).
SCHEMA_START = "<!-- BYOCORE_GEO_SCHEMA_START -->"
SCHEMA_END = "<!-- BYOCORE_GEO_SCHEMA_END -->"
REQUEST_TIMEOUT = 20
BACKUP_DIR = Path(__file__).resolve().parents[2] / "data" / "desc_backups"


def _base_url() -> str:
    return f"https://{config.require('CAFE24_MALL_ID')}.cafe24api.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {cafe24_auth.get_access_token()}",
        "X-Cafe24-Api-Version": config.CAFE24_API_VERSION,
        "Content-Type": "application/json",
    }


def _get_description(product_no: int) -> tuple[str, str]:
    """현재 description + 상품명 GET (401→refresh 1회). READ-ONLY."""
    url = f"{_base_url()}/api/v2/admin/products/{product_no}"
    r = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT)
    if r.status_code == 401:
        cafe24_auth.refresh_access_token()
        r = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    p = r.json().get("product", {}) or {}
    return (p.get("description", "") or ""), (p.get("product_name", "") or "")


def _put_description(product_no: int, desc: str) -> requests.Response:
    """description PUT (401→refresh 1회)."""
    url = f"{_base_url()}/api/v2/admin/products/{product_no}"
    body = {"request": {"description": desc}}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    r = requests.put(url, headers=_headers(), data=data, timeout=REQUEST_TIMEOUT)
    if r.status_code == 401:
        cafe24_auth.refresh_access_token()
        r = requests.put(url, headers=_headers(), data=data, timeout=REQUEST_TIMEOUT)
    return r


def merge_description(existing: str, ai_html: str) -> str:
    """
    ★ append-only + 멱등. 기존 HTML(이미지 등) 보존.
    - 기존에 AI 마커 블록 존재 → 그 블록만 교체 (재발행 시 중복 방지).
    - 없으면 → 기존 끝에 마커 블록 추가.
    """
    block = f"{AI_START}\n{ai_html}\n{AI_END}"
    if AI_START in existing and AI_END in existing:
        i = existing.index(AI_START)
        j = existing.index(AI_END) + len(AI_END)
        return existing[:i] + block + existing[j:]
    sep = "" if existing.endswith("\n") or not existing else "\n"
    return existing + sep + block


def _save_backup(product_no: int, description: str) -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = BACKUP_DIR / f"{product_no}_{ts}.json"
    path.write_text(
        json.dumps({"product_no": product_no, "ts": ts, "description": description},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    return str(path)


def deploy_detail(product_no: int, html_detail: str, dry_run: bool = True) -> dict:
    """
    AI 텍스트(html_detail)를 상품 description 끝에 append (이미지 보존). dry_run 기본.
    반환: {product_no, status, backup_path, applied, error, product_name, 기존_길이, 적용후_길이, 이미지_보존}
    """
    result = {"product_no": product_no, "status": None, "backup_path": None,
              "applied": False, "error": None}

    if not str(html_detail or "").strip():
        result["status"] = "거부"
        result["error"] = "html_detail 비어있음 (생성 사실 없음 — 업로드 안 함)"
        return result

    # 1) 현재 description GET
    try:
        orig, name = _get_description(product_no)
    except Exception as e:
        result["status"] = "실패"
        result["error"] = f"description GET 실패: {type(e).__name__}: {e}"
        return result

    new_desc = merge_description(orig, html_detail)
    result["product_name"] = name
    result["기존_길이"] = len(orig)
    result["적용후_길이"] = len(new_desc)
    # 이미지 보존 검증: append-only 라 기존 <img> 개수 유지/증가여야 함
    result["이미지_보존"] = orig.count("<img") <= new_desc.count("<img")

    # 2) dry_run → 변경계획만 (PUT·백업 없음)
    if dry_run:
        result["status"] = "dry_run (승인/실행 안 함 — 변경계획만)"
        return result

    # 3) 실제 적용: 백업 먼저 → PUT
    try:
        result["backup_path"] = _save_backup(product_no, orig)
    except Exception as e:
        result["status"] = "실패"
        result["error"] = f"백업 저장 실패(업로드 중단): {type(e).__name__}: {e}"
        return result

    try:
        r = _put_description(product_no, new_desc)
    except Exception as e:
        result["status"] = "실패"
        result["error"] = f"PUT 예외: {type(e).__name__}: {e} (백업 보존: {result['backup_path']})"
        return result

    if r.status_code >= 400:
        result["status"] = "실패"
        result["error"] = f"PUT {r.status_code}: {r.text[:200]} (백업 보존: {result['backup_path']})"
        return result

    result["applied"] = True
    result["status"] = "발행됨"
    return result


def merge_schema(existing: str, jsonld: str) -> str:
    """
    ★ JSON-LD 블록을 GEO_SCHEMA 마커로 replace-or-append (멱등).
    - AI 텍스트 마커(AI_START/END)는 절대 건드리지 않음 → 보이는 텍스트·이미지 무변.
    - 기존 schema 블록 존재 → 그 블록만 교체. 없으면 → 기존 끝에 추가.
    """
    block = f'{SCHEMA_START}\n<script type="application/ld+json">\n{jsonld}\n</script>\n{SCHEMA_END}'
    if SCHEMA_START in existing and SCHEMA_END in existing:
        i = existing.index(SCHEMA_START)
        j = existing.index(SCHEMA_END) + len(SCHEMA_END)
        return existing[:i] + block + existing[j:]
    sep = "" if existing.endswith("\n") or not existing else "\n"
    return existing + sep + block


def deploy_schema(product_no: int, jsonld: str, dry_run: bool = True) -> dict:
    """
    FAQPage 등 JSON-LD(schema.org)를 description 에 GEO_SCHEMA 마커로 주입. dry_run 기본.
    ★ deploy_detail 과 동일 안전장치(GET→merge→백업→PUT). AI 블록·이미지 미접촉.
    반환: {product_no, status, backup_path, applied, error, product_name, 기존_길이, 적용후_길이, 이미지_보존, AI블록_보존}
    """
    result = {"product_no": product_no, "status": None, "backup_path": None,
              "applied": False, "error": None}
    if not str(jsonld or "").strip():
        result["status"] = "거부"; result["error"] = "jsonld 비어있음 (주입 안 함)"; return result

    try:
        orig, name = _get_description(product_no)
    except Exception as e:
        result["status"] = "실패"; result["error"] = f"description GET 실패: {type(e).__name__}: {e}"; return result

    new_desc = merge_schema(orig, jsonld)
    result["product_name"] = name
    result["기존_길이"] = len(orig)
    result["적용후_길이"] = len(new_desc)
    result["이미지_보존"] = orig.count("<img") <= new_desc.count("<img")
    # ★ AI 텍스트 블록 무변 검증 (schema 주입이 AI 마커를 건드리지 않았는지)
    def _ai_block(s: str) -> str:
        if AI_START in s and AI_END in s:
            return s[s.index(AI_START): s.index(AI_END) + len(AI_END)]
        return ""
    result["AI블록_보존"] = (_ai_block(orig) == _ai_block(new_desc))

    if dry_run:
        result["status"] = "dry_run (승인/실행 안 함 — 변경계획만)"
        return result

    try:
        result["backup_path"] = _save_backup(product_no, orig)
    except Exception as e:
        result["status"] = "실패"; result["error"] = f"백업 저장 실패(주입 중단): {type(e).__name__}: {e}"; return result
    try:
        r = _put_description(product_no, new_desc)
    except Exception as e:
        result["status"] = "실패"; result["error"] = f"PUT 예외: {type(e).__name__}: {e} (백업 보존: {result['backup_path']})"; return result
    if r.status_code >= 400:
        result["status"] = "실패"; result["error"] = f"PUT {r.status_code}: {r.text[:200]} (백업 보존: {result['backup_path']})"; return result
    result["applied"] = True
    result["status"] = "발행됨"
    return result


def rollback(product_no: int, backup_file: str) -> dict:
    """백업 파일의 description 으로 PUT 복원."""
    result = {"product_no": product_no, "status": None, "applied": False, "error": None}
    try:
        data = json.loads(Path(backup_file).read_text(encoding="utf-8"))
    except Exception as e:
        result["status"] = "실패"; result["error"] = f"백업 읽기 실패: {e}"; return result
    desc = data.get("description")
    if desc is None:
        result["status"] = "실패"; result["error"] = "백업에 description 없음"; return result
    try:
        r = _put_description(product_no, desc)
    except Exception as e:
        result["status"] = "실패"; result["error"] = f"PUT 예외: {e}"; return result
    if r.status_code >= 400:
        result["status"] = "실패"; result["error"] = f"PUT {r.status_code}: {r.text[:200]}"; return result
    result["applied"] = True; result["status"] = "롤백됨"
    return result


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="cafe24_deploy", description="Cafe24 description WRITE (반자동).")
    ap.add_argument("--product-no", type=int, required=True)
    ap.add_argument("--html-file", help="추가할 AI 텍스트(html_detail) 파일 경로")
    ap.add_argument("--schema-file", help="주입할 JSON-LD(schema.org) 파일 경로 (GEO_SCHEMA 마커)")
    ap.add_argument("--apply", action="store_true", help="실제 PUT (없으면 dry-run)")
    ap.add_argument("--rollback-file", help="롤백할 백업 JSON 경로")
    args = ap.parse_args()

    if args.rollback_file:
        res = rollback(args.product_no, args.rollback_file)
    elif args.schema_file:
        jsonld = Path(args.schema_file).read_text(encoding="utf-8")
        res = deploy_schema(args.product_no, jsonld, dry_run=not args.apply)
    else:
        if not args.html_file:
            print(json.dumps({"status": "실패", "error": "--html-file 또는 --schema-file 필요"}, ensure_ascii=False))
            sys.exit(1)
        html = Path(args.html_file).read_text(encoding="utf-8")
        res = deploy_detail(args.product_no, html, dry_run=not args.apply)

    # 결과 dict 는 항상 stdout JSON 으로 (호출측이 error/applied 필드로 판정).
    # exit 0 = 결과 산출 성공(논리 실패 포함). 비0 은 argparse/파일읽기 등 크래시만.
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    _cli()
