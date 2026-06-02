"""
dashboard.py — 운영 대시보드 HTML 생성 → docs/index.html (GitHub Pages)

[READ-ONLY / 보안]
- HTML 에 집계 표시 데이터만 포함. API 키·토큰·서비스계정·이메일 절대 미포함.
- <meta name="robots" content="noindex,nofollow"> 검색 노출 차단.

[데이터]
- collect_sales / geo_citation / reporter 판단 함수 재사용. 신규 API 엔드포인트 없음.
- 7일 베이스라인 1회 수집 → anomaly·funnel·차트 3곳 공용(신규 API 호출 0 추가).

[CLI]
  python -m src.dashboard             # 어제(KST) 기준 → docs/index.html
  python -m src.dashboard 2026-05-31  # 특정일
"""

import datetime
import html as _html_mod
import json
import os
import sys
from decimal import Decimal

from . import config  # noqa: F401 (하드코딩 방지 — 추후 config 경유 설정 확장용)
from .collectors import cafe24_sales, geo_citation
from .reporter import (
    KST,
    _cite_rate_trend,
    _collect_baseline_full,
    _funnel_proxy,
    _judgment_card,
    _median,
    _money,
)

# docs/index.html — GitHub Pages 기본 경로
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOCS_DIR  = os.path.join(_REPO_ROOT, "docs")
_OUT_FILE  = os.path.join(_DOCS_DIR, "index.html")

# 세일즈 에이전트가 생성한 scan_summary.json 기본 경로
# (환경변수 SCAN_SUMMARY_PATH 로 override 가능)
_DEFAULT_SCAN_SUMMARY = os.path.join(
    os.path.dirname(_REPO_ROOT),           # C:\Users\Administrator
    "byocore-sales-agent", "data", "scan_summary.json",
)

# 수퍼바이저 배치가 생성한 batch_result.json 기본 경로
# (환경변수 BATCH_RESULT_PATH 로 override 가능)
_DEFAULT_BATCH_RESULT = os.path.join(
    os.path.dirname(_REPO_ROOT),           # C:\Users\Administrator
    "byocore-supervisor-agent", "data", "batch_result.json",
)


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------
def _esc(s) -> str:
    """HTML 특수문자 이스케이프 (XSS 방지)."""
    return _html_mod.escape(str(s))


# ---------------------------------------------------------------------------
# 세일즈 위험 섹션 (READ-ONLY · 독립 try)
# ---------------------------------------------------------------------------
def _load_sales_summary() -> dict | None:
    """
    scan_summary.json 읽기. 없거나 파싱 실패 시 None (graceful degradation).
    ★ 자사가(가격) 필드는 이 함수에서도, 렌더링에서도 절대 참조하지 않는다.
    """
    path = (os.getenv("SCAN_SUMMARY_PATH") or "").strip() or _DEFAULT_SCAN_SUMMARY
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# 원인태그 → (배지 CSS 클래스, 표시 문자열)
_BADGE_CFG = {
    "콘텐츠없음": "badge-orange",
    "질의미등록": "badge-yellow",
    "가격불균형": "badge-red",
    "니치":       "badge-gray",
    "질의없음":   "badge-muted",
}
# measurement_type → 짧은 라벨
_MTYPE_LABEL = {"전용": "[전]", "희석": "[희]", "없음": "[없]"}
# 위험_등급 → CSS 클래스 (기존 --warn 팔레트 재사용)
_TIER_CLS = {
    "위험": "stier-red",
    "주의": "stier-orange",
    "보통": "stier-gray",
    "양호": "stier-blue",
}


def _build_sales_html(summary: dict | None) -> str:
    """
    세일즈 위험 Top 섹션 HTML 반환.
    summary 가 None 이면 '세일즈 데이터 없음' graceful 표시.
    ★ 자사가(가격) 절대 미렌더링 — top_risk 항목에서 읽지 않음.
    """
    if summary is None:
        return (
            '<div class="section-wrap">'
            '<div class="section-title">세일즈 위험 Top</div>'
            '<div class="sales-meta" style="color:var(--muted);font-size:.78rem">'
            '세일즈 데이터 없음 — scan_summary.json 미생성'
            '</div></div>'
        )

    # 기준일: updated_at 앞 10자(YYYY-MM-DD)만 사용
    updated  = _esc(str(summary.get("updated_at", ""))[:10])
    total    = int(summary.get("total_scanned", 0))
    risk_sum = summary.get("risk_summary", {})
    top_risk = summary.get("top_risk", [])

    # 원인 배지
    badges = []
    for tag, cls in _BADGE_CFG.items():
        cnt = int(risk_sum.get(tag, 0))
        if cnt > 0:
            badges.append(f'<span class="badge {cls}">{_esc(tag)} {cnt}</span>')
    badges_html = "\n".join(badges)

    # top_risk 행 (★ 자사가 필드 참조 없음)
    rows = []
    for r in top_risk:
        rank       = int(r.get("rank", 0))
        name_full  = str(r.get("제품명", ""))
        # 제품명이 길면 앞 22자 + "…" 으로 잘라서 폰 화면에 맞춤
        name_short = (name_full[:22] + "…") if len(name_full) > 22 else name_full
        risk_score = r.get("위험도")
        geo_rate   = r.get("geo인용률")
        mtype      = str(r.get("measurement_type", ""))
        cause      = str(r.get("원인태그", ""))
        tier       = str(r.get("위험_등급", ""))

        score_s = f"{risk_score:.0f}" if risk_score is not None else "—"
        geo_s   = f"{geo_rate:.1f}%" if geo_rate is not None else "없음"
        mt_s    = _MTYPE_LABEL.get(mtype, _esc(mtype))
        tier_c  = _TIER_CLS.get(tier, "stier-gray")

        rows.append(
            f'<div class="sales-row">'
            f'<div class="sales-rank">#{rank}</div>'
            f'<div class="sales-info">'
            f'<div class="sales-name">{_esc(name_short)}</div>'
            f'<div class="sales-detail">'
            f'위험도 {score_s} · GEO {_esc(geo_s)} · {mt_s} {_esc(cause)}'
            f'</div></div>'
            f'<div class="sales-tier {tier_c}">{_esc(tier)}</div>'
            f'</div>'
        )
    rows_html = "\n".join(rows)

    return (
        f'<div class="section-wrap">'
        f'<div class="section-title">세일즈 위험 Top</div>'
        f'<div class="sales-meta">스캔 {total}개 · 기준 {updated}</div>'
        f'<div class="risk-badges">{badges_html}</div>'
        f'<div class="sales-list">{rows_html}</div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# 이번 주 처방 섹션 (수퍼바이저 batch_result.json · READ-ONLY · 독립 try)
# ---------------------------------------------------------------------------
def _load_batch_result() -> dict | None:
    """
    batch_result.json 읽기. 없거나 파싱 실패 시 None (graceful degradation).
    ★ 진단.가격위치 등 가격 필드는 이 함수에서도 렌더링에서도 절대 참조하지 않는다.
    """
    path = (os.getenv("BATCH_RESULT_PATH") or "").strip() or _DEFAULT_BATCH_RESULT
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# 처리 상태 → (배지 CSS 클래스, 표시 라벨)
_PSTAT_CFG = {
    "처방": ("pstat-ok", "처방✓"),
    "보류": ("pstat-hold", "보류"),
    "실패": ("pstat-fail", "실패"),
}


def _build_prescription_html(batch: dict | None) -> str:
    """
    이번 주 처방 섹션 HTML. batch=None 이면 빈 문자열(섹션 숨김 — graceful).
    ★ 가격 미렌더링: 진단.가격위치 를 읽지 않는다. 위험도·GEO인용률·처방상태만.
    제품별: 위험도 + GEO인용률 + 처방상태 배지(처방✓/보류/실패).
      · 처방 → 콘텐츠안 제목(요약, 전문 X)
      · 보류 → own_facts 사실 필요
      · 실패 → 디자이너 호출 실패
    """
    if batch is None:
        return ""   # 섹션 숨김 (배치 미실행/파일 없음)

    summary = batch.get("_배치요약", {}) if isinstance(batch.get("_배치요약"), dict) else {}
    results = batch.get("결과", [])
    if not isinstance(results, list):
        results = []

    n_total = summary.get("위험제품_수", len(results))
    n_presc = summary.get("처방생성_수", 0)
    n_hold  = summary.get("처방보류_수", 0)
    n_fail  = summary.get("처방실패_수", 0)
    meta = f"위험 {n_total}건 · 처방 {n_presc} / 보류 {n_hold} / 실패 {n_fail}"

    rows = []
    for r in results:
        if not isinstance(r, dict):
            continue
        rank = r.get("rank")
        name_full = str(r.get("제품명", ""))
        name_short = (name_full[:24] + "…") if len(name_full) > 24 else name_full

        diag = r.get("진단", {}) if isinstance(r.get("진단"), dict) else {}
        risk = str(diag.get("위험도", ""))          # ★ 가격위치는 읽지 않음
        geo  = str(diag.get("GEO인용률", ""))

        status = str(r.get("처리", "")).strip()
        cls, label = _PSTAT_CFG.get(status, ("pstat-hold", status or "?"))

        # 상태별 상세 한 줄
        presc = r.get("처방", {}) if isinstance(r.get("처방"), dict) else {}
        if status == "처방":
            detail = str(presc.get("콘텐츠안_요약", ""))   # [초안] 제목만
        elif status == "실패":
            detail = "디자이너 호출 실패"
        else:  # 보류 등
            detail = "own_facts 사실 데이터 필요"

        rank_disp = f"#{rank}" if rank is not None else "·"
        meta_line = " · ".join(p for p in (f"위험 {risk}" if risk else "",
                                           f"GEO {geo}" if geo else "") if p)
        rows.append(
            f'<div class="rx-row">'
            f'<div class="rx-rank">{_esc(rank_disp)}</div>'
            f'<div class="rx-info">'
            f'<div class="rx-name">{_esc(name_short)}</div>'
            f'<div class="rx-meta">{_esc(meta_line)}</div>'
            f'<div class="rx-detail">{_esc(detail)}</div>'
            f'</div>'
            f'<div class="rx-stat {cls}">{_esc(label)}</div>'
            f'</div>'
        )
    rows_html = "\n".join(rows) if rows else '<div class="sales-meta">처방 대상 없음</div>'

    return (
        f'<div class="section-wrap">'
        f'<div class="section-title">이번 주 처방</div>'
        f'<div class="sales-meta">{_esc(meta)}</div>'
        f'<div class="rx-list">{rows_html}</div>'
        f'</div>'
    )


def _yesterday_kst() -> str:
    now_kst = datetime.datetime.now(KST)
    return (now_kst.date() - datetime.timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# HTML 빌더
# ---------------------------------------------------------------------------
def build_dashboard_html(date: str) -> str:
    """
    date(YYYY-MM-DD) 기준 운영 대시보드 self-contained HTML 생성. READ-ONLY.
    Chart.js CDN + 데이터 인라인 → 파일을 열면 즉시 표시.
    민감정보(키/토큰/계정) 절대 미포함.
    """
    # ── CORE: 오늘 매출 수집 ──────────────────────────────────────────────
    sales     = cafe24_sales.collect_sales(date, date)
    currency  = sales["currency"]
    net_disp  = _money(sales["net_payment_amount"], currency)
    yest_net  = Decimal(sales["net_payment_amount"])
    order_cnt = sales.get("net_order_count", 0)
    buyers    = sales.get("unique_buyers", 0)
    canceled_cnt = sales.get("canceled_count", 0)

    # ── 7일 베이스라인 1회 (anomaly·funnel·차트 공용) ─────────────────────
    baseline: list = []
    try:
        baseline = _collect_baseline_full(date, days=7)
    except Exception:
        pass

    # ── GEO 인용률 ───────────────────────────────────────────────────────
    geo_rate_html = "측정 대기"   # 이미 안전한 HTML 문자열로 직접 구성
    geo_sub_html  = "Top-100 기준"
    try:
        geo = geo_citation.collect_citation_rate()
        if geo and geo.get("citation_rate") is not None:
            r = float(geo["citation_rate"])
            geo_rate_html = f"{r:.1f}%"
            if geo.get("is_biased"):
                geo_rate_html += " <small style='color:var(--muted)'>(추정)</small>"
    except Exception:
        geo_rate_html = "조회 실패"

    # ── GEO recent (인용 이상알림·판단카드·인용률추세 공용) ───────────────
    # n=3: _cite_rate_trend 가 3개 필요 (부족 시 "na" graceful degrade)
    geo_recent: list = []
    try:
        geo_recent = geo_citation.recent_unbiased_citations(3)
    except Exception:
        pass

    # 인용 건수 변화 sub 텍스트 ("건수:" 라벨로 율과 구분)
    cite_sub = ""
    if len(geo_recent) >= 2:
        cur, prev = geo_recent[0]["citation_count"], geo_recent[1]["citation_count"]
        if cur != prev:
            cite_sub = f"건수 {prev}→{cur}건 {'▲' if cur > prev else '▼'}"
    if cite_sub:
        geo_sub_html = _esc(cite_sub)

    # ── 퍼널프록시 + 판단 카드 ───────────────────────────────────────────
    card_txt = "진단: 산출 실패"
    try:
        proxy    = _funnel_proxy(sales, baseline)
        card_txt = _judgment_card(sales, geo_recent, proxy, baseline)
    except Exception:
        pass

    # ── 이상알림 배너 (warn=⚠️ 빨강, info=▲ 파랑) ──────────────────────
    # ⚠️ warn: 나쁜 신호만 (매출 급락, 인용률↓ 추세, 인용 건수↓)
    # ▲ info: 좋거나 중립 신호 (매출 급등, 인용 건수↑)
    warn_alerts: list[str] = []
    info_alerts: list[str] = []

    try:
        nets = [r["net"] for r in baseline]
        if len(nets) >= 7:
            med = _median(nets)
            if med > 0:
                dev = (yest_net - med) / med * Decimal(100)
                if dev < Decimal(-30):
                    warn_alerts.append(f"매출 7일중앙값比 {int(dev)}% 이탈 감지")
                elif dev > Decimal(30):
                    info_alerts.append(f"매출 7일중앙값比 +{int(dev)}% 상승")
    except Exception:
        pass

    # 인용 건수 변화 ("건수:" 라벨로 율과 명확히 구분)
    try:
        if len(geo_recent) >= 2:
            cur, prev = geo_recent[0]["citation_count"], geo_recent[1]["citation_count"]
            if cur > prev:
                info_alerts.append(f"인용 건수: {prev}→{cur}건 ▲")
            elif cur < prev:
                warn_alerts.append(f"인용 건수: {prev}→{cur}건 ▼")
    except Exception:
        pass

    # 인용률 변화 — 실제 값 표시 ("율:" 라벨). 연속 하락 확인 시 맥락 추가.
    try:
        if len(geo_recent) >= 2:
            cur_rate  = geo_recent[0].get("citation_rate")
            prev_rate = geo_recent[1].get("citation_rate")
            if cur_rate is not None and prev_rate is not None and cur_rate != prev_rate:
                if cur_rate < prev_rate:
                    suffix = " (연속 하락)" if _cite_rate_trend(geo_recent) == "down" else ""
                    warn_alerts.append(f"인용률: {prev_rate:.1f}→{cur_rate:.1f}% ▼{suffix}")
                else:
                    info_alerts.append(f"인용률: {prev_rate:.1f}→{cur_rate:.1f}% ▲")
    except Exception:
        pass

    all_items: list[str] = []
    for a in warn_alerts:
        all_items.append(f'<div class="alert alert-warn">⚠️ {_esc(a)}</div>')
    for a in info_alerts:
        # info는 텍스트 안에 이미 방향 표시(▲) 포함 — 별도 prefix 없음. 파란 테두리로 구분.
        all_items.append(f'<div class="alert alert-info">{_esc(a)}</div>')
    alerts_html = ""
    if all_items:
        alerts_html = '<div class="alert-section">\n' + "\n".join(all_items) + '\n</div>'

    # ── 세일즈 위험 섹션 (독립 try — 실패해도 기존 대시보드 안 깨짐) ──────
    sales_section_html = ""
    try:
        sales_summary = _load_sales_summary()
        sales_section_html = _build_sales_html(sales_summary)
    except Exception:
        sales_section_html = (
            '<div class="section-wrap">'
            '<div class="section-title">세일즈 위험 Top</div>'
            '<div class="sales-meta" style="color:var(--warn)">세일즈 섹션 오류</div>'
            '</div>'
        )

    # ── 이번 주 처방 섹션 (독립 try — batch_result 없으면 섹션 숨김) ───────
    prescription_section_html = ""
    try:
        batch = _load_batch_result()
        prescription_section_html = _build_prescription_html(batch)
    except Exception:
        prescription_section_html = (
            '<div class="section-wrap">'
            '<div class="section-title">이번 주 처방</div>'
            '<div class="sales-meta" style="color:var(--warn)">처방 섹션 오류</div>'
            '</div>'
        )

    # ── 7일 차트 데이터 (date-6 ~ date, 빈 날짜는 0) ─────────────────────
    d0           = datetime.date.fromisoformat(date)
    chart_window = [d0 - datetime.timedelta(days=i) for i in range(6, -1, -1)]
    bl_by_date   = {r["date"]: r["net"] for r in baseline}
    chart_labels  = [d.strftime("%m/%d") for d in chart_window]
    chart_data: list[int] = []
    for d in chart_window:
        ds = d.isoformat()
        if ds == date:
            chart_data.append(int(yest_net))
        else:
            chart_data.append(int(bl_by_date[ds]) if ds in bl_by_date else 0)

    labels_json = json.dumps(chart_labels, ensure_ascii=False)
    data_json   = json.dumps(chart_data)
    updated_kst = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    # ── HTML 조립 ─────────────────────────────────────────────────────────
    # CSS·JS 내부의 { } 는 {{ }} 로 이스케이프 (f-string 규칙)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="robots" content="noindex,nofollow">
  <title>BYOCORE 운영 대시보드</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    :root{{
      --bg:#0d1117;--card:#161b22;--border:#30363d;
      --accent:#58a6ff;--text:#c9d1d9;--muted:#8b949e;
      --warn-bg:#1a0e0e;--warn:#f85149
    }}
    body{{
      background:var(--bg);color:var(--text);
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      padding:16px;max-width:480px;margin:0 auto
    }}
    h1{{font-size:1.05rem;color:var(--accent);margin-bottom:2px;letter-spacing:.01em}}
    .subtitle{{color:var(--muted);font-size:.78rem;margin-bottom:16px}}
    .cards{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px}}
    .card{{
      background:var(--card);border:1px solid var(--border);
      border-radius:10px;padding:13px
    }}
    .card-label{{
      font-size:.68rem;color:var(--muted);margin-bottom:5px;
      text-transform:uppercase;letter-spacing:.04em
    }}
    .card-value{{font-size:1.35rem;font-weight:700;line-height:1.2;word-break:break-all}}
    .card-sub{{font-size:.71rem;color:var(--muted);margin-top:5px}}
    .card.wide{{grid-column:1/-1}}
    .card.wide .card-value{{font-size:.92rem;font-weight:500;color:var(--accent)}}
    .chart-wrap{{
      background:var(--card);border:1px solid var(--border);
      border-radius:10px;padding:14px;margin-bottom:16px
    }}
    .chart-label{{font-size:.68rem;color:var(--muted);margin-bottom:10px;
      text-transform:uppercase;letter-spacing:.04em}}
    .alert-section{{margin-bottom:16px}}
    .alert{{border-radius:6px;padding:9px 12px;margin-bottom:7px;font-size:.82rem}}
    .alert-warn{{background:var(--warn-bg);border-left:3px solid var(--warn)}}
    .alert-info{{background:#0d1f30;border-left:3px solid var(--accent)}}
    footer{{
      text-align:center;color:var(--muted);font-size:.7rem;
      border-top:1px solid var(--border);padding-top:12px
    }}
    /* ── 세일즈 위험 섹션 ── */
    .section-wrap{{
      background:var(--card);border:1px solid var(--border);
      border-radius:10px;padding:14px;margin-bottom:16px
    }}
    .section-title{{
      font-size:.68rem;color:var(--muted);
      text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px
    }}
    .sales-meta{{font-size:.72rem;color:var(--muted);margin-bottom:10px}}
    .risk-badges{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}}
    .badge{{
      font-size:.7rem;font-weight:600;padding:3px 8px;
      border-radius:12px;white-space:nowrap
    }}
    .badge-orange{{background:rgba(240,165,0,.18);color:#f0a500}}
    .badge-yellow{{background:rgba(212,192,0,.18);color:#cbb800}}
    .badge-red{{background:rgba(248,81,73,.18);color:#f85149}}
    .badge-gray{{background:rgba(139,148,158,.18);color:#8b949e}}
    .badge-muted{{background:rgba(139,148,158,.12);color:#8b949e}}
    .sales-list{{}}
    .sales-row{{
      display:flex;align-items:flex-start;gap:8px;
      padding:9px 0;border-bottom:1px solid var(--border)
    }}
    .sales-row:last-child{{border-bottom:none}}
    .sales-rank{{font-size:.72rem;color:var(--muted);min-width:24px;padding-top:2px}}
    .sales-info{{flex:1;min-width:0}}
    .sales-name{{
      font-size:.84rem;font-weight:500;
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:3px
    }}
    .sales-detail{{font-size:.7rem;color:var(--muted)}}
    .sales-tier{{
      font-size:.65rem;font-weight:700;padding:2px 6px;
      border-radius:5px;white-space:nowrap;align-self:center
    }}
    .stier-red{{background:rgba(248,81,73,.2);color:#f85149}}
    .stier-orange{{background:rgba(240,165,0,.2);color:#f0a500}}
    .stier-gray{{background:rgba(139,148,158,.2);color:#8b949e}}
    .stier-blue{{background:rgba(88,166,255,.2);color:#58a6ff}}
    /* ── 이번 주 처방 섹션 ── */
    .rx-row{{
      display:flex;align-items:flex-start;gap:8px;
      padding:9px 0;border-bottom:1px solid var(--border)
    }}
    .rx-row:last-child{{border-bottom:none}}
    .rx-rank{{font-size:.72rem;color:var(--muted);min-width:24px;padding-top:2px}}
    .rx-info{{flex:1;min-width:0}}
    .rx-name{{
      font-size:.84rem;font-weight:500;
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:3px
    }}
    .rx-meta{{font-size:.7rem;color:var(--muted);margin-bottom:2px}}
    .rx-detail{{
      font-size:.72rem;color:var(--text);
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis
    }}
    .rx-stat{{
      font-size:.65rem;font-weight:700;padding:2px 7px;
      border-radius:5px;white-space:nowrap;align-self:center
    }}
    .pstat-ok{{background:rgba(63,185,80,.2);color:#3fb950}}
    .pstat-hold{{background:rgba(139,148,158,.2);color:#8b949e}}
    .pstat-fail{{background:rgba(248,81,73,.2);color:#f85149}}
  </style>
</head>
<body>

<h1>BYOCORE 운영 대시보드</h1>
<p class="subtitle">{_esc(date)} 기준</p>

<div class="cards">
  <div class="card">
    <div class="card-label">매출 (net)</div>
    <div class="card-value">{_esc(net_disp)}</div>
    <div class="card-sub">취소 {canceled_cnt}건 제외</div>
  </div>
  <div class="card">
    <div class="card-label">핵심 인용률</div>
    <div class="card-value">{geo_rate_html}</div>
    <div class="card-sub">{geo_sub_html}</div>
  </div>
  <div class="card">
    <div class="card-label">주문 / 구매자</div>
    <div class="card-value">{order_cnt}건</div>
    <div class="card-sub">{buyers}명 구매</div>
  </div>
  <div class="card wide">
    <div class="card-label">오늘 진단</div>
    <div class="card-value">{_esc(card_txt)}</div>
  </div>
</div>

<div class="chart-wrap">
  <div class="chart-label">최근 7일 net 매출</div>
  <canvas id="salesChart" height="150"></canvas>
</div>

{alerts_html}

{sales_section_html}

{prescription_section_html}

<footer>마지막 갱신: {_esc(updated_kst)}</footer>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<script>
(function() {{
  var ctx = document.getElementById('salesChart').getContext('2d');
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: {labels_json},
      datasets: [{{
        data: {data_json},
        backgroundColor: 'rgba(88,166,255,0.45)',
        borderColor:     'rgba(88,166,255,0.9)',
        borderRadius: 5,
        borderWidth: 1
      }}]
    }},
    options: {{
      responsive: true,
      plugins: {{
        legend: {{display: false}},
        tooltip: {{
          callbacks: {{
            label: function(c) {{ return '₩' + c.parsed.y.toLocaleString(); }}
          }}
        }}
      }},
      scales: {{
        x: {{
          grid:  {{color: 'rgba(255,255,255,0.04)'}},
          ticks: {{color: '#8b949e', font: {{size: 11}}}}
        }},
        y: {{
          beginAtZero: true,
          grid:  {{color: 'rgba(255,255,255,0.04)'}},
          ticks: {{
            color: '#8b949e',
            font:  {{size: 10}},
            callback: function(v) {{
              if (v === 0) return '0';
              return v >= 10000 ? '₩' + Math.round(v / 10000) + '만' : '₩' + v;
            }}
          }}
        }}
      }}
    }}
  }});
}})();
</script>

</body>
</html>"""


# ---------------------------------------------------------------------------
# 저장 + CLI
# ---------------------------------------------------------------------------
def save_dashboard(date: str | None = None) -> str:
    """대시보드 HTML 생성 후 docs/index.html 저장. 저장 경로 반환."""
    target_date = date or _yesterday_kst()
    html_str = build_dashboard_html(target_date)
    os.makedirs(_DOCS_DIR, exist_ok=True)
    with open(_OUT_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(html_str)
    return _OUT_FILE


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    date = sys.argv[1] if len(sys.argv) > 1 else _yesterday_kst()
    try:
        out  = save_dashboard(date)
        size = os.path.getsize(out)
        print(f"대시보드 생성 완료: {out}")
        print(f"  날짜: {date}  |  파일 크기: {size:,} bytes")
    except Exception as e:
        print(f"[오류] 대시보드 생성 실패: {type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    _cli()
