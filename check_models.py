import json
from pathlib import Path
import ssl
import urllib.error
import urllib.request


def get_ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def load_api_key() -> str:
    env_path = Path(__file__).resolve().parent / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("AI_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(".env에서 AI_API_KEY를 찾을 수 없습니다.")


def test_post(url: str, payload: dict, key: str) -> tuple[bool, str]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": key,
            "Authorization": key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20, context=get_ssl_ctx()) as r:
            body = r.read().decode("utf-8")
            return True, body[:180]
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        return False, f"HTTP {e.code}: {err_body[:150]}"
    except Exception as e:
        return False, str(e)


def main():
    key = load_api_key()
    print("=" * 65)
    print("🔍 포스텍 생성형 AI API (genai.postech.ac.kr) 사용 가능 모델 전체 점검")
    print("=" * 65)

    # 1. Google Gemini 계열 (a51: /google/models/{model}:generateContent)
    print("\n[1] Google Gemini 모델 (엔드포인트: a51/google/models/{model}:generateContent)")
    gemini_models = [
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ]
    for m in gemini_models:
        url = f"https://genai.postech.ac.kr/agent/api/a51/google/models/{m}:generateContent"
        ok, msg = test_post(url, {"contents": [{"role": "user", "parts": [{"text": "OK"}]}]}, key)
        status = "✅ 작동" if ok else "❌ 불가"
        print(f"  - {m:<26} : {status}")

    # 2. Anthropic Claude 계열 (a45: /anthropic/messages)
    print("\n[2] Anthropic Claude 모델 (엔드포인트: a45/anthropic/messages)")
    claude_models = [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-opus-4-5",
        "claude-opus-4-5-20251101",
        "claude-sonnet-4-5",
        "claude-sonnet-4-5-20250929",
        "claude-opus-4-1-20250805",
        "claude-opus-4-20250514",
        "claude-sonnet-4-20250514",
        "claude-haiku-4-5",
    ]
    for m in claude_models:
        url = "https://genai.postech.ac.kr/agent/api/a45/anthropic/messages"
        payload = {
            "model": m,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "OK"}],
        }
        ok, msg = test_post(url, payload, key)
        status = "✅ 작동" if ok else f"❌ 불가 ({msg[:60]})"
        print(f"  - {m:<26} : {status}")

    # 3. OpenAI GPT / o3 계열 (a11: /openai/responses)
    print("\n[3] OpenAI GPT / o3 모델 (엔드포인트: a11/openai/responses)")
    gpt_models = [
        "gpt-5.2-gs-2025-12-11",
        "gpt-5.1-gs-2025-11-13",
        "gpt-5-gs-2025-08-07",
        "gpt-5-mini-gs-2025-08-07",
        "o3-deep-research-gs-2025-06-26",
        "gpt-4.1-mini-gs-2025-04-14",
    ]
    for m in gpt_models:
        url = "https://genai.postech.ac.kr/agent/api/a11/openai/responses"
        ok, msg = test_post(url, {"model": m, "input": "OK"}, key)
        status = "✅ 작동" if ok else f"❌ 불가 ({msg[:60]})"
        print(f"  - {m:<32} : {status}")


if __name__ == "__main__":
    main()
