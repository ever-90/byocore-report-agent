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


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------
def _esc(s) -> str:
    """HTML 특수문자 이스케이프 (XSS 방지)."""
    return _html_mod.escape(str(s))


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
