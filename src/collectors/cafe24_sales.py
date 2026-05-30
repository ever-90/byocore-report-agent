"""
cafe24_sales.py — [스텁] Cafe24 주문 조회 → 매출 집계.

[1단계 READ-ONLY]
- Admin API GET 만 사용. 주문/상품/회원을 변경하는 API(POST/PUT/DELETE) 호출 금지.
- 엔드포인트: GET https://{mall_id}.cafe24api.com/api/v2/admin/orders
- 필수 헤더: Authorization: Bearer {token}, X-Cafe24-Api-Version: 2026-03-01

이 모듈은 1단계에서 스텁이며, 실제 수집 로직은 2단계에서 구현한다.
"""

from .. import config
# from .. import cafe24_auth   # 2단계에서 토큰 사용 시 활성화

ORDERS_PATH = "/api/v2/admin/orders"


def _orders_url() -> str:
    mall_id = config.require("CAFE24_MALL_ID")
    return f"https://{mall_id}.cafe24api.com{ORDERS_PATH}"


def collect_sales(start_date: str, end_date: str) -> dict:
    """
    [스텁] start_date~end_date (YYYY-MM-DD) 기간의 매출 집계 결과를 반환.

    TODO(2단계):
      1) cafe24_auth.get_access_token() 으로 access_token 확보.
      2) GET {_orders_url()}?start_date=&end_date=&limit=&offset= 페이지네이션 순회.
         - 헤더: Authorization: Bearer ...,
                 X-Cafe24-Api-Version: config.CAFE24_API_VERSION
      3) 주문 항목에서 결제완료/취소/환불을 분리하고
         총매출·주문수·평균객단가(AOV) 계산.
      4) 반환 예: {"start": ..., "end": ..., "gross_sales": ..., "orders": ...,
                  "cancels": ..., "refunds": ..., "aov": ...}
      5) READ-ONLY: GET 외 메서드 호출 금지.
    """
    raise NotImplementedError("cafe24_sales.collect_sales 는 2단계에서 구현 예정 (스텁)")
