import time

from bot.session_bot import build_all_sessions_message, build_session_message
from bot.telegram import send_telegram_message, telegram_request


COMMANDS = {
    "/tokyo": "tokyo",
    "/london": "london",
    "/newyork": "new_york",
    "/new_york": "new_york",
    "/overlap": "overlap",
}


HELP_TEXT = """Forex Signal Bot

Commands:
/london - Scan London session
/tokyo - Scan Tokyo session
/newyork - Scan New York session
/overlap - Scan London/New York overlap
/scanall - Scan all market sessions
/status - Check bot status
/help - Show commands
"""


def handle_message(message: dict) -> None:
    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip().lower()

    if not chat_id or not text:
        return

    command = text.split()[0]

    if command in {"/start", "/help"}:
        send_telegram_message(HELP_TEXT, chat_id=chat_id)
        return

    if command == "/scanall":
        send_telegram_message("Scanning all market sessions. One moment...", chat_id=chat_id)
        message_text = build_all_sessions_message(min_confidence=60)
        send_telegram_message(message_text, chat_id=chat_id)
        return

    if command == "/status":
        send_telegram_message("Forex Signal Bot is online.", chat_id=chat_id)
        return

    if command in COMMANDS:
        send_telegram_message("Scanning market. One moment...", chat_id=chat_id)
        message_text = build_session_message(COMMANDS[command], min_confidence=60)
        send_telegram_message(message_text, chat_id=chat_id)
        return

    send_telegram_message("Unknown command. Send /help to see available commands.", chat_id=chat_id)


def main() -> None:
    print("Telegram command bot is running...")
    offset = None

    while True:
        payload = {"timeout": 30}
        if offset is not None:
            payload["offset"] = offset

        try:
            updates = telegram_request("getUpdates", payload).get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update:
                    handle_message(update["message"])

        except Exception as exc:
            print(f"Command bot error: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
