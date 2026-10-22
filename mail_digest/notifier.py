import datetime
import json
import os
from pathlib import Path
import ssl
from typing import Dict
import urllib.parse
import urllib.request

from fetcher import load_env


def _get_ssl_context() -> ssl.SSLContext:
    """macOS Python 기본 인증서 누락 환경 대응 SSL Context 생성"""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


def save_markdown_report(base_dir: Path, summary_text: str) -> Path:
    """요약 결과를 reports/YYYY-MM-DD_digest.md 파일로 저장"""
    reports_dir = base_dir / "reports"
    reports_dir.mkdir(exist_ok=True)
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    report_path = reports_dir / f"{today_str}_digest.md"
    report_path.write_text(summary_text, encoding="utf-8")
    print(f"📝 마크다운 리포트 저장 완료: {report_path}")
    return report_path


def send_discord(webhook_url: str, summary_text: str) -> bool:
    """디스코드 웹훅으로 메일 브리핑 전송 (2000자 제한 대응 분할 전송)"""
    if not webhook_url:
        return False
    ctx = _get_ssl_context()
    try:
        full_msg = f"@everyone\n{summary_text}" if not summary_text.startswith("@everyone") else summary_text
        chunks = [full_msg[i : i + 1900] for i in range(0, len(full_msg), 1900)]
        for chunk in chunks:
            payload = json.dumps({"content": chunk}).encode("utf-8")
            req = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "PostechMailDigest/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                if resp.status not in (200, 204):
                    print(f"⚠️ 디스코드 전송 응답 코드: {resp.status}")
                    return False
        print("✅ 디스코드 알림 전송 완료!")
        return True
    except Exception as e:
        print(f"❌ 디스코드 전송 실패: {e}")
        return False


def refresh_kakao_access_token(rest_api_key: str, refresh_token: str) -> str:
    """카카오 Refresh Token으로 새 Access Token 발급"""
    ctx = _get_ssl_context()
    url = "https://kauth.kakao.com/oauth/token"
    data = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": rest_api_key,
            "refresh_token": refresh_token,
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        return res_data.get("access_token", "")


def send_kakao_to_me(rest_api_key: str, refresh_token: str, summary_text: str) -> bool:
    """카카오톡 '나에게 보내기' API로 브리핑 전송"""
    if not rest_api_key or not refresh_token:
        return False
    ctx = _get_ssl_context()
    try:
        access_token = refresh_kakao_access_token(rest_api_key, refresh_token)
        if not access_token:
            print("❌ 카카오 액세스 토큰 갱신 실패")
            return False

        url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
        template_object = {
            "object_type": "text",
            "text": summary_text[:950],
            "link": {
                "web_url": "https://outlook.office.com",
                "mobile_web_url": "https://outlook.office.com",
            },
            "button_title": "메일함 열기",
        }
        data = urllib.parse.urlencode(
            {"template_object": json.dumps(template_object, ensure_ascii=False)}
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={"Authorization": f"Bearer {access_token}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            if resp.status == 200:
                print("✅ 카카오톡 나에게 보내기 전송 완료!")
                return True
    except Exception as e:
        print(f"❌ 카카오톡 전송 실패: {e}")
    return False


def dispatch_notifications(base_dir: Path, summary_text: str) -> None:
    """설정된 알림 채널(마크다운 파일, 디스코드, 카카오톡)로 일괄 발송"""
    config = json.loads((base_dir / "config.json").read_text(encoding="utf-8"))
    env_vars: Dict[str, str] = {**os.environ, **load_env(base_dir / ".env")}
    notif_cfg = config.get("notification", {})

    if notif_cfg.get("save_markdown", True):
        save_markdown_report(base_dir, summary_text)

    if notif_cfg.get("use_discord") or env_vars.get("DISCORD_WEBHOOK_URL"):
        send_discord(env_vars.get("DISCORD_WEBHOOK_URL", ""), summary_text)

    if notif_cfg.get("use_kakao") or (
        env_vars.get("KAKAO_REST_API_KEY") and env_vars.get("KAKAO_REFRESH_TOKEN")
    ):
        send_kakao_to_me(
            env_vars.get("KAKAO_REST_API_KEY", ""),
            env_vars.get("KAKAO_REFRESH_TOKEN", ""),
            summary_text,
        )


if __name__ == "__main__":
    base = Path(__file__).resolve().parent.parent
    reports = sorted((base / "reports").glob("*_digest.md")) if (base / "reports").exists() else []
    if reports:
        dispatch_notifications(base, reports[-1].read_text(encoding="utf-8"))
    else:
        print("전송할 리포트 파일이 없습니다.")
