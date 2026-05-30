"""
reporter.py — 일간 운영 리포트 생성 + 카카오 '나에게 보내기' 발송 (2단계).

[READ-ONLY]
- 데이터는 collectors(GET)로만 수집. 발송은 카카오 '나에게 보내기'(보고 채널) 단독.
- 데이터 소스를 변경하지 않음.

[구성]
  collect_sales(어제 KST) → net 중심 텍스트 생성 → kakao_send.send_text 발송.

[함수]
- build_daily_report(date) -> str   : 리포트 텍스트 생성(수집 포함, 발송 안 함)
- send_daily_report(date)  -> dict  : 생성 + 카카오 발송, 결과 반환

[단독 실행]
  python -m src.reporter              # 어제(KST) 리포트 생성→카톡 발송
  python -m src.reporter 2026-05-29   # 특정일

[메모]
- 카카오 기본 text 템플릿 본문 한도(약 200자) 내로 유지. 이상알림/인용률은 다음 단계.
"""

import datetime
import sys
from decimal import Decimal, InvalidOperation

import requests

from . import config, kakao_send
from .collectors import cafe24_sales

KST = datetime.timezone(datetime.timedelta(hours=9))
KAKAO_TEXT_LIMIT = 200  # 카카오 기본 text 템플릿 본문 한도(자)


def _yesterday_kst() -> str:
    now_kst = datetime.datetime.now(KST)
    return (now_kst.date() - datetime.timedelta(days=1)).isoformat()


def _money(amount_str: str, currency: str = "KRW") -> str:
    """금액 문자열 → 사람용 표기. KRW 는 '₩' 접두, 그 외는 '값 통화'."""
    try:
        d = Decimal(str(amount_str))
    except (InvalidOperation, TypeError, ValueError):
        return f"{amount_str} {currency}"
    whole = d == d.to_integral_value()
    if currency == "KRW":
        return f"₩{int(d):,}" if whole else f"₩{d:,.2f}"
    return f"{int(d):,} {currency}" if whole else f"{d:,.2f} {currency}"


def build_daily_report(date: str) -> str:
    """date(YYYY-MM-DD) 하루 매출을 net 중심 박스 텍스트로 생성. (READ-ONLY 수집)"""
    r = cafe24_sales.collect_sales(date, date)
    currency = r["currency"]
    net = _money(r["net_payment_amount"], currency)
    gross = _money(r["total_payment_amount"], currency)
    canceled_amt = _money(r["canceled_amount"], currency)
    canceled_cnt = r["canceled_count"]

    lines = [
        f"┌ BYOCORE 일간 리포트 ({date})",
        f"│ 매출(net): {net}",
    ]
    if canceled_cnt > 0:
        lines.append(f"│   └ 취소 {canceled_cnt}건 {canceled_amt} 제외 (gross {gross})")
        lines.append(f"│ 주문: {r['net_order_count']}건 (취소 {canceled_cnt} 제외)")
    else:
        lines.append("│   └ 취소 없음")
        lines.append(f"│ 주문: {r['net_order_count']}건")
    lines.append(f"│ 구매자: {r['unique_buyers']}명")
    lines.append("└ (이상알림·인용률은 다음 단계)")
    return "\n".join(lines)


def _report_link() -> str:
    """카카오 메모 버튼 링크. 브랜드 도메인(REDIRECT_URI) 우선, 없으면 기본 링크."""
    return config.get("CAFE24_REDIRECT_URI") or kakao_send.DEFAULT_LINK


def send_daily_report(date: str) -> dict:
    """일간 리포트 생성 후 카카오 '나에게 보내기'로 발송. 결과 dict 반환."""
    text = build_daily_report(date)
    result = kakao_send.send_text(text, link_url=_report_link(), button_title="BYOCORE")
    return {"date": date, "text": text, "send_result": result}


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows 콘솔 대비
    except Exception:
        pass

    date = sys.argv[1] if len(sys.argv) > 1 else _yesterday_kst()

    # 1) 수집 + 리포트 생성 (Cafe24 GET) — 발송 전에 미리보기
    try:
        text = build_daily_report(date)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"[오류] Cafe24 수집 실패(HTTP {status}). "
              f"토큰 만료 시 'python -m src.cafe24_auth authorize' 로 재인가하세요.")
        raise

    print("----- 리포트 미리보기 -----")
    print(text)
    print(f"(본문 {len(text)}자 / 카카오 한도 {KAKAO_TEXT_LIMIT}자)")
    if len(text) > KAKAO_TEXT_LIMIT:
        print(f"[주의] 본문이 {KAKAO_TEXT_LIMIT}자를 초과 — 분할 발송은 다음 단계 과제.")

    # 2) 카카오 '나에게 보내기' 발송
    print("----- 카카오 발송 -----")
    try:
        result = kakao_send.send_text(text, link_url=_report_link(), button_title="BYOCORE")
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"[오류] 카카오 발송 실패(HTTP {status}). "
              f"토큰 만료/동의 시 'python -m src.kakao_auth authorize' 로 재인가하세요.")
        raise
    print(f"발송 성공: {result}")


if __name__ == "__main__":
    _cli()
