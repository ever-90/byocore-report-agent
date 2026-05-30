"""
geo_citation.py — [스텁] GEO 인용률 (Google Sheet) 조회.

[1단계 READ-ONLY]
- 구글 시트 읽기 전용. 시트 수정/쓰기(update/append) 금지.
- 시트 ID: config.GEO_SHEET_ID (기본값 = RECON 확정 ID)
- 인증: GOOGLE_SERVICE_ACCOUNT_JSON (서비스 계정)
  → google 라이브러리(google-auth / google-api-python-client 또는 gspread)는 2단계에서 추가.

이 모듈은 1단계에서 스텁이며, 실제 수집 로직은 2단계에서 구현한다.
"""

from .. import config


def collect_citation_rate(target_date: str) -> dict:
    """
    [스텁] target_date (YYYY-MM-DD) 의 GEO 인용률을 반환.

    TODO(2단계):
      1) GOOGLE_SERVICE_ACCOUNT_JSON 으로 Google Sheets API 인증(읽기 전용 스코프).
      2) config.GEO_SHEET_ID 시트에서 해당 일자 행의 인용률(브랜드/제품 GEO citation) 추출.
      3) 반환 예: {"date": target_date, "citation_rate": 0.0, "samples": 0, ...}
      4) READ-ONLY: spreadsheets.values.get 만 사용. update/append/batchUpdate 금지.
    """
    raise NotImplementedError("geo_citation.collect_citation_rate 는 2단계에서 구현 예정 (스텁)")
