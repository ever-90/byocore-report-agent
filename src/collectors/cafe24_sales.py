"""
cafe24_sales.py — Cafe24 주문 조회 → 일별 매출 집계 (2단계 구현).

[READ-ONLY 원칙]
- Admin API GET 만 사용. 주문/상품/회원을 변경하는 API(POST/PUT/DELETE) 호출 금지.

[엔드포인트 — RECON/실측 확정]
- 목록 : GET https://{mall_id}.cafe24api.com/api/v2/admin/orders
- 건수 : GET https://{mall_id}.cafe24api.com/api/v2/admin/orders/count
- 헤더 : Authorization: Bearer {access_token},  X-Cafe24-Api-Version: 2026-03-01

[요청 파라미터]
- start_date, end_date : YYYY-MM-DD (필수, 종료일 포함). 기간은 date_type 기준.
- date_type            : (선택) 미지정 시 Cafe24 기본값(order_date) 사용.
- limit, offset        : 페이지네이션. limit=100 고정, offset 으로 반복 조회.

[응답 필드 — 2026-03-01 버전 실측(2026-05-30)]
- 최상위         : {"orders": [...], "links": {...}}
- order.payment_amount      : 문자열 금액(예 "20770.00") = 실결제액  ← 매출 합산 기준
- order.actual_order_amount : 금액 상세 분해(주문가/배송비/쿠폰할인 등) 객체
- order.member_id           : 회원 식별자(비회원은 빈 문자열) ← 구매자수 기준
- order.canceled            : 취소 표시("T"/"F")  ← 참고용 canceled_count
- order.currency            : 통화(예 "KRW")

[집계 항목]
- 주문건수      : /orders/count (권위값) + 합산 주문수(교차검증), net/취소 분리
- 총결제(gross) : payment_amount 합 (Decimal 정밀 합산, 취소 포함)
- 실매출(net)   : canceled="F" 주문만 합산 (취소 제외 = 실매출)
- 취소         : canceled="T" 주문 합산/건수
- 구매자수      : 회원 member_id distinct + 비회원 주문 개별 계수

[단독 실행]
  python -m src.collectors.cafe24_sales              # 어제(KST) 집계
  python -m src.collectors.cafe24_sales 2026-05-29   # 특정일
  python -m src.collectors.cafe24_sales 2026-05-01 2026-05-29   # 기간
"""

import datetime
import sys
import time
from decimal import Decimal, InvalidOperation

import requests

from .. import cafe24_auth, config

API_PATH_ORDERS = "/api/v2/admin/orders"
API_PATH_ORDERS_COUNT = "/api/v2/admin/orders/count"

PAGE_LIMIT = 100            # Cafe24 안전 페이지 크기 (100건 초과 시 offset 반복)
MAX_OFFSET = 10_000         # Cafe24 offset 상한(초과 시 날짜 분할 필요)
REQUEST_TIMEOUT = 20        # seconds
PAGE_DELAY_SEC = 0.3        # 페이지 간 호출 간격(레이트리밋 배려)
TRANSIENT_STATUSES = (429, 500, 502, 503, 504)

KST = datetime.timezone(datetime.timedelta(hours=9))


# ---------------------------------------------------------------------------
# HTTP (READ-ONLY GET)
# ---------------------------------------------------------------------------
def _base_url() -> str:
    mall_id = config.require("CAFE24_MALL_ID")
    return f"https://{mall_id}.cafe24api.com"


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "X-Cafe24-Api-Version": config.CAFE24_API_VERSION,
    }


def _request_once(url: str, params: dict) -> requests.Response:
    """GET 1회. 401(만료) 시 refresh 후 1회 재시도."""
    token = cafe24_auth.get_access_token()
    resp = requests.get(url, headers=_headers(token), params=params, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 401:
        token = cafe24_auth.refresh_access_token().get("access_token", "")
        resp = requests.get(url, headers=_headers(token), params=params, timeout=REQUEST_TIMEOUT)
    return resp


def _get(path: str, params: dict) -> dict:
    """READ-ONLY GET + JSON. 429/5xx 일시 오류는 백오프 후 재시도."""
    url = _base_url() + path
    resp = None
    for attempt in range(3):
        resp = _request_once(url, params)
        if resp.status_code < 400:
            return resp.json()
        if resp.status_code in TRANSIENT_STATUSES and attempt < 2:
            time.sleep(1.0 * (attempt + 1))
            continue
        break
    # 비정상 상태 → 명확한 에러로 승격
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------
def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _validate_date(label: str, d: str) -> None:
    try:
        datetime.date.fromisoformat(d)
    except (ValueError, TypeError):
        raise ValueError(f"{label} 날짜 형식 오류(YYYY-MM-DD 이어야 함): {d!r}")


def _format_amount(total: Decimal, currency: str) -> str:
    if total == total.to_integral_value():
        return f"{int(total):,} {currency}"
    return f"{total:,.2f} {currency}"


def _amount_str(d: Decimal) -> str:
    """정밀 금액 문자열: 정수면 정수, 아니면 소수 2자리."""
    if d == d.to_integral_value():
        return str(int(d))
    return f"{d:.2f}"


# ---------------------------------------------------------------------------
# API 래퍼
# ---------------------------------------------------------------------------
def get_order_count(start_date: str, end_date: str, date_type: str | None = None) -> int:
    """기간 내 주문 건수(권위값)."""
    params = {"start_date": start_date, "end_date": end_date}
    if date_type:
        params["date_type"] = date_type
    data = _get(API_PATH_ORDERS_COUNT, params)
    return int(data.get("count", 0))


def iter_orders(start_date: str, end_date: str, date_type: str | None = None):
    """기간 내 주문을 offset 페이지네이션으로 순회(제너레이터). READ-ONLY."""
    offset = 0
    while True:
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "limit": PAGE_LIMIT,
            "offset": offset,
        }
        if date_type:
            params["date_type"] = date_type
        data = _get(API_PATH_ORDERS, params)
        orders = data.get("orders", [])
        if not orders:
            break
        yield from orders
        if len(orders) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
        if offset > MAX_OFFSET:
            # Cafe24 offset 상한(유효 범위 0..MAX_OFFSET). 더 큰 기간은 날짜 분할로 호출해야 함.
            raise RuntimeError(
                f"offset 상한({MAX_OFFSET}) 초과: 기간을 좁혀(일 단위 분할) 호출하세요."
            )
        time.sleep(PAGE_DELAY_SEC)


# ---------------------------------------------------------------------------
# 집계 (메인 진입점)
# ---------------------------------------------------------------------------
def collect_sales(start_date: str, end_date: str, date_type: str | None = None) -> dict:
    """
    start_date~end_date (YYYY-MM-DD, 종료일 포함) 기간의 매출을 집계해 반환. READ-ONLY.

    반환 dict:
      start_date, end_date, date_type
      order_count           : /orders/count 권위값
      orders_summed         : 실제 합산한 주문 수 (order_count 와 교차검증)
      total_payment_amount  : 총결제 gross(payment_amount 합, 취소 포함) — 문자열(정밀)
      total_payment_display : gross 사람용 표기(천단위+통화)
      net_payment_amount    : 실매출 net(canceled="F" 합, 취소 제외) — 문자열(정밀)
      net_payment_display   : net 사람용 표기
      net_order_count       : 취소 제외 주문건수
      canceled_count        : 취소(canceled="T") 주문 수
      canceled_amount       : 취소 주문 합 — 문자열(정밀)
      canceled_amount_display : 취소 합 사람용 표기
      currency              : 통화(단일이면 값, 혼재 시 "MIXED:...")
      unique_buyers         : 구매자수(회원 distinct + 비회원 주문 개별)
      member_buyers         : 회원 구매자수(distinct member_id)
      guest_orders          : 비회원 주문 수
      warnings              : 점검 경고 리스트(주문수/정합성/통화)
    """
    _validate_date("start_date", start_date)
    _validate_date("end_date", end_date)
    if start_date > end_date:
        raise ValueError(f"start_date({start_date}) 가 end_date({end_date}) 보다 큽니다.")

    authoritative_count = get_order_count(start_date, end_date, date_type)

    total = Decimal("0")          # gross (취소 포함)
    net_total = Decimal("0")      # net  (canceled="F" 만)
    canceled_total = Decimal("0") # 취소 (canceled="T" 만)
    summed = 0
    net_order_count = 0
    canceled_count = 0
    member_ids: set[str] = set()
    guest_orders = 0
    currencies: set[str] = set()

    for o in iter_orders(start_date, end_date, date_type):
        summed += 1
        amount = _to_decimal(o.get("payment_amount"))
        total += amount

        if str(o.get("canceled", "")).upper() == "T":
            canceled_count += 1
            canceled_total += amount
        else:
            net_order_count += 1
            net_total += amount

        member_id = (o.get("member_id") or "").strip()
        if member_id:
            member_ids.add(member_id)
        else:
            guest_orders += 1

        cur = o.get("currency")
        if cur:
            currencies.add(cur)

    warnings: list[str] = []
    if summed != authoritative_count:
        warnings.append(
            f"주문수 불일치(count API={authoritative_count}, 합산={summed}): "
            f"집계 중 신규주문 유입 또는 기간 경계 차이 가능."
        )
    # 내부 정합성 교차검증: 분해 합 == 전체
    if net_total + canceled_total != total:
        warnings.append("내부정합성 경고: net+취소 금액 합이 gross 와 불일치.")
    if net_order_count + canceled_count != summed:
        warnings.append("내부정합성 경고: net+취소 건수 합이 합산건수와 불일치.")

    if len(currencies) <= 1:
        currency = next(iter(currencies)) if currencies else "KRW"
    else:
        currency = "MIXED:" + ",".join(sorted(currencies))
        warnings.append(f"통화 혼재: {sorted(currencies)} → 금액 단순 합산 주의.")

    return {
        "start_date": start_date,
        "end_date": end_date,
        "date_type": date_type or "(Cafe24 기본값)",
        "order_count": authoritative_count,
        "orders_summed": summed,
        # gross (취소 포함)
        "total_payment_amount": _amount_str(total),
        "total_payment_display": _format_amount(total, currency),
        # net (취소 제외 = 실매출)
        "net_payment_amount": _amount_str(net_total),
        "net_payment_display": _format_amount(net_total, currency),
        "net_order_count": net_order_count,
        # 취소
        "canceled_count": canceled_count,
        "canceled_amount": _amount_str(canceled_total),
        "canceled_amount_display": _format_amount(canceled_total, currency),
        "currency": currency,
        "unique_buyers": len(member_ids) + guest_orders,
        "member_buyers": len(member_ids),
        "guest_orders": guest_orders,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 단독 실행
# ---------------------------------------------------------------------------
def _yesterday_kst() -> str:
    now_kst = datetime.datetime.now(KST)
    return (now_kst.date() - datetime.timedelta(days=1)).isoformat()


def _print_result(r: dict) -> None:
    print(f"[Cafe24 매출 집계] {r['start_date']} ~ {r['end_date']}  (date_type={r['date_type']})")
    print(f"  주문건수      : {r['order_count']:,} 건  (net {r['net_order_count']:,} / 취소 {r['canceled_count']:,})")
    print(f"  실매출(net)   : {r['net_payment_display']}  (취소 제외)")
    print(f"  총결제(gross) : {r['total_payment_display']}  (취소 {r['canceled_amount_display']} 포함)")
    print(f"  구매자수      : {r['unique_buyers']:,} 명  "
          f"(회원 {r['member_buyers']:,} distinct + 비회원주문 {r['guest_orders']:,})")
    print(f"  교차검증      : count={r['order_count']:,} / 합산={r['orders_summed']:,} "
          f"→ {'일치' if not r['warnings'] else '경고있음'}")
    for w in r["warnings"]:
        print(f"  [경고] {w}")


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows 콘솔 대비
    except Exception:
        pass

    start = sys.argv[1] if len(sys.argv) > 1 else _yesterday_kst()
    end = sys.argv[2] if len(sys.argv) > 2 else start
    try:
        result = collect_sales(start, end)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"[오류] Cafe24 API 호출 실패(HTTP {status}). "
              f"토큰 만료/폐기 시 'python -m src.cafe24_auth authorize' 로 재인가하세요.")
        raise
    _print_result(result)


if __name__ == "__main__":
    _cli()
