import datetime
import email
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import imaplib
import json
import os
from pathlib import Path
import re
import ssl
from typing import Any, Dict, List


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


class HTMLTextExtractor(HTMLParser):
    """HTML 본문에서 태그를 제거하고 깔끔한 텍스트만 추출하는 파서"""

    def __init__(self):
        super().__init__()
        self.result: List[str] = []
        self._ignore = False

    def handle_starttag(self, tag: str, attrs: list):
        if tag in ("script", "style", "head", "title", "meta"):
            self._ignore = True
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4"):
            self.result.append("\n")

    def handle_endtag(self, tag: str):
        if tag in ("script", "style", "head", "title", "meta"):
            self._ignore = False

    def handle_data(self, data: str):
        if not self._ignore:
            self.result.append(data)

    def get_text(self) -> str:
        raw = "".join(self.result)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
        cleaned = "\n".join(line for line in lines if line)
        return cleaned


def load_env(env_path: Path) -> Dict[str, str]:
    """외부 패키지 없이 .env 파일을 파싱 (Mac/Windows/Linux 공통)"""
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


def decode_mime_str(value: str) -> str:
    """이메일 제목/발신자 MIME 인코딩 디코딩"""
    if not value:
        return ""
    decoded_parts = decode_header(value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            for enc in (charset, "utf-8", "euc-kr", "cp949", "latin1"):
                if not enc:
                    continue
                try:
                    result.append(part.decode(enc, errors="replace"))
                    break
                except LookupError:
                    continue
            else:
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result).strip()


def extract_body(msg: email.message.Message, max_chars: int = 1200) -> str:
    """메일 본문에서 텍스트 추출 (토큰 절약을 위해 핵심 앞부분 위주로 길이 제한)"""
    plain_text = ""
    html_text = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in content_disposition:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except LookupError:
                decoded = payload.decode("utf-8", errors="replace")

            if content_type == "text/plain" and not plain_text:
                plain_text = decoded
            elif content_type == "text/html" and not html_text:
                html_text = decoded
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except LookupError:
                decoded = payload.decode("utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                html_text = decoded
            else:
                plain_text = decoded

    if plain_text.strip():
        text = re.sub(r"\n{3,}", "\n\n", plain_text.strip())
    elif html_text.strip():
        parser = HTMLTextExtractor()
        parser.feed(html_text)
        text = parser.get_text()
    else:
        text = "(본문 없음)"

    return text[:max_chars]


def _kw_match(kw: str, text: str) -> bool:
    """영문 짧은 단어(예: AI)가 mail/email 내부에 오탐되는 현상 방지"""
    kw_low = kw.lower()
    if re.fullmatch(r"[a-z0-9]+", kw_low):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(kw_low)}(?![a-z0-9])", text.lower()))
    return kw_low in text.lower()


def evaluate_email(item: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    로컬 규칙 기반 1차 필터링 및 관심도 점수 계산 (API 비용 0원)
    """
    subject = item.get("subject", "")
    sender = item.get("sender", "")
    body = item.get("body", "")
    combined = f"{subject}\n{body}".lower()

    # 1. 스팸/광고 제외 검사
    for ex_sender in config.get("exclude_senders", []):
        if ex_sender.lower() in sender.lower():
            return {**item, "keep": False, "reason": f"제외 발신자({ex_sender})"}

    for ex_kw in config.get("exclude_keywords", []):
        if ex_kw.lower() in subject.lower() or ex_kw.lower() in body.lower():
            if "postech.ac.kr" not in combined and "포스텍" not in combined:
                return {**item, "keep": False, "reason": f"제외 키워드({ex_kw})"}

    # 2. 관심 카테고리 및 키워드 점수 산정
    matched_categories: List[str] = []
    matched_keywords: List[str] = []
    score = 0

    if (
        item.get("source") == "POSTECH"
        or "postech.ac.kr" in combined
        or "postech.ac.kr" in sender.lower()
        or "포항공과대학교" in combined
        or "포스텍" in combined
    ):
        score += 3
        matched_keywords.append("POSTECH")

    custom_interests = config.get("user_profile", {}).get("custom_interests", [])
    for kw in custom_interests:
        if _kw_match(kw, combined):
            score += 4
            matched_keywords.append(kw)

    categories = config.get("categories", {})
    for cat_name, kw_list in categories.items():
        cat_hit = False
        for kw in kw_list:
            if _kw_match(kw, subject):
                score += 3
                matched_keywords.append(kw)
                cat_hit = True
            elif _kw_match(kw, combined):
                score += 1
                matched_keywords.append(kw)
                cat_hit = True
        if cat_hit:
            matched_categories.append(cat_name)

    matched_keywords = list(dict.fromkeys(matched_keywords))
    matched_categories = list(dict.fromkeys(matched_categories))

    return {
        **item,
        "keep": True,
        "score": score,
        "matched_categories": matched_categories or ["기타_일반"],
        "matched_keywords": matched_keywords,
    }


def fetch_from_imap_folder(
    account_name: str,
    imap_server: str,
    email_user: str,
    email_pass: str,
    mailbox: str = "INBOX",
    lookback_hours: int = 24,
    delete_after_fetch: bool = False,
    auto_ensure_folder: bool = False,
) -> List[Dict[str, Any]]:
    """
    지정된 IMAP 메일함에서 최근 N시간 메일 수집.
    auto_ensure_folder=True 인 경우 폴더가 없으면 자동으로 생성합니다.
    """
    if not email_user or not email_pass or "your_" in email_user:
        return []

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=lookback_hours)
    since_date_str = (datetime.date.today() - datetime.timedelta(days=2)).strftime("%d-%b-%Y")

    collected: List[Dict[str, Any]] = []
    try:
        mail = imaplib.IMAP4_SSL(imap_server, ssl_context=_get_ssl_context())
        mail.login(email_user, email_pass)

        status, _ = mail.select(f'"{mailbox}"', readonly=not delete_after_fetch)
        if status != "OK":
            if auto_ensure_folder:
                mail.create(f'"{mailbox}"')
                status, _ = mail.select(f'"{mailbox}"', readonly=not delete_after_fetch)
            if status != "OK":
                mail.logout()
                return []

        status, data = mail.search(None, f'(SINCE "{since_date_str}")')
        if status != "OK" or not data or not data[0]:
            mail.logout()
            return []

        msg_ids = data[0].split()
        ids_to_delete = []

        for msg_id in reversed(msg_ids[-80:]):
            res, msg_data = mail.fetch(msg_id, "(RFC822)")
            if res != "OK" or not msg_data or not msg_data[0]:
                continue
            raw_email = msg_data[0][1]
            if not isinstance(raw_email, bytes):
                continue

            msg = email.message_from_bytes(raw_email)
            date_hdr = msg.get("Date")
            msg_dt = None
            if date_hdr:
                try:
                    msg_dt = parsedate_to_datetime(date_hdr)
                    if msg_dt.tzinfo is None:
                        msg_dt = msg_dt.replace(tzinfo=datetime.timezone.utc)
                except Exception:
                    msg_dt = None

            if msg_dt and msg_dt < cutoff:
                if delete_after_fetch:
                    ids_to_delete.append(msg_id)
                continue

            subject = decode_mime_str(msg.get("Subject", "(제목 없음)"))
            sender = decode_mime_str(msg.get("From", ""))
            to_hdr = decode_mime_str(msg.get("To", ""))
            body = extract_body(msg)

            # INBOX에 들어왔더라도 받는사람/보낸사람이 postech.ac.kr이면 자동으로 POSTECH 출처로 태깅
            detected_source = account_name
            if "postech.ac.kr" in sender.lower() or "postech.ac.kr" in to_hdr.lower():
                detected_source = "POSTECH"

            local_dt_str = (
                msg_dt.astimezone().strftime("%Y-%m-%d %H:%M")
                if msg_dt
                else datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            )

            collected.append(
                {
                    "source": detected_source,
                    "subject": subject,
                    "sender": sender,
                    "date": local_dt_str,
                    "body": body,
                }
            )
            if delete_after_fetch:
                ids_to_delete.append(msg_id)

        if delete_after_fetch and ids_to_delete:
            for mid in ids_to_delete:
                mail.store(mid, "+FLAGS", "\\Deleted")
            mail.expunge()
            print(f"🧹 [{mailbox}] 격리 폴더에서 수집 완료된 전달본 {len(ids_to_delete)}통 자동 정리 완료")

        mail.logout()
    except Exception as e:
        print(f"[{account_name}/{mailbox}] IMAP 연결/수집 오류: {e}")

    return collected


def run_fetch(base_dir: Path) -> Dict[str, Any]:
    """전체 계정/폴더에서 메일을 수집하고 1차 로컬 필터링 결과를 data/latest_candidates.json에 저장"""
    config_path = base_dir / "config.json"
    env_path = base_dir / ".env"
    data_dir = base_dir / "data"
    data_dir.mkdir(exist_ok=True)

    config = json.loads(config_path.read_text(encoding="utf-8"))
    env_vars = {**os.environ, **load_env(env_path)}
    lookback_hours = int(config.get("lookback_hours", 24))

    raw_emails: List[Dict[str, Any]] = []

    naver_server = env_vars.get("NAVER_IMAP_SERVER", "imap.naver.com")
    naver_user = env_vars.get("NAVER_EMAIL", "")
    naver_pass = env_vars.get("NAVER_APP_PASSWORD", "")
    postech_bot_folder = env_vars.get("NAVER_POSTECH_FOLDER", "")
    auto_clean_folder = env_vars.get("AUTO_CLEAN_POSTECH_FOLDER", "true").lower() == "true"

    # 1. 네이버 기본 받은편지함(INBOX) 및 청구·결제 스마트메일함 수집 (절대 삭제 안 함)
    for naver_box in ("INBOX", "&zK2tbAC3rLDIHA-"):
        raw_emails.extend(
            fetch_from_imap_folder(
                account_name="Naver",
                imap_server=naver_server,
                email_user=naver_user,
                email_pass=naver_pass,
                mailbox=naver_box,
                lookback_hours=lookback_hours,
                delete_after_fetch=False,
            )
        )

    # 2. 네이버 내 포스텍 격리 폴더(POSTECH_BOT) 수집 (폴더가 없으면 파이썬이 자동 생성!)
    if postech_bot_folder:
        raw_emails.extend(
            fetch_from_imap_folder(
                account_name="POSTECH",
                imap_server=naver_server,
                email_user=naver_user,
                email_pass=naver_pass,
                mailbox=postech_bot_folder,
                lookback_hours=lookback_hours,
                delete_after_fetch=auto_clean_folder,
                auto_ensure_folder=True,
            )
        )

    # 3. 포스텍 메일 직접 IMAP 수집 (직접 연결 설정 시)
    raw_emails.extend(
        fetch_from_imap_folder(
            account_name="POSTECH",
            imap_server=env_vars.get("POSTECH_IMAP_SERVER", "outlook.office365.com"),
            email_user=env_vars.get("POSTECH_EMAIL", ""),
            email_pass=env_vars.get("POSTECH_PASSWORD", ""),
            mailbox="INBOX",
            lookback_hours=lookback_hours,
            delete_after_fetch=False,
        )
    )

    # 중복 메일 제거
    deduped: List[Dict[str, Any]] = []
    seen_keys = set()
    for item in raw_emails:
        norm_subj = re.sub(r"^(fw:|fwd:|전달:)\s*", "", item["subject"].strip(), flags=re.I)
        key = (norm_subj, item["date"][:13])
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(item)

    evaluated = [evaluate_email(item, config) for item in deduped]
    candidates = [e for e in evaluated if e.get("keep")]
    excluded = [e for e in evaluated if not e.get("keep")]

    candidates.sort(key=lambda x: x.get("score", 0), reverse=True)

    output = {
        "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "lookback_hours": lookback_hours,
        "total_fetched": len(deduped),
        "candidates_count": len(candidates),
        "excluded_count": len(excluded),
        "user_profile": config.get("user_profile", {}),
        "candidates": candidates,
    }

    out_file = data_dir / "latest_candidates.json"
    out_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"✅ 메일 수집 완료: 총 {len(deduped)}통 중 관심 후보 {len(candidates)}통 선별 (제외 {len(excluded)}통)"
    )
    print(f"📄 저장 위치: {out_file}")
    return output


if __name__ == "__main__":
    run_fetch(Path(__file__).resolve().parent.parent)
