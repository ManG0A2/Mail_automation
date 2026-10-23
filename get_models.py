import argparse
import json
from pathlib import Path
import ssl
import sys
import urllib.error
import urllib.request


def get_ssl_context() -> ssl.SSLContext:
    """macOS 환경에서 발생할 수 있는 SSL 인증서 오류 방지"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def load_key_from_env(key_name: str) -> str:
    """프로젝트 루트의 .env 파일에서 키 탐색"""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key_name:
            return v.strip().strip('"').strip("'")
    return ""


def list_gemini_models(api_key: str):
    """Google Gemini API 사용 가능 모델 목록 조회"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    req = urllib.request.Request(url)

    try:
        with urllib.request.urlopen(req, timeout=15, context=get_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = data.get("models", [])

            print("=" * 80)
            print(f"✨ Google Gemini API 사용 가능 모델 목록 (총 {len(models)}개)")
            print("=" * 80)

            # generateContent (대화/텍스트/멀티모달 생성) 지원 모델 우선 출력
            chat_models = []
            other_models = []

            for m in models:
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods:
                    chat_models.append(m)
                else:
                    other_models.append(m)

            print("\n[💬 대화 / 텍스트·멀티모달 생성 모델 (generateContent)]")
            print(f"{'모델 ID':<35} | {'입력 토큰':<10} | {'출력 토큰':<10} | {'표시 이름'}")
            print("-" * 80)
            for m in chat_models:
                model_id = m.get("name", "").replace("models/", "")
                display_name = m.get("displayName", "")
                input_limit = f"{m.get('inputTokenLimit', '-'):,}" if isinstance(m.get('inputTokenLimit'), int) else "-"
                output_limit = f"{m.get('outputTokenLimit', '-'):,}" if isinstance(m.get('outputTokenLimit'), int) else "-"
                print(f"{model_id:<35} | {input_limit:<10} | {output_limit:<10} | {display_name}")

            if other_models:
                print("\n[🔍 기타 모델 (임베딩/특수 목적 등)]")
                for m in other_models:
                    model_id = m.get("name", "").replace("models/", "")
                    methods = ", ".join(m.get("supportedGenerationMethods", []))
                    print(f"- {model_id:<33} (지원 기능: {methods})")

    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        print(f"❌ Gemini API 요청 실패 (HTTP {e.code}):\n{err}")
    except Exception as e:
        print(f"❌ 오류 발생: {e}")


def list_openai_models(api_key: str):
    """OpenAI API 사용 가능 모델 목록 조회"""
    url = "https://api.openai.com/v1/models"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15, context=get_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = sorted(data.get("data", []), key=lambda x: x.get("id", ""))

            print("=" * 80)
            print(f"✨ OpenAI API 사용 가능 모델 목록 (총 {len(models)}개)")
            print("=" * 80)
            for m in models:
                model_id = m.get("id", "")
                owned_by = m.get("owned_by", "")
                print(f"- {model_id:<40} (소유: {owned_by})")

    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        print(f"❌ OpenAI API 요청 실패 (HTTP {e.code}):\n{err}")
    except Exception as e:
        print(f"❌ 오류 발생: {e}")


def list_anthropic_models(api_key: str):
    """Anthropic Claude API 사용 가능 모델 목록 조회"""
    url = "https://api.anthropic.com/v1/models"
    req = urllib.request.Request(
        url,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15, context=get_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = sorted(data.get("data", []), key=lambda x: x.get("id", ""))

            print("=" * 80)
            print(f"✨ Anthropic Claude API 사용 가능 모델 목록 (총 {len(models)}개)")
            print("=" * 80)
            for m in models:
                model_id = m.get("id", "")
                display_name = m.get("display_name", "")
                print(f"- {model_id:<35} | {display_name}")

    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        print(f"❌ Anthropic API 요청 실패 (HTTP {e.code}):\n{err}")
    except Exception as e:
        print(f"❌ 오류 발생: {e}")


def main():
    parser = argparse.ArgumentParser(description="API 키로 사용 가능한 AI 모델 목록을 조회합니다.")
    parser.add_argument("--key", "-k", type=str, help="API 키 (입력하지 않으면 .env에서 자동 로드)")
    parser.add_argument(
        "--provider",
        "-p",
        choices=["gemini", "openai", "claude"],
        default="gemini",
        help="API 제공자 (기본값: gemini)",
    )
    args = parser.parse_args()

    api_key = args.key
    if not api_key:
        if args.provider == "gemini":
            api_key = load_key_from_env("GEMINI_API_KEY")
        elif args.provider == "openai":
            api_key = load_key_from_env("OPENAI_API_KEY")
        elif args.provider == "claude":
            api_key = load_key_from_env("ANTHROPIC_API_KEY")

    if not api_key:
        env_map = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY"}
        print(f"⚠️ API 키를 찾을 수 없습니다.")
        print(f"사용법 1: python3 get_models.py --key 'YOUR_API_KEY' --provider {args.provider}")
        print(f"사용법 2: .env 파일에 {env_map[args.provider]}=... 설정 후 실행")
        sys.exit(1)

    if args.provider == "gemini":
        list_gemini_models(api_key)
    elif args.provider == "openai":
        list_openai_models(api_key)
    elif args.provider == "claude":
        list_anthropic_models(api_key)


if __name__ == "__main__":
    main()
