from __future__ import annotations

import argparse
import base64
import email
import hashlib
import imaplib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from email import policy
from email.message import Message
from pathlib import Path
from typing import Any


IMAP_MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


@dataclass(frozen=True)
class MailConfig:
    email_address: str
    app_password: str
    imap_host: str
    imap_port: int
    folder: str
    lookback_months: int
    output_dir: Path
    processed_action: str
    manifest_path: Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Download PDF printouts from Mail.ru IMAP.")
    parser.add_argument("--env", default=".env", help="Path to local .env file.")
    parser.add_argument("--folder", help="Mailbox folder name. Defaults to MAILRU_FOLDER.")
    parser.add_argument("--months", type=int, help="How many months back to collect.")
    parser.add_argument("--since", help="Explicit start date in YYYY-MM-DD format.")
    parser.add_argument("--output-dir", help="Folder for downloaded PDF attachments.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Connect and count matching messages without saving attachments.",
    )
    parser.add_argument(
        "--list-folders",
        action="store_true",
        help="Print mailbox folders and exit.",
    )
    args = parser.parse_args()

    load_env_file(Path(args.env))
    config = load_config(args)
    if args.list_folders:
        list_folders(config)
        return

    since = datetime.strptime(args.since, "%Y-%m-%d").date() if args.since else subtract_months(
        date.today(), config.lookback_months
    )

    result = collect_mailru_pdfs(config, since=since, dry_run=args.dry_run)
    print(f"Folder: {config.folder}")
    print(f"Since: {since.isoformat()}")
    print(f"Matched messages: {result['messages']}")
    print(f"PDF attachments found: {result['pdf_attachments']}")
    print(f"Downloaded: {result['downloaded']}")
    print(f"Skipped duplicates: {result['duplicates']}")
    print(f"Output dir: {config.output_dir.resolve()}")
    print("Mailbox action: leave messages unchanged")


def load_config(args: argparse.Namespace) -> MailConfig:
    email_address = os.getenv("MAILRU_EMAIL", "").strip()
    app_password = os.getenv("MAILRU_APP_PASSWORD", "").strip()
    if not email_address:
        raise SystemExit("MAILRU_EMAIL is missing in .env")
    if not app_password:
        raise SystemExit("MAILRU_APP_PASSWORD is empty in .env")

    output_dir = Path(args.output_dir or os.getenv("MAILRU_OUTPUT_DIR", "mailru_pdfs"))
    return MailConfig(
        email_address=email_address,
        app_password=app_password,
        imap_host=os.getenv("MAILRU_IMAP_HOST", "imap.mail.ru").strip(),
        imap_port=int(os.getenv("MAILRU_IMAP_PORT", "993")),
        folder=args.folder or os.getenv("MAILRU_FOLDER", "Распечатки").strip(),
        lookback_months=args.months or int(os.getenv("MAILRU_LOOKBACK_MONTHS", "2")),
        output_dir=output_dir,
        processed_action=os.getenv("MAILRU_PROCESSED_ACTION", "leave").strip(),
        manifest_path=Path(os.getenv("MAILRU_MANIFEST", "mailru_downloads.json")),
    )


def collect_mailru_pdfs(
    config: MailConfig, since: date, dry_run: bool = False
) -> dict[str, int]:
    if config.processed_action != "leave":
        raise SystemExit("Only MAILRU_PROCESSED_ACTION=leave is supported in this MVP.")

    config.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(config.manifest_path)
    known_hashes = {
        item.get("sha256")
        for item in manifest.get("attachments", [])
        if item.get("sha256")
    }

    stats = {
        "messages": 0,
        "pdf_attachments": 0,
        "downloaded": 0,
        "duplicates": 0,
    }

    with imaplib.IMAP4_SSL(config.imap_host, config.imap_port) as imap:
        imap.login(config.email_address, config.app_password)
        select_folder(imap, config.folder)
        message_uids = search_since(imap, since)
        stats["messages"] = len(message_uids)

        for uid in message_uids:
            message = fetch_message_without_seen(imap, uid)
            subject = str(message.get("subject", ""))
            message_date = str(message.get("date", ""))

            for attachment_index, attachment in enumerate(iter_pdf_attachments(message), start=1):
                stats["pdf_attachments"] += 1
                filename = attachment["filename"]
                content = attachment["content"]
                digest = hashlib.sha256(content).hexdigest()
                if digest in known_hashes:
                    stats["duplicates"] += 1
                    continue

                if dry_run:
                    continue

                saved_path = save_attachment(
                    config.output_dir,
                    uid=uid.decode("ascii", errors="ignore"),
                    attachment_index=attachment_index,
                    filename=filename,
                    content=content,
                    digest=digest,
                )
                known_hashes.add(digest)
                manifest.setdefault("attachments", []).append(
                    {
                        "uid": uid.decode("ascii", errors="ignore"),
                        "filename": filename,
                        "saved_path": str(saved_path),
                        "sha256": digest,
                        "subject": subject,
                        "message_date": message_date,
                        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
                stats["downloaded"] += 1

        if not dry_run:
            config.manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        imap.logout()

    return stats


def list_folders(config: MailConfig) -> None:
    with imaplib.IMAP4_SSL(config.imap_host, config.imap_port) as imap:
        imap.login(config.email_address, config.app_password)
        status, data = imap.list()
        if status != "OK":
            raise SystemExit(f"IMAP LIST failed: {data}")
        for raw_line in data:
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace")
            encoded_name = extract_list_mailbox_name(line)
            display_name = modified_utf7_decode(encoded_name) if encoded_name else line
            print(display_name)
        imap.logout()


def extract_list_mailbox_name(line: str) -> str | None:
    quoted = re.findall(r'"([^"]*)"', line)
    if quoted:
        return quoted[-1]
    parts = line.rsplit(" ", 1)
    return parts[-1] if parts else None


def select_folder(imap: imaplib.IMAP4_SSL, folder: str) -> None:
    candidates = []
    if folder.isascii():
        candidates.append(folder)
        candidates.append(f"INBOX/{folder}")
    candidates.extend(
        [
            modified_utf7_encode(folder),
            modified_utf7_encode(f"INBOX/{folder}"),
        ]
    )

    errors: list[str] = []
    for candidate in dict.fromkeys(candidates):
        try:
            status, data = imap.select(f'"{candidate}"', readonly=True)
        except UnicodeEncodeError as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        if status == "OK":
            return
        errors.append(f"{candidate}: {data}")

    raise SystemExit(
        "Could not select IMAP folder. Tried: " + "; ".join(errors)
    )


def search_since(imap: imaplib.IMAP4_SSL, since: date) -> list[bytes]:
    status, data = imap.uid("SEARCH", None, "SINCE", imap_date(since))
    if status != "OK":
        raise SystemExit(f"IMAP SEARCH failed: {data}")
    if not data or not data[0]:
        return []
    return data[0].split()


def fetch_message_without_seen(imap: imaplib.IMAP4_SSL, uid: bytes) -> Message:
    status, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
    if status != "OK":
        raise RuntimeError(f"IMAP FETCH failed for UID {uid!r}: {data}")

    for item in data:
        if isinstance(item, tuple) and item[1]:
            return email.message_from_bytes(item[1], policy=policy.default)

    raise RuntimeError(f"Message body not found for UID {uid!r}")


def iter_pdf_attachments(message: Message) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue

        filename = part.get_filename() or ""
        content_type = part.get_content_type().lower()
        disposition = (part.get_content_disposition() or "").lower()
        is_pdf = filename.lower().endswith(".pdf") or content_type == "application/pdf"
        if not is_pdf or disposition not in {"attachment", "inline", ""}:
            continue

        payload = part.get_payload(decode=True)
        if not payload:
            continue

        attachments.append(
            {
                "filename": filename or "attachment.pdf",
                "content": payload,
            }
        )
    return attachments


def save_attachment(
    output_dir: Path,
    uid: str,
    attachment_index: int,
    filename: str,
    content: bytes,
    digest: str,
) -> Path:
    clean_name = sanitize_filename(filename)
    if not clean_name.lower().endswith(".pdf"):
        clean_name += ".pdf"

    path = output_dir / clean_name
    if path.exists():
        stem = path.stem
        path = output_dir / f"{stem}__uid{uid}_{attachment_index}_{digest[:8]}.pdf"

    path.write_bytes(content)
    return path


def sanitize_filename(filename: str) -> str:
    name = filename.replace("\x00", "").strip()
    name = re.sub(r"[/:\\]+", "_", name)
    name = re.sub(r"\s+", " ", name)
    return name or "attachment.pdf"


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"attachments": []}
    return json.loads(path.read_text(encoding="utf-8"))


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def subtract_months(value: date, months: int) -> date:
    month_index = value.month - 1 - months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, days_in_month(year, month))
    return date(year, month, day)


def days_in_month(year: int, month: int) -> int:
    if month == 2:
        if year % 400 == 0 or (year % 4 == 0 and year % 100 != 0):
            return 29
        return 28
    return 30 if month in {4, 6, 9, 11} else 31


def imap_date(value: date) -> str:
    return f"{value.day:02d}-{IMAP_MONTHS[value.month - 1]}-{value.year}"


def modified_utf7_encode(value: str) -> str:
    result: list[str] = []
    buffer: list[str] = []

    def flush_buffer() -> None:
        if not buffer:
            return
        utf16 = "".join(buffer).encode("utf-16-be")
        encoded = base64.b64encode(utf16).decode("ascii").rstrip("=").replace("/", ",")
        result.append(f"&{encoded}-")
        buffer.clear()

    for char in value:
        code = ord(char)
        if 0x20 <= code <= 0x7E:
            flush_buffer()
            result.append("&-" if char == "&" else char)
        else:
            buffer.append(char)

    flush_buffer()
    return "".join(result)


def modified_utf7_decode(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "&":
            result.append(value[index])
            index += 1
            continue

        end = value.find("-", index)
        if end == -1:
            result.append(value[index:])
            break

        payload = value[index + 1 : end]
        if payload == "":
            result.append("&")
        else:
            encoded = payload.replace(",", "/")
            padding = "=" * ((4 - len(encoded) % 4) % 4)
            result.append(base64.b64decode(encoded + padding).decode("utf-16-be"))
        index = end + 1
    return "".join(result)


if __name__ == "__main__":
    main()
