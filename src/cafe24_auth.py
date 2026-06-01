"""
cafe24_auth.py — Cafe24 OAuth2 토큰 발급/자동갱신 (골격).

[1단계 READ-ONLY 원칙]
- 이 스크립트는 인증(토큰 발급/갱신)만 담당한다. 쇼핑몰 데이터를 변경하는 API 는 호출하지 않는다.
- 토큰은 .env 에만 저장(CAFE24_ACCESS_TOKEN / CAFE24_REFRESH_TOKEN). 코드/콘솔 하드코딩 금지.

[토큰 수명 — RECON 확정]
- access_token : 2시간
- refresh_token: 2주

[흐름]
1) (최초 1회) build_authorize_url() 로 만든 URL 을 브라우저로 열어 로그인/동의
   → redirect_uri 로 ?code=... 수신.
2) issue_token(code) 로 code 를 access/refresh 토큰으로 교환 → .env 저장.
3) 이후 refresh_access_token() 으로 자동 갱신.

[사용 예 — PowerShell, 프로젝트 루트에서]
  python -m src.cafe24_auth authorize        # 인가 URL 출력
  python -m src.cafe24_auth issue <CODE>     # 리다이렉트 URL 의 code 로 토큰 발급
  python -m src.cafe24_auth refresh          # 수동 갱신 테스트
"""

import base64
import sys

import requests

from . import config

# 필요 scope 추가 시 여기만 수정 후 authorize → issue 1회 재인가
OAUTH_SCOPE = "mall.read_order,mall.read_product"
TOKEN_TIMEOUT = 15                # seconds


def _token_url() -> str:
    mall_id = config.require("CAFE24_MALL_ID")
    return f"https://{mall_id}.cafe24api.com/api/v2/oauth/token"


def _basic_auth_header() -> dict[str, str]:
    client_id = config.require("CAFE24_CLIENT_ID")
    client_secret = config.require("CAFE24_CLIENT_SECRET")
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    token = base64.b64encode(raw).decode("utf-8")
    return {"Authorization": f"Basic {token}"}


def build_authorize_url(state: str = "byocore") -> str:
    """최초 1회 사용자 동의를 받기 위한 인가 URL 생성."""
    mall_id = config.require("CAFE24_MALL_ID")
    client_id = config.require("CAFE24_CLIENT_ID")
    redirect_uri = config.require("CAFE24_REDIRECT_URI")
    return (
        f"https://{mall_id}.cafe24api.com/api/v2/oauth/authorize"
        f"?response_type=code"
        f"&client_id={client_id}"
        f"&state={state}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={OAUTH_SCOPE}"
    )


def _save_tokens(data: dict) -> None:
    if data.get("access_token"):
        config.set_env_value("CAFE24_ACCESS_TOKEN", data["access_token"])
    if data.get("refresh_token"):
        config.set_env_value("CAFE24_REFRESH_TOKEN", data["refresh_token"])


def issue_token(code: str) -> dict:
    """인가코드(code)를 access/refresh 토큰으로 교환하고 .env 에 저장."""
    redirect_uri = config.require("CAFE24_REDIRECT_URI")
    resp = requests.post(
        _token_url(),
        headers=_basic_auth_header(),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=TOKEN_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    _save_tokens(data)
    return data


def refresh_access_token() -> dict:
    """refresh_token 으로 access_token 을 갱신하고 .env 에 저장."""
    refresh = config.require("CAFE24_REFRESH_TOKEN")
    resp = requests.post(
        _token_url(),
        headers=_basic_auth_header(),
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        },
        timeout=TOKEN_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    _save_tokens(data)
    return data


def get_access_token(auto_refresh: bool = True) -> str:
    """
    사용 가능한 access_token 반환.
    - 토큰이 있으면 그대로 반환.
    - 없고 auto_refresh=True 이며 refresh_token 이 있으면 갱신 시도.
    - 그래도 없으면 재인가 안내 예외.
    (정밀한 만료시각 추적/사전 갱신은 2단계에서 도입)
    """
    token = config.get("CAFE24_ACCESS_TOKEN")
    if token:
        return token
    if auto_refresh and config.get("CAFE24_REFRESH_TOKEN"):
        return refresh_access_token().get("access_token", "")
    raise RuntimeError(
        "Cafe24 access_token 없음. 먼저 'authorize' → 'issue <code>' 로 발급하세요."
    )


def _cli() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "authorize":
        print("아래 URL 을 브라우저로 열어 동의 후, 리다이렉트된 주소의 code 값을 사용하세요:\n")
        print(build_authorize_url())
    elif cmd == "issue":
        if len(sys.argv) < 3:
            print("사용법: python -m src.cafe24_auth issue <CODE>")
            return
        issue_token(sys.argv[2])
        print("Cafe24 토큰 발급 완료 → .env 저장됨")
    elif cmd == "refresh":
        refresh_access_token()
        print("Cafe24 access_token 갱신 완료 → .env 저장됨")
    else:
        print(f"알 수 없는 명령: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    _cli()
