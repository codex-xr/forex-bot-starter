import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bot.autopilot import autopilot
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

🤖 <b>Auto-Pilot 24/7 (Single-Pair Alerts):</b>
• <code>/autopilot</code> — View Auto-Pilot status & recent hits
• <code>/autopilot on</code> — Activate 24/7 background signal hunting
• <code>/autopilot off</code> — Pause background monitoring

📈 <b>Manual Forex & Commodities Scans:</b>
• <code>/f1</code> — Forex Majors (EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD)
• <code>/f2</code> — Crosses & Gold (NZDUSD, EURGBP, EURJPY, GBPJPY, Gold, US30)

🚀 <b>Manual Crypto Market Scans:</b>
• <code>/c1</code> — Major Cryptos (BTC, ETH, SOL, XRP, DOGE, ADA)
• <code>/c2</code> — High-Momentum Altcoins (BNB, AVAX, LINK, SUI, NEAR, LTC)

🐶🐸 <b>Manual High-Volatility Memecoin Scans:</b>
• <code>/m1</code> — Top Memes (WIF, PEPE, SHIB, BONK, FLOKI, BRETT, ANSEM)
• <code>/m2</code> — Trending & Narrative Memes (TRUMP, BOME, PENGU, MOG, PEOPLE, ELON)

ℹ️ <b>Info:</b>
• <code>/status</code> — Bot Health & Active Status
• <code>/help</code> — Show this menu
"""


def handle_message(message: dict) -> None:
    chat_id = message.get("chat", {}).get("id")
    raw_text = (message.get("text") or "").strip()
    text = raw_text.lower()

    if not chat_id or not text:
        return

    parts = text.split()
    command = parts[0]
    subcmd = parts[1] if len(parts) > 1 else ""

    print(f"Received command '{raw_text}' from chat {chat_id}")

    try:
        if command in {"/start", "/help"}:
            send_telegram_message(HELP_TEXT, chat_id=chat_id)
            return

        if command in {"/autopilot", "/autopilot_status", "/autopilot_on", "/autopilot_off"}:
            if command == "/autopilot_on" or subcmd == "on":
                autopilot.resume()
                send_telegram_message(
                    "🟢 <b>Auto-Pilot Activated!</b>\n\n"
                    "Hunting 24/7 for single-pair trade setups ($\ge 80\%$ confidence). "
                    "When a setup triggers, you'll receive a direct single-trade alert.",
                    chat_id=chat_id,
                )
                return
            elif command == "/autopilot_off" or subcmd == "off":
                autopilot.stop()
                send_telegram_message(
                    "🔴 <b>Auto-Pilot Paused.</b>\n\nBackground scanning is suspended. Send <code>/autopilot on</code> to resume.",
                    chat_id=chat_id,
                )
                return
            else:
                send_telegram_message(autopilot.get_status_text(), chat_id=chat_id)
                return

        if command == "/status":
            auto_state = "🟢 ACTIVE" if autopilot.is_active else "🔴 PAUSED"
            send_telegram_message(
                f"✅ <b>Bot Online & Active</b>\n\n"
                f"• <b>Commands:</b> <code>/f1</code>, <code>/f2</code>, <code>/c1</code>, <code>/c2</code>, <code>/m1</code>, <code>/m2</code>\n"
                f"• <b>Auto-Pilot:</b> {auto_state}\n"
                f"• <b>DEX Streamer:</b> Solana ($ANSEM) connected",
                chat_id=chat_id,
            )
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
    print("Starting AutoPilot background engine...")
    autopilot.start_background()
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
