"""
reporter.py — 일간 운영 리포트 생성 + 카카오 '나에게 보내기' 발송 (2단계).

[READ-ONLY]
- 데이터는 collectors(GET)로만 수집. 발송은 카카오 '나에게 보내기'(보고 채널) 단독.
- 데이터 소스를 변경하지 않음.

[구성]
  collect_sales(어제 KST) + geo_citation(인용률) + 이상알림(매출 7일중앙값比 / 인용 건수변화)
  → net 중심 텍스트 → kakao_send.send_text 발송.
  GEO·이상알림은 각각 독립 try — 실패/측정대기여도 매출 리포트는 정상 발송.

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
from .collectors import cafe24_sales, geo_citation

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
    """date(YYYY-MM-DD) 하루 매출 + 최신 GEO 핵심 인용률을 박스 텍스트로 생성. (READ-ONLY)"""
    r = cafe24_sales.collect_sales(date, date)
    currency = r["currency"]
    net = _money(r["net_payment_amount"], currency)
    gross = _money(r["total_payment_amount"], currency)
    canceled_amt = _money(r["canceled_amount"], currency)
    canceled_cnt = r["canceled_count"]

    lines = [f"┌ BYOCORE 일간 리포트 ({date})"]
    lines.append(f"│ 매출(net): {net}")
    if canceled_cnt > 0:
        lines.append(f"│   └ 취소 {canceled_cnt}건 {canceled_amt} 제외 (gross {gross})")
    else:
        lines.append("│   └ 취소 없음")
    # 핵심 인용률 — GEO 조회 실패가 매출 리포트를 막지 않도록 독립 처리(매출 블록 아래)
    lines.append(_citation_line())
    if canceled_cnt > 0:
        lines.append(f"│ 주문: {r['net_order_count']}건 (취소 {canceled_cnt} 제외)")
    else:
        lines.append(f"│ 주문: {r['net_order_count']}건")
    lines.append(f"│ 구매자: {r['unique_buyers']}명")
    # 이상알림 (각각 독립 try — 실패해도 매출 리포트는 정상 발송)
    lines.append(f"│ {_sales_anomaly_text(date, Decimal(r['net_payment_amount']))}")
    lines.append(f"└ {_citation_anomaly_text()}")
    return "\n".join(lines)


def _median(values: list) -> Decimal:
    """정렬 후 중앙값(짝수면 두 중앙값 평균). Decimal 정밀 유지. (평균 아님 — 이벤트일 강건)"""
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def _baseline_nets(date: str, days: int = 7) -> list:
    """date 직전 `days`일의 net 매출(활동일=order_count>0)을 collect_sales 일별 호출로 수집. READ-ONLY."""
    d0 = datetime.date.fromisoformat(date)
    nets = []
    for i in range(1, days + 1):
        dd = (d0 - datetime.timedelta(days=i)).isoformat()
        rr = cafe24_sales.collect_sales(dd, dd)
        if rr.get("order_count", 0) > 0:          # 활동일만 기준에 포함
            nets.append(Decimal(rr["net_payment_amount"]))
    return nets


def _sales_anomaly_text(date: str, yest_net: Decimal) -> str:
    """
    어제 net vs 직전 7일 net '중앙값'(이벤트일 강건) 비교. ±30% 벗어나면 ⚠️ + 편차%.
    독립 try: 실패/데이터부족(7일 미만)이면 알림 skip 메시지(매출 리포트는 정상 발송).
    """
    try:
        baseline = _baseline_nets(date, days=7)
    except Exception:
        return "매출 점검: 비교 실패"
    if len(baseline) < 7:
        return "매출 점검: 비교 데이터 부족"
    median = _median(baseline)
    if median <= 0:
        return "매출 점검: 비교 데이터 부족"
    dev = (yest_net - median) / median * Decimal(100)
    sign = "+" if dev >= 0 else "-"
    body = f"매출 7일중앙값比 {sign}{abs(dev):.0f}%"
    return f"⚠️ {body}" if abs(dev) > Decimal(30) else body


def _citation_anomaly_text() -> str:
    """
    인용 '건수'(direct+indirect) 변화. 최근 2개 비-biased 측정 비교(단일일 % 비교 금지, biased 제외).
    독립 try: 실패/2개 미만이면 알림 skip 메시지.
    """
    try:
        recent = geo_citation.recent_unbiased_citations(2)
    except Exception:
        return "인용 점검: 비교 실패"
    if len(recent) < 2:
        return "인용 점검: 비교 데이터 부족"
    cur = recent[0]["citation_count"]
    prev = recent[1]["citation_count"]
    if cur == prev:
        return f"인용 {cur}건 유지"
    arrow = "▲" if cur > prev else "▼"
    return f"인용 {prev}건→{cur}건 {arrow}"


def _citation_line() -> str:
    """
    GEO 핵심 인용률 한 줄 생성. READ-ONLY.
    독립 try: GEO 조회가 실패/지연돼도 예외를 전파하지 않아 매출 리포트는 정상 발송된다.
    - None(측정 대기)/값 없음 → "핵심 인용률: 측정 대기"
    - 예외(권한/네트워크 등)    → "핵심 인용률: 조회 실패"
    - is_biased=True           → "(추정)" 부기
    """
    try:
        geo = geo_citation.collect_citation_rate()  # 최신(date max) 행
    except Exception:
        return "│ 핵심 인용률: 조회 실패"
    if not geo or geo.get("citation_rate") is None:
        return "│ 핵심 인용률: 측정 대기"
    rate_disp = f"{geo['citation_rate']:.1f}%"
    if geo.get("is_biased"):
        rate_disp += " (추정)"
    return f"│ 핵심 인용률(Top-100): {rate_disp}"


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
