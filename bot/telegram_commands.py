import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bot.session_bot import build_session_message
from bot.telegram import send_telegram_message, telegram_request


COMMANDS = {
    "/f1": "f1",
    "/f2": "f2",
    "/c1": "c1",
    "/c2": "c2",
    "/crypto": "c1",
    "/crypto1": "c1",
    "/crypto2": "c2",
    "/m1": "m1",
    "/m2": "m2",
    "/meme": "m1",
    "/meme1": "m1",
    "/meme2": "m2",
    "/memes": "m1",
}


HELP_TEXT = """🔥 <b>Forex, Crypto & Memecoin Signal Bot</b>

📈 <b>Forex & Commodities:</b>
• <code>/f1</code> — Forex Majors (EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD)
• <code>/f2</code> — Crosses & Gold (NZDUSD, EURGBP, EURJPY, GBPJPY, Gold, US30)

🚀 <b>Crypto Markets:</b>
• <code>/c1</code> — Major Cryptos (BTC, ETH, SOL, XRP, DOGE, ADA)
• <code>/c2</code> — High-Momentum Altcoins (BNB, AVAX, LINK, SUI, NEAR, LTC)

🐶🐸 <b>High-Volatility Memecoins:</b>
• <code>/m1</code> — Top Memes (WIF, PEPE, SHIB, BONK, FLOKI, BRETT, ANSEM)
• <code>/m2</code> — Trending & Narrative Memes (TRUMP, BOME, PENGU, MOG, PEOPLE, ELON)

ℹ️ <b>Info:</b>
• <code>/status</code> — Bot Health & Status
• <code>/help</code> — Show this menu
"""


def handle_message(message: dict) -> None:
    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip().lower()

    if not chat_id or not text:
        return

    command = text.split()[0]
    print(f"Received command '{command}' from chat {chat_id}")

    try:
        if command in {"/start", "/help"}:
            send_telegram_message(HELP_TEXT, chat_id=chat_id)
            return

        if command == "/status":
            send_telegram_message("Forex & Crypto Signal Bot is online and active.", chat_id=chat_id)
            return

        if command in COMMANDS:
            send_telegram_message("Scanning market. One moment...", chat_id=chat_id)
            message_text = build_session_message(COMMANDS[command], min_confidence=60)
            send_telegram_message(message_text, chat_id=chat_id)
            return

        send_telegram_message("Unknown command. Send /help to see available commands.", chat_id=chat_id)
    except Exception as exc:
        print(f"Error handling command '{command}' for chat {chat_id}: {exc}")
        send_telegram_message(f"Error processing {command}: {exc}", chat_id=chat_id)


def main() -> None:
    print("Telegram command bot is active and listening for messages...")
    offset = None

    while True:
        payload = {"timeout": 10}
        if offset is not None:
            payload["offset"] = offset

        try:
            updates = telegram_request("getUpdates", payload, timeout=25).get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update:
                    handle_message(update["message"])

        except Exception as exc:
            print(f"Polling error: {exc}")
            time.sleep(3)


if __name__ == "__main__":
    main()
