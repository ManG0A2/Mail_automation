import asyncio
import http.cookiejar
import io
import os
from pathlib import Path
import re
import ssl
import tempfile
from typing import Optional, Set
import urllib.parse
import urllib.request

import aiohttp
import discord
from discord.ext import commands

from audio_processor import MODEL_PRESETS, _get_ssl_context, load_env, process_audio_file

ssl._create_default_https_context = _get_ssl_context
if aiohttp.TCPConnector.__init__.__kwdefaults__ is not None:
    aiohttp.TCPConnector.__init__.__kwdefaults__["ssl"] = False

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".flac", ".mp4", ".webm", ".caf"}
DEFAULT_CHANNEL_ID = 1562640017371697276

MODE_LABELS = {
    "seminar": "📊 세미나 레포트",
    "meeting": "📝 회의록",
    "idea": "💡 아이디어/연구노트",
    "transcript": "📜 전체 녹취록",
}

GDRIVE_REGEX = re.compile(
    r"https?://drive\.google\.com/(?:file/d/|open\?id=|uc\?id=)([a-zA-Z0-9_-]+)"
)
URL_REGEX = re.compile(r"https?://[^\s<>\"']+")


def download_from_url_or_gdrive(url: str, dest_dir: Path) -> Path:
    ctx = _get_ssl_context()
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(cookie_jar),
    )
    opener.addheaders = [
        (
            "User-Agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )
    ]

    m = GDRIVE_REGEX.search(url)
    if m:
        file_id = m.group(1)
        dl_url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t"
        resp = opener.open(dl_url, timeout=180)
        content_type = resp.headers.get("Content-Type", "")

        if "text/html" in content_type:
            html_text = resp.read().decode("utf-8", errors="ignore")
            uuid_match = re.search(r'name="uuid"\s+value="([^"]+)"', html_text)
            confirm_match = re.search(r'name="confirm"\s+value="([^"]+)"', html_text)
            if uuid_match:
                confirm_val = confirm_match.group(1) if confirm_match else "t"
                dl_url = (
                    f"https://drive.usercontent.google.com/download?id={file_id}"
                    f"&export=download&confirm={confirm_val}&uuid={uuid_match.group(1)}"
                )
                resp = opener.open(dl_url, timeout=180)

        cd = resp.headers.get("Content-Disposition", "")
        fname_match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]+)"?', cd)
        if fname_match:
            filename = urllib.parse.unquote(fname_match.group(1).strip('"'))
        else:
            filename = f"gdrive_{file_id}.m4a"

        if not Path(filename).suffix:
            filename += ".m4a"

        out_path = dest_dir / filename
        out_path.write_bytes(resp.read())
        return out_path

    resp = opener.open(url, timeout=180)
    parsed_path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
    filename = Path(parsed_path).name or "recording.m4a"
    if not Path(filename).suffix:
        filename += ".m4a"
    out_path = dest_dir / filename
    out_path.write_bytes(resp.read())
    return out_path


class AudioModeView(discord.ui.View):
    """모델 선택 드롭다운 + 양식 선택 버튼 4개를 제공하는 디스코드 UI"""

    def __init__(
        self,
        attachment: Optional[discord.Attachment] = None,
        source_url: Optional[str] = None,
        local_file: Optional[Path] = None,
        custom_note: str = "",
    ):
        super().__init__(timeout=7200)
        self.attachment = attachment
        self.source_url = source_url
        self.local_file = local_file
        self.custom_note = custom_note
        self.selected_preset: str = "opus_combo"

    def _display_name(self) -> str:
        if self.attachment:
            return self.attachment.filename
        if self.local_file:
            return self.local_file.name
        if self.source_url:
            return "클라우드 공유 녹음 파일"
        return "녹음 파일"

    @discord.ui.select(
        placeholder="🤖 사용할 AI 모델 조합 선택 (기본: Gemini 3.1 Pro + Claude Opus 4.6)",
        min_values=1,
        max_values=1,
        row=0,
        options=[
            discord.SelectOption(
                label="Gemini 3.1 Pro + Claude Opus 4.6 (최고급 추천)",
                value="opus_combo",
                emoji="🌟",
                description="Gemini 3.1 Pro가 음성 청취 후 Claude Opus 4.6이 보고서 집필",
                default=True,
            ),
            discord.SelectOption(
                label="Gemini 3.1 Pro + Claude Sonnet 4.6",
                value="sonnet_combo",
                emoji="💎",
                description="Gemini 3.1 Pro 음성 청취 + Claude Sonnet 4.6 빠른 집필",
            ),
            discord.SelectOption(
                label="Gemini 3.1 Pro + GPT-5.2",
                value="gpt52_combo",
                emoji="🔥",
                description="Gemini 3.1 Pro 음성 청취 + OpenAI GPT-5.2 구조화",
            ),
            discord.SelectOption(
                label="Gemini 3.1 Pro 단독 (원스톱 멀티모달)",
                value="gemini31_pro",
                emoji="⚡",
                description="Gemini 3.1 Pro가 오디오 청취부터 마크다운 작성까지 한 번에 처리",
            ),
            discord.SelectOption(
                label="Gemini 3 Flash 단독 (초고속 요약)",
                value="gemini3_flash",
                emoji="🚀",
                description="가장 빠른 속도로 결과를 확인하고 싶을 때 선택",
            ),
        ],
    )
    async def select_model(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected_preset = select.values[0]
        preset_label = MODEL_PRESETS[self.selected_preset]["label"]
        for opt in select.options:
            opt.default = opt.value == self.selected_preset
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            f"🤖 모델이 **`{preset_label}`**(으)로 선택되었습니다! 이제 아래 양식 버튼을 눌러주세요.",
            ephemeral=True,
        )

    async def _handle_selection(self, interaction: discord.Interaction, mode: str):
        label = MODE_LABELS.get(mode, mode)
        disp = self._display_name()
        preset_label = MODEL_PRESETS.get(self.selected_preset, MODEL_PRESETS["opus_combo"])["label"]

        await interaction.response.send_message(
            f"⏳ **`{disp}`** 분석 시작!\n"
            f"- **양식**: {label}\n"
            f"- **모델**: `{preset_label}`\n"
            f"- **요청자**: {interaction.user.mention}\n"
            f"*(완료되면 깔끔한 `.md` 마크다운 파일로 첨부해 드립니다)*"
        )

        tmp_dir = Path(tempfile.mkdtemp(prefix="postech_voice_"))
        local_audio_path: Optional[Path] = None

        try:
            if self.attachment is not None:
                local_audio_path = tmp_dir / self.attachment.filename
                await self.attachment.save(local_audio_path)
            elif self.source_url is not None:
                local_audio_path = await asyncio.to_thread(
                    download_from_url_or_gdrive, self.source_url, tmp_dir
                )
            elif self.local_file is not None:
                local_audio_path = self.local_file

            if local_audio_path is None or not local_audio_path.exists():
                raise RuntimeError("오디오 파일을 찾을 수 없습니다.")

            result_md = await asyncio.to_thread(
                process_audio_file,
                local_audio_path,
                mode,
                self.custom_note,
                self.selected_preset,
            )

            md_filename = f"{local_audio_path.stem}_{mode}.md"
            md_file = discord.File(
                fp=io.BytesIO(result_md.encode("utf-8")),
                filename=md_filename,
            )

            # 채팅창 도배 없이 핵심 프리뷰(앞 500자) + .md 파일 첨부로 깔끔하게 반환
            preview_lines = "\n".join(result_md.splitlines()[:12])[:500]
            msg = (
                f"✅ **{label} 생성 완료!** ({interaction.user.mention})\n"
                f"- **원본 파일**: `{local_audio_path.name}`\n"
                f"- **사용 모델**: `{preset_label}`\n"
                f"- **결과 파일**: 아래 첨부된 **`{md_filename}`** 파일을 클릭/다운로드하세요! 👇\n\n"
                f"```markdown\n{preview_lines}\n...\n```"
            )
            await interaction.followup.send(content=msg, file=md_file)

        except Exception as e:
            await interaction.followup.send(f"❌ 음성 분석 중 오류가 발생했습니다: `{e}`")
        finally:
            try:
                if local_audio_path and local_audio_path.parent == tmp_dir and local_audio_path.exists():
                    local_audio_path.unlink()
                if tmp_dir.exists():
                    tmp_dir.rmdir()
            except Exception:
                pass

    @discord.ui.button(label="📊 세미나 레포트", style=discord.ButtonStyle.primary, row=1)
    async def btn_seminar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_selection(interaction, "seminar")

    @discord.ui.button(label="📝 회의록", style=discord.ButtonStyle.success, row=1)
    async def btn_meeting(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_selection(interaction, "meeting")

    @discord.ui.button(label="💡 아이디어/연구노트", style=discord.ButtonStyle.secondary, row=1)
    async def btn_idea(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_selection(interaction, "idea")

    @discord.ui.button(label="📜 전체 녹취록", style=discord.ButtonStyle.secondary, row=1)
    async def btn_transcript(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_selection(interaction, "transcript")


def create_bot(base_dir: Path) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    inbox_dir = base_dir / "recordings_inbox"
    inbox_dir.mkdir(exist_ok=True)
    seen_files: Set[str] = {p.name for p in inbox_dir.iterdir() if p.is_file()}

    async def watch_inbox_folder():
        await bot.wait_until_ready()
        while not bot.is_closed():
            try:
                current_names = {p.name for p in inbox_dir.iterdir() if p.is_file()}
                # 폴더에서 빠진 파일은 기억에서 지워서, 같은 이름으로 다시 넣으면 재감지되도록 함
                seen_files.intersection_update(current_names)
                for fpath in sorted(inbox_dir.iterdir()):
                    if (
                        fpath.is_file()
                        and fpath.suffix.lower() in AUDIO_EXTENSIONS
                        and fpath.name not in seen_files
                    ):
                        seen_files.add(fpath.name)
                        channel = bot.get_channel(DEFAULT_CHANNEL_ID)
                        if channel is None:
                            for g in bot.guilds:
                                for ch in g.text_channels:
                                    if ch.permissions_for(g.me).send_messages:
                                        channel = ch
                                        break
                                if channel:
                                    break
                        if channel is not None:
                            size_mb = fpath.stat().st_size / (1024 * 1024)
                            view = AudioModeView(local_file=fpath)
                            await channel.send(
                                f"📂 **`recordings_inbox` 폴더**에 대용량 녹음 파일이 들어왔습니다!\n"
                                f"🎙️ 파일명: **`{fpath.name}`** (`{size_mb:.1f} MB`)\n"
                                f"모델을 선택하고 원하는 양식 버튼을 눌러주세요 👇",
                                view=view,
                            )
            except Exception as e:
                print(f"Inbox watcher error: {e}")
            await asyncio.sleep(3)

    @bot.event
    async def on_ready():
        print(f"🎙️ 포스텍 음성 메모 정리 봇 온라인: {bot.user}")
        bot.loop.create_task(watch_inbox_folder())

    @bot.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return

        audio_attachments = [
            att
            for att in message.attachments
            if Path(att.filename).suffix.lower() in AUDIO_EXTENSIONS
            or (att.content_type and att.content_type.startswith("audio/"))
        ]

        for att in audio_attachments:
            view = AudioModeView(attachment=att, custom_note=message.content.strip())
            await message.reply(
                f"🎙️ **`{att.filename}`** ({att.size / (1024*1024):.1f} MB) 녹음 파일이 감지되었습니다!\n"
                f"모델을 선택(기본: `Gemini 3.1 Pro + Claude Opus 4.6`)하고 양식 버튼을 눌러주세요 👇",
                view=view,
            )

        if not audio_attachments and message.content:
            urls = URL_REGEX.findall(message.content)
            for u in urls:
                is_gdrive = bool(GDRIVE_REGEX.search(u))
                is_audio_url = any(u.lower().split("?")[0].endswith(ext) for ext in AUDIO_EXTENSIONS)
                if is_gdrive or is_audio_url:
                    clean_note = URL_REGEX.sub("", message.content).strip()
                    view = AudioModeView(source_url=u, custom_note=clean_note)
                    await message.reply(
                        "☁️ **대용량 클라우드 녹음 링크**가 감지되었습니다!\n"
                        "모델을 선택하고 원하는 양식 버튼을 눌러주세요 👇",
                        view=view,
                    )
                    break

        await bot.process_commands(message)

    return bot


if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent.parent
    env_vars = {**os.environ, **load_env(base_dir / ".env")}
    bot_token = env_vars.get("DISCORD_BOT_TOKEN", "").strip()
    if not bot_token:
        print("⚠️ .env 파일에 DISCORD_BOT_TOKEN을 설정해주세요.")
    else:
        bot = create_bot(base_dir)
        bot.run(bot_token)
