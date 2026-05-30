"""
kakao_send.py — 카카오 '나에게 보내기' 메모 발송.

[1단계 범위 — '보고' 채널]
- 리포트를 카카오톡 '나에게 보내기' 로만 전송한다(현재 구성원 확장 없음).
- 타인/단체 발송 없음(friends 메시지 API 사용 안 함).
- 데이터 소스(Cafe24/시트/market repo)를 변경하지 않음 → READ-ONLY 원칙 유지.
  ('보고'는 1단계 허용 범위. 발송은 내 카카오 채널로의 알림일 뿐 데이터 변경이 아님)

[API — RECON 확정]
- POST https://kapi.kakao.com/v2/api/talk/memo/default/send
- 헤더: Authorization: Bearer {KAKAO_ACCESS_TOKEN}
- 본문: template_object (JSON 문자열)

[사용 예 — PowerShell, 프로젝트 루트에서]
  python -m src.kakao_send "BYOCORE 테스트 메시지"
"""

import json
import sys

import requests

from . import config, kakao_auth  # noqa: F401  (config 는 향후 확장용)

SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
SEND_TIMEOUT = 15

# 카카오 text 템플릿은 link 가 필수 → 기본 링크(필요 시 .env/리포트 링크로 교체)
DEFAULT_LINK = "https://www.kakaocorp.com"


def send_text(text: str, link_url: str = DEFAULT_LINK, button_title: str = "확인") -> dict:
    """
    '나에게 보내기' 로 텍스트 메모 전송.
    - text: 본문(카카오 text 템플릿 권장 길이 ~200자; 분할 발송은 2단계).
    - access_token 만료(401) 시 1회 자동 갱신 후 재시도.
    """
    template_object = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": link_url, "mobile_web_url": link_url},
        "button_title": button_title,
    }

    def _post(access_token: str) -> requests.Response:
        return requests.post(
            SEND_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            data={"template_object": json.dumps(template_object, ensure_ascii=False)},
            timeout=SEND_TIMEOUT,
        )

    access_token = kakao_auth.get_access_token()
    resp = _post(access_token)
    if resp.status_code == 401:
        # access_token 만료 추정 → 갱신 후 1회 재시도
        access_token = kakao_auth.refresh_access_token().get("access_token", "")
        resp = _post(access_token)
    resp.raise_for_status()
    return resp.json()


def _cli() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    message = sys.argv[1]
    result = send_text(message)
    print(f"발송 결과: {result}")


if __name__ == "__main__":
    _cli()
