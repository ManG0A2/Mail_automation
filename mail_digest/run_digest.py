import argparse
import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, List
import urllib.request

from fetcher import evaluate_email, load_env, run_fetch
from notifier import _get_ssl_context, dispatch_notifications


SAMPLE_EMAILS = [
    {
        "source": "POSTECH",
        "subject": "[학생지원팀] 2026학년도 2학기 대학원 우수연구 장학금 신청 안내",
        "sender": "학생지원팀 <student-affairs@postech.ac.kr>",
        "date": "2026-10-22 09:10",
        "body": "안녕하세요, 학생지원팀입니다. 2026학년도 2학기 대학원생 대상 우수연구 장학금 신청을 안내드립니다. 신청 기한은 10월 28일(수) 17:00까지이며 POVIS에서 온라인 접수 후 지도교수 확인서를 제출하시기 바랍니다. 지원 금액: 석사 200만원, 박사 300만원.",
    },
    {
        "source": "POSTECH",
        "subject": "[컴퓨터공학과] AI 대학원 합동 콜로키움: Large Language Models for Scientific Discovery",
        "sender": "CSE 행정실 <cse-office@postech.ac.kr>",
        "date": "2026-10-22 08:45",
        "body": "이번 주 금요일 오후 4시 박태준학술정보관 2층에서 LLM 및 인공지능 에이전트 기반 과학적 발견을 주제로 특별 세미나가 진행됩니다. 사전 신청자에게는 간단한 다과가 제공됩니다.",
    },
    {
        "source": "POSTECH",
        "subject": "[취업진로팀] 삼성리서치 AI 분야 산학장학생 및 동계 인턴십 모집 공고",
        "sender": "취업진로팀 <career@postech.ac.kr>",
        "date": "2026-10-21 16:30",
        "body": "삼성리서치에서 2026 하반기 포스텍 석/박사 대상 AI·머신러닝 분야 산학장학생 및 연구 인턴을 모집합니다. 서류 마감: 10월 30일(금). 온라인 채용 설명회가 10월 25일 14시에 진행됩니다.",
    },
    {
        "source": "Naver",
        "subject": "(광고) 가을맞이 특가 할인 쿠폰이 도착했습니다!",
        "sender": "쇼핑몰뉴스레터 <newsletter@shopping.example.com>",
        "date": "2026-10-22 07:00",
        "body": "지금 접속하시면 최대 50% 할인 쿠폰을 드립니다. 수신거부(unsubscribe)는 여기를 클릭하세요.",
    },
    {
        "source": "Naver",
        "subject": "[네이버페이] 정기결제 승인 안내 (클라우드 서버 이용료)",
        "sender": "네이버페이 <no-reply@pay.naver.com>",
        "date": "2026-10-22 08:15",
        "body": "고객님의 네이버페이 정기결제(29,000원)가 정상 승인되었습니다.",
    },
]


def build_prompt(candidates: List[Dict[str, Any]], user_profile: Dict[str, Any]) -> str:
    """요약용 프롬프트 구성"""
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    items_text = []
    for idx, c in enumerate(candidates[:20], 1):
        items_text.append(
            f"[{idx}] 출처:{c['source']} | 점수:{c.get('score',0)} | 분류:{','.join(c.get('matched_categories',[]))}\n"
            f"제목: {c['subject']}\n"
            f"발신: {c['sender']} ({c['date']})\n"
            f"내용: {c['body'][:400]}"
        )

    joined = "\n\n".join(items_text)
    return (
        f"당신은 포스텍 대학원생을 위한 스마트 메일 비서입니다.\n"
        f"사용자 프로필: {user_profile.get('description', '')}\n"
        f"관심 키워드: {', '.join(user_profile.get('custom_interests', []))}\n\n"
        f"아래는 최근 24시간 동안 도착한 메일 후보 목록입니다.\n"
        f"꼭 확인해야 할 중요 메일만 골라 디스코드에서도 읽기 편한 간결한 브리핑(`📬 [{today_str}] 오늘의 핵심 메일 브리핑`)으로 작성해주세요.\n\n"
        f"작성 형식:\n"
        f"1. 🚨 **최우선 확인 (마감/장학/행정/채용)** - 핵심 내용 1~2줄 요약 및 기한 명시\n"
        f"2. 💡 **연구/세미나 & 내 관심사** - 주요 주제 및 일시\n"
        f"3. 📌 **기타 참고 알림** - 한 줄 요약\n\n"
        f"--- 메일 목록 ---\n{joined}"
    )


def summarize_with_llm(prompt: str, env_vars: Dict[str, str]) -> str:
    """
    1순위: 포스텍 공식 생성형 AI API (https://genai.postech.ac.kr/agent/api/...)
    2순위: OpenAI 호환 엔드포인트
    3순위: Google Gemini API
    """
    api_key = env_vars.get("AI_API_KEY") or env_vars.get("GEMINI_API_KEY", "")
    base_url = (
        env_vars.get("AI_API_BASE_URL", "").strip().rstrip("/")
        or "https://genai.postech.ac.kr/agent/api/a3/claude"
    )
    model = env_vars.get("AI_MODEL") or env_vars.get("GEMINI_MODEL", "claude-sonnet-4.5")

    # 1. 포스텍 공식 생성형 AI API (genai.postech.ac.kr)
    if "genai.postech.ac.kr" in base_url:
        payload = {"message": prompt, "stream": False}
        req = urllib.request.Request(
            base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "Authorization": api_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60, context=_get_ssl_context()) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            if isinstance(res_data.get("message"), str):
                return res_data["message"]
            return json.dumps(res_data, ensure_ascii=False)

    # 2. 일반 OpenAI 호환 규격
    if base_url or api_key.startswith("sk-"):
        endpoint = base_url if base_url else "https://api.openai.com/v1"
        if not endpoint.endswith("/chat/completions"):
            endpoint = f"{endpoint}/chat/completions"

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45, context=_get_ssl_context()) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            return res_data["choices"][0]["message"]["content"]

    # 3. Google Gemini 기본 API 규격
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45, context=_get_ssl_context()) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        return res_data["candidates"][0]["content"]["parts"][0]["text"]


def run_demo(base_dir: Path) -> Dict[str, Any]:
    """샘플 메일 데이터로 필터링 동작 테스트"""
    config = json.loads((base_dir / "config.json").read_text(encoding="utf-8"))
    data_dir = base_dir / "data"
    data_dir.mkdir(exist_ok=True)

    evaluated = [evaluate_email(item, config) for item in SAMPLE_EMAILS]
    candidates = [e for e in evaluated if e.get("keep")]
    excluded = [e for e in evaluated if not e.get("keep")]
    candidates.sort(key=lambda x: x.get("score", 0), reverse=True)

    output = {
        "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "demo",
        "total_fetched": len(SAMPLE_EMAILS),
        "candidates_count": len(candidates),
        "excluded_count": len(excluded),
        "user_profile": config.get("user_profile", {}),
        "candidates": candidates,
        "excluded": excluded,
    }
    out_file = data_dir / "latest_candidates.json"
    out_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"🧪 [데모 모드] 샘플 메일 {len(SAMPLE_EMAILS)}통 중 {len(candidates)}통 선별 (광고 {len(excluded)}통 자동 제외)")
    return output


def main():
    parser = argparse.ArgumentParser(description="포스텍 + 네이버 스마트 메일 브리핑")
    parser.add_argument("--demo", action="store_true", help="샘플 메일로 로컬 필터링 테스트")
    parser.add_argument("--standalone", action="store_true", help="API 키를 사용해 요약까지 단독 실행")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent.parent
    data = run_demo(base_dir) if args.demo else run_fetch(base_dir)

    if args.standalone:
        env_vars = {**os.environ, **load_env(base_dir / ".env")}
        api_key = env_vars.get("AI_API_KEY") or env_vars.get("GEMINI_API_KEY", "")
        if not api_key or "your_" in api_key:
            print("⚠️ API 키가 설정되지 않았습니다. 안티그래비티에게 요약을 요청하거나 .env에 키를 입력해주세요.")
            return
        if not data.get("candidates"):
            print("📭 최근 24시간 내 새로운 중요 메일이 없습니다.")
            return
        prompt = build_prompt(data["candidates"], data.get("user_profile", {}))
        summary = summarize_with_llm(prompt, env_vars)
        dispatch_notifications(base_dir, summary)


if __name__ == "__main__":
    main()
