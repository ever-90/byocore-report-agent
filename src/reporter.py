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


def _today_kst() -> datetime.date:
    return datetime.datetime.now(KST).date()


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
    """
    date(YYYY-MM-DD) 일간 리포트 텍스트 생성. READ-ONLY.
    구성: 매출(net) + GEO인용률 + 주문/구매자 + 이상알림 + 판단카드(GEO×매출×퍼널프록시).
    7일 베이스라인 1회 수집 → anomaly·funnel proxy 공용(신규 API 호출 추가 없음).
    각 섹션 독립 try — 한 섹션 실패해도 나머지 정상 발송.
    200자 트림 우선순위(높을수록 먼저 제거): 취소상세(5) > 주문/구매자(4) > 인용률(3) >
      이상알림(2) > 판단카드(1). 제목·매출(net)(0) 보호.
    """
    # CORE — 오늘 매출. 실패 시 일간 리포트 불가.
    r           = cafe24_sales.collect_sales(date, date)
    currency    = r["currency"]
    net         = _money(r["net_payment_amount"], currency)
    gross       = _money(r["total_payment_amount"], currency)
    canceled_amt = _money(r["canceled_amount"], currency)
    canceled_cnt = r["canceled_count"]
    yest_net    = Decimal(r["net_payment_amount"])

    # 7일 베이스라인 1회 수집 (이상알림 + 퍼널프록시 공용, 독립 try)
    baseline: list = []
    try:
        baseline = _collect_baseline_full(date, days=7)
    except Exception:
        pass   # 빈 리스트 → 비교 불가로 graceful degrade

    # GEO 인용률 줄 (_citation_line 내부에 독립 try 있음)
    cit_line = _citation_line()

    # GEO recent 1회 수집 (인용 이상알림 + 판단카드 + 인용률추세 공용, 독립 try)
    # n=3: 인용률 추세 판정에 3개 필요 (부족 시 _cite_rate_trend → "na" graceful degrade)
    geo_recent: list = []
    try:
        geo_recent = geo_citation.recent_unbiased_citations(3)
    except Exception:
        pass

    # 매출 이상알림 (독립 try, 공용 baseline 사용 — 추가 API 호출 없음)
    try:
        nets = [row["net"] for row in baseline]
        if len(nets) < 7:
            sales_anom = "매출 점검: 비교 데이터 부족"
        else:
            med  = _median(nets)
            if med <= 0:
                sales_anom = "매출 점검: 비교 데이터 부족"
            else:
                dev  = (yest_net - med) / med * Decimal(100)
                sign = "+" if dev >= 0 else "-"
                body = f"매출 7일중앙값比 {sign}{abs(dev):.0f}%"
                # ⚠️ = 나쁜 신호(급락)만. 급등은 경고 아님 — 아이콘 없이 표시.
                sales_anom = f"⚠️ {body}" if dev < Decimal(-30) else body
    except Exception:
        sales_anom = "매출 점검: 비교 실패"

    # 인용 건수 이상알림 (독립 try, 공용 geo_recent)
    try:
        if len(geo_recent) < 2:
            cite_cnt_anom = "인용 건수: 비교 데이터 부족"
        else:
            cur, prev = geo_recent[0]["citation_count"], geo_recent[1]["citation_count"]
            cite_cnt_anom = (
                f"인용 건수: {cur}건 유지" if cur == prev
                else f"인용 건수: {prev}→{cur}건 {'▲' if cur > prev else '▼'}"
            )
    except Exception:
        cite_cnt_anom = "인용 건수: 비교 실패"

    # 인용률 변화 (독립 try — rate 값 있을 때만 표시, 없으면 빈 문자열로 생략)
    # 건수(cite_cnt_anom)와 구분: "율"이라는 단어로 지표 명확화
    cite_rate_anom = ""
    try:
        if len(geo_recent) >= 2:
            cur_rate  = geo_recent[0].get("citation_rate")
            prev_rate = geo_recent[1].get("citation_rate")
            if cur_rate is not None and prev_rate is not None and cur_rate != prev_rate:
                arrow = "▼" if cur_rate < prev_rate else "▲"
                cite_rate_anom = f"인용률: {prev_rate:.1f}→{cur_rate:.1f}% {arrow}"
    except Exception:
        pass   # 율 표시 실패 시 생략 — 건수 줄은 정상 유지

    # 판단 카드 (독립 try — 실패해도 나머지 리포트 정상 발송)
    try:
        proxy    = _funnel_proxy(r, baseline)
        card_txt = _judgment_card(r, geo_recent, proxy, baseline)
    except Exception:
        card_txt = "진단: 산출 실패"

    if canceled_cnt > 0:
        cancel_detail = f"│   └ 취소 {canceled_cnt}건 {canceled_amt} 제외 (gross {gross})"
        order_line    = f"│ 주문: {r['net_order_count']}건 (취소 {canceled_cnt} 제외)"
    else:
        cancel_detail = "│   └ 취소 없음"
        order_line    = f"│ 주문: {r['net_order_count']}건"

    # 200자 트림 가드
    # 우선순위: 취소상세(5) > 주문/구매자(4) > 인용률현재값/건수(3) > 매출이상/율변화(2) > 판단(1)
    # 율(2) vs 건수(3): 율이 더 보호됨 (건수는 트림 가능)
    parts = [
        (0, f"┌ BYOCORE 일간 리포트 ({date})"),
        (0, f"│ 매출(net): {net}"),
        (5, cancel_detail),
        (3, cit_line),
        (4, order_line),
        (4, f"│ 구매자: {r['unique_buyers']}명"),
        (2, f"│ {sales_anom}"),
    ]
    if cite_rate_anom:
        parts.append((2, f"│ {cite_rate_anom}"))   # 율 변화 — 건수보다 우선 보호
    parts.extend([
        (3, f"│ {cite_cnt_anom}"),                  # 건수 변화 — 트림 가능
        (1, f"└ {card_txt}"),
    ])
    return _assemble_with_limit(parts, KAKAO_TEXT_LIMIT)


def _median(values: list) -> Decimal:
    """정렬 후 중앙값(짝수면 두 중앙값 평균). Decimal 정밀 유지. (평균 아님 — 이벤트일 강건)"""
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def _collect_baseline_full(date: str, days: int = 7) -> list:
    """
    date 직전 days일의 일별 집계(활동일=order_count>0 만). READ-ONLY.
    반환 list[dict]: {date, net, order_count, net_order_count, canceled_count, cancel_rate, aov}
    anomaly·funnel proxy 공용 — 7일 수집 1회로 공유(신규 API 호출 없음).
    """
    d0 = datetime.date.fromisoformat(date)
    rows = []
    for i in range(1, days + 1):
        dd = (d0 - datetime.timedelta(days=i)).isoformat()
        rr = cafe24_sales.collect_sales(dd, dd)
        oc = rr.get("order_count", 0)
        if oc == 0:
            continue
        noc = rr.get("net_order_count", 0)
        cc  = rr.get("canceled_count", 0)
        net = Decimal(rr["net_payment_amount"])
        cancel_rate = Decimal(cc) / Decimal(oc)           # oc > 0 보장
        aov = net / Decimal(noc) if noc > 0 else Decimal("0")
        rows.append({
            "date": dd,
            "net": net,
            "order_count": oc,
            "net_order_count": noc,
            "canceled_count": cc,
            "cancel_rate": cancel_rate,
            "aov": aov,
        })
    return rows


def _baseline_nets(date: str, days: int = 7) -> list:
    """date 직전 days일의 net 매출(활동일). _collect_baseline_full 래퍼."""
    return [r["net"] for r in _collect_baseline_full(date, days)]


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


def _funnel_proxy(today_r: dict, baseline: list) -> dict:
    """
    퍼널 프록시 신호 산출. today_r = collect_sales 반환값. 신규 API 호출 없음.
    반환: {aov, cancel_rate, aov_flag("low"/"normal"/"high"), cancel_flag("high"/"normal"), has_baseline}
    임계치: AOV ±30%, 취소율 7일중앙값+5%p 이상 AND 오늘 취소율 10%+.
    """
    oc  = today_r.get("order_count", 0)
    noc = today_r.get("net_order_count", 0)
    cc  = today_r.get("canceled_count", 0)
    net = Decimal(today_r["net_payment_amount"])
    cancel_rate = Decimal(cc) / Decimal(oc) if oc > 0 else Decimal("0")
    aov = net / Decimal(noc) if noc > 0 else Decimal("0")

    result = {
        "aov": aov, "cancel_rate": cancel_rate,
        "aov_flag": "normal", "cancel_flag": "normal", "has_baseline": False,
    }
    if len(baseline) < 3:   # 최소 3일치 없으면 비교 의미 없음
        return result

    result["has_baseline"] = True
    b_aovs    = [r["aov"]         for r in baseline if r["aov"] > 0]
    b_cancels = [r["cancel_rate"] for r in baseline]

    if b_aovs:
        med_aov = _median(b_aovs)
        if med_aov > 0:
            dev = (aov - med_aov) / med_aov
            result["aov_flag"] = (
                "low"  if dev < Decimal("-0.3") else
                "high" if dev > Decimal("0.3")  else "normal"
            )

    if b_cancels:
        med_cancel = _median(b_cancels)
        result["cancel_flag"] = (
            "high" if cancel_rate > med_cancel + Decimal("0.05")
                      and cancel_rate > Decimal("0.1")
            else "normal"
        )
    return result


def _cite_rate_trend(geo_recent: list) -> str:
    """
    citation_rate 연속 추세 판정 (보수적). READ-ONLY.
    geo_recent: recent_unbiased_citations 결과(date 내림차순, citation_rate 포함).
    조건: 비-biased 3개 이상 + 전부 rate 값 있음 + 방향 일관 시만 판정.
    3개 미만이거나 rate None 포함 → "na" (데이터 축적 중, 단정 금지).
    반환: "down" | "up" | "stable" | "na"
    """
    if len(geo_recent) < 3:
        return "na"
    rates: list[float] = []
    for entry in geo_recent[:3]:       # 최신 3개만 사용
        rate = entry.get("citation_rate")
        if rate is None:
            return "na"                # 값 없으면 판정 불가 → 축적 중
        rates.append(float(rate))
    # rates[0]=최신, rates[1]=두번째, rates[2]=가장 오래된
    # 연속 하락: 최신 < 두번째 < 세번째 (오래된 쪽이 더 높음)
    if rates[0] < rates[1] < rates[2]:
        return "down"
    # 연속 상승: 최신 > 두번째 > 세번째
    if rates[0] > rates[1] > rates[2]:
        return "up"
    return "stable"


def _judgment_card(today_r: dict, geo_recent: list, proxy: dict, baseline: list) -> str:
    """
    GEO×매출×퍼널프록시×인용률추세 신호 교차 → 운영 가설 1줄.
    포지셔닝: 규칙 기반 신호 패턴 매칭. 어조 = "가능성/점검 권장" (단정 금지).
    graceful degradation:
      - GEO 비-biased 2개 미만 → GEO 건수 레이어 제외 (geo_trend="na")
      - 비-biased 3개 미만 or rate None → 인용률 추세 제외 (cite_trend="na")
    매칭 우선순위:
      취소율↑ → 인용률↓+매출↓ → GEO↑매출↓ → 인용률↓ → GEO↓매출↓ → GEO↑매출↑
      → AOV↓매출↓ → 매출↓ → 매출↑ → 정상
    """
    net = Decimal(today_r["net_payment_amount"])

    # 매출 추세 (7d median ±30%)
    sales_trend = "normal"
    if len(baseline) >= 3:
        med = _median([r["net"] for r in baseline])
        if med > 0:
            dev = (net - med) / med
            sales_trend = "down" if dev < Decimal("-0.3") else ("up" if dev > Decimal("0.3") else "normal")

    # 주문수 추세 (7d median ±30%)
    order_trend = "normal"
    if len(baseline) >= 3:
        med_o   = _median([Decimal(r["net_order_count"]) for r in baseline])
        today_o = Decimal(today_r.get("net_order_count", 0))
        if med_o > 0:
            dev_o = (today_o - med_o) / med_o
            order_trend = "low" if dev_o < Decimal("-0.3") else ("high" if dev_o > Decimal("0.3") else "normal")

    # GEO 건수 추세 (비-biased 2개+, graceful degradation)
    geo_trend = "na"
    if isinstance(geo_recent, list) and len(geo_recent) >= 2:
        gc, gp = geo_recent[0]["citation_count"], geo_recent[1]["citation_count"]
        geo_trend = "up" if gc > gp else ("down" if gc < gp else "neutral")

    # 인용률 추세 (비-biased 3개+, rate 값 있을 때만 — 보수적)
    cite_trend = _cite_rate_trend(geo_recent)

    cancel_flag = proxy.get("cancel_flag", "normal")
    aov_flag    = proxy.get("aov_flag",    "normal")

    # 첫 번째 매칭 반환 (가설 어조, 단정 금지)
    if cancel_flag == "high":
        return "취소율↑ 가능성 · CS/배송/재고 점검 권장"
    if cite_trend == "down" and sales_trend == "down":
        return "인용률↓+매출↓ · 콘텐츠·전환 동시 점검 권장"
    if geo_trend == "up" and sales_trend == "down" and order_trend == "low":
        return "AI유입 가능성 · 발견→구매 전환 구간 점검 권장"
    if cite_trend == "down":
        return "인용률 하락 추세 · GEO 콘텐츠 점검 권장"
    if geo_trend == "down" and sales_trend == "down":
        return "인지도 하락 가능성 · 콘텐츠 우선 점검 권장"
    if geo_trend == "up" and sales_trend == "up":
        return "GEO·매출 시너지 · 현 방향 유지 권장"
    if aov_flag == "low" and sales_trend == "down":
        return "객단가↓ 가능성 · 고가상품 노출 순서 확인 권장"
    if sales_trend == "down":
        return "매출↓ · 프로모션/광고 현황 확인 권장"
    if sales_trend == "up":
        return "매출↑ · 전환 요인 확인 권장"
    return "정상 범위"


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


# ===========================================================================
# 주간 리포트 (일간 헬퍼 _median/_money/geo recent_unbiased 재사용)
# ===========================================================================
def _collect_week(dates: list) -> list:
    """날짜 리스트의 일별 net/주문수/통화를 collect_sales로 수집(READ-ONLY)."""
    rows = []
    for d in dates:
        ds = d.isoformat()
        r = cafe24_sales.collect_sales(ds, ds)
        rows.append({
            "date": ds,
            "net": Decimal(r["net_payment_amount"]),
            "orders": r.get("order_count", 0),
            "currency": r.get("currency", "KRW"),
        })
    return rows


def _pct_vs(this_sum: Decimal, prev_sum: Decimal) -> str:
    """전주比 텍스트. 전주 합계 0 이하면 비교 불가."""
    if prev_sum <= 0:
        return "전주 데이터 없음"
    pct = (this_sum - prev_sum) / prev_sum * Decimal(100)
    sign = "+" if pct >= 0 else "-"
    return f"전주比 {sign}{abs(pct):.0f}%"


def _weekly_citation_text(recent) -> str:
    """주간 인용 줄. None=조회실패, <2=누적 중, 그 외 건수 변화(▲▼/유지)."""
    if recent is None:
        return "인용 조회 실패"
    if len(recent) < 2:
        return "인용 누적 중"
    cur, prev = recent[0]["citation_count"], recent[1]["citation_count"]
    if cur == prev:
        return f"인용 {cur}건 유지"
    return f"인용 {prev}건→{cur}건 {'▲' if cur > prev else '▼'}"


def _weekly_issues_text(active: list, recent) -> str:
    """
    Top 이슈 3 자동 추출: 주간 median±30% 이탈일 + 인용 변화. 점수순 상위 3, 없으면 특이사항 없음.
    (최고/최저일은 별도 줄에 표시하므로 이슈 중복 제외)
    """
    cands = []  # (score: Decimal, text)
    nets = [r["net"] for r in active]
    if nets:
        med = _median(nets)
        if med > 0:
            for r in active:
                dev = (r["net"] - med) / med * Decimal(100)
                if abs(dev) > Decimal(30):
                    sign = "+" if dev >= 0 else "-"
                    cands.append((abs(dev), f"{r['date'][5:]} {sign}{abs(dev):.0f}%"))
    if recent and len(recent) >= 2:
        cur, prev = recent[0]["citation_count"], recent[1]["citation_count"]
        if cur != prev:
            score = Decimal(abs(cur - prev)) / Decimal(max(prev, 1)) * Decimal(100)
            cands.append((score, f"인용{prev}→{cur}{'▲' if cur > prev else '▼'}"))
    if not cands:
        return "이슈: 특이사항 없음"
    cands.sort(key=lambda t: t[0], reverse=True)
    return "이슈: " + " · ".join(t[1] for t in cands[:3])


def _assemble_with_limit(parts: list, limit: int = KAKAO_TEXT_LIMIT) -> str:
    """
    parts: [(priority, line)]. priority 0=보호(항상 유지), 그 외는 숫자 클수록 먼저 잘림.
    총 길이 > limit 면 우선순위 낮은(숫자 큰) 줄부터 제거. 출력은 원래 순서 유지.
    """
    kept = list(parts)

    def total(ps):
        return len("\n".join(p[1] for p in ps))

    while total(kept) > limit:
        droppable = [i for i, (pr, _) in enumerate(kept) if pr > 0]
        if not droppable:
            break
        idx = max(droppable, key=lambda i: (kept[i][0], i))   # 최저 우선순위, 동률이면 뒤쪽
        kept.pop(idx)
    return "\n".join(p[1] for p in kept)


def build_weekly_report(today: str | None = None) -> str:
    """
    주간 리포트 생성. 기준일(today, 미지정=오늘 KST) 직전 7일(어제~7일전) vs 전주 7일. (READ-ONLY)
    구성: net 합계+전주比 / 일평균+최고·최저일 / 인용 변화 or 누적중 / Top이슈3 / "상세는 대시보드".
    전주比·인용·이슈는 각각 독립 try, 200자 초과 시 우선순위 낮은 줄부터 트림.
    """
    base = datetime.date.fromisoformat(today) if today else _today_kst()
    this_dates = [base - datetime.timedelta(days=i) for i in range(7, 0, -1)]    # base-7..base-1
    prev_dates = [base - datetime.timedelta(days=i) for i in range(14, 7, -1)]   # base-14..base-8

    this_rows = _collect_week(this_dates)   # CORE — 실패 시 리포트 불가(매출이 핵심)
    currency = next((r["currency"] for r in this_rows if r.get("currency")), "KRW")
    net_sum = sum((r["net"] for r in this_rows), Decimal("0"))
    active = [r for r in this_rows if r["orders"] > 0]
    avg = (net_sum / Decimal(len(this_rows))).quantize(Decimal("1")) if this_rows else Decimal("0")
    period = f"{this_dates[0].strftime('%m-%d')}~{this_dates[-1].strftime('%m-%d')}"

    # 전주比 (독립 try) — 전주 수집 실패해도 이번주 net 은 표시
    try:
        prev_rows = _collect_week(prev_dates)
        prev_sum = sum((r["net"] for r in prev_rows), Decimal("0"))
        prev_txt = _pct_vs(net_sum, prev_sum)
    except Exception:
        prev_txt = "전주比 실패"

    # 인용 (독립 try, 1회 조회 후 인용줄/이슈 공용)
    try:
        recent_cit = geo_citation.recent_unbiased_citations(2)
    except Exception:
        recent_cit = None
    cit_line = _weekly_citation_text(recent_cit)

    # Top 이슈 (독립 try)
    try:
        issues_line = _weekly_issues_text(active, recent_cit)
    except Exception:
        issues_line = "이슈: 산출 실패"

    if active:
        hi = max(active, key=lambda r: r["net"])
        lo = min(active, key=lambda r: r["net"])
        hilo = f"최고 {hi['date'][5:]} · 최저 {lo['date'][5:]}"
    else:
        hilo = "데이터 없음"

    parts = [
        (0, f"┌ BYOCORE 주간 ({period})"),
        (0, f"│ net {_money(net_sum, currency)} ({prev_txt})"),
        (4, f"│ 일평균 {_money(avg, currency)} · {hilo}"),
        (3, f"│ {cit_line}"),
        (2, f"│ {issues_line}"),
        (0, "└ 상세는 대시보드"),
    ]
    return _assemble_with_limit(parts, KAKAO_TEXT_LIMIT)


def send_weekly_report(today: str | None = None) -> dict:
    """주간 리포트 생성 후 카카오 '나에게 보내기'로 발송."""
    text = build_weekly_report(today)
    result = kakao_send.send_text(text, link_url=_report_link(), button_title="BYOCORE")
    return {"text": text, "send_result": result}


# ===========================================================================
# 월간 리포트 (일간/주간 헬퍼 재사용 — _collect_week / _median / _money / _weekly_issues_text)
# ===========================================================================

def _pct_month(this_sum: Decimal, prev_sum: Decimal) -> str:
    """전월比 텍스트. 전월 합계 0 이하면 비교 불가."""
    if prev_sum <= 0:
        return "전월 데이터 없음"
    pct = (this_sum - prev_sum) / prev_sum * Decimal(100)
    sign = "+" if pct >= 0 else "-"
    return f"전월比 {sign}{abs(pct):.0f}%"


def _monthly_citation_text(month_cit: list) -> str:
    """
    월간 인용 줄. month_cit = 당월 비-biased 측정 리스트(date 내림차순).
    0건 → "인용 누적 중", 1건 → "인용 N건 (1회)", >=2건 → 월초→월말 추세.
    (month_cit은 date 내림차순이므로 [0]=최신, [-1]=가장 오래된)
    """
    if not month_cit:
        return "인용 누적 중"
    if len(month_cit) < 2:
        cnt = month_cit[0]["citation_count"]
        return f"인용 {cnt}건 (1회)"
    first = month_cit[-1]["citation_count"]   # 가장 오래된 측정 (내림차순 → 마지막)
    last  = month_cit[0]["citation_count"]    # 최신 측정 (내림차순 → 첫 번째)
    if first == last:
        return f"인용 {last}건 유지"
    arrow = "▲" if last > first else "▼"
    return f"인용 {first}→{last}건 {arrow}"


def _kpi_line(net_sum: Decimal, currency: str) -> str:
    """
    KPI 달성률 한 줄. config.MONTHLY_SALES_TARGET 미설정이면 "KPI 목표 미설정".
    향후 .env 에 MONTHLY_SALES_TARGET=10000000 설정 시 달성률 자동 계산.
    READ-ONLY / 하드코딩 금지 — 목표값은 반드시 config 경유.
    """
    target_str = config.get("MONTHLY_SALES_TARGET")
    if not target_str:
        return "KPI 목표 미설정"
    try:
        target = Decimal(str(target_str).replace(",", ""))
        if target <= 0:
            return "KPI 목표 미설정"
        rate = (net_sum / target * Decimal(100)).quantize(Decimal("1"))
        return f"KPI {rate}% 달성 (목표 {_money(target, currency)})"
    except (InvalidOperation, ZeroDivisionError, ValueError):
        return "KPI 산출 실패"


def build_monthly_report(today: str | None = None) -> str:
    """
    월간 리포트 생성. 기준일(today, 미지정=오늘 KST) 직전 30일 vs 전월 30일. (READ-ONLY)
    구성: net 합계+전월比 / 일평균+최고·최저일 / KPI 달성률 / 인용 추세 / Top이슈3 / "상세는 대시보드".
    collect_sales 최대 60회(이번달30+전월30) — 각 호출은 cafe24_sales 내 backoff 자동 적용.
    전월比·KPI·인용·이슈는 각각 독립 try, 200자 초과 시 우선순위 낮은 줄부터 트림.
    우선순위(높을수록 먼저 제거): 일평균(5) > KPI(4) > 인용(3) > 이슈(2), 제목·net·푸터(0) 보호.
    """
    base = datetime.date.fromisoformat(today) if today else _today_kst()
    this_dates = [base - datetime.timedelta(days=i) for i in range(30, 0, -1)]   # base-30..base-1
    prev_dates = [base - datetime.timedelta(days=i) for i in range(60, 30, -1)]  # base-60..base-31

    # CORE — 이번달 30일 수집. 실패 시 월간 리포트 자체가 불가(매출이 핵심).
    this_rows = _collect_week(this_dates)
    currency = next((r["currency"] for r in this_rows if r.get("currency")), "KRW")
    net_sum  = sum((r["net"] for r in this_rows), Decimal("0"))
    active   = [r for r in this_rows if r["orders"] > 0]
    avg      = (net_sum / Decimal(len(this_rows))).quantize(Decimal("1")) if this_rows else Decimal("0")
    period   = f"{this_dates[0].strftime('%m-%d')}~{this_dates[-1].strftime('%m-%d')}"

    # 전월比 (독립 try) — 전월 수집 실패해도 이번달 net 은 표시
    try:
        prev_rows = _collect_week(prev_dates)
        prev_sum  = sum((r["net"] for r in prev_rows), Decimal("0"))
        prev_txt  = _pct_month(net_sum, prev_sum)
    except Exception:
        prev_txt = "전월比 실패"

    # 최고/최저 매출일 — 활동일(주문 1건+)만 후보
    if active:
        hi   = max(active, key=lambda r: r["net"])
        lo   = min(active, key=lambda r: r["net"])
        hilo = f"최고 {hi['date'][5:]} · 최저 {lo['date'][5:]}"
    else:
        hilo = "데이터 없음"

    # 인용 (독립 try) — 당월 범위에 포함된 비-biased 측정만
    month_cit: list = []
    try:
        this_date_strs = {d.isoformat() for d in this_dates}
        all_cit   = geo_citation.recent_unbiased_citations(30)   # 최근 30개 비-biased
        month_cit = [c for c in all_cit if c["date"] in this_date_strs]
        cit_line  = _monthly_citation_text(month_cit)
    except Exception:
        cit_line = "인용 조회 실패"

    # KPI (독립 try) — config.MONTHLY_SALES_TARGET 경유, 미설정이면 "목표 미설정"
    try:
        kpi_txt = _kpi_line(net_sum, currency)
    except Exception:
        kpi_txt = "KPI 산출 실패"

    # Top 이슈 (독립 try) — _weekly_issues_text 재사용, 당월 비-biased 2개+ 있으면 인용변화 포함
    try:
        issues_cit  = month_cit if len(month_cit) >= 2 else None
        issues_line = _weekly_issues_text(active, issues_cit)
    except Exception:
        issues_line = "이슈: 산출 실패"

    # 200자 트림 가드
    parts = [
        (0, f"┌ BYOCORE 월간 ({period})"),
        (0, f"│ net {_money(net_sum, currency)} ({prev_txt})"),
        (5, f"│ 일평균 {_money(avg, currency)} · {hilo}"),
        (4, f"│ {kpi_txt}"),
        (3, f"│ {cit_line}"),
        (2, f"│ {issues_line}"),
        (0, "└ 상세는 대시보드"),
    ]
    return _assemble_with_limit(parts, KAKAO_TEXT_LIMIT)


def send_monthly_report(today: str | None = None) -> dict:
    """월간 리포트 생성 후 카카오 '나에게 보내기'로 발송."""
    text = build_monthly_report(today)
    result = kakao_send.send_text(text, link_url=_report_link(), button_title="BYOCORE")
    return {"text": text, "send_result": result}


def _report_link() -> str:
    """카카오 메모 버튼 링크. 브랜드 도메인(REDIRECT_URI) 우선, 없으면 기본 링크."""
    return config.get("CAFE24_REDIRECT_URI") or kakao_send.DEFAULT_LINK


def send_daily_report(date: str) -> dict:
    """일간 리포트 생성 후 카카오 '나에게 보내기'로 발송. 결과 dict 반환."""
    text = build_daily_report(date)
    result = kakao_send.send_text(text, link_url=_report_link(), button_title="BYOCORE")
    return {"date": date, "text": text, "send_result": result}


def _preview_and_send(text: str) -> None:
    """미리보기 출력 + 길이 점검 + 카카오 '나에게 보내기' 발송(공용)."""
    print("----- 리포트 미리보기 -----")
    print(text)
    print(f"(본문 {len(text)}자 / 카카오 한도 {KAKAO_TEXT_LIMIT}자)")
    if len(text) > KAKAO_TEXT_LIMIT:
        print(f"[주의] 본문이 {KAKAO_TEXT_LIMIT}자를 초과.")
    print("----- 카카오 발송 -----")
    try:
        result = kakao_send.send_text(text, link_url=_report_link(), button_title="BYOCORE")
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"[오류] 카카오 발송 실패(HTTP {status}). "
              f"토큰 만료/동의 시 'python -m src.kakao_auth authorize' 로 재인가하세요.")
        raise
    print(f"발송 성공: {result}")


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows 콘솔 대비
    except Exception:
        pass

    args = sys.argv[1:]

    # 월간: python -m src.reporter monthly [YYYY-MM-DD]
    if args and args[0].lower() == "monthly":
        base = args[1] if len(args) > 1 else None
        try:
            text = build_monthly_report(base)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            print(f"[오류] Cafe24 수집 실패(HTTP {status}). "
                  f"토큰 만료 시 'python -m src.cafe24_auth authorize' 로 재인가하세요.")
            raise
        _preview_and_send(text)
        return

    # 주간: python -m src.reporter weekly [YYYY-MM-DD]
    if args and args[0].lower() == "weekly":
        base = args[1] if len(args) > 1 else None
        try:
            text = build_weekly_report(base)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            print(f"[오류] Cafe24 수집 실패(HTTP {status}). "
                  f"토큰 만료 시 'python -m src.cafe24_auth authorize' 로 재인가하세요.")
            raise
        _preview_and_send(text)
        return

    # 일간(기본): python -m src.reporter [YYYY-MM-DD]
    date = args[0] if args else _yesterday_kst()
    try:
        text = build_daily_report(date)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"[오류] Cafe24 수집 실패(HTTP {status}). "
              f"토큰 만료 시 'python -m src.cafe24_auth authorize' 로 재인가하세요.")
        raise
    _preview_and_send(text)


if __name__ == "__main__":
    _cli()
