"""
market_trend.py — 경쟁동향(Market Insight repo 로컬 clone) 읽기. (2단계 구현)

[목표] 최신 경쟁사 동향 요약을 dict 로 반환(주간 리포트 Top이슈용). 미연동 시 None.

[RECON 결과 — 2026-05-30 (실측, 추측 아님)]
- GitHub repo ever-90/market-insight-byocore (private, main 단일 브랜치)의 '커밋된' 트리:
    .gitignore · README.md · _phase1a.ipynb · _phase1bcd.ipynb · _three_tier_classifier.py
  → 정형 '주간 경쟁동향' 산출물(reports/*.json 등)이 존재하지 않음.
  → 데이터/영업 자료는 .gitignore 로 보호되어 어떤 clone 에도 포함되지 않음
    (README: "API 키·영업 자료 = .gitignore 보호, 절대 미포함").
- 로컬 배포 사본(market-insight-deploy)에도 'reports/' 폴더나 *_trend.json 형태의
  경쟁동향 산출물은 없다(분석 파이프라인 중간 산출 JSON 들만 존재).
- 즉, 두 repo 사이에 '관측 가능한' 경쟁동향 데이터 계약이 아직 없다.
- 분석 대상 5개 경쟁 브랜드(README): 락토핏 · 듀오락 · 바이오코어(자사) · 덴프스 · 드시모네.

[설계 — 명시적 '연동 계약(handoff)' (사용자 승인: Option 1)]
- 관측된 스키마를 위조하지 않는다. 본 모듈이 두 repo 간 연동을 위해 '신설·문서화'하는
  핸드오프 JSON 계약을 정의하고, 그 파일이 있으면 읽는다(없으면 None=미연동).
- market-insight 측(동일 소유자 ever-90)이 아래 파일을 생성하면 자동 연동된다.

  위치(config.MARKET_REPO_PATH 기준, 아래 순서로 탐색):
    1) <repo>/reports/latest_trend.json                (정규 '최신' 핸드오프 — 우선)
    2) <repo>/reports/YYYY-MM-DD_trend.json            (날짜 스냅샷 → 파일명 날짜 최댓값 채택)
  스키마(schema_version=1):
    {
      "schema_version": 1,
      "as_of": "YYYY-MM-DD",                # 동향 기준일 (권장)
      "generated_at": "ISO8601",            # 생성시각 (선택)
      "competitors": [
        {"name": "락토핏",                  # 경쟁사/브랜드명 (필수)
         "change": "검색량 +12% ...",       # 주요 변화 한 줄 (선택)
         "signal": "up|down|flat",          # 방향 (선택, 기본 flat)
         "confidence": "H|M|L",             # 신뢰등급 (선택, 기본 L=보수적)
         "sources": ["url", ...]}           # 근거 (선택)
      ],
      "top_issues": ["...", ...]            # 경쟁 Top 이슈 (선택)
    }

[READ-ONLY]
- 로컬 clone 산출물 '파일 읽기'만. 원격 commit/push, 파일 쓰기 금지.
- 경로는 config.MARKET_REPO_PATH (.env) 경유. 하드코딩 금지
  (서브폴더/파일명/스키마명은 연동 계약 상수 — geo_citation 의 SHEET_TAB 과 동일 성격).

[함수] collect_market_trend(base_path=None) -> dict | None   (READ-ONLY)
  반환 dict:
    as_of, generated_at, schema_version, source_file,
    competitors:[{name, change, signal(up|down|flat), confidence(H|M|L), sources:[...]}],
    competitor_count, top_issues:[...], warnings:[...]
  None 반환:
    경로 미설정 / clone 경로 없음 / reports 폴더 없음 / 핸드오프 파일 없음 / 전부 손상
    → 리포트가 "경쟁동향 미연동"으로 처리 가능.

[단독 실행]
  python -m src.collectors.market_trend             # config(MARKET_REPO_PATH)에서 최신 동향
  python -m src.collectors.market_trend <repo경로>   # 경로 직접 지정(테스트/오버라이드)
"""

import datetime
import json
import re
import sys
from pathlib import Path

from .. import config

# --- 연동 계약 상수(스키마/네이밍/서브폴더 — 비밀 아님) ---
# 배포 사본은 src/reports/, clean git clone 은 reports/ 를 쓸 수 있어 둘 다 탐색.
REPORT_SUBDIRS = ("reports", "src/reports")
CANONICAL_NAME = "latest_trend.json"                         # 정규 '최신' 핸드오프 파일
DATED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_trend\.json$")  # 날짜 스냅샷(파일명 날짜 최댓값 채택)

VALID_SIGNALS = ("up", "down", "flat")
VALID_CONFIDENCE = ("H", "M", "L")
DEFAULT_CONFIDENCE = "L"   # 계약: confidence 누락 시 보수적 기본값
DEFAULT_SIGNAL = "flat"    # 방향 미지정 → 중립
UNKNOWN_NAME = "(미상)"


# ---------------------------------------------------------------------------
# 경로 해석 (config 경유 — 하드코딩 금지)
# ---------------------------------------------------------------------------
def _resolve_base(base_path=None) -> Path | None:
    """
    경쟁동향 repo 로컬 clone 루트. base_path(명시 오버라이드) 우선,
    없으면 config.MARKET_REPO_PATH(.env). 미설정/빈값이면 None.
    호출 시점에 config.get 으로 읽어 .env 변경을 즉시 반영(테스트 용이).
    """
    raw = base_path if base_path is not None else config.get("MARKET_REPO_PATH")
    if raw is None or str(raw).strip() == "":
        return None
    return Path(str(raw).strip()).expanduser()


def _existing_report_dirs(base: Path):
    """base 하위 실재하는 reports 후보 폴더 목록(reports, src/reports 순)."""
    return [d for d in (base / sub for sub in REPORT_SUBDIRS) if d.is_dir()]


# ---------------------------------------------------------------------------
# 핸드오프 파일 선택 (정규 latest 우선 → 날짜 스냅샷 최신)
# ---------------------------------------------------------------------------
def _dated_candidates(report_dirs):
    """report_dirs 의 'YYYY-MM-DD_trend.json' 만 (date, path), 날짜 내림차순 반환. READ-ONLY."""
    items = []
    for reports_dir in report_dirs:
        try:
            entries = list(reports_dir.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file():
                continue
            m = DATED_RE.match(entry.name)
            if not m:
                continue
            try:
                d = datetime.date.fromisoformat(m.group(1))
            except ValueError:
                continue   # 파일명 날짜가 실제 날짜가 아님 → 배제
            items.append((d, entry))
    items.sort(key=lambda t: (t[0], t[1].name), reverse=True)
    return items


def _ordered_handoff_files(report_dirs):
    """후보 핸드오프 파일을 우선순위 순(정규 latest → 날짜 최신)으로 반환."""
    ordered: list[Path] = []
    for reports_dir in report_dirs:
        canonical = reports_dir / CANONICAL_NAME
        if canonical.is_file():
            ordered.append(canonical)
    ordered.extend(p for _d, p in _dated_candidates(report_dirs))
    return ordered


# ---------------------------------------------------------------------------
# 파일 읽기 / 파싱 유틸 (READ-ONLY)
# ---------------------------------------------------------------------------
def _load_json(path: Path):
    """핸드오프 파일 읽어 dict 반환. 인코딩/JSON/형식 오류 시 None(손상 처리)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean_str(v) -> str:
    return str(v).strip() if v is not None else ""


def _clean_sources(v) -> list:
    """sources → 공백 제거된 비어있지 않은 문자열 리스트. 리스트 아님/이상값은 무시."""
    if not isinstance(v, list):
        return []
    return [s for s in (_clean_str(x) for x in v) if s]


# ---------------------------------------------------------------------------
# 엔트리 정규화 (계약 → 통일 형태 + 교차검증)
# ---------------------------------------------------------------------------
def _normalize_entry(e):
    """competitors 항목 → 통일 dict + 점검주석. 비-dict 면 (None, [경고])."""
    warns: list[str] = []
    if not isinstance(e, dict):
        return None, [f"competitors 항목이 객체가 아님(무시): {e!r}"]

    name = _clean_str(e.get("name")) or UNKNOWN_NAME
    if name == UNKNOWN_NAME:
        warns.append("경쟁사 name 누락 → '(미상)'")

    raw_sig = _clean_str(e.get("signal")).lower()
    signal = raw_sig if raw_sig in VALID_SIGNALS else DEFAULT_SIGNAL
    if raw_sig not in VALID_SIGNALS:
        warns.append(f"[{name}] signal 미지정/이상 → '{signal}'")

    raw_conf = _clean_str(e.get("confidence")).upper()
    confidence = raw_conf if raw_conf in VALID_CONFIDENCE else DEFAULT_CONFIDENCE
    if raw_conf not in VALID_CONFIDENCE:
        warns.append(f"[{name}] confidence 미지정/이상 → 기본 '{confidence}'(보수적)")

    return {
        "name": name,
        "change": _clean_str(e.get("change")),
        "signal": signal,
        "confidence": confidence,
        "sources": _clean_sources(e.get("sources")),
    }, warns


# ---------------------------------------------------------------------------
# 리포트 정규화 (교차검증)
# ---------------------------------------------------------------------------
def _normalize_report(data: dict, path: Path) -> dict:
    """핸드오프 dict → 통일 반환 dict. 교차검증 결과는 warnings 로 수집."""
    warnings: list[str] = []

    sv = data.get("schema_version")
    norm_sv = sv if isinstance(sv, int) else 1
    if not isinstance(sv, int):
        warnings.append(f"schema_version 누락/비정상({sv!r}) → 1 로 간주")
    elif sv > 1:
        warnings.append(f"schema_version={sv}(미래 버전) → 알려진 필드만 해석")

    raw_entries = data.get("competitors", [])
    if not isinstance(raw_entries, list):
        warnings.append("competitors 가 리스트가 아님 → 빈 목록 처리")
        raw_entries = []

    competitors: list[dict] = []
    for e in raw_entries:
        norm, warns = _normalize_entry(e)
        warnings.extend(warns)
        if norm is not None:
            competitors.append(norm)

    # 중복 경쟁사명 교차검증
    names = [c["name"] for c in competitors]
    dups = sorted({n for n in names if names.count(n) > 1})
    if dups:
        warnings.append(f"중복 경쟁사명: {', '.join(dups)}")

    # top_issues 정제 — 문자열 항목만 채택(숫자 등 비문자는 노이즈로 간주해 제외)
    raw_issues = data.get("top_issues", [])
    if not isinstance(raw_issues, list):
        warnings.append("top_issues 가 리스트가 아님 → 빈 목록 처리")
        raw_issues = []
    top_issues = [s for s in (_clean_str(x) for x in raw_issues if isinstance(x, str)) if s]

    # 날짜 필드 — as_of: JSON 우선, 없으면 날짜 스냅샷 파일명에서 보강
    as_of = _clean_str(data.get("as_of")) or None
    m = DATED_RE.match(path.name)
    if as_of is None and m:
        as_of = m.group(1)
    if as_of is None:
        warnings.append("as_of 미상(JSON·파일명 모두 날짜 없음)")
    else:
        try:
            datetime.date.fromisoformat(as_of[:10])
        except ValueError:
            warnings.append(f"as_of 날짜 파싱 불가: {as_of!r}")

    generated_at = _clean_str(data.get("generated_at")) or None

    if not competitors and not top_issues:
        warnings.append("경쟁사/Top이슈가 모두 비어 있음(빈 산출물)")

    return {
        "as_of": as_of,
        "generated_at": generated_at,
        "schema_version": norm_sv,
        "source_file": path.name,
        "competitors": competitors,
        "competitor_count": len(competitors),
        "top_issues": top_issues,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 수집 (메인 진입점)
# ---------------------------------------------------------------------------
# 진단 코드(단독 실행 시 사람용 메시지로 변환). 공개 함수는 dict|None 만 반환.
REASON_NO_PATH = "no_path"                # 경로 미설정(.env MARKET_REPO_PATH 빈값)
REASON_NO_BASE = "no_base"                # clone 경로 없음/디렉터리 아님
REASON_NO_REPORTS_DIR = "no_reports_dir"  # reports/ 폴더 없음
REASON_NO_REPORTS = "no_reports"          # 핸드오프 파일 없음
REASON_ALL_CORRUPT = "all_corrupt"        # 후보 전부 손상/무효
REASON_OK = "ok"


def _collect_with_reason(base_path=None):
    """
    (result_or_None, reason) 반환. result 는 정규화 dict 또는 None.
    공개 함수 collect_market_trend 와 단독 실행 진단이 공유하는 단일 로직(SSOT). READ-ONLY.
    """
    base = _resolve_base(base_path)
    if base is None:
        return None, REASON_NO_PATH
    if not base.is_dir():
        return None, REASON_NO_BASE
    report_dirs = _existing_report_dirs(base)
    if not report_dirs:
        return None, REASON_NO_REPORTS_DIR

    candidates = _ordered_handoff_files(report_dirs)
    if not candidates:
        return None, REASON_NO_REPORTS

    # 우선순위 순으로 시도. 손상 파일은 건너뛰고 다음 후보로 폴백.
    skipped: list[str] = []
    for path in candidates:
        data = _load_json(path)
        if data is None:
            skipped.append(path.name)
            continue
        result = _normalize_report(data, path)
        if skipped:
            result["warnings"].insert(0, f"손상/무효 산출물 건너뜀: {', '.join(skipped)}")
        return result, REASON_OK

    return None, REASON_ALL_CORRUPT


def collect_market_trend(base_path=None) -> dict | None:
    """
    경쟁동향 repo 로컬 clone 의 '최신' 핸드오프 산출물을 읽어 정규화 dict 로 반환. READ-ONLY.
    - base_path 미지정 시 config.MARKET_REPO_PATH(.env) 사용.
    - 경로 미설정/산출물 없음/전부 손상 → None (리포트가 "경쟁동향 미연동"으로 처리 가능).
    반환 dict 형태는 모듈 docstring 참조.
    """
    result, _reason = _collect_with_reason(base_path)
    return result


# ---------------------------------------------------------------------------
# 단독 실행
# ---------------------------------------------------------------------------
_REASON_MSG = {
    REASON_NO_PATH: "경로 미설정 — .env 의 MARKET_REPO_PATH 가 비어 있음 (경쟁동향 미연동)",
    REASON_NO_BASE: "clone 경로 없음/디렉터리 아님 — MARKET_REPO_PATH 확인",
    REASON_NO_REPORTS_DIR: f"reports 폴더 없음 ({'/'.join(REPORT_SUBDIRS)} 중 하나) — 핸드오프 산출물 미생성",
    REASON_NO_REPORTS: f"핸드오프 파일 없음 ('{CANONICAL_NAME}' 또는 'YYYY-MM-DD_trend.json')",
    REASON_ALL_CORRUPT: "핸드오프 파일이 모두 손상/무효 — 읽기 실패",
}
_SIGNAL_MARK = {"up": "▲", "down": "▼", "flat": "─"}


def _print_result(r: dict) -> None:
    print(f"[경쟁동향] {r['source_file']}  (as_of {r['as_of']} · schema v{r['schema_version']})")
    if r.get("generated_at"):
        print(f"  생성: {r['generated_at']}")
    print(f"  경쟁사 {r['competitor_count']}곳:")
    for c in r["competitors"]:
        mark = _SIGNAL_MARK.get(c["signal"], "─")
        print(f"    [{c['confidence']}] {mark} {c['name']}: {c['change'] or '(변화 설명 없음)'}")
        for s in c["sources"]:
            print(f"          ↳ {s}")
    if r["top_issues"]:
        print("  Top 이슈:")
        for i, t in enumerate(r["top_issues"], 1):
            print(f"    {i}. {t}")
    else:
        print("  Top 이슈: (없음)")
    for w in r["warnings"]:
        print(f"  [점검] {w}")


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows 콘솔 대비
    except Exception:
        pass

    override = sys.argv[1] if len(sys.argv) > 1 else None
    result, reason = _collect_with_reason(override)
    if result is None:
        print(f"경쟁동향: {_REASON_MSG.get(reason, reason)} → None 반환")
        return
    _print_result(result)


if __name__ == "__main__":
    _cli()
