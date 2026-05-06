from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from telegram_report import (
    DEFAULT_DATA_PATH,
    ReportError,
    available_resources,
    load_meters,
    render_hourly_report,
    resource_label,
    short_meter_title,
)


ENV_PATH = Path(".env")
TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
ALLOWED_CHAT_IDS_ENV = "TELEGRAM_ALLOWED_CHAT_IDS"
POLL_TIMEOUT = 45
HTTP_TIMEOUT = 90

RESOURCE_CODES = {
    "ГВС": "gvs",
    "ТС": "ts",
    "ТЭ": "ts",
}
CODE_RESOURCES = {value: key for key, value in RESOURCE_CODES.items()}
CODE_RESOURCES["ts"] = "ТС"


def main() -> None:
    load_env_file(ENV_PATH)
    token = os.getenv(TOKEN_ENV)
    if not token or token.startswith("put_"):
        raise SystemExit(
            "Добавьте TELEGRAM_BOT_TOKEN в .env. Токен берется у @BotFather."
        )

    bot = TelegramBot(
        token=token,
        data_path=DEFAULT_DATA_PATH,
        allowed_chat_ids=parse_allowed_chat_ids(os.getenv(ALLOWED_CHAT_IDS_ENV, "")),
    )
    print("Telegram bot started. Press Ctrl+C to stop.")
    bot.run()


class TelegramBot:
    def __init__(
        self,
        token: str,
        data_path: str | Path,
        allowed_chat_ids: set[int] | None = None,
    ) -> None:
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.data_path = Path(data_path)
        self.allowed_chat_ids = allowed_chat_ids or set()
        self.offset: int | None = None

    def run(self) -> None:
        while True:
            try:
                updates = self.api(
                    "getUpdates",
                    {
                        "timeout": POLL_TIMEOUT,
                        "offset": self.offset,
                        "allowed_updates": json.dumps(["message", "callback_query"]),
                    },
                    timeout=POLL_TIMEOUT + 10,
                ).get("result", [])
                for update in updates:
                    self.offset = update["update_id"] + 1
                    self.handle_update(update)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"Telegram bot error: {exc}")
                time.sleep(5)

    def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            self.handle_callback(update["callback_query"])
            return

        message = update.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        if not chat_id:
            return
        if not self.is_allowed(chat_id):
            self.send_message(chat_id, f"Доступ не разрешен. Ваш Telegram ID: {chat_id}")
            return

        text = (message.get("text") or "").strip()
        if text.startswith("/start") or text.startswith("/help"):
            self.send_main_menu(chat_id)
            return

        self.send_message(
            chat_id,
            "Нажмите кнопку ниже, чтобы создать часовую распечатку за последние 36 часов.",
            reply_markup=main_menu_markup(),
        )

    def handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback.get("id")
        message = callback.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        data = callback.get("data") or ""
        if callback_id:
            self.api("answerCallbackQuery", {"callback_query_id": callback_id}, timeout=15)
        if not chat_id:
            return
        if not self.is_allowed(chat_id):
            self.send_message(chat_id, f"Доступ не разрешен. Ваш Telegram ID: {chat_id}")
            return

        if data == "create_report":
            self.send_resource_menu(chat_id)
            return
        if data.startswith("res:"):
            self.send_meter_menu(chat_id, data.split(":", 1)[1])
            return
        if data.startswith("meter:"):
            self.create_report(chat_id, data.split(":", 1)[1])
            return
        if data == "back:start":
            self.send_main_menu(chat_id)
            return

        self.send_message(chat_id, "Команда устарела. Начните заново.", reply_markup=main_menu_markup())

    def send_main_menu(self, chat_id: int) -> None:
        self.send_message(
            chat_id,
            "МОЭК: контроль теплосчетчиков.\nПервая функция: графическая часовая распечатка за последние 36 часов.",
            reply_markup=main_menu_markup(),
        )

    def send_resource_menu(self, chat_id: int) -> None:
        try:
            meters = load_meters(self.data_path)
        except ReportError as exc:
            self.send_message(chat_id, str(exc))
            return

        rows = []
        for resource in available_resources(meters):
            code = RESOURCE_CODES.get(resource, urllib.parse.quote(resource, safe=""))
            count = sum(1 for meter in meters if meter.get("resource") == resource)
            rows.append(
                [
                    {
                        "text": f"{resource_label(resource)} ({count})",
                        "callback_data": f"res:{code}",
                    }
                ]
            )
        rows.append([{"text": "Назад", "callback_data": "back:start"}])
        self.send_message(chat_id, "Выберите систему:", reply_markup={"inline_keyboard": rows})

    def send_meter_menu(self, chat_id: int, resource_code: str) -> None:
        try:
            meters = load_meters(self.data_path)
        except ReportError as exc:
            self.send_message(chat_id, str(exc))
            return

        resource = CODE_RESOURCES.get(resource_code)
        if resource is None:
            resource = urllib.parse.unquote(resource_code)

        rows = []
        for index, meter in enumerate(meters):
            if meter.get("resource") != resource:
                continue
            rows.append(
                [
                    {
                        "text": trim_button(short_meter_title(meter)),
                        "callback_data": f"meter:{index}",
                    }
                ]
            )
        if not rows:
            self.send_message(chat_id, "Для этой системы счетчики не найдены.", reply_markup=main_menu_markup())
            return
        rows.append([{"text": "Назад", "callback_data": "create_report"}])
        self.send_message(chat_id, f"Выберите теплосчетчик ({resource_label(resource)}):", reply_markup={"inline_keyboard": rows})

    def create_report(self, chat_id: int, meter_index: str) -> None:
        try:
            index = int(meter_index)
            meters = load_meters(self.data_path)
            meter = meters[index]
        except (ValueError, IndexError):
            self.send_message(chat_id, "Не удалось найти выбранный теплосчетчик. Начните заново.", reply_markup=main_menu_markup())
            return
        except ReportError as exc:
            self.send_message(chat_id, str(exc))
            return

        self.send_message(chat_id, "Создаю графическую распечатку...")
        try:
            path = render_hourly_report(meter)
        except ReportError as exc:
            self.send_message(chat_id, str(exc))
            return

        caption = f"Часовая распечатка: {short_meter_title(meter)}"
        self.send_photo(chat_id, path, caption=caption)

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        self.api("sendMessage", payload)

    def send_photo(self, chat_id: int, path: str | Path, caption: str = "") -> None:
        fields = {"chat_id": str(chat_id), "caption": caption}
        files = {"photo": Path(path)}
        self.multipart_api("sendPhoto", fields, files)

    def is_allowed(self, chat_id: int) -> bool:
        return not self.allowed_chat_ids or chat_id in self.allowed_chat_ids

    def api(
        self,
        method: str,
        payload: dict[str, Any],
        timeout: int = HTTP_TIMEOUT,
    ) -> dict[str, Any]:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(result)
        return result

    def multipart_api(
        self,
        method: str,
        fields: dict[str, str],
        files: dict[str, Path],
    ) -> dict[str, Any]:
        boundary = f"----moek-{uuid.uuid4().hex}"
        body = bytearray()
        for name, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        for name, path in files.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{path.name}"\r\n'
                    "Content-Type: image/png\r\n\r\n"
                ).encode("utf-8")
            )
            body.extend(path.read_bytes())
            body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode())

        request = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(exc.read().decode("utf-8")) from exc
        if not result.get("ok"):
            raise RuntimeError(result)
        return result


def main_menu_markup() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Создать часовую распечатку (36ч)",
                    "callback_data": "create_report",
                }
            ]
        ]
    }


def trim_button(text: str, limit: int = 58) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


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


def parse_allowed_chat_ids(value: str) -> set[int]:
    ids: set[int] = set()
    for item in value.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.add(int(item))
        except ValueError:
            print(f"Skipped invalid Telegram chat ID: {item}")
    return ids


if __name__ == "__main__":
    main()
