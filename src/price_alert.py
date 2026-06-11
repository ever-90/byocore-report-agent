"""
price_alert.py — 가격 경쟁 알림 (읽기 집계 + 자기 전용 이력). ★ 추적만 — 가격 변경/PUT 절대 없음.

[원칙]
- LLM 0 · 네트워크 0: scan.py(byocore-sales-agent)가 이미 수집한 scan_result.json 만 read.
- 기존 파일 write 0: 쓰는 파일은 자기 전용 이력 price_history.json 1개뿐 (원자적 upsert).
- 실데이터만: 경쟁가분포 없는 행(스캔 에러)은 스킵 카운트로만 보고 — 가짜값 없음.

[알림 기준]
절대 (전이 알림 — 전일 비-A 상태에서 오늘 새로 진입한 제품만 개별 알림):
  A1 가격역전 : 자사가 <= 중위가 × PA_ABS_MEDIAN_RATIO (기본 1.0)
  A2 완전역전 : 자사가 <= 경쟁 최저가 (A1과 동시면 A2만 표시)
  지속분은 요약 1줄. 이력 1일차는 전이 비교 불가 → 전체 baseline 적재만 표시.
변동 (2일차부터 — 이력의 직전 날짜 대비):
  D1 중위가변동 : ±PA_DELTA_MEDIAN_PCT % 이상 (기본 10)
  D2 최저가하락 : -PA_DELTA_LOW_PCT % 이상 하락 (기본 15)
  D3 자사가변동 : ±PA_DELTA_OWN_PCT % 이상 (기본 5 — 프로모 중 노이즈 방지.
                  0 = 모든 변동 알림, 'off' = 비활성. .env 로 조절)
  D4 위치이동   : 우리위치_퍼센트 ±PA_DELTA_POS_PT pt 이상 (기본 20)
  D5 경쟁출현/소멸 : 건수 0 → PA_DELTA_COUNT_MIN(기본 3)건 이상, 또는 그 역

[이력] data/price_history.json (PRICE_HISTORY_PATH override)
  {"snapshots": {"YYYY-MM-DD": {"scanned_at_kst", "products": {code: {own,low,mid,high,n,pos}}}}}
  같은 날 재실행 = 해당 날짜 덮어쓰기(멱등). PA_HISTORY_KEEP_DAYS(기본 90) 초과분 prune.

[CLI]
  python -m src.price_alert            # 판정 + 이력 upsert + 사람용 출력
  python -m src.price_alert --json     # JSON 출력
  python -m src.price_alert --dry      # 이력 안 씀 (판정만)
종료코드: scan_result.json 없음/파손 = 1, 그 외 항상 0 (알림 발생 != 실패).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_SCAN_RESULT = _REPO.parent / "byocore-sales-agent" / "data" / "scan_result.json"
_DEFAULT_HISTORY = _REPO / "data" / "price_history.json"


# ── 경로 / 임계값 (.env override) ───────────────────────────────────────────
def _scan_result_path() -> Path:
    raw = (config.get("SCAN_RESULT_PATH") or "").strip()
    return Path(raw) if raw else _DEFAULT_SCAN_RESULT


def _history_path() -> Path:
    raw = (config.get("PRICE_HISTORY_PATH") or "").strip()
    return Path(raw) if raw else _DEFAULT_HISTORY


def _env_float(key: str, default: float) -> float:
    try:
        return float(config.get(key) or default)
    except ValueError:
        return default


def _own_pct_threshold() -> float | None:
    """D3 임계(%). 기본 5. 0 = 모든 변동, 'off'/'-1' = 비활성(None)."""
    raw = str(config.get("PA_DELTA_OWN_PCT") or "5").strip().lower()
    if raw in ("off", "-1"):
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 5.0


# ── scan_result 읽기 → 스냅샷 ──────────────────────────────────────────────
def load_scan(path: Path | None = None) -> dict:
    """scan_result.json → dict. 없음/파손 → FileNotFoundError/ValueError (호출부에서 rc=1)."""
    p = path or _scan_result_path()
    if not p.exists():
        raise FileNotFoundError(f"scan_result.json 없음: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def snapshot_from_scan(scan: dict) -> tuple[str, dict, dict, int]:
    """
    scan_result → (date, snapshot_products, code→제품명, 스킵행수).
    results + niches 모두 포함. 경쟁가분포/자사가 없는 에러 행은 스킵.
    products[code] = {own, low, mid, high, n, pos}
    """
    scanned_at = str(scan.get("스캔시각_kst", ""))
    date = scanned_at[:10]
    products: dict[str, dict] = {}
    names: dict[str, str] = {}
    skipped = 0
    for row in list(scan.get("results", [])) + list(scan.get("niches", [])):
        code = str(row.get("product_code", "")).strip()
        own = row.get("자사가")
        dist = row.get("경쟁가분포")
        if not code or not isinstance(own, (int, float)) or not isinstance(dist, dict):
            skipped += 1
            continue
        products[code] = {
            "own": own,
            "low": dist.get("최저"), "mid": dist.get("중위"), "high": dist.get("최고"),
            "n": int(dist.get("건수") or 0),
            "pos": row.get("우리위치_퍼센트"),
        }
        names[code] = str(row.get("제품명", ""))
    return date, products, names, skipped


# ── 이력 (자기 전용 JSON — upsert by date, 원자적 쓰기) ─────────────────────
def load_history(path: Path | None = None) -> dict:
    p = path or _history_path()
    if not p.exists():
        return {"_안내": "price_alert.py 전용 이력. 추적만 — 가격 변경 기능 없음.", "snapshots": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("snapshots"), dict):
            return data
    except Exception:
        pass
    return {"_안내": "price_alert.py 전용 이력. 추적만 — 가격 변경 기능 없음.", "snapshots": {}}


def upsert_history(history: dict, date: str, scanned_at: str, products: dict,
                   path: Path | None = None) -> Path:
    """snapshots[date] 덮어쓰기(멱등) + keep_days 초과 prune + tmp→replace 원자 저장."""
    keep = int(_env_float("PA_HISTORY_KEEP_DAYS", 90))
    snaps = history["snapshots"]
    snaps[date] = {"scanned_at_kst": scanned_at, "products": products}
    for d in sorted(snaps)[:-keep] if len(snaps) > keep else []:
        del snaps[d]
    out = path or _history_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(out)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
    return out


def previous_snapshot(history: dict, today: str) -> tuple[str, dict] | None:
    """오늘 제외 가장 최근 날짜의 스냅샷. 없으면 None (이력 1일차)."""
    dates = sorted(d for d in history["snapshots"] if d < today)
    if not dates:
        return None
    prev = dates[-1]
    return prev, history["snapshots"][prev].get("products", {})


# ── 판정 ────────────────────────────────────────────────────────────────────
def check_absolute(products: dict, names: dict) -> list[dict]:
    """A1/A2 — 당일 스냅샷만으로 판정 (1일차부터)."""
    ratio = _env_float("PA_ABS_MEDIAN_RATIO", 1.0)
    alerts: list[dict] = []
    for code, p in sorted(products.items()):
        own, low, mid, n = p["own"], p["low"], p["mid"], p["n"]
        if n <= 0:
            continue   # 니치 — 비교 대상 없음
        if isinstance(low, (int, float)) and own <= low:
            alerts.append({"code": "A2", "kind": "완전역전", "product_code": code,
                           "제품명": names.get(code, ""),
                           "detail": f"자사가 {own:,.0f} <= 최저 {low:,.0f}"})
        elif isinstance(mid, (int, float)) and own <= mid * ratio:
            alerts.append({"code": "A1", "kind": "가격역전", "product_code": code,
                           "제품명": names.get(code, ""),
                           "detail": f"자사가 {own:,.0f} <= 중위 {mid:,.0f}"
                                     + (f" × {ratio}" if ratio != 1.0 else "")})
    return alerts


def _pct(cur, prev) -> float | None:
    if not isinstance(cur, (int, float)) or not isinstance(prev, (int, float)) or prev == 0:
        return None
    return (cur - prev) / prev * 100


def check_delta(today: dict, prev: dict, names: dict) -> list[dict]:
    """D1~D5 — 직전 스냅샷 대비 (양쪽에 있는 product_code만)."""
    th_mid = _env_float("PA_DELTA_MEDIAN_PCT", 10)
    th_low = _env_float("PA_DELTA_LOW_PCT", 15)
    th_own = _own_pct_threshold()           # None = off, 0 = 모든 변동
    th_pos = _env_float("PA_DELTA_POS_PT", 20)
    cnt_min = int(_env_float("PA_DELTA_COUNT_MIN", 3))

    alerts: list[dict] = []
    for code in sorted(set(today) & set(prev)):
        t, p = today[code], prev[code]
        name = names.get(code, "")

        d = _pct(t["mid"], p["mid"])        # D1 중위가변동
        if d is not None and abs(d) >= th_mid:
            alerts.append({"code": "D1", "kind": "중위가변동", "product_code": code, "제품명": name,
                           "detail": f"중위 {p['mid']:,.0f}→{t['mid']:,.0f} ({d:+.1f}%)"})

        d = _pct(t["low"], p["low"])        # D2 최저가하락
        if d is not None and d <= -th_low:
            alerts.append({"code": "D2", "kind": "최저가하락", "product_code": code, "제품명": name,
                           "detail": f"최저 {p['low']:,.0f}→{t['low']:,.0f} ({d:+.1f}%)"})

        d = _pct(t["own"], p["own"])        # D3 자사가변동 (추적만 — 가격 변경 아님)
        if th_own is not None and d is not None and d != 0 and abs(d) >= th_own:
            alerts.append({"code": "D3", "kind": "자사가변동", "product_code": code, "제품명": name,
                           "detail": f"자사가 {p['own']:,.0f}→{t['own']:,.0f} ({d:+.1f}%)"})

        tp, pp = t.get("pos"), p.get("pos")  # D4 위치이동
        if isinstance(tp, (int, float)) and isinstance(pp, (int, float)) and abs(tp - pp) >= th_pos:
            alerts.append({"code": "D4", "kind": "위치이동", "product_code": code, "제품명": name,
                           "detail": f"우리위치 {pp:.0f}%→{tp:.0f}% ({tp - pp:+.0f}pt)"})

        tn, pn = t["n"], p["n"]              # D5 경쟁출현/소멸
        if pn == 0 and tn >= cnt_min:
            alerts.append({"code": "D5", "kind": "경쟁출현", "product_code": code, "제품명": name,
                           "detail": f"경쟁 0→{tn}건 (니치→경쟁)"})
        elif pn >= cnt_min and tn == 0:
            alerts.append({"code": "D5", "kind": "경쟁소멸", "product_code": code, "제품명": name,
                           "detail": f"경쟁 {pn}→0건 (경쟁→니치)"})
    return alerts


# ── 메인 ────────────────────────────────────────────────────────────────────
def run(dry: bool = False) -> dict:
    """판정 + (dry 아니면) 이력 upsert. 반환: 구조화 결과 dict."""
    scan = load_scan()
    date, products, names, skipped = snapshot_from_scan(scan)
    history = load_history()
    prev = previous_snapshot(history, date)

    abs_all = check_absolute(products, names)
    state = {"A1": sum(1 for a in abs_all if a["code"] == "A1"),
             "A2": sum(1 for a in abs_all if a["code"] == "A2"), "total": len(abs_all)}
    if prev:   # 전이 판정: 전일 스냅샷에 A 상태가 아니었던 제품만 개별 알림
        prev_abs = {a["product_code"] for a in check_absolute(prev[1], {})}
        absolute = [a for a in abs_all if a["product_code"] not in prev_abs]
    else:      # 이력 1일차 — 전이 비교 불가, baseline 적재만
        absolute = []
    delta = check_delta(products, prev[1], names) if prev else []

    if not dry:
        upsert_history(history, date, str(scan.get("스캔시각_kst", "")), products)

    return {
        "date": date,
        "scanned_at_kst": str(scan.get("스캔시각_kst", "")),
        "compared_to": prev[0] if prev else None,   # None = 이력 1일차
        "absolute": absolute,            # 신규 진입(전이)만
        "absolute_state": state,         # 당일 A 상태 전체 집계 (지속 포함)
        "delta": delta,
        "counts": {"absolute": len(absolute), "delta": len(delta),
                   "제품수": len(products), "스킵행": skipped},
        "history_path": str(_history_path()),
        "dry_run": dry,
        "_안내": "추적 전용 — 가격 변경 기능 없음 (PUT 0 · LLM 0)",
    }


def _format_human(res: dict) -> str:
    lines = [f"[가격경쟁 알림] {res['date']} (스캔 {res['scanned_at_kst'][11:16] or '?'} KST"
             + (f" · 비교기준 {res['compared_to']})" if res["compared_to"] else ")")]
    st = res["absolute_state"]
    if res["absolute"]:
        lines.append(f"■ 절대 기준 신규 진입: {len(res['absolute'])}건")
        lines += [f"  · {a['code']} {a['kind']} | {a['product_code']} {a['제품명'][:30]} | {a['detail']}"
                  for a in res["absolute"]]
    if res["compared_to"] is None:
        if st["total"]:
            lines.append(f"■ 절대 상태 {st['total']}건 적재 (A1 {st['A1']} · A2 {st['A2']} — 전이 비교 익일부터)")
        lines.append("변동 비교 대기 (이력 1일차 — baseline 적재"
                     + (" 완료)" if not res["dry_run"] else " 안 함: --dry)"))
    elif st["total"]:
        persist = st["total"] - len(res["absolute"])
        lines.append(f"■ 절대 기준 상태: A1 {st['A1']} · A2 {st['A2']}건"
                     + (f" (지속 {persist} · 신규 {len(res['absolute'])})" if res["absolute"]
                        else " 지속(전이 없음)"))
    if res["compared_to"] is not None and res["delta"]:
        lines.append(f"■ 변동 기준: {len(res['delta'])}건")
        lines += [f"  · {a['code']} {a['kind']} | {a['product_code']} {a['제품명'][:30]} | {a['detail']}"
                  for a in res["delta"]]
    if not res["absolute"] and not res["delta"] and res["compared_to"] is not None:
        lines.append("신규 알림 없음 (절대 전이 0 · 변동 0)")
    if res["counts"]["스킵행"]:
        lines.append(f"(스캔 에러 행 {res['counts']['스킵행']}건 스킵)")
    lines.append("※ 추적 전용 — 가격 변경 기능 없음")
    return "\n".join(lines)


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="price_alert")
    ap.add_argument("--json", action="store_true", help="구조화 JSON 출력")
    ap.add_argument("--dry", action="store_true", help="이력 안 씀 (판정만)")
    args = ap.parse_args()
    try:
        res = run(dry=args.dry)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        print(f"[오류] scan_result.json 읽기 실패: {e}")
        sys.exit(1)
    print(json.dumps(res, ensure_ascii=False, indent=2) if args.json else _format_human(res))
    sys.exit(0)


if __name__ == "__main__":
    _cli()
