"""
perf_daily.py — Cafe24 매출을 GEO 시트 BYOCORE_PERF_DAILY 에 upsert (복구 방향 A).

[배경]
GAS byo_dailyReport 가 Python reporter 와 Cafe24 토큰 충돌 회피로 5/30 비활성화됨.
PERF_DAILY 시트 쓰기가 그 함수에 묶여 같이 멈춰 공백 발생.
→ Python reporter 가 이미 매일 Cafe24 매출을 수집하므로, 그 데이터를 시트에 직접 upsert.
  GAS 재가동·재인증 없이 토큰 충돌 원천 제거.

[upsert]
- 같은 date 행이 있으면  amount / order_count / updated_at 만 갱신 (나머지 컬럼 보존).
- 없으면 신규 행 추가. ★ 미수집 컬럼은 빈칸('') — 가짜값(0) 절대 금지.

[컬럼 매핑 — 기존 시트 헤더 그대로]
  date          : YYYY-MM-DD
  amount        : gross (total_payment_amount, 주문 payment_amount 합) — 에버 확정(2026-06-08)
  order_count   : /orders/count 권위값
  new_members   : '' (reporter 미수집 — GAS 도 없으면 빈칸)
  sold_out      : '' (미수집)
  new_reviews   : '' (미수집)
  reconcile_ok  : '' (Python 은 대시보드 대사 안 함 — 가짜 TRUE 금지)
  reconcile_diff: '' (미대사)
  updated_at    : 기록 시각 (KST)

[GAS 와의 차이 — 정직 고지]
- GAS amount = Cafe24 대시보드 KPI(dashAmount) 우선. reporter 는 그 API 없음 → 주문 합산(gross).
  5/29 경계에서 정의 차이로 소폭 불연속 가능(취소 적은 날은 거의 동일).

[권한]
- service account = byocore-report-reader@... (이름은 reader 이나 시트 editor 공유 확인됨, no-op write 검증)
- scope = spreadsheets (읽기쓰기). geo_citation(readonly)과 별도.

[단독 실행]
  python -m src.perf_daily                      # 어제(KST) upsert
  python -m src.perf_daily 2026-06-05           # 특정일 upsert
  python -m src.perf_daily backfill 2026-06-01 2026-06-07   # 기간 백필
"""

import datetime
import sys

import gspread
from google.oauth2.service_account import Credentials

from . import config
from .collectors import cafe24_sales
from .collectors.geo_citation import _service_account_path

SHEET_TAB = "BYOCORE_PERF_DAILY"
SCOPES_RW = ["https://www.googleapis.com/auth/spreadsheets"]  # 읽기쓰기 (upsert)
KST = datetime.timezone(datetime.timedelta(hours=9))

# 기존 시트 헤더 (컬럼 순서 고정 — 신규 행 작성 기준)
HEADER = ["date", "amount", "order_count", "new_members", "sold_out",
          "new_reviews", "reconcile_ok", "reconcile_diff", "updated_at"]


def _open_ws():
    """BYOCORE_PERF_DAILY 워크시트(rw). 시트 ID 는 config 경유(하드코딩 0)."""
    creds = Credentials.from_service_account_file(_service_account_path(), scopes=SCOPES_RW)
    gc = gspread.authorize(creds)
    sheet_id = config.GEO_SHEET_ID or config.DEFAULT_GEO_SHEET_ID
    return gc.open_by_key(sheet_id).worksheet(SHEET_TAB)


def _now_kst() -> str:
    return datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def _yesterday_kst() -> str:
    return (datetime.datetime.now(KST).date() - datetime.timedelta(days=1)).isoformat()


def upsert_perf_daily(date: str, amount, order_count, ws=None) -> dict:
    """
    date 행 upsert. READ-then-WRITE.
    - amount: gross 금액(문자열/숫자). 정수 문자열로 기록.
    - order_count: 주문수.
    - 기존 행: B(amount)/C(order_count)/I(updated_at) 만 갱신 → new_members 등 GAS 기존값 보존.
    - 신규 행: 9컬럼, 미수집 컬럼 빈칸('') — ★가짜값 금지.
    반환: {action: 'update'|'append', date, row?}
    """
    datetime.date.fromisoformat(date)  # 형식 검증 (불량 날짜 차단)
    if ws is None:
        ws = _open_ws()

    col_dates = ws.col_values(1)   # A열 전체(헤더 포함)
    updated = _now_kst()
    # 금액 정규화: 정수 문자열 (소수면 반올림 없이 정수부)
    a = str(amount).strip()
    amount_str = str(int(float(a))) if a else "0"
    oc_str = str(int(order_count)) if str(order_count).strip() else "0"

    row_idx = None
    for i, d in enumerate(col_dates):
        if i == 0:
            continue   # 헤더 스킵
        if str(d).strip() == date:
            row_idx = i + 1   # gspread 1-based row
            break

    if row_idx:
        # 기존 행 부분 갱신 (나머지 컬럼 보존)
        ws.update_acell(f"B{row_idx}", amount_str)
        ws.update_acell(f"C{row_idx}", oc_str)
        ws.update_acell(f"I{row_idx}", updated)
        return {"action": "update", "date": date, "row": row_idx}

    # 신규 행 추가 (미수집 컬럼 빈칸)
    new_row = [date, amount_str, oc_str, "", "", "", "", "", updated]
    ws.append_row(new_row, value_input_option="USER_ENTERED")
    return {"action": "append", "date": date}


def sync_date(date: str, ws=None) -> dict:
    """collect_sales(date) → gross/order_count 추출 → upsert. ★실데이터만."""
    r = cafe24_sales.collect_sales(date, date)
    amount = r["total_payment_amount"]      # gross (에버 확정)
    order_count = r.get("order_count", 0)
    return upsert_perf_daily(date, amount, order_count, ws=ws)


def backfill(start: str, end: str) -> list:
    """start~end(YYYY-MM-DD 포함) 날짜별 sync. 같은 ws 재사용. 제품별 독립 try."""
    ws = _open_ws()
    d0 = datetime.date.fromisoformat(start)
    d1 = datetime.date.fromisoformat(end)
    if d0 > d1:
        raise ValueError(f"start({start}) > end({end})")

    results = []
    d = d0
    while d <= d1:
        ds = d.isoformat()
        try:
            res = sync_date(ds, ws=ws)
            results.append(res)
            print(f"[perf_daily] {ds}: {res['action']}", file=sys.stderr)
        except Exception as e:
            print(f"[perf_daily] {ds} 실패: {type(e).__name__}: {e}", file=sys.stderr)
            results.append({"date": ds, "error": str(e)})
        d += datetime.timedelta(days=1)
    return results


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    args = sys.argv[1:]
    if args and args[0].lower() == "backfill":
        if len(args) < 3:
            print("[오류] backfill <start> <end> (YYYY-MM-DD)", file=sys.stderr)
            raise SystemExit(1)
        res = backfill(args[1], args[2])
        ok = len([r for r in res if "error" not in r])
        print(f"백필 완료: {ok}/{len(res)}건 upsert")
        return

    date = args[0] if args else _yesterday_kst()
    res = sync_date(date)
    print(f"[PERF_DAILY] {date}: {res}")


if __name__ == "__main__":
    _cli()
