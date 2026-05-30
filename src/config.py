"""
config.py — .env 로더 및 설정 단일 진입점.

[원칙]
- 모든 비밀값/설정은 .env 에서만 온다. 코드 하드코딩 금지(1단계 READ-ONLY).
- python-dotenv 로 프로젝트 루트의 .env 를 읽어 환경변수로 로드한다.
- 토큰 4종(CAFE24_*, KAKAO_*)은 auth 스크립트가 set_env_value() 로 .env 에 기록한다.
"""

import os
from pathlib import Path

from dotenv import load_dotenv, set_key

# 이 파일은 src/config.py → 프로젝트 루트는 한 단계 위
ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"

# .env 로드 (없어도 에러를 내지 않음 — .env.example 참고)
load_dotenv(ENV_PATH)

# GEO 시트 기본값 (RECON 확정 사실). .env 의 GEO_SHEET_ID 가 비어 있으면 이 값을 사용.
DEFAULT_GEO_SHEET_ID = "18rrHs_ifXSxZlY3mboRftCXO3KRl3JRBnZ2P-c2ufZA"

# Cafe24 Admin API 버전 (RECON 확정). 모든 Cafe24 요청 헤더에 포함.
CAFE24_API_VERSION = "2026-03-01"


def get(key: str, default: str | None = None) -> str | None:
    """환경변수 조회. 빈 문자열/공백은 None 으로 취급."""
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    return val.strip()


def require(key: str) -> str:
    """필수 환경변수 조회. 없으면 명확한 에러."""
    val = get(key)
    if not val:
        raise RuntimeError(
            f"필수 환경변수 '{key}' 가 .env 에 설정되지 않았습니다. .env.example 를 참고하세요."
        )
    return val


def set_env_value(key: str, value: str) -> None:
    """
    .env 의 `key=value` 한 줄을 갱신(없으면 추가)한다.
    auth 스크립트가 발급/갱신한 토큰을 다시 .env 에 저장할 때 사용.
    현재 프로세스 환경에도 즉시 반영한다.
    """
    if not ENV_PATH.exists():
        ENV_PATH.touch()
    set_key(str(ENV_PATH), key, value)
    os.environ[key] = value


# ---- Cafe24 ----
CAFE24_MALL_ID = get("CAFE24_MALL_ID")
CAFE24_CLIENT_ID = get("CAFE24_CLIENT_ID")
CAFE24_CLIENT_SECRET = get("CAFE24_CLIENT_SECRET")
CAFE24_REDIRECT_URI = get("CAFE24_REDIRECT_URI")
CAFE24_ACCESS_TOKEN = get("CAFE24_ACCESS_TOKEN")
CAFE24_REFRESH_TOKEN = get("CAFE24_REFRESH_TOKEN")

# ---- Kakao ----
KAKAO_REST_API_KEY = get("KAKAO_REST_API_KEY")
KAKAO_CLIENT_SECRET = get("KAKAO_CLIENT_SECRET")
KAKAO_REDIRECT_URI = get("KAKAO_REDIRECT_URI")
KAKAO_ACCESS_TOKEN = get("KAKAO_ACCESS_TOKEN")
KAKAO_REFRESH_TOKEN = get("KAKAO_REFRESH_TOKEN")

# ---- GEO 인용률 ----
GEO_SHEET_ID = get("GEO_SHEET_ID", DEFAULT_GEO_SHEET_ID)
GOOGLE_SERVICE_ACCOUNT_JSON = get("GOOGLE_SERVICE_ACCOUNT_JSON")

# ---- 경쟁동향 ----
MARKET_REPO_PATH = get("MARKET_REPO_PATH")


def summary() -> dict[str, bool]:
    """설정 점검용. 비밀값 노출 없이 '채워졌는지' 여부만 반환."""
    keys = [
        "CAFE24_MALL_ID", "CAFE24_CLIENT_ID", "CAFE24_CLIENT_SECRET", "CAFE24_REDIRECT_URI",
        "KAKAO_REST_API_KEY", "KAKAO_CLIENT_SECRET", "KAKAO_REDIRECT_URI",
        "GEO_SHEET_ID", "GOOGLE_SERVICE_ACCOUNT_JSON", "MARKET_REPO_PATH",
        "CAFE24_ACCESS_TOKEN", "CAFE24_REFRESH_TOKEN", "KAKAO_ACCESS_TOKEN", "KAKAO_REFRESH_TOKEN",
    ]
    return {k: bool(get(k)) for k in keys}


if __name__ == "__main__":
    # 설정 점검: 각 키가 채워졌는지(True/False)만 출력. 값 자체는 출력하지 않음.
    print(f"ROOT_DIR        = {ROOT_DIR}")
    print(f"ENV_PATH        = {ENV_PATH}  (exists={ENV_PATH.exists()})")
    print(f"GEO_SHEET_ID    = {GEO_SHEET_ID}")
    print(f"CAFE24_API_VER  = {CAFE24_API_VERSION}")
    print("--- 키 설정 여부 (값 비노출) ---")
    for k, ok in summary().items():
        print(f"  [{'OK ' if ok else '   '}] {k}")
