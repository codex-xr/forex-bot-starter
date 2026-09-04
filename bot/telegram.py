import os
import requests
from dotenv import load_dotenv


def telegram_request(method: str, payload: dict | None = None, timeout: int = 35) -> dict:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    url = f"https://api.telegram.org/bot{token}/{method}"
    response = requests.post(url, json=payload or {}, timeout=timeout)

    if not response.ok:
        try:
            err_data = response.json()
            description = err_data.get("description", f"HTTP {response.status_code}")
        except Exception:
            description = f"HTTP {response.status_code}"
        raise RuntimeError(f"Telegram API error ({description})")

    return response.json()


def send_telegram_message(
    message: str,
    chat_id: str | int | None = None,
    parse_mode: str = "HTML",
    reply_markup: dict | None = None,
) -> None:
    load_dotenv()

    target_chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    if not target_chat_id:
        print("Telegram chat is not configured. Skipping message.")
        return

    payload = {
        "chat_id": target_chat_id,
        "text": message,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    telegram_request("sendMessage", payload)


def register_telegram_commands() -> dict:
    """
    Registers command lists with Telegram:
    - Default scope (regular users): signal scanning, account, help commands only.
    - Admin scope (admin chat): full admin suite + signal scanning commands.
    """
    user_commands = [
        {"command": "menu", "description": "📱 Interactive Signal Dashboard"},
        {"command": "f1", "description": "📈 Forex Majors (EUR, GBP, JPY, CAD...)"},
        {"command": "f2", "description": "🏆 Crosses, Gold (XAU) & US30"},
        {"command": "c1", "description": "🚀 Major Cryptos (BTC, ETH, SOL, XRP...)"},
        {"command": "c2", "description": "⚡ High-Momentum Altcoins (BNB, SUI...)"},
        {"command": "m1", "description": "🐶 Top Memes (WIF, PEPE, BONK, SHIB)"},
        {"command": "m2", "description": "🐸 Trending Memes (TRUMP, PENGU...)"},
        {"command": "news", "description": "📰 Breaking Catalysts & News Monitor"},
        {"command": "autopilot", "description": "🤖 Auto-Pilot 24/7 Status"},
        {"command": "myplan", "description": "ℹ️ View VIP Subscription Status"},
        {"command": "redeem", "description": "🔑 Activate VIP Key (/redeem KEY)"},
        {"command": "help", "description": "❓ Show Full Help & Command Guide"},
    ]

    admin_commands = [
        {"command": "admin", "description": "👑 Master Admin Control Panel"},
        {"command": "menu", "description": "📱 Interactive Signal Dashboard"},
        {"command": "users", "description": "👥 User Dashboard & Access Tracker"},
        {"command": "genkey", "description": "🔑 Generate VIP Key (/genkey 30d)"},
        {"command": "grant", "description": "🎁 Direct VIP Grant (/grant id 30d)"},
        {"command": "revoke", "description": "🚫 Revoke/Ban User Access"},
        {"command": "unban", "description": "✅ Restore/Unban User Access"},
        {"command": "keys", "description": "🗝️ View Available & Redeemed Keys"},
        {"command": "broadcast", "description": "📢 Instant Announcement to All"},
        {"command": "schedule", "description": "⏰ Schedule Broadcast Alert"},
        {"command": "schedules", "description": "📋 View Active Schedules"},
        {"command": "f1", "description": "📈 Forex Majors Scan"},
        {"command": "c1", "description": "🚀 Major Cryptos Scan"},
        {"command": "m1", "description": "🐶 Top Memes Scan"},
        {"command": "news", "description": "📰 Breaking Catalysts & News"},
        {"command": "autopilot", "description": "🤖 Auto-Pilot 24/7 Status"},
        {"command": "status", "description": "📊 Bot Health Status"},
        {"command": "help", "description": "❓ Show Full Help & Command Guide"},
    ]

    res = {}
    try:
        res["default"] = telegram_request("setMyCommands", {"commands": user_commands, "scope": {"type": "default"}})
    except Exception as exc:
        res["default_error"] = str(exc)

    admin_env = os.getenv("TELEGRAM_CHAT_ID", "6686703329").strip().strip("<>\"'")
    for aid in admin_env.split(","):
        clean_aid = aid.strip()
        if clean_aid.isdigit():
            try:
                res[f"admin_{clean_aid}"] = telegram_request(
                    "setMyCommands",
                    {"commands": admin_commands, "scope": {"type": "chat", "chat_id": int(clean_aid)}},
                )
            except Exception as exc:
                res[f"admin_{clean_aid}_error"] = str(exc)

    return res


