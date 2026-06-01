"""
geo_citation.py — GEO 인용률(Google Sheet) 조회. (2단계 구현)

[목표] BYOCORE_GEO_SUMMARY 탭에서 '최신 일간' 핵심 인용률(Top-100)을 읽는다.

[READ-ONLY]
- gspread + 서비스 계정. 스코프 spreadsheets.readonly → 시트 쓰기 원천 차단.
- 키 파일: config.GOOGLE_SERVICE_ACCOUNT_JSON (.env, 기본 service_account.json). 하드코딩 금지.
- 시트 ID: config.GEO_SHEET_ID.

[읽기 로직 — GEO 엔진팀 RECON 확정]
- 탭: BYOCORE_GEO_SUMMARY
- 인용률 = byocore_citation_rate (F열). mention rate = (direct+indirect)/total×100.
- 최신 = date(A열) 최댓값 행. (⚠️ 물리적 마지막 행 아님 — upsert 혼재 → 반드시 date max)
- get_all_records()로 dict 읽고 date max 행 선택. (동일 날짜 중복 시 물리적 마지막 채택)
- 데이터 없으면(배치 미실행) None 반환 → 리포트에서 "인용률 측정 대기" 처리.

[bias 경계]
- date <= 2026-05-29 → is_biased=True (과대계상 추정). 리포트 "(추정)" 표시용.
- Top-100 샘플 기준 → "핵심 인용률(Top-100)" 라벨.

[반환] dict 또는 None
  {date, citation_rate, sov_byocore, is_biased, sample_note}

[단독 실행]
  python -m src.collectors.geo_citation              # 최신 인용률
  python -m src.collectors.geo_citation 2026-05-30   # 특정일
"""

import datetime
import os
import sys

import gspread
from google.oauth2.service_account import Credentials

from .. import config

SHEET_TAB = "BYOCORE_GEO_SUMMARY"
# READ-ONLY 스코프 — 시트 쓰기 API를 OAuth 단계에서 원천 차단
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
DEFAULT_SA_PATH = "service_account.json"
BIAS_CUTOFF = datetime.date(2026, 5, 29)   # 이 날짜 이하 = biased(과대계상)

FIELD_DATE = "date"                  # A열
FIELD_RATE = "byocore_citation_rate" # F열
FIELD_SOV = "sov_byocore"            # J열 (별개지표)
FIELD_DIRECT = "direct_count"        # C열 (인용 건수: 직접)
FIELD_INDIRECT = "indirect_count"    # D열 (인용 건수: 간접)


# ---------------------------------------------------------------------------
# 인증 / 시트 접근 (READ-ONLY)
# ---------------------------------------------------------------------------
def _service_account_path() -> str:
    return config.get("GOOGLE_SERVICE_ACCOUNT_JSON", DEFAULT_SA_PATH)


def _open_worksheet():
    sa_path = _service_account_path()
    if not os.path.exists(sa_path):
        raise FileNotFoundError(
            f"서비스 계정 키 파일 없음: {sa_path!r}. "
            f".env 의 GOOGLE_SERVICE_ACCOUNT_JSON 또는 service_account.json 을 확인하세요."
        )
    creds = Credentials.from_service_account_file(sa_path, scopes=SCOPES)
    client = gspread.authorize(creds)
    # config.GEO_SHEET_ID 는 .env 가 비어 있으면 기본 시트 ID(RECON 확정)로 폴백됨
    sheet_id = config.GEO_SHEET_ID or config.DEFAULT_GEO_SHEET_ID
    spreadsheet = client.open_by_key(sheet_id)
    return spreadsheet.worksheet(SHEET_TAB)


def _fetch_records() -> list[dict]:
    """BYOCORE_GEO_SUMMARY 의 모든 데이터행을 dict 리스트로 반환(READ-ONLY)."""
    return _open_worksheet().get_all_records()


# ---------------------------------------------------------------------------
# 파싱 유틸
# ---------------------------------------------------------------------------
def _get_field(record: dict, name: str):
    """헤더명 관용 조회(공백/대소문자 무시)."""
    if name in record:
        return record[name]
    target = name.strip().lower()
    for k, v in record.items():
        if str(k).strip().lower() == target:
            return v
    return None


def _parse_date(value):
    """date 셀 → datetime.date (실패 시 None). ISO 우선 + 일부 포맷/구글 serial 대응."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # 구글 시트 serial date (epoch 1899-12-30)
        try:
            return datetime.date(1899, 12, 30) + datetime.timedelta(days=int(value))
        except (ValueError, OverflowError):
            return None
    s = str(value).strip()
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        pass
    for fmt in ("%Y/%m/%d", "%Y.%m.%d", "%Y. %m. %d", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _to_float(value):
    """숫자/문자 비율 → float (실패/빈값 시 None). '%'·',' 제거."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").rstrip("%").strip()
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 집계 (메인 진입점)
# ---------------------------------------------------------------------------
def collect_citation_rate(target_date: str | None = None) -> dict | None:
    """
    최신(date max) 핵심 인용률 행을 읽어 반환. READ-ONLY.
    - target_date(YYYY-MM-DD) 지정 시 해당 날짜 행, 미지정 시 date 최댓값 행.
    - 데이터 없음/해당 날짜 없음 → None.

    반환 dict:
      date          : 선택된 행의 날짜(YYYY-MM-DD)
      citation_rate : byocore_citation_rate (% 값, float) 또는 None
      sov_byocore   : sov_byocore (J열, 별개지표, float) 또는 None
      is_biased     : date <= 2026-05-29 → True (과대계상 추정)
      sample_note   : "핵심 인용률(Top-100)" (+ biased 시 추정 안내)
    """
    records = _fetch_records()

    # 날짜 파싱 가능한 행만 (date, 물리적 index, record)
    dated: list[tuple[datetime.date, int, dict]] = []
    for idx, r in enumerate(records):
        d = _parse_date(_get_field(r, FIELD_DATE))
        if d is not None:
            dated.append((d, idx, r))
    if not dated:
        return None  # 배치 미실행 등 데이터 없음

    if target_date:
        td = _parse_date(target_date)
        candidates = [t for t in dated if t[0] == td]
        if not candidates:
            return None
    else:
        max_date = max(t[0] for t in dated)
        candidates = [t for t in dated if t[0] == max_date]

    # 동일 날짜 중복(upsert 잔재) → 물리적으로 마지막 행 채택
    chosen_date, _, latest = max(candidates, key=lambda t: t[1])

    rate = _to_float(_get_field(latest, FIELD_RATE))
    sov = _to_float(_get_field(latest, FIELD_SOV))
    direct_cnt = int(_to_float(_get_field(latest, FIELD_DIRECT)) or 0)
    indirect_cnt = int(_to_float(_get_field(latest, FIELD_INDIRECT)) or 0)
    is_biased = chosen_date <= BIAS_CUTOFF

    note = "핵심 인용률(Top-100)"
    if is_biased:
        note += f" · {BIAS_CUTOFF.isoformat()} 이전 과대계상(추정)"

    return {
        "date": chosen_date.isoformat(),
        "citation_rate": rate,
        "sov_byocore": sov,
        "direct_count": direct_cnt,
        "indirect_count": indirect_cnt,
        "citation_count": direct_cnt + indirect_cnt,
        "is_biased": is_biased,
        "sample_note": note,
    }


def recent_unbiased_citations(n: int = 2) -> list[dict]:
    """
    최근 n개 '비-biased'(date > 2026-05-29) 측정값을 date 내림차순으로 반환. READ-ONLY.
    인용 '건수' 변화 비교 + 인용률 추세 판정용. 동일 날짜 중복은 1개로 정리.
    각 dict: {date, direct_count, indirect_count, citation_count, citation_rate}
      citation_rate: byocore_citation_rate (float, 없으면 None) — 추세 판정용
    """
    records = _fetch_records()
    rows: list[tuple[datetime.date, dict]] = []
    for r in records:
        d = _parse_date(_get_field(r, FIELD_DATE))
        if d is None or d <= BIAS_CUTOFF:   # biased 데이터는 비교에서 제외
            continue
        direct_cnt   = int(_to_float(_get_field(r, FIELD_DIRECT)) or 0)
        indirect_cnt = int(_to_float(_get_field(r, FIELD_INDIRECT)) or 0)
        rate         = _to_float(_get_field(r, FIELD_RATE))   # float or None
        rows.append((d, {
            "date": d.isoformat(),
            "direct_count":   direct_cnt,
            "indirect_count": indirect_cnt,
            "citation_count": direct_cnt + indirect_cnt,
            "citation_rate":  rate,          # 추세 판정용 — 없으면 None
        }))
    rows.sort(key=lambda t: t[0], reverse=True)

    out: list[dict] = []
    seen: set[datetime.date] = set()
    for d, payload in rows:
        if d in seen:
            continue
        seen.add(d)
        out.append(payload)
        if len(out) >= n:
            break
    return out


# ---------------------------------------------------------------------------
# 단독 실행
# ---------------------------------------------------------------------------
def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    target = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        result = collect_citation_rate(target)
    except FileNotFoundError as e:
        print(f"[오류] {e}")
        raise
    except Exception as e:
        print(f"[오류] GEO 시트 읽기 실패: {type(e).__name__}: {e}")
        print("  · 시트가 서비스 계정과 '공유'됐는지, 탭명/시트ID가 맞는지 확인하세요.")
        raise

    if result is None:
        print("GEO 인용률: 측정 대기 (데이터 없음 — 배치 미실행/해당 날짜 없음)")
        return

    rate = result["citation_rate"]
    rate_disp = f"{rate:.2f}%" if isinstance(rate, (int, float)) else "(값 없음)"
    print("[GEO 핵심 인용률]")
    print(f"  날짜          : {result['date']}{' (추정)' if result['is_biased'] else ''}")
    print(f"  핵심 인용률   : {rate_disp}  ({result['sample_note']})")
    print(f"  SoV(byocore)  : {result['sov_byocore']}  (별개지표)")
    print(f"  is_biased     : {result['is_biased']}")

    if rate is None:  # 헤더명 불일치 등 진단 보조
        try:
            recs = _fetch_records()
            print(f"  [진단] 시트 헤더: {list(recs[0].keys()) if recs else '없음'}")
        except Exception:
            pass


if __name__ == "__main__":
    _cli()
