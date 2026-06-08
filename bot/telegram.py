import os
import requests
from dotenv import load_dotenv


def telegram_request(method: str, payload: dict | None = None) -> dict:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    url = f"https://api.telegram.org/bot{token}/{method}"
    response = requests.post(url, json=payload or {}, timeout=30)

    if not response.ok:
        raise RuntimeError(f"Telegram API error: HTTP {response.status_code}")

    return response.json()


def send_telegram_message(message: str, chat_id: str | int | None = None) -> None:
    load_dotenv()

    target_chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    if not target_chat_id:
        print("Telegram chat is not configured. Skipping message.")
        return

    telegram_request(
        "sendMessage",
        {
            "chat_id": target_chat_id,
            "text": message,
        },
    )
