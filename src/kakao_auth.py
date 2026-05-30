"""
kakao_auth.py — 카카오 OAuth2 토큰 발급/자동갱신 (골격).

[1단계 READ-ONLY 원칙]
- 인증(토큰 발급/갱신)만 담당. 실제 발송은 kakao_send.py 의 '나에게 보내기' 로만 수행.
- 토큰은 .env 에만 저장(KAKAO_ACCESS_TOKEN / KAKAO_REFRESH_TOKEN).

[토큰 수명 / 동의 — RECON 확정]
- access_token : 6시간
- refresh_token: 2개월
- 동의항목(scope): talk_message

[흐름]
1) build_authorize_url() URL 을 브라우저로 열어 동의 → redirect_uri 로 ?code=... 수신.
2) issue_token(code) 로 토큰 교환 → .env 저장.
3) refresh_access_token() 으로 자동 갱신.
   (카카오는 refresh_token 만료가 임박할 때만 응답에 새 refresh_token 을 포함)

[사용 예 — PowerShell, 프로젝트 루트에서]
  python -m src.kakao_auth authorize
  python -m src.kakao_auth issue <CODE>
  python -m src.kakao_auth refresh
"""

import sys

import requests

from . import config

AUTH_HOST = "https://kauth.kakao.com"
OAUTH_SCOPE = "talk_message"   # 나에게 보내기 동의항목
TOKEN_TIMEOUT = 15


def build_authorize_url(state: str = "byocore") -> str:
    client_id = config.require("KAKAO_REST_API_KEY")
    redirect_uri = config.require("KAKAO_REDIRECT_URI")
    return (
        f"{AUTH_HOST}/oauth/authorize"
        f"?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={OAUTH_SCOPE}"
        f"&state={state}"
    )


def _save_tokens(data: dict) -> None:
    if data.get("access_token"):
        config.set_env_value("KAKAO_ACCESS_TOKEN", data["access_token"])
    if data.get("refresh_token"):   # 갱신 임박 시에만 내려옴 → 있을 때만 저장
        config.set_env_value("KAKAO_REFRESH_TOKEN", data["refresh_token"])


def issue_token(code: str) -> dict:
    """인가코드(code)를 access/refresh 토큰으로 교환하고 .env 에 저장."""
    payload = {
        "grant_type": "authorization_code",
        "client_id": config.require("KAKAO_REST_API_KEY"),
        "redirect_uri": config.require("KAKAO_REDIRECT_URI"),
        "code": code,
    }
    secret = config.get("KAKAO_CLIENT_SECRET")
    if secret:
        payload["client_secret"] = secret
    resp = requests.post(f"{AUTH_HOST}/oauth/token", data=payload, timeout=TOKEN_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    _save_tokens(data)
    return data


def refresh_access_token() -> dict:
    """refresh_token 으로 access_token 을 갱신하고 .env 에 저장."""
    payload = {
        "grant_type": "refresh_token",
        "client_id": config.require("KAKAO_REST_API_KEY"),
        "refresh_token": config.require("KAKAO_REFRESH_TOKEN"),
    }
    secret = config.get("KAKAO_CLIENT_SECRET")
    if secret:
        payload["client_secret"] = secret
    resp = requests.post(f"{AUTH_HOST}/oauth/token", data=payload, timeout=TOKEN_TIMEOUT)
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
    """
    token = config.get("KAKAO_ACCESS_TOKEN")
    if token:
        return token
    if auto_refresh and config.get("KAKAO_REFRESH_TOKEN"):
        return refresh_access_token().get("access_token", "")
    raise RuntimeError(
        "Kakao access_token 없음. 먼저 'authorize' → 'issue <code>' 로 발급하세요."
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
            print("사용법: python -m src.kakao_auth issue <CODE>")
            return
        issue_token(sys.argv[2])
        print("Kakao 토큰 발급 완료 → .env 저장됨")
    elif cmd == "refresh":
        refresh_access_token()
        print("Kakao access_token 갱신 완료 → .env 저장됨")
    else:
        print(f"알 수 없는 명령: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    _cli()
