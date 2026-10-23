import datetime
import json
import mimetypes
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
from typing import Dict
import urllib.request
import uuid


def _get_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


def load_env(env_path: Path) -> Dict[str, str]:
    env_vars: Dict[str, str] = {}
    if not env_path.exists():
        return env_vars
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env_vars[k.strip()] = v.strip().strip('"').strip("'")
    return env_vars


PROMPTS: Dict[str, str] = {
    "seminar": (
        "당신은 포스텍(POSTECH) 대학원 연구실의 전문 학술 세미나 서기입니다.\n"
        "제공된 세미나/강연 녹음 내용을 바탕으로 아래 구조에 맞춰 완성도 높은 마크다운(Markdown) **세미나 레포트**를 작성해주세요.\n\n"
        "# 📊 세미나 레포트: {title}\n"
        "- **일시**: {date}\n"
        "- **사용 모델**: `{model_label}`\n\n"
        "## 1. 🎯 핵심 요약 (Executive Summary)\n"
        "- 발표의 핵심 주제와 결론을 3~5줄로 명쾌하게 요약\n\n"
        "## 2. 🧩 연구 배경 및 문제 정의 (Background & Motivation)\n"
        "- 기존 연구의 한계점과 본 발표가 해결하고자 하는 핵심 문제\n\n"
        "## 3. ⚙️ 제안 방법론 및 핵심 내용 (Methodology & Key Content)\n"
        "- 발표에서 설명한 핵심 개념, 구조, 알고리즘, 수식/원리, 사례 등을 단계별로 상세히 정리 (전문 용어는 영문 병기)\n\n"
        "## 4. 📈 주요 실험/분석 결과 및 인사이트 (Results & Findings)\n"
        "- 정량적/정성적 결과 및 발표자가 강조한 핵심 포인트\n\n"
        "## 5. 💬 질의응답 (Q&A) 및 디스커션\n"
        "- 녹음에 포함된 질문과 답변 내용 정리 (없을 경우 주요 논의 사항)\n\n"
        "## 6. 💡 내 연구/커리어에 적용할 점 & 후속 아이디어\n"
        "- 이 세미나에서 얻을 수 있는 시사점 및 추가 확인이 필요한 키워드"
    ),
    "meeting": (
        "당신은 포스텍(POSTECH) 연구실/프로젝트 팀의 전문 회의록 작성 비서입니다.\n"
        "제공된 회의 녹음 내용을 바탕으로 아래 구조에 맞춰 깔끔하고 실용적인 마크다운(Markdown) **회의록**을 작성해주세요.\n\n"
        "# 📝 회의록: {title}\n"
        "- **일시**: {date}\n"
        "- **사용 모델**: `{model_label}`\n\n"
        "## 1. 📌 회의 개요 및 핵심 요약\n"
        "- 회의 목적 및 전체 결론 3줄 요약\n\n"
        "## 2. 🗣️ 안건별 주요 논의 내용\n"
        "- 논의된 세부 주제별로 발언 내용과 근거를 구조화하여 정리\n\n"
        "## 3. ✅ 결정된 사항 (Key Decisions)\n"
        "- 이번 회의에서 확정된 사항들을 명확한 불릿 포인트로 정리\n\n"
        "## 4. 🚀 향후 할 일 (Action Items)\n"
        "- [ ] 해야 할 작업 내용 (담당자 / 기한이 언급되었다면 함께 명시)\n\n"
        "## 5. 🔍 다음 회의 안건 / 미결 사항 (Open Issues)\n"
        "- 추후 추가 논의나 확인이 필요한 사항"
    ),
    "idea": (
        "당신은 연구자의 음성 메모를 체계적인 연구 노트로 발전시켜 주는 AI 파트너입니다.\n"
        "제공된 음성 메모 내용을 바탕으로 아래 구조에 맞춰 마크다운 문서를 작성해주세요.\n\n"
        "# 💡 연구/아이디어 노트: {title}\n"
        "- **일시**: {date}\n"
        "- **사용 모델**: `{model_label}`\n\n"
        "## 1. 핵심 아이디어 요약\n"
        "## 2. 세부 내용 및 논리 전개\n"
        "## 3. 구체화/검증을 위한 Next Steps\n"
        "## 4. 원본 음성 주요 발언 정리"
    ),
    "transcript": (
        "첨부된 오디오 파일의 내용을 처음부터 끝까지 빠짐없이 정확하게 받아적어 마크다운 **전체 녹취록(Transcript)**을 작성해주세요.\n"
        "# 📜 전체 녹취록: {title}\n"
        "- **일시**: {date}\n"
        "- **사용 모델**: `{model_label}`\n\n"
        "- 화자가 구분된다면 `**화자 1**`, `**화자 2**` (또는 발표자/질문자) 형태로 구분하고 대략적인 타임스탬프(`[MM:SS]`)를 함께 표시해주세요.\n"
        "- 학술/전공 용어(영어 약어 등)는 문맥에 맞게 정확한 표기로 교정해주세요."
    ),
}

MODEL_PRESETS: Dict[str, Dict[str, str]] = {
    "opus_combo": {
        "label": "🌟 Gemini 3.1 Pro (청취) + Claude Opus 4.6 (집필)",
        "audio_model": "gemini-3.1-pro-preview",
        "writer_provider": "anthropic",
        "writer_model": "claude-opus-4-6",
    },
    "sonnet_combo": {
        "label": "💎 Gemini 3.1 Pro (청취) + Claude Sonnet 4.6 (집필)",
        "audio_model": "gemini-3.1-pro-preview",
        "writer_provider": "anthropic",
        "writer_model": "claude-sonnet-4-6",
    },
    "gpt52_combo": {
        "label": "🔥 Gemini 3.1 Pro (청취) + GPT-5.2 (집필)",
        "audio_model": "gemini-3.1-pro-preview",
        "writer_provider": "openai",
        "writer_model": "gpt-5.2-gs-2025-12-11",
    },
    "gemini31_pro": {
        "label": "⚡ Gemini 3.1 Pro 단독 (원스톱 멀티모달)",
        "audio_model": "gemini-3.1-pro-preview",
        "writer_provider": "none",
        "writer_model": "",
    },
    "gemini3_flash": {
        "label": "🚀 Gemini 3 Flash 단독 (초고속 요약)",
        "audio_model": "gemini-3-flash-preview",
        "writer_provider": "none",
        "writer_model": "",
    },
}


def detect_mime_type(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    mapping = {
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
        ".webm": "audio/webm",
    }
    return mapping.get(ext) or mimetypes.guess_type(str(file_path))[0] or "audio/mp4"


def compress_audio_if_large(file_path: Path, max_mb: float = 12.0) -> tuple[Path, bool]:
    """
    오디오 파일이 12MB 이상(예: 1시간짜리 30MB~80MB 녹음)인 경우,
    음성 인식 품질은 유지하면서 용량만 1/3 이하로 초고속 다이어트하여 업로드 속도와 안정성을 높입니다.
    반환값: (사용할_파일_경로, 임시파일_여부)
    """
    size_mb = file_path.stat().st_size / (1024 * 1024)
    if size_mb <= max_mb:
        return file_path, False

    tmp_out = Path(tempfile.gettempdir()) / f"compact_{uuid.uuid4().hex}.m4a"
    try:
        # 1. macOS 내장 afconvert 사용 (HE-AAC 모노 20kbps: 1시간 녹음 = 약 9MB)
        if shutil.which("afconvert"):
            subprocess.run(
                [
                    "afconvert",
                    "-f",
                    "m4af",
                    "-d",
                    "aach",
                    "-b",
                    "20000",
                    "-c",
                    "1",
                    str(file_path),
                    str(tmp_out),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if tmp_out.exists() and tmp_out.stat().st_size > 0:
                return tmp_out, True
        # 2. Windows/Linux ffmpeg 사용 가능 시
        elif shutil.which("ffmpeg"):
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(file_path),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-b:a",
                    "24k",
                    str(tmp_out),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if tmp_out.exists() and tmp_out.stat().st_size > 0:
                return tmp_out, True
    except Exception as e:
        print(f"오디오 압축 스킵 (원본 사용): {e}")

    return file_path, False


def upload_audio_to_postech_api(file_path: Path, api_key: str) -> Dict[str, str]:
    """포스텍 AI API Google File 업로드 엔드포인트(a61/google/files)로 오디오 업로드"""
    mime_type = detect_mime_type(file_path)
    boundary = f"----PostechAudioBoundary{uuid.uuid4().hex}"
    file_bytes = file_path.read_bytes()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        "https://genai.postech.ac.kr/agent/api/a61/google/files",
        data=body,
        headers={
            "x-api-key": api_key,
            "Authorization": api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180, context=_get_ssl_context()) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        return {
            "uri": res_data["uri"],
            "mime_type": res_data.get("mimeType") or mime_type,
            "name": res_data.get("name", ""),
        }


def delete_audio_from_postech_api(file_name: str, api_key: str) -> None:
    """처리 완료 후 서버 임시 오디오 파일 삭제(a64)"""
    if not file_name:
        return
    clean_name = file_name.split("/")[-1]
    url = f"https://genai.postech.ac.kr/agent/api/a64/google/files/{clean_name}"
    req = urllib.request.Request(
        url,
        headers={"x-api-key": api_key, "Authorization": api_key},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_get_ssl_context()):
            pass
    except Exception:
        pass


def _call_gemini_audio(
    uploaded: Dict[str, str], prompt: str, model: str, api_key: str
) -> str:
    url = f"https://genai.postech.ac.kr/agent/api/a51/google/models/{model}:generateContent"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "file_data": {
                            "file_uri": uploaded["uri"],
                            "mime_type": uploaded["mime_type"],
                        }
                    },
                    {"text": prompt},
                ],
            }
        ],
        "config": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "Authorization": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300, context=_get_ssl_context()) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        return res_data["candidates"][0]["content"]["parts"][0]["text"]


def _call_claude_writer(prompt: str, model: str, api_key: str) -> str:
    """포스텍 Anthropic 엔드포인트(a45) 호출 (temperature 인자 미지원 대응)"""
    url = "https://genai.postech.ac.kr/agent/api/a45/anthropic/messages"
    payload = {
        "model": model,
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "Authorization": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300, context=_get_ssl_context()) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        return res_data["content"][0]["text"]


def _call_openai_writer(prompt: str, model: str, api_key: str) -> str:
    url = "https://genai.postech.ac.kr/agent/api/a11/openai/responses"
    payload = {
        "model": model,
        "input": prompt,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "Authorization": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300, context=_get_ssl_context()) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        for item in res_data.get("output", []):
            for c in item.get("content", []):
                if c.get("type") == "output_text" and c.get("text"):
                    return c["text"]
        return json.dumps(res_data, ensure_ascii=False)


def process_audio_file(
    file_path: Path,
    mode: str = "seminar",
    custom_instruction: str = "",
    preset_key: str = "opus_combo",
) -> str:
    """
    선택된 모델 프리셋(예: Gemini 3.1 Pro + Claude Opus 4.6 등)으로
    오디오 파일을 분석하여 마크다운 문서를 생성 및 반환합니다.
    """
    base_dir = Path(__file__).resolve().parent.parent
    env_vars = {**os.environ, **load_env(base_dir / ".env")}
    api_key = env_vars.get("AI_API_KEY", "").strip()
    if not api_key:
        raise ValueError(".env 파일에 AI_API_KEY가 설정되어 있지 않습니다.")

    preset = MODEL_PRESETS.get(preset_key, MODEL_PRESETS["opus_combo"])
    audio_model = preset["audio_model"]
    writer_provider = preset["writer_provider"]
    writer_model = preset["writer_model"]
    model_label = preset["label"]

    today_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    target_prompt = PROMPTS.get(mode, PROMPTS["seminar"]).format(
        title=file_path.stem,
        date=today_str,
        model_label=model_label,
    )
    if custom_instruction:
        target_prompt += f"\n\n[사용자 추가 요청사항]: {custom_instruction}"

    upload_target, is_temp = compress_audio_if_large(file_path)
    uploaded = {}
    try:
        uploaded = upload_audio_to_postech_api(upload_target, api_key)

        if writer_provider == "none" or mode == "transcript":
            result_text = _call_gemini_audio(uploaded, target_prompt, audio_model, api_key)
        else:
            extract_prompt = (
                "첨부된 오디오를 처음부터 끝까지 주의 깊게 듣고, 발표/회의의 모든 핵심 발언, 세부 논리, "
                "수식/기술 용어, 수치 데이터, 질문/답변, 결정 사항을 빠짐없이 상세하게 텍스트로 추출해주세요."
            )
            extracted_notes = _call_gemini_audio(uploaded, extract_prompt, audio_model, api_key)

            combined_prompt = (
                f"{target_prompt}\n\n"
                f"--- [오디오 정밀 추출 원문 및 상세 노트] ---\n{extracted_notes}"
            )
            if writer_provider == "anthropic":
                result_text = _call_claude_writer(combined_prompt, writer_model, api_key)
            elif writer_provider == "openai":
                result_text = _call_openai_writer(combined_prompt, writer_model, api_key)
            else:
                result_text = extracted_notes
    finally:
        if uploaded.get("name"):
            delete_audio_from_postech_api(uploaded["name"], api_key)
        if is_temp and upload_target.exists():
            try:
                upload_target.unlink()
            except Exception:
                pass

    notes_dir = base_dir / "notes"
    notes_dir.mkdir(exist_ok=True)
    date_prefix = datetime.date.today().strftime("%Y-%m-%d")
    out_path = notes_dir / f"{date_prefix}_{mode}_{file_path.stem}.md"
    out_path.write_text(result_text, encoding="utf-8")

    return result_text
