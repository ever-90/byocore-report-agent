"""
market_trend.py — [스텁] 경쟁동향 (market repo 로컬 clone) 읽기.

[1단계 READ-ONLY]
- 로컬 clone 산출물(파일) 읽기 전용. 원격 commit/push 금지.
- repo: ever-90/market-insight-byocore → 로컬 경로 config.MARKET_REPO_PATH

이 모듈은 1단계에서 스텁이며, 실제 파싱 로직은 2단계에서 구현한다.
"""

from pathlib import Path

from .. import config


def collect_market_trend() -> dict:
    """
    [스텁] market repo 로컬 clone 에서 경쟁동향 산출물을 읽어 반환.

    TODO(2단계):
      1) base = Path(config.require("MARKET_REPO_PATH")).
      2) 산출물 파일(예: reports/*.md, data/*.json) 탐색 후 최신본 파싱.
      3) 경쟁사 추세 / Top 이슈 후보 추출.
      4) 반환 예: {"as_of": ..., "competitors": [...], "top_issues": [...]}
      5) READ-ONLY: 파일 읽기만. 쓰기/커밋/푸시 금지.
    """
    _ = Path  # 2단계에서 사용 (현재는 스텁)
    raise NotImplementedError("market_trend.collect_market_trend 는 2단계에서 구현 예정 (스텁)")
